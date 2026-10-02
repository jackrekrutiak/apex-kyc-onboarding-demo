"""The JavaScript rules engine must agree with the Python one. Skipped if Node.js isn't installed."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app  # noqa: E402

AS_OF = "2026-10-01"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_javascript_engine_matches_python():
    out = subprocess.run(["node", os.path.join(ROOT, "rules-engine.js"), "--json"], capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    py = {c["legal_name"]: c for c in app.SAMPLE_CLIENTS}
    shared = sorted(set(js) & set(py))
    assert len(shared) >= 4, shared
    for name in shared:
        payload = app.build_payload(py[name], AS_OF)
        assert js[name]["errors"] == [], (name, js[name]["errors"])
        assert js[name]["routing"]["workflow_status"] == payload["routing"]["workflow_status"], name
        assert js[name]["risk_rating"] == payload["compliance"]["risk_rating"], name
        assert js[name]["routing"]["flags"] == payload["routing"]["flags"], name
        assert js[name]["routing"]["tags"] == payload["routing"]["tags"], name


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_javascript_validation_blocks_expired_id():
    js = json.loads(subprocess.run(["node", os.path.join(ROOT, "rules-engine.js"), "--json"], capture_output=True, text=True,
                                   check=True).stdout)
    blocked = js["Quickstart Trading Co."]["errors"]
    assert any("expired" in e for e in blocked)
