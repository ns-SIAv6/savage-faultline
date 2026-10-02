"""Guard tests for the critical defects found by the 2026-09-30 audit.

Each test plants a case in the fixture and asserts the CORRECT behaviour. They
are written before the fixes, so that a fix is proven rather than asserted.

Every one of these failed when written.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_fixture  # noqa: E402
from faultline.analysis import seams as ecosystem  # noqa: E402
from faultline.extract import structure as parse_any  # noqa: E402
from faultline.views import dataset as viz  # noqa: E402

NO_COUPLING = "/tmp/definitely-not-a-coupling-file.json"


def build():
    root = make_fixture.build()
    astp = os.path.join(HERE, "fixture-ast-critical.json")
    parse_any.parse_ecosystem(root, astp, verbose=False)
    owners = json.load(open(os.path.join(HERE, "fixture-owners.json")))
    R = ecosystem.analyze(root, ast_all=json.load(open(astp)), owners=owners)
    V = viz.build(argparse.Namespace(
        ast=astp, repos_dir=root, owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(HERE, "fixture-viz-critical.json"), coupling=NO_COUPLING,
        forks=NO_COUPLING),
        owners)
    return R, V


R, V = build()
EDGES = {(e["from"], e["to"]) for e in R["edges"]}
KINDS = {}
for f in R["findings"]:
    KINDS.setdefault(f["kind"], []).append(f)


def touching(repo):
    return [e for e in EDGES if repo in e]


# ---------------------------------------------------------------- defect 3

def test_stdlib_import_does_not_become_a_seam():
    """`import types` is the stdlib. A repo owning a file called types.py must
    not be treated as its owner — that is how a false seam to Infineum appeared."""
    assert not touching("kappa-app"), f"kappa-app has spurious seams: {touching('kappa-app')}"
    assert not touching("iota-lib"), f"iota-lib has spurious seams: {touching('iota-lib')}"


def test_file_stem_does_not_publish_a_package_name():
    """iota-lib has types.py. That is not an importable package it publishes."""
    assert "types" not in {i for i in R.get("published_names", {}).get("iota-lib", [])}


# ---------------------------------------------------------------- defect 4

def test_config_filename_does_not_publish_a_package_name():
    """lambda-web has vite.config.ts. Importing the real npm `vite` from mu-web
    must not become an edge to lambda-web. (mu-web legitimately touches
    gamma-lib — the assertion is about lambda-web, not about mu-web being
    edge-free.)"""
    assert not touching("lambda-web"), f"lambda-web has spurious seams: {touching('lambda-web')}"
    assert ("mu-web", "lambda-web") not in EDGES and ("lambda-web", "mu-web") not in EDGES


# ---------------------------------------------------------------- defect 5

def test_ambiguous_name_is_not_silently_assigned():
    """`shared` is published by both xi-one and omicron-two. pi-three's import
    of it is ambiguous: report it, do not invent a seam to whichever sorts first."""
    assert not touching("pi-three"), \
        f"ambiguous import produced a seam: {touching('pi-three')}"
    assert KINDS.get("AMBIGUOUS_NAME"), "ambiguity was not reported at all"


# ---------------------------------------------------------------- defect 1

def test_repo_with_nothing_parsed_is_never_certified():
    """nu-empty has no source files. You cannot certify what you did not read."""
    certified = {c["repo"] for c in V["certificates"]}
    assert "nu-empty" not in certified, "certified a repo with zero files examined"
    for c in V["certificates"]:
        assert c["files_parsed"] > 0, f"{c['repo']} certified with 0 files parsed"


# ---------------------------------------------------------------- defect 2

def test_missing_coupling_data_denies_the_certificate():
    """If operational coupling could not be checked, no certificate may be
    issued — the certificate claims both checks held."""
    assert V["certificates"] == [], \
        "issued certificates while the operational check could not run"
    assert V["certificate_denied"], "no record of why certification was withheld"


# ---------------------------------------------------------------- defect 6

def test_every_denied_repo_says_why():
    for d in V["certificate_denied"]:
        assert d.get("reason"), f"{d['repo']} denied with no reason given"


# ----------------------------------------------------------- preconditions

def test_fixture_still_holds_the_original_ground_truth():
    """The new planted cases must not have broken the original ones."""
    assert ("alpha-service", "beta-engine") in EDGES
    assert ("epsilon-a", "epsilon-b") in EDGES
    assert ("epsilon-b", "epsilon-a") in EDGES
    assert KINDS.get("UNDECLARED_DEPENDENCY")
    assert KINDS.get("CYCLE")
    assert KINDS.get("UNRESOLVED_INTERFACE")
    assert KINDS.get("DECLARATION_MISMATCH")


# ---------------------------------------------------------------- defect 8

def test_manifest_with_a_byte_order_mark_is_still_read():
    """agent-pay's package.json starts with a BOM. json.load threw, the failure
    was swallowed, and the repo registered no package name — so nothing could
    ever resolve to it. A BOM must not cost a repo its identity."""
    from faultline.analysis.seams import load_manifests
    import make_fixture
    m = load_manifests(make_fixture.build())
    assert m["lambda-web"]["names"] == ["lambda-web"], \
        f"BOM-prefixed manifest not read: {m['lambda-web']['names']}"


def test_scoped_dependency_names_are_parsed():
    """`@sia/foundry` used to normalise to the empty string, so every scoped
    npm dependency silently failed to match."""
    from faultline.analysis.seams import dep_name
    assert dep_name("@sia/foundry") == "@sia/foundry"
    assert dep_name("@savage/marketplace") == "@savage/marketplace"
    assert dep_name("-e git+https://github.com/x/y.git") == "y"
    assert dep_name("requests>=2.0") == "requests"
