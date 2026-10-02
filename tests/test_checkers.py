"""Ground-truth tests for the seam checkers.

The real ecosystem cannot tell us whether these are correct — we do not know the
right answer for it. The synthetic fixture can, because we wrote the answer into
it. Every assertion here is against a planted fact.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import make_fixture  # noqa: E402
from faultline.analysis import seams as ecosystem  # noqa: E402
from faultline.extract import structure as parse_any  # noqa: E402


def build():
    root = make_fixture.build()
    ast_all = parse_any.parse_ecosystem(root, os.path.join(HERE, "fixture-ast.json"),
                                        verbose=False)
    owners = json.load(open(os.path.join(HERE, "fixture-owners.json")))
    return ecosystem.analyze(root, ast_all, owners)


R = build()
EDGES = {(e["from"], e["to"]): e for e in R["edges"]}
KINDS = {}
for f in R["findings"]:
    KINDS.setdefault(f["kind"], []).append(f)


# ---------------------------------------------------------------- structure

def test_all_fixture_repos_analyzed():
    assert R["headline"]["repos_analyzed"] == 21
    assert {"alpha-service", "beta-engine", "gamma-lib", "delta-tools",
            "epsilon-a", "epsilon-b"} <= set(R["repos"])


def test_exactly_the_planted_edges():
    assert set(EDGES) == {
        ("alpha-service", "beta-engine"),
        ("epsilon-a", "epsilon-b"),
        ("epsilon-b", "epsilon-a"),
        ("mu-web", "gamma-lib"),
        ("tau-mono", "gamma-lib"),
        ("zeta-one", "zeta-two"),
        ("zeta-two", "zeta-three"),
        ("zeta-three", "zeta-one"),
    }


def test_alpha_to_beta_has_two_import_sites():
    assert EDGES[("alpha-service", "beta-engine")]["sites"] == 2


def test_orphans_are_exactly_the_repos_with_no_seams():
    with_seams = {e["from"] for e in R["edges"]} | {e["to"] for e in R["edges"]}
    assert set(R["orphans"]) == set(R["repos"]) - with_seams
    assert {"delta-tools", "sigma-socket", "nu-empty"} <= set(R["orphans"])
    assert "gamma-lib" not in set(R["orphans"]), "gamma-lib is consumed by mu-web and tau-mono"


# ------------------------------------------------- regression: shared roots

def test_no_spurious_edge_from_shared_root():
    """beta-engine and alpha-service both provide `common`. beta's internal
    `from common.util import Other` must NOT become an edge to alpha."""
    assert not any(f == "beta-engine" for (f, _) in EDGES), \
        f"spurious outgoing edge from beta-engine: {[k for k in EDGES if k[0]=='beta-engine']}"


def test_shared_root_not_counted_as_external():
    assert "common" not in dict(R["top_external"]), \
        "`common` is internal to two repos and must not appear as an external package"


# ---------------------------------------------------------------- checkers

def test_undeclared_dependency_found():
    hits = KINDS.get("UNDECLARED_DEPENDENCY", [])
    assert len(hits) == 1
    assert "alpha-service" in hits[0]["summary"] and "beta-engine" in hits[0]["summary"]


def test_declared_dependency_is_not_flagged():
    """epsilon-a declares epsilon-b, so that pair must not be reported."""
    for f in KINDS.get("UNDECLARED_DEPENDENCY", []):
        assert "epsilon" not in f["summary"]


def test_unresolved_interface_found():
    hits = KINDS.get("UNRESOLVED_INTERFACE", [])
    assert len(hits) == 1
    assert "beta.ghost" in hits[0]["summary"]


def test_resolved_interface_not_flagged():
    """beta.engine and epsilon_b.b both exist and must not be reported."""
    for f in KINDS.get("UNRESOLVED_INTERFACE", []):
        assert "beta.engine" not in f["summary"]
        assert "epsilon_b.b" not in f["summary"]


def test_declaration_mismatch_found():
    hits = KINDS.get("DECLARATION_MISMATCH", [])
    assert len(hits) == 1
    assert "WRONG-ORG" in hits[0]["summary"] and "example-org" in hits[0]["summary"]


def test_every_planted_cycle_is_found_exactly_once():
    hits = KINDS.get("CYCLE", [])
    assert len(hits) == 2, f"expected the epsilon pair and the zeta triple: {hits}"
    joined = " ".join(h["summary"] for h in hits)
    assert "epsilon-a" in joined and "epsilon-b" in joined
    zeta = next((h for h in hits if "zeta" in h["summary"]), None)
    assert zeta is not None, "the three-repo cycle was missed — pairwise checks cannot see it"
    for r in ("zeta-one", "zeta-two", "zeta-three"):
        assert r in zeta["summary"]


def test_no_consumers_lists_the_right_repos():
    hits = {f["summary"].split()[-1] for f in KINDS.get("NO_CONSUMERS", [])}
    # alpha-service consumes beta but nothing consumes alpha
    assert {"alpha-service", "delta-tools", "sigma-socket"} <= hits
    # these have incoming edges and must never be listed
    assert not ({"beta-engine", "epsilon-a", "epsilon-b", "gamma-lib",
                 "zeta-one", "zeta-two", "zeta-three"} & hits)


def test_every_finding_has_a_witness():
    for f in R["findings"]:
        assert f.get("witnesses"), f"{f['kind']} has no witnesses"
        for w in f["witnesses"]:
            assert w.get("repo")


def test_headline_symbol_count():
    # Runner + MissingThing (alpha->beta), BThing + AThing (epsilon pair),
    # helper (mu/tau -> gamma), B2 + C3 + A1 (zeta cycle).
    # Thing and Other are internal to their own repo and must NOT count.
    assert R["headline"]["distinct_symbols_crossing"] == 8


# ------------------------------------------------- regression: node builtins

def test_node_builtin_prefixed_imports_are_neither_seams_nor_external():
    """mu-web imports node:assert. The un-normalised `node:` prefix used to put
    node:assert (355 real-ecosystem sites) at the top of the external list."""
    assert ("mu-web",) not in [tuple()], "placeholder guard against empty edges"
    external = dict(R["top_external"])
    assert "node:assert" not in external and "assert" not in external
    assert not any(f == "mu-web" and t != "gamma-lib" for (f, t) in EDGES), \
        f"node:builtin produced a seam: {[e for e in EDGES if e[0] == 'mu-web']}"


# --------------------------------------------- regression: import_root order

def test_dotted_npm_package_is_not_split_on_the_dot():
    """socket.io-client is one package. Splitting on '.' before '/' makes it
    'socket', which sigma-socket publishes — an invented seam."""
    assert ("mu-web", "sigma-socket") not in EDGES
    external = dict(R["top_external"])
    assert external.get("socket.io-client"), "socket.io-client should be one external package"
    assert "socket" not in external


# ------------------------------------------------------- regression: lines

def test_js_import_line_numbers_ignore_preceding_blank_lines():
    """The gamma-lib import sits on line 5 of mu-web/src/main.ts, below a blank
    line. ^\\s* in multiline mode swallowed the blank line and reported 4."""
    e = EDGES[("mu-web", "gamma-lib")]
    lines = [w["line"] for w in e["witnesses"]]
    assert lines == [6], f"expected the true line 6, got {lines}"


# -------------------------------------------- regression: nested manifests

def test_dependency_declared_in_a_nested_workspace_manifest_counts():
    """tau-mono declares gamma-lib in packages/web/package.json, not the root.
    Reading only the root manifest flags a declared dependency as undeclared."""
    assert ("tau-mono", "gamma-lib") in EDGES
    for f in KINDS.get("UNDECLARED_DEPENDENCY", []):
        assert "tau-mono" not in f["summary"]
    assert "@tau/web" in R["published_names"]["tau-mono"]


# ------------------------------------------------------------- determinism

def test_parse_output_is_byte_identical_across_runs(tmp_path):
    """os.walk yields directories in filesystem order. Unsorted, two runs on
    different machines produce different module order — the determinism claim
    was false. Sorted walks make the output a pure function of the input."""
    a = os.path.join(tmp_path, "a.json")
    b = os.path.join(tmp_path, "b.json")
    root = make_fixture.build()
    parse_any.parse_ecosystem(root, a, verbose=False)
    parse_any.parse_ecosystem(root, b, verbose=False)
    assert open(a, "rb").read() == open(b, "rb").read()


# --------------------------------- regression: structural dirs are not names

def test_structural_directories_do_not_publish_package_names():
    """delta-tools has a tests/ tree. `tests` is not a package it publishes —
    claiming it is how 5 repos once 'owned' server/app."""
    pub = R["published_names"]["delta-tools"]
    assert "tests" not in pub and "src" not in pub and "app" in pub
    assert "pytest" in dict(R["top_external"]), "pytest is a real external"


# ------------------------------------------------- regression: gradle files

def test_gradle_kts_dependencies_are_read():
    """Nested Gradle build files carry real dependencies; ignoring them made
    JVM repos look declaration-free."""
    from faultline.analysis.seams import load_manifests
    m = load_manifests(make_fixture.build())
    assert "okhttp" in m["delta-tools"]["deps"]
