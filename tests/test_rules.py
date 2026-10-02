"""Rules-engine tests: run with `python tests/test_rules.py` (no extra packages needed)."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

AS_OF = "2026-10-01"
EXPECTED = {
    "Maple Ridge Capital Corp.": (app.STATUS_STP, "LOW"),
    "Tidewater Global Macro Fund": (app.STATUS_COMPLIANCE, "HIGH"),
    "Lakeshore Imports Inc.": (app.STATUS_CREDIT, "LOW"),
    "Northgate Infrastructure Partners LP": (app.STATUS_STP, "LOW"),
    "Halcyon Family Trust": (app.STATUS_COMPLIANCE, "HIGH"),
    "Meridian Shell Holdings Ltd.": (app.STATUS_BLOCKED, "HIGH"),
}


def outcome(client):
    payload = app.build_payload(client, AS_OF)
    return payload, app.validate_payload(payload, AS_OF)


def test_sample_clients_route_correctly():
    for client in app.SAMPLE_CLIENTS:
        payload, errors = outcome(client)
        assert errors == [], (client["legal_name"], errors)
        got = (payload["routing"]["workflow_status"], payload["compliance"]["risk_rating"])
        assert got == EXPECTED[client["legal_name"]], (client["legal_name"], got)


def test_any_credit_line_needs_review():
    small = copy.deepcopy(app.SAMPLE_CLIENTS[0])
    small["settlement"] = app.SETTLEMENT_CREDIT
    payload, _ = outcome(small)
    assert payload["routing"]["flags"] == ["CREDIT_RISK_FLAG_DESK_REVIEW"]


def test_lei_is_a_tag_not_a_blocker():
    payload, _ = outcome(app.SAMPLE_CLIENTS[3])
    assert payload["routing"]["flags"] == []
    assert "DATA_LEI_CAPTURED" in payload["routing"]["tags"]


def test_expired_photo_id_is_rejected():
    client = copy.deepcopy(app.SAMPLE_CLIENTS[0])
    client["signatory"]["doc_expiry"] = "2020-01-01"
    _, errors = outcome(client)
    assert any("expired" in e for e in errors)


def test_unconfirmed_ownership_is_high_risk_and_needs_ceo():
    client = copy.deepcopy(app.SAMPLE_CLIENTS[0])
    client["bo_outcome"] = app.BO_UNABLE
    payload, errors = outcome(client)
    assert "RISK_FLAG_BO_UNCONFIRMED" in payload["routing"]["flags"]
    assert payload["compliance"]["risk_rating"] == "HIGH"
    assert any("CEO" in e for e in errors)


def test_owners_under_25_percent_are_dropped():
    client = copy.deepcopy(app.SAMPLE_CLIENTS[0])
    client["owners"].append({"name": "Minor Holder", "address": "x", "ownership_pct": 10, "pep_status": app.PEP_FOREIGN})
    payload, _ = outcome(client)
    assert [o["name"] for o in payload["beneficial_ownership"]["owners"]] == ["Dana Whitfield"]
    assert "RISK_FLAG_FOREIGN_PEP" not in payload["routing"]["flags"]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
