"""Ground-truth tests for fork / shared-content detection.

rho-app and upsilon-fork share three byte-identical files at different
directory depths. Full-path matching finds zero overlap between them — the
suffix match is the only honest signal, and it is what found 162 identical
files between example-repo and example-style in the real ecosystem.
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
from faultline.views import dataset  # noqa: E402
from faultline.extract import structure as parse_any  # noqa: E402

ROOT = make_fixture.build()
DOC = forks.build(ROOT, os.path.join(tempfile.gettempdir(),
                                     "faultline-fixture-forks.json"))
PAIR = next((p for p in DOC["pairs"]
             if {p["a"], p["b"]} == {"rho-app", "upsilon-fork"}), None)


def test_nested_fork_is_found():
    assert PAIR is not None, "the planted fork was not detected at all"
    assert PAIR["identical_files"] == 3
    assert PAIR["pct_of_smaller_repo"] == 100.0
    # the fixture is tiny; the real ecosystem's 162-file pair trips "fork"
    assert PAIR["verdict"] == "shared_content"


def test_suffix_matching_not_full_path():
    """Every witness has different paths in the two repos — full-path
    matching would have found nothing."""
    assert PAIR["witnesses"]
    for w in PAIR["witnesses"]:
        assert w["a_path"] != w["b_path"]
        assert w["a_path"].endswith(w["suffix"])
        assert w["b_path"].endswith(w["suffix"])


def test_boilerplate_and_tiny_files_are_not_evidence():
    """upsilon-fork's 6-byte __init__.py and rho-app's empty one pair up
    under suffix rules; near-empty files must never become evidence."""
    assert not any(w["suffix"].endswith("__init__.py")
                   for p in DOC["pairs"] for w in p["witnesses"])


def test_unrelated_repos_produce_no_pair():
    assert not any({p["a"], p["b"]} == {"alpha-service", "beta-engine"}
                   for p in DOC["pairs"])


def test_forks_build_is_deterministic(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    forks.build(ROOT, str(a))
    forks.build(ROOT, str(b))
    assert a.read_bytes() == b.read_bytes()


def test_fork_coupling_denies_the_certificate():
    """A repo sharing byte-identical files is not archivable-isolated, even
    with zero imports and zero shared config. Both members of the planted
    pair must be denied; a genuinely clear repo (delta-tools) must certify."""
    root = make_fixture.build()
    tmp = tempfile.mkdtemp()
    astp = os.path.join(tmp, "ast.json")
    parse_any.parse_ecosystem(root, astp, verbose=False)
    couplingp = os.path.join(tmp, "coupling.json")
    operational.build(root, couplingp)
    forksp = os.path.join(tmp, "forks.json")
    forks.build(root, forksp)
    owners = json.load(open(os.path.join(HERE, "fixture-owners.json")))
    V = dataset.build(argparse.Namespace(
        ast=astp, repos_dir=root,
        owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(tmp, "viz.json"),
        coupling=couplingp, forks=forksp), owners)
    certified = {c["repo"] for c in V["certificates"]}
    denied = {d["repo"]: d["reason"] for d in V["certificate_denied"]}
    assert "rho-app" not in certified and "upsilon-fork" not in certified
    assert "identical" in denied["rho-app"]
    assert "identical" in denied["upsilon-fork"]
    assert "delta-tools" in certified, \
        f"a clear repo must certify when every check ran: {denied.get('delta-tools')}"
