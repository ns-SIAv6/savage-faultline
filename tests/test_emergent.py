"""Ground-truth tests for emergent-property detection.

The definition is strict: a claim is emergent iff no single repository could
establish it. The fixture plants one of each class — a cycle (no member can
see the loop), a credential fan-out, a convergence pair, a partially-read
surface — and the tests pin both presence AND honest absence.
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

TMP = tempfile.mkdtemp()
ROOT = make_fixture.build()


def build():
    astp = os.path.join(TMP, "ast.json")
    ast_all = parse_any.parse_ecosystem(ROOT, astp, verbose=False)
    couplingp = os.path.join(TMP, "coupling.json")
    operational.build(ROOT, couplingp)
    forksp = os.path.join(TMP, "forks.json")
    forks.build(ROOT, forksp)
    owners = json.load(open(os.path.join(HERE, "fixture-owners.json")))
    return dataset.build(argparse.Namespace(
        ast=astp, repos_dir=ROOT,
        owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(TMP, "viz.json"),
        coupling=couplingp, forks=forksp), owners)


DOC = build()
EM = DOC["emergent"]
BY_KIND = {}
for c in EM:
    BY_KIND.setdefault(c["kind"], []).append(c)


def test_every_claim_says_why_no_single_repo_could_know():
    for c in EM:
        assert c.get("because"), f"{c['kind']} claim has no because line"
        assert c.get("evidence"), f"{c['kind']} claim has no evidence"


def test_cycles_are_emergent():
    topo = BY_KIND.get("topology", [])
    assert any("cyclic cluster" in c["claim"] for c in topo), \
        "the two planted cycles were not surfaced as whole-graph properties"


def test_credential_fanout_is_emergent():
    fans = BY_KIND.get("fanout", [])
    hit = [c for c in fans if "SHARED_SECRET" in c["claim"]]
    assert hit, "SHARED_SECRET fan-out not detected"
    assert "kappa-app" in str(hit[0]["evidence"]) and "mu-web" in str(hit[0]["evidence"])


def test_convergence_is_detected_and_labelled_hypothesis():
    conv = BY_KIND.get("convergence", [])
    hit = [c for c in conv if {c["evidence"].get("a", c["evidence"].get("b"))}]
    zust = [c for c in conv if "zustand" in str(c["evidence"].get("shared_rare", []))]
    assert zust, "lambda-web and mu-web both adopted zustand; that convergence was missed"
    assert "hypothesis" in zust[0]["claim"], "convergence must be labelled hypothesis, never finding"


def test_partially_read_surface_is_detected():
    surf = BY_KIND.get("surface", [])
    g = [c for c in surf if c["evidence"].get("repo") == "gamma-lib"]
    assert g, "gamma-lib's surface was not measured"
    assert g[0]["evidence"]["read"] == ["helper"]
    assert sorted(g[0]["evidence"]["offered"]) == ["helper", "unusedHelper"]
    assert "1 of 2" in g[0]["claim"]


def test_no_constellation_for_a_mere_pair():
    """rho-app/upsilon-fork share content as a PAIR — a constellation needs
    three. Absence is the honest answer here."""
    assert not BY_KIND.get("constellation")


def test_emergent_claims_are_deterministic():
    a = [c["claim"] for c in build()["emergent"]]
    assert [c["claim"] for c in EM] == a


def test_narrative_carries_the_emergent_section():
    from faultline.analysis import narrative
    text = narrative.tell(DOC, "2026-09-30")
    assert "What only the whole can see" in text
