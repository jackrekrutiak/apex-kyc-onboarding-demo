"""
Apex Global Liquidity Solutions — Enterprise Onboarding Demo (v2)
==================================================================

A three-phase pre-sales / post-sales walkthrough of an institutional FX
onboarding solution, with KYC data collection mapped to FINTRAC's client
identification requirements for money services businesses:

    Phase 1  Pre-Sales Architecture   (system flow + target payload contract)
    Phase 2  Client Intake Portal     (entity, IDV, beneficial ownership, PEP,
                                       third party, EDD — all rules-driven)
    Phase 3  Internal Ops Dashboard   (compliance / credit review queue)

Run locally:
    pip install "streamlit>=1.50"
    streamlit run app.py

Scope note: this app maps onboarding data to FINTRAC client identification
requirements. An intake form alone does not make a firm FINTRAC compliant;
that also takes a compliance officer, written policies, a risk assessment,
training, ongoing monitoring and transaction reporting.

Architecture notes
------------------
* All state lives in ``st.session_state``. Streamlit re-executes this script
  top-to-bottom on every interaction, so anything not in session state is
  rebuilt (and lost) on each rerun or page switch.
* Business rules are pure functions over plain dicts. They mirror
  ``rules-engine.js`` one-to-one, so the same logic can run in a browser,
  a form platform's custom-logic step, or a server-side re-check.
* State changes (submit, override, seed, reset) run in ``on_click`` callbacks.
  Callbacks execute *before* the rerun, which is the only safe point to
  modify widget-bound keys (e.g. clearing the form after a submit).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone

import streamlit as st

# ---------------------------------------------------------------------------
# Configuration & domain constants
# Centralised so thresholds, picklists and flag codes are defined once and
# shared by the form, the rules engine, the JSON Schema and the dashboard.
# In production these would sit in a config table owned by compliance.
# ---------------------------------------------------------------------------

CLIENT_NAME = "Apex Global Liquidity Solutions"
SCHEMA_VERSION = "2.0.0"

ENTITY_TYPES = ["Corporation", "Hedge Fund", "LP", "Offshore Trust"]
TRUST = "Offshore Trust"
JURISDICTIONS = ["Canada", "United States", "United Kingdom", "Cayman Islands", "Panama"]
EDD_JURISDICTIONS = {"Cayman Islands", "Panama"}

SETTLEMENT_PREFUNDED = "Pre-Funded"
SETTLEMENT_CREDIT = "Post-Trade Credit Line"
SETTLEMENT_OPTIONS = [SETTLEMENT_PREFUNDED, SETTLEMENT_CREDIT]

FX_MIN, FX_MAX, FX_DEFAULT, FX_STEP = 500_000, 50_000_000, 2_500_000, 100_000
ENTERPRISE_THRESHOLD = 10_000_000
BO_THRESHOLD_PCT = 25  # FINTRAC: individuals owning or controlling 25%+
MAX_OWNERS = 4

LEI_PATTERN = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")  # ISO 17442

# Fictional names standing in for a real sanctions-list screening vendor.
DEMO_WATCHLIST = {"viktor ironhold", "meridian shell holdings ltd."}

EXISTENCE_DOCUMENTS = [
    "Certificate of incorporation", "Certificate of status", "Annual filing / registry record",
    "Partnership agreement", "Trust deed",
]
PURPOSES = ["Hedging commercial FX exposure", "Cross-border supplier payments", "Investment portfolio FX", "Treasury management"]

ID_PHOTO = "Government-issued photo ID"
ID_CREDIT = "Credit file"
ID_DUAL = "Dual-process"
ID_METHODS = [ID_PHOTO, ID_CREDIT, ID_DUAL]
DOC_TYPES = ["Passport", "Driver's licence", "Provincial photo ID card", "Permanent resident card"]
CREDIT_BUREAUS = ["Equifax Canada", "TransUnion Canada"]

BO_CONFIRMED = "Owners identified and confirmed"
BO_NONE_OVER = "No individual holds 25% or more"
BO_UNABLE = "Unable to obtain or confirm"
BO_OUTCOMES = [BO_CONFIRMED, BO_NONE_OVER, BO_UNABLE]
BO_CONFIRMATION_METHODS = [
    "Corporate registry search", "Shareholder register and minute book review", "Partnership agreement review",
    "Trust deed review", "Signed client attestation with supporting documents",
]

PEP_NONE = "Not a PEP or HIO"
PEP_DOMESTIC = "Domestic PEP"
PEP_HIO = "Head of an international organization"
PEP_DOMESTIC_ASSOC = "Family / close associate of a domestic PEP or HIO"
PEP_FOREIGN = "Foreign PEP"
PEP_FOREIGN_ASSOC = "Family / close associate of a foreign PEP"
PEP_STATUSES = [PEP_NONE, PEP_DOMESTIC, PEP_HIO, PEP_DOMESTIC_ASSOC, PEP_FOREIGN, PEP_FOREIGN_ASSOC]
FOREIGN_PEP_VALUES = {PEP_FOREIGN, PEP_FOREIGN_ASSOC}
REVIEW_PEP_VALUES = {PEP_DOMESTIC, PEP_HIO, PEP_DOMESTIC_ASSOC}

# Flags block straight-through approval and name the queue that reviews them.
FLAGS = {
    "SANCTIONS": ("RISK_FLAG_SANCTIONS_MATCH", "BLOCK",
                  "A screened name matches the watchlist. Onboarding is frozen and escalated to the compliance officer."),
    "OFFSHORE": ("RISK_FLAG_OFFSHORE_JURISDICTION", "COMPLIANCE", "Higher-risk jurisdiction. Enhanced due diligence applies."),
    "FOREIGN_PEP": ("RISK_FLAG_FOREIGN_PEP", "COMPLIANCE",
                    "Foreign PEP or associate. Always high risk: source of funds and wealth, plus senior management approval."),
    "PEP_REVIEW": ("RISK_FLAG_PEP_HIO_REVIEW", "COMPLIANCE",
                   "Domestic PEP, HIO or associate. Compliance decides on risk whether enhanced measures apply."),
    "BO_UNCONFIRMED": ("RISK_FLAG_BO_UNCONFIRMED", "COMPLIANCE",
                       "Beneficial ownership could not be obtained or confirmed. CEO identity verified instead; client treated as high risk."),
    "THIRD_PARTY": ("RISK_FLAG_THIRD_PARTY", "COMPLIANCE", "Client is acting on behalf of a third party, whose details are recorded."),
    "CREDIT_EXEC": ("CREDIT_RISK_FLAG_EXECUTIVE_DESK_REVIEW", "CREDIT",
                    "Credit line at enterprise volume. Executive credit desk underwrites the exposure."),
    "CREDIT": ("CREDIT_RISK_FLAG_DESK_REVIEW", "CREDIT", "Credit line requested. Credit desk underwrites the exposure before approval."),
}
FLAG_CODE = {k: v[0] for k, v in FLAGS.items()}
FLAG_QUEUE = {v[0]: v[1] for v in FLAGS.values()}
FLAG_NOTE = {v[0]: v[2] for v in FLAGS.values()}
ALL_FLAGS = list(FLAG_QUEUE)

# Tags are informational. They never block approval.
TAGS = {
    "DATA_LEI_CAPTURED": "Enterprise volume. LEI validated and passed to the CRM.",
    "TASK_BO_DISCREPANCY_CHECK": "High-risk Canadian corporation. Compare owners with the Corporations Canada registry; "
                                 "report material discrepancies within 30 days.",
}

STATUS_BLOCKED = "Blocked: Sanctions Escalation"
STATUS_COMPLIANCE = "Pending Compliance Review"
STATUS_CREDIT = "Pending Credit Underwriting"
STATUS_STP = "Straight-Through Approved"
STATUS_OVERRIDE = "Approved via Specialist Sign-Off"
ALL_STATUSES = [STATUS_BLOCKED, STATUS_COMPLIANCE, STATUS_CREDIT, STATUS_STP, STATUS_OVERRIDE]
PENDING_STATUSES = {STATUS_COMPLIANCE, STATUS_CREDIT}
STATUS_ICONS = {STATUS_BLOCKED: "⛔", STATUS_COMPLIANCE: "🟡", STATUS_CREDIT: "🟠", STATUS_STP: "🟢", STATUS_OVERRIDE: "🔵"}
RISK_ICONS = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}

WEBHOOK_BASE = "https://mock-api.apex-demo.internal/v1/webhooks"

PAGE_ARCHITECTURE = "📐 Phase 1: Pre-Sales Architecture"
PAGE_INTAKE = "📋 Phase 2: Client Intake Portal"
PAGE_DASHBOARD = "📊 Phase 3: Internal Ops Dashboard"
PAGES = [PAGE_ARCHITECTURE, PAGE_INTAKE, PAGE_DASHBOARD]

# Widget keys for the intake form and their reset values.
FORM_DEFAULTS: dict = {
    "f_legal_name": "", "f_structure_type": ENTITY_TYPES[0], "f_jurisdiction": JURISDICTIONS[0],
    "f_registration_number": "", "f_existence_document": None, "f_principal_business": "", "f_directors": "",
    "f_purpose": None, "f_fx_volume": FX_DEFAULT, "f_settlement": SETTLEMENT_PREFUNDED, "f_lei": "",
    "f_sig_name": "", "f_sig_dob": None, "f_sig_address": "", "f_sig_occupation": "", "f_sig_pep": PEP_NONE,
    "f_id_method": ID_PHOTO, "f_doc_type": None, "f_doc_number": "", "f_doc_jurisdiction": "", "f_doc_expiry": None,
    "f_auth_check": False, "f_bureau": None, "f_credit_ref": "", "f_source_one": "", "f_source_two": "",
    "f_bo_outcome": None, "f_bo_count": 1, "f_bo_confirmation": None, "f_control_person": "",
    "f_trustees": "", "f_settlors": "", "f_beneficiaries": "",
    "f_third_party": False, "f_tp_name": "", "f_tp_relationship": "", "f_source_of_wealth": "",
}
for _i in range(MAX_OWNERS):
    FORM_DEFAULTS.update({f"f_bo_{_i}_name": "", f"f_bo_{_i}_address": "", f"f_bo_{_i}_pct": 0, f"f_bo_{_i}_pep": PEP_NONE})


# ---------------------------------------------------------------------------
# Session state bootstrap
# Seeds every key on first load. Re-assigning the form keys on each run stops
# Streamlit from garbage-collecting widget state while the user is on another
# page, so a half-completed intake form survives sidebar navigation.
# ---------------------------------------------------------------------------

def init_session_state() -> None:
    st.session_state.setdefault("client_database", [])  # list[dict] of submitted payloads
    st.session_state.setdefault("system_flags", [])     # active flag events across all records
    st.session_state.setdefault("submit_feedback", None)
    st.session_state.setdefault("override_errors", {})

    for key, default in FORM_DEFAULTS.items():
        st.session_state.setdefault(key, default)
        st.session_state[key] = st.session_state[key]


# ---------------------------------------------------------------------------
# Rules engine (pure functions) — mirrors rules-engine.js
# No Streamlit calls here, so the logic is deterministic and testable.
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def split_names(text) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]", clean(text)) if part.strip()]


def qualifying_owners(owners: list[dict] | None) -> list[dict]:
    """Owners at or above the threshold with a name. Rows under 25% are dropped
    (FINTRAC asks for 25%+ owners; data minimization says keep nothing extra)."""
    result = []
    for o in owners or []:
        name, pct = clean(o.get("name")), float(o.get("ownership_pct") or 0)
        if name and pct >= BO_THRESHOLD_PCT:
            result.append({"name": name, "address": clean(o.get("address")), "ownership_pct": pct,
                           "pep_status": o.get("pep_status") or PEP_NONE})
    return result


def screened_names(data: dict) -> list[str]:
    sig = data.get("signatory") or {}
    names = [clean(data.get("legal_name")), clean(sig.get("name")), clean(data.get("third_party_name")),
             clean(data.get("control_person_name"))]
    names += [o["name"] for o in qualifying_owners(data.get("owners"))]
    if data.get("structure_type") == TRUST:
        parties = data.get("trust_parties") or {}
        for key in ("trustees", "settlors", "beneficiaries"):
            names += split_names(parties.get(key))
    return [n for n in names if n]


def compute_flags(data: dict) -> list[str]:
    is_trust = data.get("structure_type") == TRUST
    owners = [] if is_trust else qualifying_owners(data.get("owners"))
    pep_statuses = [(data.get("signatory") or {}).get("pep_status")] + [o["pep_status"] for o in owners]
    names = [n.lower() for n in screened_names(data)]

    flags: list[str] = []
    if any(n in DEMO_WATCHLIST for n in names):
        flags.append(FLAG_CODE["SANCTIONS"])
    if data.get("jurisdiction") in EDD_JURISDICTIONS:
        flags.append(FLAG_CODE["OFFSHORE"])
    if any(s in FOREIGN_PEP_VALUES for s in pep_statuses):
        flags.append(FLAG_CODE["FOREIGN_PEP"])
    elif any(s in REVIEW_PEP_VALUES for s in pep_statuses):
        flags.append(FLAG_CODE["PEP_REVIEW"])
    if data.get("bo_outcome") == BO_UNABLE:
        flags.append(FLAG_CODE["BO_UNCONFIRMED"])
    if data.get("acting_for_third_party"):
        flags.append(FLAG_CODE["THIRD_PARTY"])
    # Any credit line is credit exposure, so it always gets reviewed.
    if data.get("settlement") == SETTLEMENT_CREDIT:
        enterprise = float(data.get("fx_volume") or 0) >= ENTERPRISE_THRESHOLD
        flags.append(FLAG_CODE["CREDIT_EXEC"] if enterprise else FLAG_CODE["CREDIT"])
    return flags


def compute_risk_rating(data: dict, flags: list[str]) -> str:
    high = {FLAG_CODE["SANCTIONS"], FLAG_CODE["OFFSHORE"], FLAG_CODE["FOREIGN_PEP"], FLAG_CODE["BO_UNCONFIRMED"]}
    if any(f in high for f in flags):
        return "HIGH"
    if FLAG_CODE["PEP_REVIEW"] in flags or FLAG_CODE["THIRD_PARTY"] in flags or data.get("structure_type") == TRUST:
        return "MEDIUM"
    return "LOW"


def compute_tags(data: dict, risk_rating: str) -> list[str]:
    tags = []
    if float(data.get("fx_volume") or 0) >= ENTERPRISE_THRESHOLD:
        tags.append("DATA_LEI_CAPTURED")
    if risk_rating == "HIGH" and data.get("jurisdiction") == "Canada" and data.get("structure_type") == "Corporation":
        tags.append("TASK_BO_DISCREPANCY_CHECK")
    return tags


def resolve_status(flags: list[str]) -> str:
    queues = {FLAG_QUEUE[f] for f in flags}
    if "BLOCK" in queues:
        return STATUS_BLOCKED
    if "COMPLIANCE" in queues:
        return STATUS_COMPLIANCE
    if "CREDIT" in queues:
        return STATUS_CREDIT
    return STATUS_STP


def resolve_webhooks(flags: list[str], risk_rating: str) -> list[dict]:
    sanctions = FLAG_CODE["SANCTIONS"] in flags
    kyc_priority = "BLOCKING" if sanctions else ("ENHANCED" if risk_rating == "HIGH" else "STANDARD")
    if FLAG_CODE["CREDIT_EXEC"] in flags:
        credit_priority = "EXECUTIVE"
    elif FLAG_CODE["CREDIT"] in flags:
        credit_priority = "STANDARD"
    else:
        credit_priority = "NOTIFY_ONLY"
    return [
        {"target": "CORE_CRM_SALESFORCE", "endpoint": f"{WEBHOOK_BASE}/salesforce/account-upsert",
         "priority": "HOLD" if sanctions else "STANDARD"},
        {"target": "KYC_AML_VERIFICATION", "endpoint": f"{WEBHOOK_BASE}/kyc-aml/screen", "priority": kyc_priority},
        {"target": "CREDIT_RISK_DESK", "endpoint": f"{WEBHOOK_BASE}/credit-risk/review", "priority": credit_priority},
    ]


def build_identity_verification(sig: dict) -> dict:
    """Keep only the record fields FINTRAC asks for under the chosen method."""
    method = sig.get("id_method")
    if method == ID_PHOTO:
        return {"method": method, "document_type": sig.get("doc_type") or None,
                "document_number": clean(sig.get("doc_number")) or None,
                "issuing_jurisdiction": clean(sig.get("doc_jurisdiction")) or None,
                "expiry_date": clean(sig.get("doc_expiry")) or None,
                "authenticity_and_liveness_check": bool(sig.get("authenticity_check_passed"))}
    if method == ID_CREDIT:
        return {"method": method, "credit_bureau": sig.get("bureau_name") or None,
                "credit_file_reference": clean(sig.get("credit_file_ref")) or None}
    if method == ID_DUAL:
        return {"method": method, "source_one": clean(sig.get("source_one")) or None,
                "source_two": clean(sig.get("source_two")) or None}
    return {"method": None}


def build_payload(data: dict, as_of: str, record_id: str = "PREVIEW", submitted_at: str | None = None) -> dict:
    """Compile raw form answers into the canonical payload (same shape as the JS engine).

    ``as_of`` is today's date as YYYY-MM-DD, passed in so the function stays testable.
    """
    sig = data.get("signatory") or {}
    is_trust = data.get("structure_type") == TRUST
    volume = int(data.get("fx_volume") or 0)
    enterprise = volume >= ENTERPRISE_THRESHOLD
    flags = compute_flags(data)
    risk = compute_risk_rating(data, flags)
    outcome = data.get("bo_outcome")
    parties = data.get("trust_parties") or {}

    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "submitted_at": submitted_at,
        "assessed_on": as_of,
        "entity": {
            "legal_name": clean(data.get("legal_name")) or None,
            "structure_type": data.get("structure_type"),
            "jurisdiction": data.get("jurisdiction"),
            "principal_business": clean(data.get("principal_business")) or None,
            "existence_verification": {
                "document_type": data.get("existence_document") or None,
                "registration_number": clean(data.get("registration_number")) or None,
            },
            "directors": split_names(data.get("directors")) if data.get("structure_type") == "Corporation" else [],
            "lei": (clean(data.get("lei")).upper() or None) if enterprise else None,
        },
        "relationship": {
            "purpose": data.get("purpose") or None,
            "estimated_annual_fx_volume_cad": volume,
            "currency": "CAD",
            "volume_tier": "ENTERPRISE" if enterprise else "STANDARD",
            "settlement_structure": data.get("settlement"),
        },
        "signatory": {
            "name": clean(sig.get("name")) or None,
            "date_of_birth": clean(sig.get("date_of_birth")) or None,
            "address": clean(sig.get("address")) or None,
            "occupation": clean(sig.get("occupation")) or None,
            "pep_status": sig.get("pep_status") or PEP_NONE,
            "identity_verification": build_identity_verification(sig),
        },
        "beneficial_ownership": {
            "threshold_pct": BO_THRESHOLD_PCT,
            "outcome": outcome or None,
            "owners": [] if is_trust else [
                {"name": o["name"], "address": o["address"] or None, "ownership_pct": o["ownership_pct"], "pep_status": o["pep_status"]}
                for o in qualifying_owners(data.get("owners"))
            ],
            "trust_parties": {
                "trustees": split_names(parties.get("trustees")),
                "settlors": split_names(parties.get("settlors")),
                "beneficiaries": split_names(parties.get("beneficiaries")),
            } if is_trust else None,
            "confirmation_method": None if outcome == BO_UNABLE else (data.get("bo_confirmation_method") or None),
            "control_person_verified": (clean(data.get("control_person_name")) or None) if outcome in (BO_UNABLE, BO_NONE_OVER) else None,
        },
        "third_party": (
            {"acting_for_third_party": True, "name": clean(data.get("third_party_name")) or None,
             "relationship": clean(data.get("third_party_relationship")) or None}
            if data.get("acting_for_third_party") else {"acting_for_third_party": False}
        ),
        "compliance": {
            "risk_rating": risk,
            "enhanced_due_diligence": risk == "HIGH",
            "senior_management_approval_required": risk == "HIGH",
            "source_of_funds_and_wealth": (clean(data.get("source_of_wealth")) or None) if risk == "HIGH" else None,
            "sanctions_screening": {"list": "DEMO_WATCHLIST", "names_screened": len(screened_names(data)),
                                    "match": FLAG_CODE["SANCTIONS"] in flags},
        },
        "routing": {
            "flags": flags,
            "tags": compute_tags(data, risk),
            "workflow_status": resolve_status(flags),
            "webhooks": resolve_webhooks(flags, risk),
        },
        "record_keeping": {
            "regime": "PCMLTFA / FINTRAC (money services business)",
            "retention": "At least 5 years after the last transaction or the end of the relationship",
        },
    }


def validate_payload(payload: dict, as_of: str) -> list[str]:
    """Return human-readable validation errors (empty list means valid)."""
    errors: list[str] = []
    entity, rel, sig = payload["entity"], payload["relationship"], payload["signatory"]
    bo, tp, comp = payload["beneficial_ownership"], payload["third_party"], payload["compliance"]
    idv = sig["identity_verification"]

    # Entity existence and relationship
    if not entity["legal_name"]:
        errors.append("Legal entity name is required.")
    if not entity["existence_verification"]["registration_number"]:
        errors.append("Registration or incorporation number is required.")
    if not entity["existence_verification"]["document_type"]:
        errors.append("Choose the document used to confirm the entity exists.")
    if not entity["principal_business"]:
        errors.append("Describe the nature of the entity's principal business.")
    if entity["structure_type"] == "Corporation" and not entity["directors"]:
        errors.append("List the names of all directors.")
    if not rel["purpose"]:
        errors.append("Choose the purpose of the business relationship.")
    if rel["volume_tier"] == "ENTERPRISE":
        if not entity["lei"]:
            errors.append("LEI is mandatory for enterprise-volume accounts.")
        elif not LEI_PATTERN.match(entity["lei"]):
            errors.append("LEI must be 20 characters: 18 letters or digits, then 2 check digits.")

    # Signatory identity verification
    if not all([sig["name"], sig["date_of_birth"], sig["address"], sig["occupation"]]):
        errors.append("Signatory name, date of birth, address and occupation are all required.")
    if not idv["method"]:
        errors.append("Choose how the signatory's identity was verified.")
    if idv["method"] == ID_PHOTO:
        if not all([idv["document_type"], idv["document_number"], idv["issuing_jurisdiction"], idv["expiry_date"]]):
            errors.append("Photo ID needs document type, number, issuing jurisdiction and expiry date.")
        elif idv["expiry_date"] < as_of:
            errors.append("The photo ID has expired. FINTRAC requires a valid, current document.")
        if not idv["authenticity_and_liveness_check"]:
            errors.append("The document authenticity and liveness check has not passed.")
    if idv["method"] == ID_CREDIT and not (idv["credit_bureau"] and idv["credit_file_reference"]):
        errors.append("Credit file method needs the Canadian credit bureau and the file reference.")
    if idv["method"] == ID_DUAL:
        if not (idv["source_one"] and idv["source_two"]):
            errors.append("Dual-process needs two reliable sources.")
        elif idv["source_one"].lower() == idv["source_two"].lower():
            errors.append("Dual-process sources must be two different sources.")

    # Beneficial ownership
    is_trust = entity["structure_type"] == TRUST
    if not bo["outcome"]:
        errors.append("Record the beneficial ownership outcome.")
    if bo["outcome"] == BO_CONFIRMED:
        if is_trust:
            t = bo["trust_parties"]
            if not (t["trustees"] and t["settlors"] and t["beneficiaries"]):
                errors.append("List all trustees, settlors and known beneficiaries.")
        else:
            if not bo["owners"]:
                errors.append(f"Add every individual owning or controlling {BO_THRESHOLD_PCT}% or more.")
            if any(not o["address"] for o in bo["owners"]):
                errors.append("Each beneficial owner needs an address.")
            if sum(o["ownership_pct"] for o in bo["owners"]) > 100:
                errors.append("Beneficial ownership adds up to more than 100%.")
    if bo["outcome"] == BO_NONE_OVER and is_trust:
        errors.append("Trusts must record trustees, settlors and beneficiaries, not a 25% outcome.")
    if bo["outcome"] and bo["outcome"] != BO_UNABLE and not bo["confirmation_method"]:
        errors.append("Choose how beneficial ownership was confirmed.")
    if bo["outcome"] in (BO_UNABLE, BO_NONE_OVER) and not bo["control_person_verified"]:
        errors.append("Name the verified CEO, or the person performing that function.")

    # Third party and enhanced due diligence
    if tp["acting_for_third_party"] and not (tp["name"] and tp["relationship"]):
        errors.append("Record the third party's name and relationship to the client.")
    if comp["enhanced_due_diligence"] and not comp["source_of_funds_and_wealth"]:
        errors.append("High-risk client: describe the source of funds and source of wealth.")
    return errors


def new_record_id() -> str:
    return f"APX-{uuid.uuid4().hex[:8].upper()}"


def fmt_cad(amount: int) -> str:
    return f"${amount:,.0f} CAD"


def today_iso() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Target payload contract (JSON Schema, draft 2020-12)
# Built from the same constants as the rules engine, so the contract shown to
# the client's CTO cannot drift from what the portal actually emits.
# ---------------------------------------------------------------------------

_NULLABLE_STR = {"type": ["string", "null"]}

TARGET_PAYLOAD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"https://schemas.apex-demo.internal/onboarding/client-intake/{SCHEMA_VERSION}",
    "title": "ApexClientOnboardingPayload",
    "description": "Canonical KYC payload emitted by the rules engine to downstream webhooks. "
                   "Fields map to FINTRAC client identification records for money services businesses.",
    "type": "object",
    "required": ["schema_version", "record_id", "submitted_at", "entity", "relationship", "signatory",
                 "beneficial_ownership", "third_party", "compliance", "routing", "record_keeping"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "record_id": {"type": "string", "pattern": "^APX-[A-F0-9]{8}$"},
        "submitted_at": {"type": "string", "format": "date-time"},
        "assessed_on": {"type": "string", "format": "date"},
        "entity": {
            "type": "object",
            "required": ["legal_name", "structure_type", "jurisdiction", "principal_business", "existence_verification"],
            "properties": {
                "legal_name": {"type": "string", "minLength": 1},
                "structure_type": {"enum": ENTITY_TYPES},
                "jurisdiction": {"enum": JURISDICTIONS},
                "principal_business": {"type": "string", "description": "Nature of the entity's principal business."},
                "existence_verification": {
                    "type": "object", "required": ["document_type", "registration_number"],
                    "properties": {"document_type": {"enum": EXISTENCE_DOCUMENTS}, "registration_number": {"type": "string"}},
                },
                "directors": {"type": "array", "items": {"type": "string"}, "description": "Required for corporations."},
                "lei": {**_NULLABLE_STR, "pattern": LEI_PATTERN.pattern,
                        "description": f"Required when annual volume >= {ENTERPRISE_THRESHOLD:,} CAD."},
            },
        },
        "relationship": {
            "type": "object",
            "required": ["purpose", "estimated_annual_fx_volume_cad", "volume_tier", "settlement_structure"],
            "properties": {
                "purpose": {"enum": PURPOSES},
                "estimated_annual_fx_volume_cad": {"type": "integer", "minimum": FX_MIN, "maximum": FX_MAX},
                "currency": {"const": "CAD"},
                "volume_tier": {"enum": ["STANDARD", "ENTERPRISE"]},
                "settlement_structure": {"enum": SETTLEMENT_OPTIONS},
            },
        },
        "signatory": {
            "type": "object",
            "description": "Person signing the service agreement for the entity.",
            "required": ["name", "date_of_birth", "address", "occupation", "pep_status", "identity_verification"],
            "properties": {
                "name": {"type": "string"}, "date_of_birth": {"type": "string", "format": "date"},
                "address": {"type": "string"}, "occupation": {"type": "string"},
                "pep_status": {"enum": PEP_STATUSES},
                "identity_verification": {
                    "type": "object", "required": ["method"],
                    "properties": {
                        "method": {"enum": ID_METHODS},
                        "document_type": _NULLABLE_STR, "document_number": _NULLABLE_STR,
                        "issuing_jurisdiction": _NULLABLE_STR, "expiry_date": {**_NULLABLE_STR, "format": "date"},
                        "authenticity_and_liveness_check": {"type": "boolean"},
                        "credit_bureau": _NULLABLE_STR, "credit_file_reference": _NULLABLE_STR,
                        "source_one": _NULLABLE_STR, "source_two": _NULLABLE_STR,
                    },
                },
            },
        },
        "beneficial_ownership": {
            "type": "object", "required": ["threshold_pct", "outcome", "owners"],
            "properties": {
                "threshold_pct": {"const": BO_THRESHOLD_PCT},
                "outcome": {"enum": BO_OUTCOMES},
                "owners": {"type": "array", "items": {
                    "type": "object", "required": ["name", "address", "ownership_pct", "pep_status"],
                    "properties": {"name": {"type": "string"}, "address": {"type": "string"},
                                   "ownership_pct": {"type": "number", "minimum": BO_THRESHOLD_PCT, "maximum": 100},
                                   "pep_status": {"enum": PEP_STATUSES}}}},
                "trust_parties": {"type": ["object", "null"], "properties": {
                    "trustees": {"type": "array"}, "settlors": {"type": "array"}, "beneficiaries": {"type": "array"}}},
                "confirmation_method": {"enum": BO_CONFIRMATION_METHODS + [None]},
                "control_person_verified": {**_NULLABLE_STR,
                                            "description": "CEO or equivalent, verified when no 25% owner exists or ownership cannot be confirmed."},
            },
        },
        "third_party": {"type": "object", "required": ["acting_for_third_party"],
                        "properties": {"acting_for_third_party": {"type": "boolean"}, "name": {"type": "string"},
                                       "relationship": {"type": "string"}}},
        "compliance": {
            "type": "object",
            "required": ["risk_rating", "enhanced_due_diligence", "senior_management_approval_required", "sanctions_screening"],
            "properties": {
                "risk_rating": {"enum": ["LOW", "MEDIUM", "HIGH"]},
                "enhanced_due_diligence": {"type": "boolean"},
                "senior_management_approval_required": {"type": "boolean"},
                "source_of_funds_and_wealth": {**_NULLABLE_STR, "description": "Required when risk_rating is HIGH."},
                "sanctions_screening": {"type": "object", "properties": {
                    "list": {"type": "string"}, "names_screened": {"type": "integer"}, "match": {"type": "boolean"}}},
            },
        },
        "routing": {
            "type": "object", "required": ["flags", "tags", "workflow_status", "webhooks"],
            "properties": {
                "flags": {"type": "array", "uniqueItems": True, "items": {"enum": ALL_FLAGS}},
                "tags": {"type": "array", "items": {"enum": list(TAGS)}},
                "workflow_status": {"enum": ALL_STATUSES},
                "webhooks": {"type": "array", "items": {"type": "object", "properties": {
                    "target": {"enum": ["CORE_CRM_SALESFORCE", "KYC_AML_VERIFICATION", "CREDIT_RISK_DESK"]},
                    "endpoint": {"type": "string", "format": "uri"},
                    "priority": {"enum": ["NOTIFY_ONLY", "STANDARD", "ENHANCED", "EXECUTIVE", "BLOCKING", "HOLD"]}}}},
                "cleared_flags": {"type": "array", "items": {"enum": ALL_FLAGS}},
                "override": {"type": "object", "properties": {
                    "approver": {"type": "string"}, "role": {"type": "string"}, "reason": {"type": "string"},
                    "signed_off_at": {"type": "string", "format": "date-time"}}},
            },
        },
        "record_keeping": {"type": "object", "properties": {"regime": {"type": "string"}, "retention": {"type": "string"}}},
    },
}

ARCHITECTURE_DIAGRAM = r"""
+------------------------------------------+
|           CLIENT INPUT LAYER             |
|   Apex Intake Portal  (Phase 2 UI)       |
|   Entity, signatory ID, beneficial       |
|   owners, PEP, third party, FX profile   |
+--------------------+---------------------+
                     |
                     |  HTTPS POST /v2/onboarding/intake
                     v
+------------------------------------------+
|       FEATHERY CORE RULES ENGINE         |
|   [1] Entity existence + mandatory data  |
|   [2] Signatory IDV (photo/credit/dual)  |
|   [3] Beneficial ownership (25%+)        |
|   [4] PEP/HIO, third party, sanctions    |
|   [5] Risk rating -> EDD + approvals     |
|   [6] Credit routing + status resolve    |
+--------------------+---------------------+
                     |
                     |  Signed JSON payload + flags + tags
      +--------------+--------------+
      |              |              |
      v              v              v
+------------+ +------------+ +------------+
|  CORE CRM  | |  KYC / AML | |CREDIT RISK |
| Salesforce | |Verification| |    DESK    |
| webhook #1 | | webhook #2 | | webhook #3 |
+------------+ +------------+ +------------+
  Account        IDV vendor,    Credit-line
  upsert, LEI,   sanctions,     underwriting,
  hold on match  EDD review     exec review
""".strip("\n")


# ---------------------------------------------------------------------------
# State mutations (run as on_click callbacks)
# ---------------------------------------------------------------------------

def register_record(payload: dict) -> None:
    """Persist a payload and log each of its flags as an active system flag."""
    st.session_state["client_database"].append(payload)
    for flag in payload["routing"]["flags"]:
        st.session_state["system_flags"].append({
            "record_id": payload["record_id"], "entity": payload["entity"]["legal_name"],
            "flag": flag, "queue": FLAG_QUEUE[flag], "raised_at": payload["submitted_at"],
        })


def form_data() -> dict:
    """Read the live widget values from session state into the engine's input shape."""
    s = st.session_state
    iso = lambda d: d.isoformat() if isinstance(d, date) else (d or "")  # noqa: E731
    return {
        "legal_name": s["f_legal_name"], "structure_type": s["f_structure_type"], "jurisdiction": s["f_jurisdiction"],
        "registration_number": s["f_registration_number"], "existence_document": s["f_existence_document"],
        "principal_business": s["f_principal_business"], "directors": s["f_directors"],
        "purpose": s["f_purpose"], "fx_volume": s["f_fx_volume"], "settlement": s["f_settlement"], "lei": s["f_lei"],
        "signatory": {
            "name": s["f_sig_name"], "date_of_birth": iso(s["f_sig_dob"]), "address": s["f_sig_address"],
            "occupation": s["f_sig_occupation"], "pep_status": s["f_sig_pep"], "id_method": s["f_id_method"],
            "doc_type": s["f_doc_type"], "doc_number": s["f_doc_number"], "doc_jurisdiction": s["f_doc_jurisdiction"],
            "doc_expiry": iso(s["f_doc_expiry"]), "authenticity_check_passed": s["f_auth_check"],
            "bureau_name": s["f_bureau"], "credit_file_ref": s["f_credit_ref"],
            "source_one": s["f_source_one"], "source_two": s["f_source_two"],
        },
        "owners": [
            {"name": s[f"f_bo_{i}_name"], "address": s[f"f_bo_{i}_address"],
             "ownership_pct": s[f"f_bo_{i}_pct"], "pep_status": s[f"f_bo_{i}_pep"]}
            for i in range(int(s["f_bo_count"]))
        ],
        "trust_parties": {"trustees": s["f_trustees"], "settlors": s["f_settlors"], "beneficiaries": s["f_beneficiaries"]},
        "bo_outcome": s["f_bo_outcome"], "bo_confirmation_method": s["f_bo_confirmation"],
        "control_person_name": s["f_control_person"],
        "acting_for_third_party": s["f_third_party"], "third_party_name": s["f_tp_name"],
        "third_party_relationship": s["f_tp_relationship"], "source_of_wealth": s["f_source_of_wealth"],
    }


def reset_form() -> None:
    for key, default in FORM_DEFAULTS.items():
        st.session_state[key] = default


def handle_submit() -> None:
    """Validate, stamp, persist, then clear the form. Errors keep the user's input."""
    as_of = today_iso()
    data = form_data()
    errors = validate_payload(build_payload(data, as_of), as_of)
    if errors:
        st.session_state["submit_feedback"] = ("error", errors)
        return

    payload = build_payload(data, as_of, record_id=new_record_id(), submitted_at=utc_now_iso())
    register_record(payload)
    flags = payload["routing"]["flags"]
    summary = (f"{payload['entity']['legal_name']} logged as {payload['record_id']} → "
               f"{payload['routing']['workflow_status']} · {payload['compliance']['risk_rating']} risk"
               + (f" · {len(flags)} flag{'s' if len(flags) != 1 else ''}" if flags else ""))
    st.session_state["submit_feedback"] = ("success", [summary])
    reset_form()


def handle_override(record_id: str) -> None:
    """Specialist sign-off: needs a named approver and a written reason, keeps an audit trail.
    Sanctions matches can't be cleared here; they go to the compliance officer."""
    approver = clean(st.session_state.get(f"approver_{record_id}"))
    reason = clean(st.session_state.get(f"reason_{record_id}"))
    errs = st.session_state["override_errors"]
    if not approver or not reason:
        errs[record_id] = "Enter the approver's name and the reason before signing off."
        return
    errs.pop(record_id, None)

    for record in st.session_state["client_database"]:
        if record["record_id"] == record_id:
            routing = record["routing"]
            if FLAG_CODE["SANCTIONS"] in routing["flags"]:
                return
            routing["cleared_flags"] = list(routing["flags"])
            routing["flags"] = []
            routing["workflow_status"] = STATUS_OVERRIDE
            routing["override"] = {
                "approver": approver,
                "role": "Senior management" if record["compliance"]["senior_management_approval_required"] else "Compliance specialist",
                "reason": reason,
                "signed_off_at": utc_now_iso(),
            }
            break

    st.session_state["system_flags"] = [e for e in st.session_state["system_flags"] if e["record_id"] != record_id]


def _sig_photo(name, dob, address, occupation, **extra) -> dict:
    return {"name": name, "date_of_birth": dob, "address": address, "occupation": occupation, "pep_status": PEP_NONE,
            "id_method": ID_PHOTO, "doc_type": "Passport", "doc_number": "HK482193", "doc_jurisdiction": "Canada",
            "doc_expiry": "2031-04-30", "authenticity_check_passed": True, **extra}


SAMPLE_CLIENTS = [
    {"legal_name": "Maple Ridge Capital Corp.", "structure_type": "Corporation", "jurisdiction": "Canada",
     "registration_number": "1234567-8", "existence_document": "Certificate of incorporation",
     "principal_business": "Agricultural commodity exporter", "directors": "Dana Whitfield, Omar Haddad",
     "fx_volume": 2_500_000, "settlement": SETTLEMENT_PREFUNDED, "purpose": "Hedging commercial FX exposure",
     "signatory": _sig_photo("Dana Whitfield", "1979-06-14", "220 Bay St, Toronto ON", "Chief Financial Officer",
                             doc_type="Driver's licence", doc_jurisdiction="Ontario"),
     "owners": [{"name": "Dana Whitfield", "address": "220 Bay St, Toronto ON", "ownership_pct": 60, "pep_status": PEP_NONE}],
     "bo_outcome": BO_CONFIRMED, "bo_confirmation_method": "Corporate registry search"},
    {"legal_name": "Tidewater Global Macro Fund", "structure_type": "Hedge Fund", "jurisdiction": "Cayman Islands",
     "registration_number": "MC-339201", "existence_document": "Certificate of incorporation",
     "principal_business": "Global macro investment fund", "fx_volume": 18_000_000, "settlement": SETTLEMENT_CREDIT,
     "purpose": "Investment portfolio FX", "lei": "5493001KJTIIGC8Y1R12",
     "signatory": _sig_photo("Elena Marsh", "1984-02-03", "1 Harbour Dr, George Town", "Chief Operating Officer",
                             doc_jurisdiction="United Kingdom", doc_number="551203984"),
     "owners": [{"name": "Rafael Quintero", "address": "Av. Balboa 12, Panama City", "ownership_pct": 40, "pep_status": PEP_FOREIGN},
                {"name": "Elena Marsh", "address": "1 Harbour Dr, George Town", "ownership_pct": 30, "pep_status": PEP_NONE}],
     "bo_outcome": BO_CONFIRMED, "bo_confirmation_method": "Shareholder register and minute book review",
     "source_of_wealth": "Institutional LP commitments; audited fund statements FY2025. "
                         "Principal owner's wealth from prior private equity career."},
    {"legal_name": "Lakeshore Imports Inc.", "structure_type": "Corporation", "jurisdiction": "Canada",
     "registration_number": "7788123-4", "existence_document": "Certificate of status",
     "principal_business": "Consumer electronics importer", "directors": "Priya Natarajan",
     "fx_volume": 1_200_000, "settlement": SETTLEMENT_CREDIT, "purpose": "Cross-border supplier payments",
     "signatory": {"name": "Priya Natarajan", "date_of_birth": "1988-09-21", "address": "45 Front St, Toronto ON",
                   "occupation": "President", "pep_status": PEP_NONE, "id_method": ID_CREDIT,
                   "bureau_name": "Equifax Canada", "credit_file_ref": "EQ-20931-7741"},
     "owners": [{"name": "Priya Natarajan", "address": "45 Front St, Toronto ON", "ownership_pct": 100, "pep_status": PEP_NONE}],
     "bo_outcome": BO_CONFIRMED, "bo_confirmation_method": "Corporate registry search"},
    {"legal_name": "Northgate Infrastructure Partners LP", "structure_type": "LP", "jurisdiction": "United Kingdom",
     "registration_number": "LP019284", "existence_document": "Partnership agreement",
     "principal_business": "Infrastructure investment partnership", "fx_volume": 12_000_000,
     "settlement": SETTLEMENT_PREFUNDED, "purpose": "Investment portfolio FX", "lei": "213800D1EI4B9WTWWD28",
     "signatory": {"name": "James Okafor", "date_of_birth": "1975-11-30", "address": "10 Gresham St, London",
                   "occupation": "Managing Partner", "pep_status": PEP_NONE, "id_method": ID_DUAL,
                   "source_one": "Bank statement (Barclays)", "source_two": "Utility bill (Thames Water)"},
     "owners": [], "bo_outcome": BO_NONE_OVER, "bo_confirmation_method": "Partnership agreement review",
     "control_person_name": "James Okafor"},
    {"legal_name": "Halcyon Family Trust", "structure_type": TRUST, "jurisdiction": "Panama",
     "registration_number": "PT-77120", "existence_document": "Trust deed",
     "principal_business": "Private family wealth holding", "fx_volume": 6_500_000, "settlement": SETTLEMENT_PREFUNDED,
     "purpose": "Investment portfolio FX", "acting_for_third_party": True,
     "third_party_name": "Halcyon Holdings S.A.", "third_party_relationship": "Underlying asset owner",
     "signatory": _sig_photo("Marguerite Lavoie", "1966-03-12", "88 Rue Sherbrooke O, Montréal QC", "Professional trustee",
                             pep_status=PEP_DOMESTIC_ASSOC),
     "trust_parties": {"trustees": "Marguerite Lavoie, 88 Rue Sherbrooke O, Montréal QC",
                       "settlors": "Arthur Penhallow, Calle 50, Panama City",
                       "beneficiaries": "Clara Penhallow, Calle 50, Panama City\nJonah Penhallow, Calle 50, Panama City"},
     "owners": [], "bo_outcome": BO_CONFIRMED, "bo_confirmation_method": "Trust deed review",
     "source_of_wealth": "Settlor's proceeds from sale of a logistics company (2019), per sale agreement."},
    {"legal_name": "Meridian Shell Holdings Ltd.", "structure_type": "Corporation", "jurisdiction": "Panama",
     "registration_number": "PA-55102", "existence_document": "Annual filing / registry record",
     "principal_business": "Holding company", "directors": "Viktor Ironhold", "fx_volume": 3_000_000,
     "settlement": SETTLEMENT_PREFUNDED, "purpose": "Treasury management",
     "signatory": _sig_photo("Viktor Ironhold", "1969-01-01", "Calle 50, Panama City", "Director",
                             doc_jurisdiction="Panama", doc_number="PA9920113"),
     "owners": [{"name": "Viktor Ironhold", "address": "Calle 50, Panama City", "ownership_pct": 100, "pep_status": PEP_NONE}],
     "bo_outcome": BO_CONFIRMED, "bo_confirmation_method": "Signed client attestation with supporting documents",
     "source_of_wealth": "Declared: proceeds from sale of a shipping business."},
]


def seed_demo_records() -> None:
    """Load representative clients so the dashboard can be demoed cold."""
    as_of = today_iso()
    for sample in SAMPLE_CLIENTS:
        register_record(build_payload(sample, as_of, record_id=new_record_id(), submitted_at=utc_now_iso()))


def reset_demo() -> None:
    st.session_state["client_database"] = []
    st.session_state["system_flags"] = []
    st.session_state["submit_feedback"] = None
    st.session_state["override_errors"] = {}
    reset_form()


# ---------------------------------------------------------------------------
# Phase 1 — Pre-Sales Architecture
# Static, read-only view. Renders the integration flow and the payload
# contract generated from the live constants above.
# ---------------------------------------------------------------------------

def render_architecture() -> None:
    st.title("📐 Pre-Sales Architecture")
    st.markdown(
        f"Proposed onboarding data flow for **{CLIENT_NAME}**. Every submission passes through a single rules "
        "layer before fan-out, so KYC and credit logic lives in one place and each downstream system receives "
        "the same validated, versioned payload."
    )

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.subheader("System flow")
        st.code(ARCHITECTURE_DIAGRAM, language="text")
        st.markdown(f"""
**FINTRAC client identification, mapped to the intake**

| Requirement | Captured as |
|---|---|
| Confirm the entity exists | Registration number + source document (certificate, registry record, agreement, deed) |
| Verify the person acting for the entity | Signatory IDV: government photo ID (with authenticity + liveness), credit file, or dual-process |
| Beneficial ownership | Names and addresses of individuals owning or controlling {BO_THRESHOLD_PCT}%+; directors; trustees, settlors and beneficiaries for trusts; how it was confirmed |
| Ownership can't be confirmed | Verify the CEO (or equivalent) and treat as high risk |
| PEP / HIO determination | Status for the signatory and each owner |
| Third-party determination | Yes/no, with name and relationship |
| Purpose and intended nature | Purpose, volume and settlement structure |
| High-risk clients | Source of funds and wealth, senior management approval |
| Record keeping | Retained at least 5 years |

**Routing rules applied by the engine**

| Condition | Flag | Effect |
|---|---|---|
| Name on the sanctions list | `{FLAG_CODE['SANCTIONS']}` | Blocked; CRM on hold; compliance officer escalation |
| Cayman Islands / Panama | `{FLAG_CODE['OFFSHORE']}` | High risk; enhanced due diligence |
| Foreign PEP or associate | `{FLAG_CODE['FOREIGN_PEP']}` | High risk; source of wealth; senior management approval |
| Domestic PEP / HIO / associate | `{FLAG_CODE['PEP_REVIEW']}` | Medium risk; compliance review |
| Ownership not confirmed | `{FLAG_CODE['BO_UNCONFIRMED']}` | High risk; CEO verified instead |
| Acting for a third party | `{FLAG_CODE['THIRD_PARTY']}` | Medium risk; compliance review |
| Any post-trade credit line | `{FLAG_CODE['CREDIT']}` | Credit desk underwrites |
| Credit line at ≥ {fmt_cad(ENTERPRISE_THRESHOLD)} | `{FLAG_CODE['CREDIT_EXEC']}` | Executive credit review |
| No flags | — | Straight-through approval |
""")
        st.caption("Tags such as `DATA_LEI_CAPTURED` are informational and never block approval.")

    with right:
        st.subheader("Target payload contract")
        st.caption(f"JSON Schema (draft 2020-12) · version {SCHEMA_VERSION}")
        st.json(TARGET_PAYLOAD_SCHEMA, expanded=1)
        st.download_button(
            "⬇️ Download schema",
            data=json.dumps(TARGET_PAYLOAD_SCHEMA, indent=2),
            file_name=f"apex_onboarding_payload_v{SCHEMA_VERSION}.schema.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# Phase 2 — Client Intake Portal
# Widgets are deliberately NOT wrapped in st.form: a form defers reruns until
# submit, which would freeze the conditional sections and the live preview.
# Bare widgets + a callback-driven submit button give instant feedback while
# still committing data only on submit.
# ---------------------------------------------------------------------------

def render_intake() -> None:
    st.title("📋 Client Intake Portal")
    st.caption(f"Institutional onboarding · {CLIENT_NAME} · KYC mapped to FINTRAC client identification")

    as_of = today_iso()
    live_payload = build_payload(form_data(), as_of)
    s = st.session_state
    is_trust = s["f_structure_type"] == TRUST

    form_col, preview_col = st.columns([3, 2], gap="large")
    with form_col:
        with st.container(border=True):
            st.subheader("1 · Entity")
            st.text_input("Legal entity name *", key="f_legal_name")
            c1, c2 = st.columns(2)
            c1.selectbox("Entity structure", ENTITY_TYPES, key="f_structure_type")
            c2.selectbox("Jurisdiction of incorporation", JURISDICTIONS, key="f_jurisdiction")
            c1, c2 = st.columns(2)
            c1.text_input("Registration / incorporation no. *", key="f_registration_number")
            c2.selectbox("Existence confirmed by *", EXISTENCE_DOCUMENTS, key="f_existence_document",
                         index=None, placeholder="Choose a document")
            st.text_input("Nature of principal business *", key="f_principal_business")
            if s["f_structure_type"] == "Corporation":
                st.text_input("Names of all directors *", key="f_directors", placeholder="Separate names with commas")

        with st.container(border=True):
            st.subheader("2 · Relationship")
            st.selectbox("Purpose of the relationship *", PURPOSES, key="f_purpose", index=None, placeholder="Choose a purpose")
            st.slider("Estimated annual FX volume (CAD)", min_value=FX_MIN, max_value=FX_MAX, step=FX_STEP,
                      key="f_fx_volume", format="$%d")
            st.caption(f"Selected: **{fmt_cad(s['f_fx_volume'])}**")
            st.radio("Preferred settlement structure", SETTLEMENT_OPTIONS, key="f_settlement", horizontal=True)
            if live_payload["relationship"]["volume_tier"] == "ENTERPRISE":
                st.success("✅ Enterprise threshold met")
                st.text_input("Legal Entity Identifier (LEI) *", key="f_lei", max_chars=20,
                              placeholder="20-character ISO 17442 code")

        with st.container(border=True):
            st.subheader("3 · Signatory identity")
            st.caption("The person signing the service agreement for the entity.")
            c1, c2 = st.columns(2)
            c1.text_input("Full name *", key="f_sig_name")
            c2.date_input("Date of birth *", key="f_sig_dob", value=None, min_value=date(1900, 1, 1), max_value=date.today())
            c1, c2 = st.columns(2)
            c1.text_input("Address *", key="f_sig_address")
            c2.text_input("Occupation *", key="f_sig_occupation")
            st.selectbox("PEP / HIO determination", PEP_STATUSES, key="f_sig_pep")
            st.radio("Identity verification method", ID_METHODS, key="f_id_method", horizontal=True)
            method = s["f_id_method"]
            if method == ID_PHOTO:
                c1, c2 = st.columns(2)
                c1.selectbox("Document type", DOC_TYPES, key="f_doc_type", index=None, placeholder="Choose")
                c2.text_input("Document number", key="f_doc_number")
                c1, c2 = st.columns(2)
                c1.text_input("Issuing jurisdiction", key="f_doc_jurisdiction")
                c2.date_input("Expiry date", key="f_doc_expiry", value=None, min_value=date(2000, 1, 1), max_value=date(2050, 12, 31))
                if s["f_doc_expiry"] and s["f_doc_expiry"].isoformat() < as_of:
                    st.error("This document has expired. Ask for a current one.")
                st.checkbox("Document authenticity and liveness check passed (simulated IDV vendor result)", key="f_auth_check")
            elif method == ID_CREDIT:
                c1, c2 = st.columns(2)
                c1.selectbox("Canadian credit bureau", CREDIT_BUREAUS, key="f_bureau", index=None, placeholder="Choose")
                c2.text_input("Credit file reference", key="f_credit_ref")
                st.caption("The file must have existed for at least 3 years and match name, address and date of birth.")
            else:
                c1, c2 = st.columns(2)
                c1.text_input("Source one", key="f_source_one", placeholder="e.g. Bank statement (RBC)")
                c2.text_input("Source two", key="f_source_two", placeholder="e.g. Utility bill (Hydro One)")
                st.caption("Two different reliable sources, each confirming name plus address, date of birth or a financial account.")

        with st.container(border=True):
            st.subheader("4 · Beneficial ownership")
            st.selectbox("Outcome *", BO_OUTCOMES, key="f_bo_outcome", index=None, placeholder="Choose an outcome")
            outcome = s["f_bo_outcome"]
            if outcome == BO_CONFIRMED and not is_trust:
                st.number_input(f"Individuals owning or controlling {BO_THRESHOLD_PCT}% or more",
                                min_value=1, max_value=MAX_OWNERS, step=1, key="f_bo_count")
                for i in range(int(s["f_bo_count"])):
                    c1, c2, c3, c4 = st.columns([3, 4, 2, 3])
                    c1.text_input("Name", key=f"f_bo_{i}_name")
                    c2.text_input("Address", key=f"f_bo_{i}_address")
                    c3.number_input("Own %", min_value=0, max_value=100, step=1, key=f"f_bo_{i}_pct")
                    c4.selectbox("PEP / HIO", PEP_STATUSES, key=f"f_bo_{i}_pep")
                st.caption("Rows under 25% are not kept in the payload.")
            if is_trust and outcome != BO_UNABLE:
                st.text_area("Trustees (names and addresses)", key="f_trustees", placeholder="One per line")
                st.text_area("Settlors", key="f_settlors", placeholder="One per line")
                st.text_area("Known beneficiaries", key="f_beneficiaries", placeholder="One per line")
            if outcome != BO_UNABLE:
                st.selectbox("Confirmed by (different from how it was collected)", BO_CONFIRMATION_METHODS,
                             key="f_bo_confirmation", index=None, placeholder="Choose a method")
            if outcome in (BO_UNABLE, BO_NONE_OVER):
                st.text_input("Verified CEO, or person performing that function *", key="f_control_person")

        with st.container(border=True):
            st.subheader("5 · Third-party determination")
            st.checkbox("The client is acting on behalf of a third party", key="f_third_party")
            if s["f_third_party"]:
                c1, c2 = st.columns(2)
                c1.text_input("Third party name *", key="f_tp_name")
                c2.text_input("Relationship to client *", key="f_tp_relationship")

        if live_payload["compliance"]["enhanced_due_diligence"]:
            st.warning("⚠️ High risk: enhanced due diligence required. Senior management approval is needed before go-live.")
            st.text_area("Source of funds and source of wealth *", key="f_source_of_wealth",
                         placeholder="Where the money for these trades comes from, and how the owners built their wealth")

        st.button("Submit for Onboarding", type="primary", width="stretch", on_click=handle_submit)

        feedback = st.session_state.get("submit_feedback")
        if feedback:
            kind, messages = feedback
            if kind == "error":
                st.error("**Submission blocked:**\n\n" + "\n".join(f"- {m}" for m in messages))
            else:
                st.success(messages[0])

    with preview_col:
        live_payload = build_payload(form_data(), as_of)  # rebuilt after widgets ran this pass
        routing, comp = live_payload["routing"], live_payload["compliance"]
        st.subheader("Live assessment")
        st.markdown(f"**{STATUS_ICONS[routing['workflow_status']]} {routing['workflow_status']}** · "
                    f"{RISK_ICONS[comp['risk_rating']]} {comp['risk_rating']} risk")
        for flag in routing["flags"]:
            (st.warning if FLAG_QUEUE[flag] == "CREDIT" else st.error)(f"`{flag}` — {FLAG_NOTE[flag]}")
        for tag in routing["tags"]:
            st.info(f"`{tag}` — {TAGS[tag]}")
        if not routing["flags"]:
            st.caption("No flags raised. Eligible for straight-through approval once validation passes.")

        open_errors = validate_payload(live_payload, as_of)
        with st.expander(f"Outstanding requirements ({len(open_errors)})", expanded=bool(open_errors)):
            if open_errors:
                for e in open_errors:
                    st.markdown(f"- {e}")
            else:
                st.markdown("All KYC requirements captured.")

        with st.expander("Live payload"):
            st.json(live_payload, expanded=True)

        st.subheader("Active system flags")
        active = st.session_state["system_flags"]
        if active:
            st.dataframe(active, hide_index=True, width="stretch")
        else:
            st.caption("No open flags.")


# ---------------------------------------------------------------------------
# Phase 3 — Internal Ops Dashboard
# Reads directly from session state and derives every metric on each render,
# so counts can never fall out of sync with the underlying records.
# ---------------------------------------------------------------------------

def render_dashboard() -> None:
    st.title("📊 Internal Ops Dashboard")
    st.caption("Compliance & Credit Risk review queue")

    database = st.session_state["client_database"]
    statuses = [r["routing"]["workflow_status"] for r in database]
    pending = sum(1 for s in statuses if s in PENDING_STATUSES)
    stp = statuses.count(STATUS_STP)
    blocked = statuses.count(STATUS_BLOCKED)
    overridden = statuses.count(STATUS_OVERRIDE)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Files Logged", len(database))
    m2.metric("Pending Desk Underwriting", pending, help="Compliance review plus credit underwriting.")
    m3.metric("Straight-Through Approvals", stp)
    notes = []
    if blocked:
        notes.append(f"⛔ {blocked} blocked on sanctions")
    if overridden:
        notes.append(f"🔵 {overridden} approved via sign-off")
    if notes:
        st.caption(" · ".join(notes))

    st.divider()
    if not database:
        st.info("No submissions yet. Complete an intake in **Phase 2**, or use **Load sample clients** in the sidebar.")
        return

    for record in reversed(database):  # newest first, like a working ops queue
        render_queue_card(record)


def render_queue_card(record: dict) -> None:
    entity, rel, sig = record["entity"], record["relationship"], record["signatory"]
    bo, comp, routing = record["beneficial_ownership"], record["compliance"], record["routing"]
    status, record_id = routing["workflow_status"], record["record_id"]

    with st.container(border=True):
        head, badge = st.columns([3, 2])
        head.markdown(f"#### {entity['legal_name']}")
        head.caption(f"`{record_id}` · submitted {record['submitted_at']}")
        badge.markdown(f"**{STATUS_ICONS.get(status, '⚪')} {status}**  \n{RISK_ICONS[comp['risk_rating']]} {comp['risk_rating']} risk")

        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(f"**Structure**  \n{entity['structure_type']}")
        s2.markdown(f"**Jurisdiction**  \n{entity['jurisdiction']}")
        s3.markdown(f"**Annual FX**  \n{fmt_cad(rel['estimated_annual_fx_volume_cad'])}")
        s4.markdown(f"**Settlement**  \n{rel['settlement_structure']}")

        k1, k2, k3 = st.columns(3)
        k1.markdown(f"**Signatory**  \n{sig['name']}  \n{sig['identity_verification']['method']}")
        if bo["trust_parties"]:
            owners_text = f"{len(bo['trust_parties']['trustees'])} trustee(s), {len(bo['trust_parties']['beneficiaries'])} beneficiar(ies)"
        elif bo["owners"]:
            owners_text = "  \n".join(f"{o['name']} · {o['ownership_pct']:.0f}%" for o in bo["owners"])
        else:
            owners_text = f"{bo['outcome']}  \nControl person: {bo['control_person_verified']}"
        k2.markdown(f"**Beneficial ownership**  \n{owners_text}")
        screening = comp["sanctions_screening"]
        k3.markdown(f"**Sanctions screening**  \n{'Match' if screening['match'] else 'Clear'} · {screening['names_screened']} names")

        for flag in routing["flags"]:
            (st.warning if FLAG_QUEUE[flag] == "CREDIT" else st.error)(flag, icon="🚩")
        for tag in routing["tags"]:
            st.info(tag, icon="🏷️")

        if FLAG_CODE["SANCTIONS"] in routing["flags"]:
            st.caption("Sanctions matches can't be cleared from this queue. The compliance officer handles escalation and reporting.")
        elif routing["flags"]:
            role = "Senior management approver" if comp["senior_management_approval_required"] else "Approver"
            c1, c2 = st.columns([1, 2])
            c1.text_input(role, key=f"approver_{record_id}")
            c2.text_input("Reason for sign-off", key=f"reason_{record_id}")
            st.button("🛡️ Manual Override Clear", key=f"override_{record_id}", on_click=handle_override, args=(record_id,))
            err = st.session_state["override_errors"].get(record_id)
            if err:
                st.error(err)
        elif status == STATUS_OVERRIDE:
            o = routing["override"]
            st.success(f"Signed off by {o['approver']} ({o['role']}) at {o['signed_off_at']}.  \n"
                       f"Reason: {o['reason']}  \nCleared: {', '.join(routing.get('cleared_flags', [])) or 'none'}")
        else:
            st.success("No risk flags raised — routed straight-through.")

        with st.expander("Routed payload"):
            st.json(record, expanded=False)


# ---------------------------------------------------------------------------
# App shell
# Sidebar radio acts as the router. Its key lives in session state, so the
# selected page and all data persist across reruns.
# ---------------------------------------------------------------------------

def render_sidebar() -> str:
    with st.sidebar:
        st.markdown(f"### {CLIENT_NAME}")
        st.caption("Enterprise Onboarding Solution · Demo")
        page = st.radio("Navigate", PAGES, key="nav_page", label_visibility="collapsed")

        st.divider()
        st.metric("Records in session", len(st.session_state["client_database"]))
        st.metric("Active system flags", len(st.session_state["system_flags"]))

        st.divider()
        st.caption("Demo controls")
        st.button("Load sample clients", on_click=seed_demo_records, width="stretch")
        st.button("Reset demo", on_click=reset_demo, width="stretch")

        st.divider()
        st.caption("Independent concept demo. Fictional client and people; sanctions screening uses a made-up list. "
                   "Not affiliated with Feathery.")
    return page


def main() -> None:
    st.set_page_config(page_title=f"{CLIENT_NAME} · Onboarding", page_icon="🏦", layout="wide")
    init_session_state()

    page = render_sidebar()
    if page == PAGE_ARCHITECTURE:
        render_architecture()
    elif page == PAGE_INTAKE:
        render_intake()
    else:
        render_dashboard()


if __name__ == "__main__":
    main()
