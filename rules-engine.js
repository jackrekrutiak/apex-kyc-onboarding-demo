/**
 * Apex onboarding rules engine (v2) — JavaScript
 * ==============================================
 *
 * Business rules for onboarding an institutional FX client, aligned with
 * FINTRAC's know-your-client requirements for money services businesses:
 *
 *   - Confirm the entity exists (registration number + source document)
 *   - Verify the identity of the person signing for the entity (IDV)
 *   - Obtain and confirm beneficial ownership (individuals owning or
 *     controlling 25%+; trustees, settlors and beneficiaries for trusts)
 *   - PEP / HIO determination, third-party determination
 *   - Purpose and intended nature of the business relationship
 *   - Enhanced measures for high-risk clients, 5-year record retention
 *
 * This file only applies rules to data. A real compliance program also needs
 * a compliance officer, written policies, a risk assessment, training and an
 * effectiveness review. See the README notes in the chat for scope.
 *
 * Run it:   node rules-engine.js
 *
 * Python → JavaScript cheat sheet for this file
 *   def name(x):            →  function name(x) { ... }
 *   my_list = []            →  const myList = [];
 *   x in some_set           →  someSet.has(x)
 *   value if cond else alt  →  cond ? value : alt
 *   f"Hi {name}"            →  `Hi ${name}`
 *   None                    →  null
 *   dict {"a": 1}           →  object { a: 1 }
 */

// ---------------------------------------------------------------------------
// Configuration. In production these would live in a config table owned by
// compliance, so thresholds and lists change without a code release.
// ---------------------------------------------------------------------------

const SCHEMA_VERSION = "2.0.0";

const EDD_JURISDICTIONS = new Set(["Cayman Islands", "Panama"]);
const ENTERPRISE_THRESHOLD = 10_000_000; // CAD
const BO_THRESHOLD_PCT = 25; // FINTRAC: individuals owning or controlling 25%+
const SETTLEMENT_CREDIT = "Post-Trade Credit Line";
const LEI_PATTERN = /^[A-Z0-9]{18}[0-9]{2}$/; // ISO 17442

// Fictional names standing in for a real sanctions-list screening vendor.
const DEMO_WATCHLIST = new Set(["viktor ironhold", "meridian shell holdings ltd."]);

const ID_METHODS = {
  PHOTO_ID: "Government-issued photo ID",
  CREDIT_FILE: "Credit file",
  DUAL_PROCESS: "Dual-process",
};

const BO_OUTCOMES = {
  CONFIRMED: "Owners identified and confirmed",
  NONE_OVER_THRESHOLD: "No individual holds 25% or more",
  UNABLE: "Unable to obtain or confirm",
};

const PEP_STATUSES = {
  NONE: "Not a PEP or HIO",
  DOMESTIC: "Domestic PEP",
  HIO: "Head of an international organization",
  DOMESTIC_ASSOCIATE: "Family / close associate of a domestic PEP or HIO",
  FOREIGN: "Foreign PEP",
  FOREIGN_ASSOCIATE: "Family / close associate of a foreign PEP",
};
const FOREIGN_PEP_VALUES = new Set([PEP_STATUSES.FOREIGN, PEP_STATUSES.FOREIGN_ASSOCIATE]);
const REVIEW_PEP_VALUES = new Set([PEP_STATUSES.DOMESTIC, PEP_STATUSES.HIO, PEP_STATUSES.DOMESTIC_ASSOCIATE]);

// Flags block straight-through approval and say which queue reviews them.
const FLAGS = {
  SANCTIONS: { code: "RISK_FLAG_SANCTIONS_MATCH", queue: "BLOCK",
    note: "A screened name matches the watchlist. Onboarding is frozen and escalated to the compliance officer." },
  OFFSHORE: { code: "RISK_FLAG_OFFSHORE_JURISDICTION", queue: "COMPLIANCE",
    note: "Higher-risk jurisdiction. Enhanced due diligence applies." },
  FOREIGN_PEP: { code: "RISK_FLAG_FOREIGN_PEP", queue: "COMPLIANCE",
    note: "Foreign PEP or associate. Always high risk: source of funds and wealth, plus senior management approval." },
  PEP_REVIEW: { code: "RISK_FLAG_PEP_HIO_REVIEW", queue: "COMPLIANCE",
    note: "Domestic PEP, HIO or associate. Compliance decides on risk whether enhanced measures apply." },
  BO_UNCONFIRMED: { code: "RISK_FLAG_BO_UNCONFIRMED", queue: "COMPLIANCE",
    note: "Beneficial ownership could not be obtained or confirmed. CEO identity verified instead; client treated as high risk." },
  THIRD_PARTY: { code: "RISK_FLAG_THIRD_PARTY", queue: "COMPLIANCE",
    note: "Client is acting on behalf of a third party, whose details are recorded." },
  CREDIT_EXEC: { code: "CREDIT_RISK_FLAG_EXECUTIVE_DESK_REVIEW", queue: "CREDIT",
    note: "Credit line at enterprise volume. Executive credit desk underwrites the exposure." },
  CREDIT: { code: "CREDIT_RISK_FLAG_DESK_REVIEW", queue: "CREDIT",
    note: "Credit line requested. Credit desk underwrites the exposure before approval." },
};

// Tags are informational. They never block approval.
const TAGS = {
  LEI: { code: "DATA_LEI_CAPTURED", note: "Enterprise volume. LEI validated and passed to the CRM." },
  BO_DISCREPANCY: { code: "TASK_BO_DISCREPANCY_CHECK",
    note: "High-risk Canadian corporation. Compare owners with the Corporations Canada registry; report material discrepancies within 30 days." },
};

const STATUS = {
  BLOCKED: "Blocked: Sanctions Escalation",
  COMPLIANCE: "Pending Compliance Review",
  CREDIT: "Pending Credit Underwriting",
  STP: "Straight-Through Approved",
};

const WEBHOOK_BASE = "https://mock-api.apex-demo.internal/v1/webhooks";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const clean = (value) => (value == null ? "" : String(value).trim());
const splitNames = (text) => clean(text).split(/[,\n]/).map((s) => s.trim()).filter(Boolean);

// Owners at or above the threshold with a name. Rows below 25% are dropped:
// FINTRAC asks for 25%+ owners, and data minimization says keep nothing extra.
function qualifyingOwners(owners) {
  return (owners || [])
    .map((o) => ({ name: clean(o.name), address: clean(o.address), ownershipPct: Number(o.ownershipPct) || 0, pepStatus: o.pepStatus || PEP_STATUSES.NONE }))
    .filter((o) => o.name && o.ownershipPct >= BO_THRESHOLD_PCT);
}

function screenedNames(input) {
  const names = [clean(input.legalName), clean(input.signatory?.name), clean(input.thirdPartyName), clean(input.controlPersonName)];
  qualifyingOwners(input.owners).forEach((o) => names.push(o.name));
  if (input.structureType === "Offshore Trust") {
    ["trustees", "settlors", "beneficiaries"].forEach((k) => names.push(...splitNames(input.trustParties?.[k])));
  }
  return names.filter(Boolean);
}

// ---------------------------------------------------------------------------
// Rules: each is a pure function, so the same input always gives the same output.
// ---------------------------------------------------------------------------

function computeFlags(input) {
  const isTrust = input.structureType === "Offshore Trust";
  const owners = isTrust ? [] : qualifyingOwners(input.owners);
  const pepStatuses = [input.signatory?.pepStatus, ...owners.map((o) => o.pepStatus)];
  const names = screenedNames(input).map((n) => n.toLowerCase());

  const flags = [];
  if (names.some((n) => DEMO_WATCHLIST.has(n))) flags.push(FLAGS.SANCTIONS.code);
  if (EDD_JURISDICTIONS.has(input.jurisdiction)) flags.push(FLAGS.OFFSHORE.code);
  if (pepStatuses.some((s) => FOREIGN_PEP_VALUES.has(s))) flags.push(FLAGS.FOREIGN_PEP.code);
  else if (pepStatuses.some((s) => REVIEW_PEP_VALUES.has(s))) flags.push(FLAGS.PEP_REVIEW.code);
  if (input.boOutcome === BO_OUTCOMES.UNABLE) flags.push(FLAGS.BO_UNCONFIRMED.code);
  if (input.actingForThirdParty) flags.push(FLAGS.THIRD_PARTY.code);

  // Any credit line is credit exposure, so it always gets reviewed.
  if (input.settlement === SETTLEMENT_CREDIT) {
    flags.push(Number(input.fxVolume) >= ENTERPRISE_THRESHOLD ? FLAGS.CREDIT_EXEC.code : FLAGS.CREDIT.code);
  }
  return flags;
}

function computeRiskRating(input, flags) {
  const high = [FLAGS.SANCTIONS.code, FLAGS.OFFSHORE.code, FLAGS.FOREIGN_PEP.code, FLAGS.BO_UNCONFIRMED.code];
  if (flags.some((f) => high.includes(f))) return "HIGH";
  if (flags.includes(FLAGS.PEP_REVIEW.code) || flags.includes(FLAGS.THIRD_PARTY.code) || input.structureType === "Offshore Trust") return "MEDIUM";
  return "LOW";
}

function computeTags(input, riskRating) {
  const tags = [];
  if (Number(input.fxVolume) >= ENTERPRISE_THRESHOLD) tags.push(TAGS.LEI.code);
  if (riskRating === "HIGH" && input.jurisdiction === "Canada" && input.structureType === "Corporation") tags.push(TAGS.BO_DISCREPANCY.code);
  return tags;
}

function resolveStatus(flags) {
  const queueOf = (code) => Object.values(FLAGS).find((f) => f.code === code).queue;
  const queues = new Set(flags.map(queueOf));
  if (queues.has("BLOCK")) return STATUS.BLOCKED;
  if (queues.has("COMPLIANCE")) return STATUS.COMPLIANCE;
  if (queues.has("CREDIT")) return STATUS.CREDIT;
  return STATUS.STP;
}

function resolveWebhooks(input, flags, riskRating) {
  let kycPriority = riskRating === "HIGH" ? "ENHANCED" : "STANDARD";
  if (flags.includes(FLAGS.SANCTIONS.code)) kycPriority = "BLOCKING";

  let creditPriority = "NOTIFY_ONLY";
  if (flags.includes(FLAGS.CREDIT_EXEC.code)) creditPriority = "EXECUTIVE";
  else if (flags.includes(FLAGS.CREDIT.code)) creditPriority = "STANDARD";

  return [
    { target: "CORE_CRM_SALESFORCE", endpoint: `${WEBHOOK_BASE}/salesforce/account-upsert`,
      priority: flags.includes(FLAGS.SANCTIONS.code) ? "HOLD" : "STANDARD" },
    { target: "KYC_AML_VERIFICATION", endpoint: `${WEBHOOK_BASE}/kyc-aml/screen`, priority: kycPriority },
    { target: "CREDIT_RISK_DESK", endpoint: `${WEBHOOK_BASE}/credit-risk/review`, priority: creditPriority },
  ];
}

// Only the record fields FINTRAC asks for under the chosen method are kept.
function buildIdentityVerification(sig) {
  const method = sig.idMethod;
  if (method === ID_METHODS.PHOTO_ID) {
    return { method, document_type: clean(sig.docType) || null, document_number: clean(sig.docNumber) || null,
      issuing_jurisdiction: clean(sig.docJurisdiction) || null, expiry_date: clean(sig.docExpiry) || null,
      authenticity_and_liveness_check: Boolean(sig.authenticityCheckPassed) };
  }
  if (method === ID_METHODS.CREDIT_FILE) {
    return { method, credit_bureau: clean(sig.bureauName) || null, credit_file_reference: clean(sig.creditFileRef) || null };
  }
  if (method === ID_METHODS.DUAL_PROCESS) {
    return { method, source_one: clean(sig.sourceOne) || null, source_two: clean(sig.sourceTwo) || null };
  }
  return { method: null };
}

/**
 * Compile raw form answers into the canonical payload.
 * `asOf` is today's date as YYYY-MM-DD. Passing it in (instead of reading the
 * clock inside) keeps the function testable.
 */
function buildPayload(input, asOf) {
  const sig = input.signatory || {};
  const isTrust = input.structureType === "Offshore Trust";
  const volume = Number(input.fxVolume) || 0;
  const enterprise = volume >= ENTERPRISE_THRESHOLD;
  const flags = computeFlags(input);
  const riskRating = computeRiskRating(input, flags);
  const tags = computeTags(input, riskRating);
  const needsControlPerson = input.boOutcome === BO_OUTCOMES.UNABLE || input.boOutcome === BO_OUTCOMES.NONE_OVER_THRESHOLD;

  return {
    schema_version: SCHEMA_VERSION,
    assessed_on: asOf,
    entity: {
      legal_name: clean(input.legalName) || null,
      structure_type: input.structureType,
      jurisdiction: input.jurisdiction,
      principal_business: clean(input.principalBusiness) || null,
      existence_verification: {
        document_type: input.existenceDocument || null,
        registration_number: clean(input.registrationNumber) || null,
      },
      directors: input.structureType === "Corporation" ? splitNames(input.directors) : [],
      lei: enterprise ? clean(input.lei).toUpperCase() || null : null,
    },
    relationship: {
      purpose: input.purpose || null,
      estimated_annual_fx_volume_cad: volume,
      currency: "CAD",
      volume_tier: enterprise ? "ENTERPRISE" : "STANDARD",
      settlement_structure: input.settlement,
    },
    signatory: {
      name: clean(sig.name) || null,
      date_of_birth: clean(sig.dateOfBirth) || null,
      address: clean(sig.address) || null,
      occupation: clean(sig.occupation) || null,
      pep_status: sig.pepStatus || PEP_STATUSES.NONE,
      identity_verification: buildIdentityVerification(sig),
    },
    beneficial_ownership: {
      threshold_pct: BO_THRESHOLD_PCT,
      outcome: input.boOutcome || null,
      owners: isTrust ? [] : qualifyingOwners(input.owners).map((o) => ({
        name: o.name, address: o.address || null, ownership_pct: o.ownershipPct, pep_status: o.pepStatus })),
      trust_parties: isTrust ? {
        trustees: splitNames(input.trustParties?.trustees),
        settlors: splitNames(input.trustParties?.settlors),
        beneficiaries: splitNames(input.trustParties?.beneficiaries),
      } : null,
      confirmation_method: input.boOutcome === BO_OUTCOMES.UNABLE ? null : input.boConfirmationMethod || null,
      control_person_verified: needsControlPerson ? clean(input.controlPersonName) || null : null,
    },
    third_party: input.actingForThirdParty
      ? { acting_for_third_party: true, name: clean(input.thirdPartyName) || null, relationship: clean(input.thirdPartyRelationship) || null }
      : { acting_for_third_party: false },
    compliance: {
      risk_rating: riskRating,
      enhanced_due_diligence: riskRating === "HIGH",
      senior_management_approval_required: riskRating === "HIGH",
      source_of_funds_and_wealth: riskRating === "HIGH" ? clean(input.sourceOfWealth) || null : null,
      sanctions_screening: {
        list: "DEMO_WATCHLIST",
        names_screened: screenedNames(input).length,
        match: flags.includes(FLAGS.SANCTIONS.code),
      },
    },
    routing: {
      flags,
      tags,
      workflow_status: resolveStatus(flags),
      webhooks: resolveWebhooks(input, flags, riskRating),
    },
    record_keeping: {
      regime: "PCMLTFA / FINTRAC (money services business)",
      retention: "At least 5 years after the last transaction or the end of the relationship",
    },
  };
}

function validatePayload(payload, asOf) {
  const errors = [];
  const { entity, relationship, signatory, beneficial_ownership: bo, third_party: tp, compliance } = payload;
  const idv = signatory.identity_verification;

  // Entity existence and relationship
  if (!entity.legal_name) errors.push("Legal entity name is required.");
  if (!entity.existence_verification.registration_number) errors.push("Registration or incorporation number is required.");
  if (!entity.existence_verification.document_type) errors.push("Choose the document used to confirm the entity exists.");
  if (!entity.principal_business) errors.push("Describe the nature of the entity's principal business.");
  if (entity.structure_type === "Corporation" && entity.directors.length === 0) errors.push("List the names of all directors.");
  if (!relationship.purpose) errors.push("Choose the purpose of the business relationship.");
  if (relationship.volume_tier === "ENTERPRISE") {
    if (!entity.lei) errors.push("LEI is mandatory for enterprise-volume accounts.");
    else if (!LEI_PATTERN.test(entity.lei)) errors.push("LEI must be 20 characters: 18 letters or digits, then 2 check digits.");
  }

  // Signatory identity verification
  if (!signatory.name || !signatory.date_of_birth || !signatory.address || !signatory.occupation) {
    errors.push("Signatory name, date of birth, address and occupation are all required.");
  }
  if (!idv.method) errors.push("Choose how the signatory's identity was verified.");
  if (idv.method === ID_METHODS.PHOTO_ID) {
    if (!idv.document_type || !idv.document_number || !idv.issuing_jurisdiction || !idv.expiry_date) {
      errors.push("Photo ID needs document type, number, issuing jurisdiction and expiry date.");
    } else if (idv.expiry_date < asOf) {
      errors.push("The photo ID has expired. FINTRAC requires a valid, current document.");
    }
    if (!idv.authenticity_and_liveness_check) errors.push("The document authenticity and liveness check has not passed.");
  }
  if (idv.method === ID_METHODS.CREDIT_FILE && (!idv.credit_bureau || !idv.credit_file_reference)) {
    errors.push("Credit file method needs the Canadian credit bureau and the file reference.");
  }
  if (idv.method === ID_METHODS.DUAL_PROCESS) {
    if (!idv.source_one || !idv.source_two) errors.push("Dual-process needs two reliable sources.");
    else if (idv.source_one.toLowerCase() === idv.source_two.toLowerCase()) errors.push("Dual-process sources must be two different sources.");
  }

  // Beneficial ownership
  const isTrust = entity.structure_type === "Offshore Trust";
  if (!bo.outcome) errors.push("Record the beneficial ownership outcome.");
  if (bo.outcome === BO_OUTCOMES.CONFIRMED) {
    if (isTrust) {
      const t = bo.trust_parties;
      if (!t.trustees.length || !t.settlors.length || !t.beneficiaries.length) errors.push("List all trustees, settlors and known beneficiaries.");
    } else {
      if (bo.owners.length === 0) errors.push(`Add every individual owning or controlling ${BO_THRESHOLD_PCT}% or more.`);
      if (bo.owners.some((o) => !o.address)) errors.push("Each beneficial owner needs an address.");
      if (bo.owners.reduce((sum, o) => sum + o.ownership_pct, 0) > 100) errors.push("Beneficial ownership adds up to more than 100%.");
    }
  }
  if (bo.outcome === BO_OUTCOMES.NONE_OVER_THRESHOLD && isTrust) errors.push("Trusts must record trustees, settlors and beneficiaries, not a 25% outcome.");
  if (bo.outcome && bo.outcome !== BO_OUTCOMES.UNABLE && !bo.confirmation_method) errors.push("Choose how beneficial ownership was confirmed.");
  if ((bo.outcome === BO_OUTCOMES.UNABLE || bo.outcome === BO_OUTCOMES.NONE_OVER_THRESHOLD) && !bo.control_person_verified) {
    errors.push("Name the verified CEO, or the person performing that function.");
  }

  // Third party and enhanced due diligence
  if (tp.acting_for_third_party && (!tp.name || !tp.relationship)) errors.push("Record the third party's name and relationship to the client.");
  if (compliance.enhanced_due_diligence && !compliance.source_of_funds_and_wealth) {
    errors.push("High-risk client: describe the source of funds and source of wealth.");
  }
  return errors;
}

// Let other files `require` these (like a Python import).
module.exports = {
  SCHEMA_VERSION, ID_METHODS, BO_OUTCOMES, PEP_STATUSES, FLAGS, TAGS, STATUS,
  computeFlags, computeRiskRating, buildPayload, validatePayload,
};

// ---------------------------------------------------------------------------
// Demo: runs only when you execute this file directly (`node rules-engine.js`).
// ---------------------------------------------------------------------------

if (require.main === module) {
  const asOf = new Date().toISOString().slice(0, 10);
  const photoId = (name, dob, address, occupation, extra = {}) => ({
    name, dateOfBirth: dob, address, occupation, pepStatus: PEP_STATUSES.NONE,
    idMethod: ID_METHODS.PHOTO_ID, docType: "Passport", docNumber: "HK482193", docJurisdiction: "Canada",
    docExpiry: "2031-04-30", authenticityCheckPassed: true, ...extra,
  });

  const samples = [
    {
      label: "Low-risk Canadian corporation",
      legalName: "Maple Ridge Capital Corp.", structureType: "Corporation", jurisdiction: "Canada",
      registrationNumber: "1234567-8", existenceDocument: "Certificate of incorporation",
      principalBusiness: "Agricultural commodity exporter", directors: "Dana Whitfield, Omar Haddad",
      fxVolume: 2_500_000, settlement: "Pre-Funded", purpose: "Hedging commercial FX exposure",
      signatory: photoId("Dana Whitfield", "1979-06-14", "220 Bay St, Toronto ON", "Chief Financial Officer"),
      owners: [{ name: "Dana Whitfield", address: "220 Bay St, Toronto ON", ownershipPct: 60 }],
      boOutcome: BO_OUTCOMES.CONFIRMED, boConfirmationMethod: "Corporate registry search",
    },
    {
      label: "Cayman fund, foreign PEP owner, credit line",
      legalName: "Tidewater Global Macro Fund", structureType: "Hedge Fund", jurisdiction: "Cayman Islands",
      registrationNumber: "MC-339201", existenceDocument: "Certificate of incorporation",
      principalBusiness: "Global macro investment fund", fxVolume: 18_000_000, settlement: SETTLEMENT_CREDIT,
      purpose: "Investment portfolio FX", lei: "5493001KJTIIGC8Y1R12",
      signatory: photoId("Elena Marsh", "1984-02-03", "1 Harbour Dr, George Town", "Chief Operating Officer"),
      owners: [{ name: "Rafael Quintero", address: "Av. Balboa 12, Panama City", ownershipPct: 40, pepStatus: PEP_STATUSES.FOREIGN }],
      boOutcome: BO_OUTCOMES.CONFIRMED, boConfirmationMethod: "Shareholder register and minute book review",
      sourceOfWealth: "Institutional LP commitments; audited fund statements FY2025",
    },
    {
      label: "Small client asking for a credit line (gap 1 fixed)",
      legalName: "Lakeshore Imports Inc.", structureType: "Corporation", jurisdiction: "Canada",
      registrationNumber: "7788123-4", existenceDocument: "Certificate of status",
      principalBusiness: "Consumer electronics importer", directors: "Priya Natarajan",
      fxVolume: 1_200_000, settlement: SETTLEMENT_CREDIT, purpose: "Cross-border supplier payments",
      signatory: photoId("Priya Natarajan", "1988-09-21", "45 Front St, Toronto ON", "President"),
      owners: [{ name: "Priya Natarajan", address: "45 Front St, Toronto ON", ownershipPct: 100 }],
      boOutcome: BO_OUTCOMES.CONFIRMED, boConfirmationMethod: "Corporate registry search",
    },
    {
      label: "Enterprise UK LP, pre-funded (gap 2 fixed: LEI no longer blocks)",
      legalName: "Northgate Infrastructure Partners LP", structureType: "LP", jurisdiction: "United Kingdom",
      registrationNumber: "LP019284", existenceDocument: "Partnership agreement",
      principalBusiness: "Infrastructure investment partnership", fxVolume: 12_000_000, settlement: "Pre-Funded",
      purpose: "Investment portfolio FX", lei: "213800D1EI4B9WTWWD28",
      signatory: { name: "James Okafor", dateOfBirth: "1975-11-30", address: "10 Gresham St, London", occupation: "Managing Partner",
        pepStatus: PEP_STATUSES.NONE, idMethod: ID_METHODS.DUAL_PROCESS, sourceOne: "Bank statement (Barclays)", sourceTwo: "Utility bill (Thames Water)" },
      owners: [], boOutcome: BO_OUTCOMES.NONE_OVER_THRESHOLD, boConfirmationMethod: "Partnership agreement review",
      controlPersonName: "James Okafor",
    },
    {
      label: "Sanctions match (demo watchlist)",
      legalName: "Meridian Shell Holdings Ltd.", structureType: "Corporation", jurisdiction: "Panama",
      registrationNumber: "PA-55102", existenceDocument: "Annual filing / registry record",
      principalBusiness: "Holding company", directors: "Viktor Ironhold",
      fxVolume: 3_000_000, settlement: "Pre-Funded", purpose: "Treasury management",
      signatory: photoId("Viktor Ironhold", "1969-01-01", "Calle 50, Panama City", "Director"),
      owners: [{ name: "Viktor Ironhold", address: "Calle 50, Panama City", ownershipPct: 100 }],
      boOutcome: BO_OUTCOMES.CONFIRMED, boConfirmationMethod: "Signed client attestation with supporting documents",
      sourceOfWealth: "Declared: proceeds from sale of shipping business",
    },
    {
      label: "Blocked by validation (expired ID, no owners)",
      legalName: "Quickstart Trading Co.", structureType: "Corporation", jurisdiction: "Canada",
      registrationNumber: "9911223-0", existenceDocument: "Certificate of incorporation",
      principalBusiness: "Wholesale trading", directors: "Sam Lee",
      fxVolume: 900_000, settlement: "Pre-Funded", purpose: "Cross-border supplier payments",
      signatory: photoId("Sam Lee", "1990-05-05", "5 King St, Toronto ON", "Owner", { docExpiry: "2020-01-01" }),
      owners: [], boOutcome: BO_OUTCOMES.CONFIRMED, boConfirmationMethod: "Corporate registry search",
    },
  ];

  // `node rules-engine.js --json` prints machine-readable results; tests/test_parity.py uses it
  // to check the JavaScript engine against the Python one.
  if (process.argv.includes("--json")) {
    const out = {};
    for (const { label, ...input } of samples) {
      const payload = buildPayload(input, asOf);
      out[input.legalName] = { label, errors: validatePayload(payload, asOf), routing: payload.routing,
        risk_rating: payload.compliance.risk_rating };
    }
    console.log(JSON.stringify(out));
    process.exit(0);
  }

  for (const { label, ...input } of samples) {
    const payload = buildPayload(input, asOf);
    const errors = validatePayload(payload, asOf);
    console.log("─".repeat(72));
    console.log(label);
    if (errors.length) {
      console.log("  BLOCKED BY VALIDATION:");
      errors.forEach((e) => console.log(`    ✗ ${e}`));
      continue;
    }
    const { routing, compliance } = payload;
    console.log(`  Status: ${routing.workflow_status}   Risk: ${compliance.risk_rating}`);
    console.log(`  Flags:  ${routing.flags.join(", ") || "none"}`);
    console.log(`  Tags:   ${routing.tags.join(", ") || "none"}`);
  }
  console.log("─".repeat(72));
}
