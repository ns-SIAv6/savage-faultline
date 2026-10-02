# Savage Faultline — from snapshot tool to instrument

## Context

Faultline currently answers one question well: *what crosses a boundary between these repositories?* It does that with a verified engine — a real multi-repo estate, thousands of files parsed, 103 passing tests, deterministic output — and it found something genuinely important: two services that shared no code but shared a live Postgres database and 162 byte-identical files between them.

But two ceilings have to go.

**Ceiling 1 — it is a snapshot.** You run it, it prints findings, you close it. No persistence, no time, no ranking by consequence, no narrative, and a coupling model covering 5 kinds out of a dozen that can silently bind two repositories together.

**Ceiling 2 — it is bespoke.** Every design decision so far has been validated against one developer's repository estate. The resolver knows about Python nesting because *these* repos nest. The parser is hand-written per language because *these* repos use those languages. That makes it a script, not a product.

**Intended outcome:** an instrument that works on any account, any host, any language mix, any number of repositories — with zero configuration — and tells its user the story of their own system in prose.

**Decisions taken:** build the three-axis model *and* a 3D navigation view over it; the story is a generated narrative; I choose the sequencing.

---

## Built for anyone, not this setup

This is a first-class requirement, not a footnote. Every item below is a constraint on the design.

| requirement | what it means |
|---|---|
| **Works with no credentials** | Public repositories must work with **zero configuration and no token**. `faultline owner/repo` or `faultline <org>` and it runs. Auth is an upgrade, never a prerequisite. |
| **Host-agnostic** | GitHub first, but fetching sits behind a `Source` interface. GitLab, Bitbucket, and a **local path** (the most common real case — "analyse this directory") are adapters, not rewrites. |
| **Language-agnostic** | Replace hand-rolled per-language regex with **tree-sitter** grammars. Broad coverage that grows by adding a grammar, not by writing a parser. Hand-written regex stays only as a fallback for languages with no grammar. |
| **Scale-agnostic** | 1 repo or 1,000. The incremental model is what makes re-runs cheap; nothing may assume the whole ecosystem fits in one pass or one memory image. |
| **Zero-config first run** | The first command must produce something useful in under a minute on a repo the user has never analysed, with no config file. |
| **Installable** | One command to install and one to run. Not "clone this and edit these paths". |
| **Generic narrative** | The story must be **derived from the model only**. No domain knowledge, no hardcoded names. If it reads well on someone else's 4-repo project, it is correct. |
| **Honest when it cannot tell** | Any coupling kind that cannot be checked on a given repo (unsupported language, unparseable config) must be **reported as unchecked**, never silently omitted. The certificate's whole value is that it does not overclaim. |

Status update (2026-09-30): several of these now hold. The GitHub and local sources work with no token (60 req/hr) and the CLI runs against a bare directory. `extract/structure.py` remains a per-language regex pile pending tree-sitter.

> **Measured — the credential-free path is real but rate-limited.** Verified 2026-09-30: the tool reaches `api.github.com` and `codeload.github.com` directly, with no proxy and no token (both return 200). **But unauthenticated GitHub allows 60 requests/hour, against 5,000 authenticated.** That single number is why the one-request-per-repo tarball design is load-bearing rather than a nicety: at 1 request per repo, a no-credential user can analyse **60 repos/hour**; at one request per *file*, the same user gets through roughly one large repo. The tool must therefore (a) always prefer whole-repo fetches, (b) detect the 60/hour ceiling and say so plainly rather than appearing to hang, and (c) treat a token as a documented speed-up, not a gate.

---

## The three dimensions

| | what it is | status |
|---|---|---|
| **D1 Structure** | repos → modules → symbols → files | ✅ have |
| **D2 Coupling** | everything that binds two repos | ⚠️ 5 kinds of ~12 |
| **D3 Time** | commits, when each seam appeared, drift | ❌ absent |

Genuinely orthogonal — *what exists*, *how it's joined*, *when it changed* — which is why a 3D view over them is earned rather than decorative. 3D is used **only** for navigation across the three axes; analytical views stay 2D where 2D is clearer.

---

## Architecture

Four layers, each depending only on the one below.

### 1. Sources (`sources/`) — new
`Source` interface: `list_repos()`, `fetch_tree(repo, ref)`, `fetch_commits(repo, since)`.
Adapters: `github` (API + tarball), `gitlab`, `local` (a filesystem path — no network at all).
Credential resolution: env var → config file → none. **None is a valid, fully-supported mode** for public repos.

### 2. Model (`model/`) — new
Persistent, incremental store. **SQLite** (stdlib, queryable, no server).
Tables: `repos`, `files`, `symbols`, `edges` (typed), `identifiers`, `commits`, `snapshots`, `unchecked`.
Keyed by `(repo, commit_sha)`. Re-runs re-analyse only repos whose HEAD moved. Everything else depends on this layer.

### 3. Extractors (`extract/`) — generalised
One module per coupling kind, each returning `(kind, identifier, witness)` triples and declaring **which languages/files it could not read**.

### 4. Analysis
- `seams.py` — boundary-crossing detection
- `checkers.py` — the ~20 checks
- `risk.py` — **new**: rank by consequence
- `narrative.py` — **new**: generate the story from the model

### 5. Views
- `web/ecosystem.html` — the 2D instrument (keep, extend)
- `web/cosmos.html` — **new**: 3D navigation over D1/D2/D3
- `reports/<date>.md` — **new**: the generated narrative

---

## Coverage gaps to close (D2)

| gap | why it misleads | how |
|---|---|---|
| network endpoints / hostnames | two repos hitting the same private API are coupled | URL literals + host blocklist |
| deploy targets | same Cloud Run service / registry / cluster | parse workflows, Dockerfile, `*.tf`, `*.bicep` |
| **fork detection** | 162 byte-identical files shared, undetected by naive matching | **path-suffix** matching + content hashes |
| shared secrets beyond env names | a literal key present in two repos | high-entropy string detection + cross-repo match |
| tsconfig path aliases | `@/components` opaque, real structure invisible | parse `tsconfig.json` `paths` |
| monorepo workspaces | a package named `workspace` isn't modelled | parse `workspaces`; treat sub-packages as units |
| dynamic imports | `importlib`, `require(expr)` missed | tree-sitter queries + literal-string heuristic |
| CI config sharing | shared deploy identity | workflow `env:` / `secrets:` |
| data schemas | shared table *shapes*, not just names | migration / DDL parsing |

> **Note on fork detection — measured, not assumed.** Full relative-path matching finds 3 shared paths between two services in the test estate (Jaccard 0.002), which reads as "unrelated". Path-**suffix** matching finds 252, of which **162 are byte-identical** — over a quarter of the smaller service's files. Nested layout hides the relationship from naive comparison. Any fork check must be suffix-based.

---

## Risk model (new)

Findings stop being a flat list. Ranked by **consequence**:

- **Critical** — shared live credential with no declared relationship; a repo that looks independent but another writes to
- **High** — undeclared dependency; unresolved interface; cyclic coupling
- **Medium** — declared/actual drift; orphan holding a live secret
- **Low** — structural notes

Security is currently invisible and is what makes this *urgent* rather than *interesting*: a repo that is "independent" while sharing a live database credential with three other repos is worth acting on today.

---

## Time (D3)

Ingest commits → derive `first_seen`/`last_seen` per edge → answer: is coupling growing? when did this credential start spreading? what changed since last run?

---

## Bug elimination

**Live issues to fix:**
1. Certificate wording must name every check run *and* every one skipped
2. "Similar pairs" (shared rare dependencies) is weak signal — give it a second signal or cut it
3. Test files counted in export/surface metrics
4. JVM imports resolved by exact match only; no definition lookup
5. Go/Rust/Ruby parsed for imports only, no symbols
6. Coupling witnesses truncated to 8, so the evidence shown may not represent every kind in a pair

---

## Sequencing

1. **Sources + Model** — ✅ sources shipped (github tarball + local, credential-free, rate-limit-honest, `faultline <target>` CLI). SQLite incremental store remains. *Everything else depends on the model.*
2. **Bug sweep** — ✅ done: critical + high pass fixed with planted guard tests (103 green), dead code deleted.
3. **Generalise parsing** — tree-sitter, replacing per-language regex.
4. **Coverage** — the nine extractors, each with fixture tests.
5. **Risk model** — consequence ranking + security dimension.
6. **Time** — commit ingest, seam timelines, change detection.
7. **Narrative** — `narrative.py` → `reports/<date>.md`.
8. **Cosmos** — 3D navigation over D1/D2/D3.

Each phase ships something usable.

---

## Verification

- `uv run --with pytest pytest tests/ -q` — all green, extended per phase
- **Determinism**: two consecutive builds byte-identical (`md5sum data/viz.json`) — already asserted
- **Ground truth**: extend `tests/make_fixture.py` with a planted pair that shares a database but no code, and a planted fork with nested paths — the two findings that matter most
- **No vacuous tests**: every new data source gets a guard test asserting it actually loaded
- **Portability tests — the new bar:** run against (a) a public repo with **no credentials**, (b) a **local directory** with no network, (c) the synthetic fixture, (d) a real multi-repo estate. All four must work. A feature that only works on the estate it was built against is not done.
- **Visual**: load both pages in a browser; confirm every rendered element traces to `file:line`

## Open risk

The certificate is the product's central promise — *"this repo is safe to archive."* Every phase that widens coverage also widens what it must claim. If a coupling kind cannot be checked on a given repo, the certificate must say so explicitly. An overclaimed certificate is worse than no certificate.
