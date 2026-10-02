"""Ground-truth tests for the risk model.

The fixture plants one of each consequence class: a shared credential with no
code relationship (critical), undeclared deps + cycles (high), ambiguity and
operational state (medium), no-consumers (low). The register must rank by
consequence, and every entry must carry its evidence.
"""

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_fixture  # noqa: E402
from faultline.extract import forks, operational  # noqa: E402
from faultline.extract import structure as parse_any  # noqa: E402
from faultline.views import dataset  # noqa: E402


def build_doc():
    root = make_fixture.build()
    tmp = tempfile.mkdtemp()
    astp = os.path.join(tmp, "ast.json")
    parse_any.parse_ecosystem(root, astp, verbose=False)
    couplingp = os.path.join(tmp, "coupling.json")
    operational.build(root, couplingp)
    forksp = os.path.join(tmp, "forks.json")
    forks.build(root, forksp)
    owners = json.load(open(os.path.join(HERE, "fixture-owners.json")))
    return dataset.build(argparse.Namespace(
        ast=astp, repos_dir=root,
        owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(tmp, "viz.json"),
        coupling=couplingp, forks=forksp), owners)


DOC = build_doc()
RISK = DOC["risk"]
REG = RISK["register"]
BY_KIND = {}
for r in REG:
    BY_KIND.setdefault(r["kind"], []).append(r)
SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def test_shared_credential_with_no_code_relationship_is_critical():
    hits = [r for r in BY_KIND.get("SHARED_CREDENTIAL", [])
            if set(r["repos"]) == {"kappa-app", "mu-web"}]
    assert hits, "SHARED_SECRET shared across repos was not ranked critical"
    assert hits[0]["severity"] == "critical"
    assert hits[0]["summary"].startswith("SHARED_SECRET is read by 2 repositories")
    assert "declare a relationship" in hits[0]["summary"]


def test_criticals_sort_first():
    order = [SEV[r["severity"]] for r in REG]
    assert order == sorted(order), "the register is not consequence-ordered"
    assert REG[0]["severity"] == "critical"


def test_cycles_and_undeclared_are_high():
    assert len(BY_KIND.get("CYCLE", [])) == 2
    assert all(r["severity"] == "high" for r in BY_KIND["CYCLE"])
    assert all(r["severity"] == "high"
               for r in BY_KIND.get("UNDECLARED_DEPENDENCY", []))


def test_vendored_content_is_medium():
    hits = BY_KIND.get("VENDORED_CONTENT", [])
    assert any(set(r["repos"]) == {"rho-app", "upsilon-fork"} for r in hits)
    assert all(r["severity"] == "medium" for r in hits)


def test_ambiguity_is_medium_and_no_consumers_is_low():
    assert all(r["severity"] == "medium" for r in BY_KIND.get("AMBIGUOUS_NAME", []))
    assert BY_KIND.get("NO_CONSUMERS"), "fixture has isolated repos"
    assert all(r["severity"] == "low" for r in BY_KIND["NO_CONSUMERS"])


def test_every_risk_carries_evidence():
    for r in REG:
        assert r["witnesses"], f"{r['kind']} risk has no evidence"
        assert r["repos"], f"{r['kind']} risk names no repos"
        assert r["consequence"], f"{r['kind']} risk states no consequence"


def test_exposure_scores_repos_by_consequence():
    exp = RISK["exposure"]
    assert exp.get("mu-web", 0) > 0
    assert exp.get("kappa-app", 0) > 0
    # certified-isolated repos carry at most low-grade notes
    assert exp.get("delta-tools", 0) <= 1


def test_counts_match_the_register():
    from collections import Counter
    truth = Counter(r["severity"] for r in REG)
    assert RISK["counts"] == dict(truth)
