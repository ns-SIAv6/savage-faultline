"""Ground-truth tests for operational coupling (shared env / tables / buckets).

This extractor had no fixture coverage at all — every bug in it was found by
hand on the real ecosystem. The fixture plants both the true couplings and
the known false-positive shapes (template literals, shell locals), so neither
direction can regress silently.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_fixture  # noqa: E402
from faultline.extract import operational  # noqa: E402

ROOT = make_fixture.build()
DOC = operational.build(ROOT, os.path.join(tempfile.gettempdir(),
                                           "faultline-fixture-coupling.json"))


def shared(kind):
    return {i["identifier"]: i["repos"] for i in DOC["identifiers"].get(kind, [])}


def test_real_shared_env_var_is_found():
    """kappa-app (os.environ) and mu-web (process.env) both read it."""
    assert shared("env").get("SHARED_SECRET") == ["kappa-app", "mu-web"]


def test_exported_shell_variables_are_environment():
    """An exported var reaches child processes — that is environment."""
    assert shared("env").get("DEPLOY_TARGET") == ["sigma-socket", "tau-mono"]


def test_js_template_literal_is_not_an_env_var():
    """`${DESCRIPTION}` in a .ts file interpolates a local."""
    assert "DESCRIPTION" not in shared("env")


def test_shell_locals_are_not_env_vars():
    """Assigned in the script, never exported: invisible to children."""
    assert "JOB_NAME" not in shared("env")
    assert "DESCRIPTION" not in shared("env")


def test_dotenv_assignments_count_but_one_repo_is_not_coupling():
    """The positive control: the .env reader must fire (delta-tools really has
    DESCRIPTION), and one repo alone must still produce no coupling."""
    found = operational.scan_repo(os.path.join(ROOT, "delta-tools"))
    assert ("env", "DESCRIPTION") in found, ".env assignment was not read"
    assert "DESCRIPTION" not in shared("env")


def test_shared_table_is_found():
    assert shared("table").get("audit_log") == ["mu-web", "tau-mono"]


def test_shared_bucket_is_found():
    assert shared("bucket").get("savage-deploy-artifacts") == ["sigma-socket", "tau-mono"]


def test_every_shared_identifier_carries_witnesses_with_lines():
    for kind, items in DOC["identifiers"].items():
        for it in items:
            assert len(it["repos"]) >= 2
            for w in it["witnesses"]:
                assert w["repo"] and w["file"] and w["line"] > 0


def test_operational_build_is_deterministic(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    operational.build(ROOT, str(a))
    operational.build(ROOT, str(b))
    assert a.read_bytes() == b.read_bytes()
