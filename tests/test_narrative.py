"""Ground-truth tests for the generated narrative.

The narrative's promise is that it is derived from the model only. The
signature test: every name the prose mentions in backticks must exist in the
model — a story about repos that do not exist is a hallucination, and it is
caught here, on the fixture, forever.
"""

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_fixture  # noqa: E402
from faultline.analysis import narrative  # noqa: E402
from faultline.extract import forks, operational  # noqa: E402
from faultline.extract import structure as parse_any  # noqa: E402
from faultline.views import dataset  # noqa: E402

TMP = tempfile.mkdtemp()
ROOT = make_fixture.build()


def build_doc(with_checks=True):
    astp = os.path.join(TMP, "ast.json")
    parse_any.parse_ecosystem(ROOT, astp, verbose=False)
    owners = json.load(open(os.path.join(HERE, "fixture-owners.json")))
    missing = os.path.join(TMP, "definitely-missing.json")
    couplingp = os.path.join(TMP, "coupling.json")
    forksp = os.path.join(TMP, "forks.json")
    if with_checks:
        operational.build(ROOT, couplingp)
        forks.build(ROOT, forksp)
    return dataset.build(argparse.Namespace(
        ast=astp, repos_dir=ROOT,
        owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(TMP, "viz.json"),
        coupling=couplingp if with_checks else missing,
        forks=forksp if with_checks else missing), owners)


DOC = build_doc()
TEXT = narrative.tell(DOC, "2026-09-30")


def test_every_name_mentioned_exists_in_the_model():
    """No hallucinated repositories or identifiers."""
    mentioned = set(re.findall(r"`([^`]+)`", TEXT))
    known = set(DOC["repos"])
    for items in DOC.get("invisible_coupling", []):
        known.update(items["identifiers"])
    known.update(DOC.get("ambiguous_names") or {})
    unknown = mentioned - known
    assert not unknown, f"narrative names things the model does not know: {unknown}"


def test_the_fixture_story_names_its_truth():
    assert "21 repositories were examined" in TEXT
    assert "beta-engine" in TEXT                     # the consumed repo
    assert "SHARED_SECRET" in TEXT                   # the critical credential
    assert "delta-tools" in TEXT                     # certified independent


def test_no_certificates_claim_when_checks_did_not_run():
    """Withholding is part of the story — the tool must say what it could
    not check, in prose, not merely omit it."""
    bare = narrative.tell(build_doc(with_checks=False), "2026-09-30")
    assert "did not run" in bare
    assert "No repository could be certified" in bare
    assert "certified independent:" not in bare.replace(
        "No repository could be certified independent", "")


def test_narrative_is_deterministic():
    assert narrative.tell(DOC, "2026-09-30") == TEXT


def test_report_has_every_section():
    for heading in ("# Faultline report — 2026-09-30",
                    "Where the boundaries are crossed",
                    "Coupling that no code shows",
                    "What to act on first",
                    "What is safe to archive",
                    "Open questions"):
        assert heading in TEXT, f"missing section: {heading}"
