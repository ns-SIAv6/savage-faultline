"""Tests for the visualization dataset.

Two kinds:
  - invariants against the REAL ecosystem (determinism, internal consistency)
  - exact values against the synthetic fixture, where the answer is known
"""

import argparse
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_fixture  # noqa: E402
from faultline.analysis import seams as ecosystem  # noqa: E402
from faultline.extract import structure as parse_any  # noqa: E402
from faultline.views import dataset as viz  # noqa: E402

REAL = argparse.Namespace(
    ast=os.path.join(HERE, "..", "data", "ast_all.json"),
    repos_dir=os.path.join(HERE, "..", "data", "repos"),
    owners=os.path.join(HERE, "..", "data", "all-trees.json"),
    out="/tmp/viz-test.json",
    coupling=os.path.join(HERE, "..", "data", "coupling.json"),
    forks=os.path.join(HERE, "..", "data", "forks.json"),
)
NO_COUPLING = "/tmp/definitely-not-a-coupling-file.json"

# The real ecosystem snapshot lives in data/, which is gitignored and never
# shipped. A fresh public clone has no data/ — the real-data tests below must
# skip cleanly instead of failing at import or on missing files.
DATA_DIR = os.path.join(HERE, "..", "data")
HAS_REAL_DATA = os.path.isdir(DATA_DIR) and os.path.exists(
    os.path.join(DATA_DIR, "ast_all.json")
)

requires_real_data = pytest.mark.skipif(
    not HAS_REAL_DATA,
    reason="real ecosystem data/ is gitignored and absent; fixture tests still run",
)


def load_owners(path):
    out = {}
    if os.path.exists(path):
        for name, meta in json.load(open(path)).items():
            full = meta.get("full_name", "")
            if "/" in full:
                out[name] = full.split("/")[0]
    return out


def real_doc():
    return viz.build(REAL, load_owners(REAL.owners))


def fixture_doc():
    root = make_fixture.build()
    astp = os.path.join(HERE, "fixture-ast-viz.json")
    parse_any.parse_ecosystem(root, astp, verbose=False)
    return viz.build(argparse.Namespace(
        ast=astp, repos_dir=root,
        owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(HERE, "fixture-viz.json"),
        coupling=NO_COUPLING, forks=NO_COUPLING),
        json.load(open(os.path.join(HERE, "fixture-owners.json"))))


D = real_doc() if HAS_REAL_DATA else None
F = fixture_doc()


# ------------------------------------------------------------- determinism

@requires_real_data
def test_build_is_deterministic():
    """Same input must produce byte-identical output. No randomness anywhere."""
    a = json.dumps(real_doc(), sort_keys=True)
    b = json.dumps(real_doc(), sort_keys=True)
    assert a == b, "viz.build is not deterministic"


@requires_real_data
def test_no_floats_that_could_drift():
    """Only rounded floats are allowed out, so output cannot drift."""
    for p in D["similar_pairs"]:
        assert isinstance(p["score"], float)
        assert len(str(p["score"]).split(".")[-1]) <= 4


# ------------------------------------------------------- internal coherence

@requires_real_data
def test_certificates_require_both_code_and_operational_clearance():
    """A repo with no code seam can still be coupled through a shared database
    or credential, and must not be certified in that case."""
    with_seams = {s["from"] for s in D["seams"]} | {s["to"] for s in D["seams"]}
    coupled = {r for p in D["invisible_coupling"] for r in (p["a"], p["b"])}
    certified = {c["repo"] for c in D["certificates"]}
    assert not (certified & coupled), "certified a repo that shares live config"
    assert not (certified & with_seams), "certified a repo with a code seam"
    # and every certificate must rest on actually reading something
    for c in D["certificates"]:
        assert c["files_parsed"] > 0
        assert c["operational_check_ran"] is True


@requires_real_data
def test_certificates_claim_zero_and_it_is_true():
    for c in D["certificates"]:
        assert c["cross_repo_out"] == 0 and c["cross_repo_in"] == 0
        assert not any(s["from"] == c["repo"] or s["to"] == c["repo"] for s in D["seams"])


@requires_real_data
def test_real_coupling_was_found_not_skipped():
    """Guards the bug where the coupling file was looked up in the wrong place
    and every coupling test passed vacuously."""
    assert len(D["invisible_coupling"]) > 0, "coupling.json was not loaded"
    assert D["certificate_denied"], "no repo was denied on operational grounds"


@requires_real_data
def test_headline_pair_is_coupled_not_certified():
    """The headline finding: no shared code, but a shared database. Any pair
    invisibly coupled through a shared database at this strength must not
    hold a certificate."""
    certified = {c["repo"] for c in D["certificates"]}
    candidates = [p for p in D["invisible_coupling"]
                  if p["count"] >= 10
                  and any("DB_" in i or "DATABASE" in i for i in p["identifiers"])]
    assert candidates, "expected at least one strongly DB-coupled pair"
    for p in candidates:
        assert p["a"] not in certified and p["b"] not in certified, \
            f"certified a repo sharing a live database: {p['a']}/{p['b']}"


@requires_real_data
def test_invisible_coupling_entries_carry_witnesses():
    # count is recomputed from the coupling document, not trusted
    from collections import defaultdict
    truth = defaultdict(int)
    cj = json.load(open(REAL.coupling))
    for kind, items in cj["identifiers"].items():
        for it in items:
            rs = sorted(it["repos"])
            for i, a in enumerate(rs):
                for b in rs[i + 1:]:
                    truth[(a, b)] += 1
    for p in D["invisible_coupling"]:
        assert p["count"] == truth[tuple(sorted((p["a"], p["b"])))], \
            f"{p['a']}<->{p['b']}: count {p['count']} != coupling.json truth"
        assert p["witnesses"], "a coupling pair with no witnesses is a claim without evidence"
        assert {w["kind"] for w in p["witnesses"]} == set(p["kinds"]), \
            "per-kind witness sampling must show every kind of coupling"
        for w in p["witnesses"]:
            assert w["kind"] in {"env", "table", "bucket"}
            assert w["identifier"]
            if w["at"]:
                assert w["at"]["file"] and w["at"]["line"] > 0


@requires_real_data
def test_certificate_evidence_counts_are_real():
    for c in D["certificates"]:
        assert c["files_parsed"] >= 0 and c["imports_examined"] >= 0
    # at least one certified repo must have actually been examined
    assert max(c["imports_examined"] for c in D["certificates"]) > 0


@requires_real_data
def test_blast_radius_matches_reverse_transitive_closure():
    dep = {}
    for s in D["seams"]:
        dep.setdefault(s["to"], set()).add(s["from"])
    for r, b in D["blast_radius"].items():
        seen, stack = set(), list(dep.get(r, ()))
        while stack:
            d = stack.pop()
            if d in seen:
                continue
            seen.add(d)
            stack.extend(dep.get(d, ()))
        assert b["transitive"] == sorted(seen)
        assert b["count"] == len(seen)
        assert b["direct"] == sorted(dep.get(r, ()))


@requires_real_data
def test_headline_numbers_are_recomputed_not_asserted():
    m = D["measured"]
    assert m["seam_count"] == len(D["seams"])
    assert m["import_sites"] == sum(s["sites"] for s in D["seams"])
    assert m["repos_total"] == len(D["repos"])
    assert m["symbols_crossing"] == len(
        {c["symbol"] for s in D["seams"] for c in s["symbols"]})


@requires_real_data
def test_every_crossing_symbol_is_either_resolved_or_unresolved():
    for s in D["seams"]:
        assert len(s["symbols"]) == len(s["resolved_symbols"]) + len(s["unresolved_symbols"])


@requires_real_data
def test_provenance_points_at_real_locations():
    for s in D["seams"]:
        for c in s["resolved_symbols"]:
            assert c["defined_in"], f"{c['symbol']} marked resolved with no definition"
            for d in c["defined_in"]:
                assert d["file"] and d["line"] > 0
                assert os.path.exists(os.path.join(REAL.repos_dir, s["to"], d["file"])), \
                    f"{s['to']}/{d['file']} does not exist"


# ------------------------------------------------------------ no fake data

@requires_real_data
def test_aliases_are_never_counted_as_packages():
    bad = ("@/", "@workspace", "~", "--")
    for p in D["similar_pairs"]:
        for pkg in p["shared_rare"]:
            assert not pkg.startswith(bad), f"alias {pkg} leaked into shared deps"


@requires_real_data
def test_similar_pairs_have_no_seam_between_them():
    pairs = {(s["from"], s["to"]) for s in D["seams"]}
    for p in D["similar_pairs"]:
        assert (p["a"], p["b"]) not in pairs and (p["b"], p["a"]) not in pairs


@requires_real_data
def test_similar_pairs_are_labelled_as_hypothesis_not_finding():
    """Similarity must never appear in the findings list."""
    allowed = {"UNDECLARED_DEPENDENCY", "DECLARATION_MISMATCH",
               "UNRESOLVED_INTERFACE", "NO_CONSUMERS", "CYCLE", "AMBIGUOUS_NAME"}
    for f in D["findings"]:
        assert f["kind"] in allowed, f"unexpected finding kind {f['kind']}"
    assert "similar" not in json.dumps(D["findings"]).lower()


@requires_real_data
def test_every_repo_has_measured_facts():
    for r, rec in D["repos"].items():
        assert rec["name"] == r
        assert rec["files"] >= 0 and rec["loc"] >= 0
        assert isinstance(rec["languages"], dict)
        assert isinstance(rec["declared_dependencies"], list)


# ----------------------------------------------- exact values on fixture

def test_fixture_seams_are_exactly_the_planted_ones():
    got = {(s["from"], s["to"]) for s in F["seams"]}
    assert got == {("alpha-service", "beta-engine"),
                   ("epsilon-a", "epsilon-b"),
                   ("epsilon-b", "epsilon-a"),
                   ("mu-web", "gamma-lib"),
                   ("tau-mono", "gamma-lib"),
                   ("zeta-one", "zeta-two"),
                   ("zeta-two", "zeta-three"),
                   ("zeta-three", "zeta-one")}


def test_fixture_issues_no_certificates_without_coupling_data():
    """The fixture passes no coupling file, so the operational check cannot run
    and no certificate may be issued — it would claim both checks held."""
    assert F["certificates"] == []
    assert {d["repo"] for d in F["certificate_denied"]} == set(F["repos"])


def test_fixture_blast_radius():
    assert F["blast_radius"]["beta-engine"]["transitive"] == ["alpha-service"]
    assert F["blast_radius"]["delta-tools"]["count"] == 0
    # epsilon pair depends on each other
    assert F["blast_radius"]["epsilon-a"]["transitive"] == ["epsilon-b"]
    # the zeta three-cycle: each member's blast is the other two
    assert F["blast_radius"]["zeta-one"]["transitive"] == ["zeta-three", "zeta-two"]
    # gamma-lib is consumed by two repos now
    assert F["blast_radius"]["gamma-lib"]["direct"] == ["mu-web", "tau-mono"]


def test_fixture_provenance_resolves():
    seam = next(s for s in F["seams"]
                if s["from"] == "alpha-service" and s["to"] == "beta-engine")
    runner = [c for c in seam["symbols"] if c["symbol"] == "Runner"]
    assert runner, "Runner should cross the alpha->beta seam"
    assert runner[0]["defined_in"], "Runner is defined in beta-engine"
    assert runner[0]["defined_in"][0]["file"] == "src/beta/engine.py"


# ----------------------------------------------- the headline, self-detected

@requires_real_data
def test_the_headline_fork_is_detected_by_the_tool_itself():
    """The finding that justified this product was made BY HAND: ~162
    byte-identical files between two repos hidden by nested layout. The fork
    extractor now finds it on its own — this is the test that matters."""
    pair = next((p for p in D["forks"] if p["identical_files"] >= 100), None)
    assert pair is not None, "the headline fork was not detected"
    assert pair["verdict"] == "fork"


@requires_real_data
def test_fork_pairs_deny_certificates_on_real_data():
    certified = {c["repo"] for c in D["certificates"]}
    for p in D["forks"]:
        if p["identical_files"] >= 2:
            assert p["a"] not in certified and p["b"] not in certified


def test_aggregator_repos_explain_their_denial(tmp_path):
    """A repo of pure submodules examines as zero files; the denial must name
    where the code actually lives. Only reachable when the other checks ran —
    so this builds the fixture doc with coupling and forks supplied."""
    from faultline.extract import forks as forks_mod, operational
    root = make_fixture.build()
    astp = os.path.join(HERE, "fixture-ast.json")
    parse_any.parse_ecosystem(root, astp, verbose=False)
    cp = os.path.join(tmp_path, "coupling.json")
    fp = os.path.join(tmp_path, "forks.json")
    operational.build(root, cp)
    forks_mod.build(root, fp)
    doc = viz.build(argparse.Namespace(
        ast=astp, repos_dir=root,
        owners=os.path.join(HERE, "fixture-owners.json"),
        out=os.path.join(tmp_path, "viz.json"),
        coupling=cp, forks=fp),
        json.load(open(os.path.join(HERE, "fixture-owners.json"))))
    denied = {d["repo"]: d for d in doc["certificate_denied"]}
    assert "nu-empty" in denied
    assert "aggregator" in denied["nu-empty"]["reason"]
    assert "delta-tools" in denied["nu-empty"]["reason"]
    assert denied["nu-empty"]["submodules"] == [
        "https://github.com/example-org/delta-tools.git"]
    assert doc["repos"]["nu-empty"]["submodules"][0]["name"] == "delta-tools"
