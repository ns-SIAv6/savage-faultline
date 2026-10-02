# Savage Faultline

**Cross-repository coupling analysis.** Finds the relationships *between*
repositories in a multi-repo system — the ones no single-repo tool can see — so
a maintainer can safely delete, move, or refactor a repository.

---

## The idea

Every code tool maps *things*. Faultline maps **seams** — what repositories do
to each other. The admission rule is the whole design:

> A claim enters the map only if its witnesses span **two or more repositories**.

Anything you could learn by reading one repository is excluded by construction.
So the map cannot contain anything about what is inside your code; it contains
only what your repos do to each other. A repo with no connections yields an
empty map — and that emptiness is itself the finding.

### Two outputs that matter

**Isolation certificate** — a machine-checkable proof that a repository has no
code-level dependency in either direction **and** shares no environment
variable, database table or bucket name with any other repository. It answers
the question a person maintaining many repos actually has: *which of these can
I archive, delete, or hand off without breaking something?* A certificate is
only issued when both checks actually ran and at least one source file was
parsed — you cannot certify what you did not read, and a check that could not
run is reported as unchecked, never silently omitted.

**Blast radius** — exactly which repositories break if this one changes, with
the transitive closure and a `file:line` witness for every claim.

### Emergent properties

The map also surfaces claims **no single repository could establish**: hub
topology, credential fan-out (one secret read by N repos that never mention
each other), stack convergence, read-vs-offered surfaces, content
constellations. Each carries a `because` line stating why no single repo could
know it. If you can learn it by reading one repo, it is not in this map.

---

## The finding that shaped the design

A code-only version of this tool certified most of a real estate as
independent. Adding operational coupling — shared env vars, SQL tables,
buckets — cut that sharply.

The reason: two services shared **no code at all**, but they shared a live
Postgres database and **162 byte-identical files**. Full-path matching found 3
shared paths; path-*suffix* matching found 252. Nested layout hides the
relationship from naive comparison.

**A code-only independence claim would have said it was safe to delete one of
them. That would have been wrong, and expensive.**

---

## Install and run

```bash
pip install .            # or: uv run faultline ...

faultline analyze ./my-projects      # a directory of repos — no network
faultline analyze owner/repo         # one GitHub repository
faultline analyze someorg            # every visible repo of an org or user
faultline fetch someorg --out data/repos   # materialise only

uv run --with pytest pytest tests/ -q      # 103 tests
python3 -m http.server 8777                # web/ecosystem.html (2D) or web/cosmos.html (3D cyclone)
```

**No credentials required.** Public repos work with zero configuration —
GitHub allows 60 requests/hour anonymously against 5,000 with a token, which
is why fetching is always one tarball per repository, never one request per
file. `GITHUB_TOKEN` in the environment is a speed upgrade, never a gate.
When the budget runs out, the tool says so and how to fix it; it never
appears to hang.

---

## Live demo

`demo/` holds a self-contained, anonymized run against a real 20-repository
estate — open `demo/ecosystem.html` or `demo/cosmos.html` in a browser, no
server needed. Every identifier is scrubbed; only counts and shapes survive.

---

## Architecture

Layered; each layer depends only on the one below. Nothing above `sources/`
touches the network, and nothing above `extract/` touches raw files — that is
what makes the same engine run against a GitHub org, a local directory, or the
synthetic fixture unchanged.

```
faultline/
  sources/    where repositories come from (github, local; gitlab = adapter)
  extract/    structure (per-language imports/symbols) + operational
              (env vars, tables, buckets — coupling that is not an import)
  analysis/   seams and the checkers that run over them
  views/      the dataset the web instrument renders
tests/        synthetic fixture with planted ground truth + the suite
web/          ecosystem.html (2D instrument) + cosmos.html (the 3D cyclone)
docs/PLAN.md  the full plan
```

Every fix is proven the same way: the failing case is planted in
`tests/make_fixture.py` first, the guard test is watched failing, then the
fix lands. The suite asserts determinism (two runs byte-identical), witness
presence on every claim, and — against a real multi-repo estate — that the
headline couplings are actually found.

---

## What it sees today

- **Code seams**: imports crossing repo boundaries, in Python (real AST) and
  JS/TS/JVM/Go/Rust/Ruby (regex; symbols for JS/TS/JVM), plus cross-repo
  *filesystem path* references (`../OtherRepo/...`) that no import graph sees.
- **Operational coupling**: shared environment variables (incl. `.env` files
  and exported shell vars; template literals and shell locals excluded),
  shared SQL tables (API calls and context-checked raw SQL), shared buckets.
- **Manifests at any depth**: pyproject, package.json (incl. workspaces),
  requirements.txt, setup.py, go.mod, Cargo.toml, Gradle (Groovy + Kotlin DSL).
- **Forks and vendored copies**: path-suffix + SHA-1 content matching —
  byte-identical files across repos, with both paths as witnesses.
- **Risk register**: findings ranked by consequence (shared credentials first,
  grouped per credential), each with witnesses and a stated consequence.
- **The narrative**: `data/reports/<date>.md` — the ecosystem's story in prose,
  generated from the model only. Every name in it exists in the model; the
  test suite proves it.
- **Emergent properties**: claims no single repo could establish — topology
  (hubs, strata, cycles), credential fan-out, stack convergence (labelled
  hypothesis), read-vs-offered surfaces, content constellations. Each carries
  a `because` line stating why no single repo could know.
- **Persistence**: SQLite store; re-runs skip repos whose HEAD has not moved;
  every run is a snapshot.
- **Checkers**: undeclared dependency, unresolved interface, declaration
  owner-mismatch, ambiguous names (reported, never silently awarded), cycles
  of any length (Tarjan SCC — not just pairs), no-consumers.
- **Derived**: blast radius (transitive), per-repo posture (clandestine /
  phantom / library / isolated / interdependent), predicted-absent seams as
  *labelled hypotheses*, isolation certificates with denial reasons.

## Honest edges (what it cannot yet see)

- **Submodules** — a `.gitmodules` aggregator fetches as 0 files; it is
  denied a certificate, but the pointers are not yet followed.
- **Time** — the store records run snapshots and can hold commit history;
  seam timelines ("when did this coupling appear?") are not yet derived.
- Go/Rust/Ruby contribute imports only; JVM resolution is exact-match.
  tree-sitter grammars replace the regex parsers in phase 3 of the plan.

Evaluated against a real 20-repository estate: **7 code seams**, **32
invisible-coupling pairs**, **4 certified independent**, 7 denied with
reasons, 34 emergent properties.

---

## Plan

The full plan is [`docs/PLAN.md`](docs/PLAN.md). Status:

1. **Sources + model** — sources done (github + local, credential-free);
   SQLite incremental model pending.
2. **Bug sweep** — done: full critical/high pass, each with a planted guard
   test; dead code deleted.
3. **Generalise parsing** — tree-sitter replaces the regex pass.
4. **Coverage** — nine more coupling kinds (network endpoints, deploy
   targets, forks, literal secrets, path aliases, workspaces, dynamic
   imports, CI config, schemas).
5. **Risk model** — ✅ done: consequence ranking, per-credential grouping, security dimension.
6. **Time** — commit history, seam timelines, change detection.
7. **Narrative** — ✅ done: `analysis/narrative.py` → `data/reports/<date>.md`.
8. **Cosmos** — the 3D cyclone view shipped (`web/cosmos.html`); time axis pending D3.

### If you are picking this up cold

1. Run `uv run --with pytest pytest tests/ -q`. Everything green means the
   fixture's planted truth and the real-ecosystem invariants both hold.
2. The fixture is the ground truth: extend `tests/make_fixture.py` before
   fixing anything, so the fix is proven rather than asserted.
3. Every defect found so far lived in **name resolution** — not graph
   algorithms, not visuals. Verify before reporting.
