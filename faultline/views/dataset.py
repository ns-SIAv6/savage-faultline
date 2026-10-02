"""views/dataset — build the exact dataset the visualization renders.

Rule: nothing in the output of this file may be invented, estimated, or
randomized. Every number is measured from the repositories, every claim carries
witnesses, and the whole document is deterministic — same input, byte-identical
output. That determinism is asserted in tests.

Two novel qualities are computed here:

1. PROVENANCE — for every seam, which named symbols actually cross it and where
   each one is defined in the destination repo. So the picture is composed only
   of claims that can be opened.

2. PREDICTED-ABSENT SEAMS — repo pairs with strong measured similarity (shared
   third-party dependencies) that have no seam between them. Reported as a
   hypothesis with its evidence, never as a finding.
"""

import argparse
import configparser
import json
import math
import os
from collections import defaultdict

from ..analysis import emergent as emergent_model
from ..analysis import risk as risk_model
from ..analysis import seams

DEFAULT_AST = "data/ast_all.json"
DEFAULT_REPOS = "data/repos"
DEFAULT_OWNERS = "data/all-trees.json"
DEFAULT_OUT = "data/viz.json"
DEFAULT_COUPLING = "data/coupling.json"

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".kt", ".kts",
            ".java", ".go", ".rs", ".rb", ".swift", ".c", ".cpp", ".h"}

LANG_OF = {".py": "python", ".ts": "typescript", ".tsx": "typescript",
           ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
           ".cjs": "javascript", ".kt": "kotlin", ".kts": "kotlin",
           ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
           ".swift": "swift"}


def submodules_of(repos_dir, repo):
    """A repo that is only a .gitmodules aggregator examines as 0 files —
    the certificate denial must SAY it points elsewhere, and where."""
    p = os.path.join(repos_dir, repo, ".gitmodules")
    if not os.path.exists(p):
        return []
    c = configparser.ConfigParser()
    try:
        c.read(p, encoding="utf-8")
    except Exception:
        return []
    out = []
    for sect in c.sections():
        m = sect.split('"')
        out.append({
            "name": m[1] if len(m) >= 2 else sect,
            "path": c[sect].get("path", ""),
            "url": c[sect].get("url", ""),
        })
    return sorted(out, key=lambda x: x["name"])


def repo_stats(repos_dir, repo):
    """Measured facts about a repo on disk. No estimates."""
    base = os.path.join(repos_dir, repo)
    files, loc, langs = 0, 0, defaultdict(int)
    if not os.path.isdir(base):
        return {"files": 0, "loc": 0, "languages": {}}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in
                       ("node_modules", "__pycache__", ".git", "dist", "build")]
        for fn in filenames:
            files += 1
            ext = os.path.splitext(fn)[1].lower()
            if ext in CODE_EXT:
                langs[LANG_OF.get(ext, ext.lstrip("."))] += 1
                try:
                    with open(os.path.join(dirpath, fn), encoding="utf-8",
                              errors="replace") as f:
                        loc += sum(1 for _ in f)
                except Exception:
                    pass
    return {"files": files, "loc": loc,
            "languages": dict(sorted(langs.items(), key=lambda x: (-x[1], x[0])))}


def is_alias(spec):
    """Path aliases and monorepo shims are not third-party packages.

    `@/components` is a tsconfig path alias, not an npm package. Counting these
    as shared dependencies produces predictions that are pure noise.
    """
    return (spec.startswith("@/") or spec.startswith("@workspace")
            or spec.startswith("~") or spec.startswith("--")
            or spec.startswith("/") or spec.startswith("_"))


def external_sets(ast_all, published=None):
    """repo -> set of third-party package roots it imports. Measured.

    A name owned by ANY repo in the ecosystem is not third-party — without
    this, `similar_pairs` predicted coupling from repos importing each
    other's packages."""
    all_names = set()
    for names in (published or {}).values():
        all_names.update(seams.norm(n) for n in names)
    out = {}
    for repo, mods in ast_all.items():
        s = set()
        for m in mods:
            lang = m.get("lang", "python")
            for imp in m.get("imports", []):
                spec = imp.get("module") or ""
                if not spec or spec.startswith(".") or imp.get("level"):
                    continue
                if is_alias(spec):
                    continue
                key = seams.norm(seams.import_root(spec, lang))
                if seams.is_runtime_builtin(key):
                    continue
                if len(key) < 2 or key in all_names:
                    continue
                s.add(key)
        out[repo] = s
    return out


def idf_weights(ext, n_repos):
    """Rarity weight per package.

    `react` appears in most repos and says nothing; a package shared by exactly
    two repos is a real signal. Without this the predictions just surface
    whichever repos happen to use the same web framework.
    """
    df = defaultdict(int)
    for s in ext.values():
        for p in s:
            df[p] += 1
    return {p: math.log(n_repos / c) for p, c in df.items()}, df


def symbol_definitions(ast_all, repo):
    """symbol name -> list of where it is defined in that repo."""
    idx = defaultdict(list)
    for m in ast_all.get(repo, []):
        dotted = (m.get("dotted") or "")
        if dotted.startswith("src."):
            dotted = dotted[4:]
        for s in m.get("symbols", []):
            idx[s["name"]].append({"file": m["path"], "line": s["line"],
                                   "module": dotted})
    return idx


def build(args, owners):
    ast_all = json.load(open(args.ast))
    result = seams.analyze(args.repos_dir, ast_all, owners)
    manifests = seams.load_manifests(args.repos_dir)
    ext = external_sets(ast_all, result["published_names"])

    repos = sorted(result["repos"])

    # ---- per-repo measured facts ---------------------------------------
    repo_records = {}
    for r in repos:
        st = repo_stats(args.repos_dir, r)
        repo_records[r] = {
            "name": r,
            "files": st["files"],
            "loc": st["loc"],
            "languages": st["languages"],
            "package_name": (manifests.get(r, {}).get("names") or [None])[0],
            "declared_dependencies": manifests.get(r, {}).get("deps", []),
            "external_package_count": len(ext.get(r, ())),
            "submodules": submodules_of(args.repos_dir, r),
        }

    # ---- seams, with full symbol provenance ----------------------------
    seam_rows = []
    for e in result["edges"]:
        a, b = e["from"], e["to"]
        defs = symbol_definitions(ast_all, b)
        crossings = []
        for w in e["witnesses"]:
            for nm in (w.get("names") or []):
                cands = defs.get(nm, [])
                # a bare name matched anywhere in the repo is weak evidence;
                # prefer definitions inside the module actually imported
                in_module = [d for d in cands if d["module"] == w["via"]]
                crossings.append({
                    "symbol": nm,
                    "imported_at": {"file": w["file"], "line": w["line"]},
                    "via": w["via"],
                    "defined_in": in_module or cands,
                    "resolved_in_imported_module": bool(in_module),
                })
        # dedupe on (symbol, import site)
        seen, uniq = set(), []
        for c in crossings:
            k = (c["symbol"], c["imported_at"]["file"], c["imported_at"]["line"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        seam_rows.append({
            "from": a, "to": b, "sites": e["sites"],
            "symbols": uniq,
            "resolved_symbols": [c for c in uniq if c["defined_in"]],
            "unresolved_symbols": [c for c in uniq if not c["defined_in"]],
        })

    seam_pairs = {(s["from"], s["to"]) for s in seam_rows}

    # ---- predicted-absent seams ----------------------------------------
    # pairs with strong shared third-party dependency overlap and no seam.
    # A hypothesis with its evidence attached, never presented as fact.
    idf, df = idf_weights(ext, len(repos))
    ubiquity_cut = max(2, len(repos) // 3)
    predicted = []
    for i, a in enumerate(repos):
        for b in repos[i + 1:]:
            if (a, b) in seam_pairs or (b, a) in seam_pairs:
                continue
            shared = ext.get(a, set()) & ext.get(b, set())
            # only count dependencies that are actually rare in this ecosystem
            rare = sorted((p for p in shared if df[p] <= ubiquity_cut),
                          key=lambda p: (-idf[p], p))
            if not rare:
                continue
            predicted.append({
                "a": a, "b": b,
                "score": round(sum(idf[p] for p in rare), 4),
                "shared_total": len(shared),
                "shared_rare": rare[:12],
                "shared_rare_count": len(rare),
                "strongest": rare[0],
                "strongest_df": df[rare[0]],
            })
    predicted.sort(key=lambda x: (-x["score"], x["a"], x["b"]))

    # ---- coupling that is not an import --------------------------------
    # Two repos can share a database without sharing a line of code. A
    # code-only certificate would call them independent, which is a dangerous
    # thing to be wrong about, so the certificate now requires both.
    invisible = defaultdict(list)
    cpath = getattr(args, "coupling", None) or DEFAULT_COUPLING
    coupling_checked = os.path.exists(cpath)
    if coupling_checked:
        cj = json.load(open(cpath))
        for kind, items in cj.get("identifiers", {}).items():
            for it in items:
                rs = sorted(it["repos"])
                for i, a in enumerate(rs):
                    for b in rs[i + 1:]:
                        invisible[(a, b)].append({
                            "kind": kind,
                            "identifier": it["identifier"],
                            "at": next(({"repo": w["repo"], "file": w["file"], "line": w["line"]}
                                        for w in it["witnesses"] if w["repo"] == a), None),
                            "at_other": next(({"repo": w["repo"], "file": w["file"], "line": w["line"]}
                                              for w in it["witnesses"] if w["repo"] == b), None),
                        })

    # ---- shared content (forks / vendored copies) --------------------------
    # The third way two repos can be bound: one is a copy of the other. No
    # import graph or env scan sees it; only hashing does.
    fpath = getattr(args, "forks", None)
    forks_checked = bool(fpath) and os.path.exists(fpath)
    fork_pairs = []
    fork_coupled = defaultdict(list)
    if forks_checked:
        fork_pairs = json.load(open(fpath)).get("pairs", [])
        for fp in fork_pairs:
            if fp["identical_files"] >= 2:
                fork_coupled[fp["a"]].append((fp["b"], fp["identical_files"]))
                fork_coupled[fp["b"]].append((fp["a"], fp["identical_files"]))

    invisible_coupling = []
    for (a, b), v in sorted(invisible.items()):
        shown, per_kind = [], defaultdict(int)
        for x in sorted(v, key=lambda x: (x["kind"], x["identifier"])):
            if per_kind[x["kind"]] < 8:
                shown.append(x)
                per_kind[x["kind"]] += 1
        invisible_coupling.append({
            "a": a, "b": b, "count": len(v),
            "kinds": sorted({x["kind"] for x in v}),
            "identifiers": sorted({x["identifier"] for x in v}),
            # up to 8 per kind: slicing the flat list used to hide entire
            # kinds of coupling when one kind dominated
            "witnesses": shown,
        })
    invisible_coupling.sort(key=lambda x: (-x["count"], x["a"], x["b"]))
    coupled_repos = {r for pair in invisible for r in pair}

    # ---- isolation certificate -----------------------------------------
    # A machine-checkable proof of independence. This is the question a person
    # maintaining 27 repos actually has: which of these can I archive, delete,
    # or hand off without breaking anything? Nothing else answers it.
    certificates = []
    denied = []
    for r in repos:
        code_clear = not any(s["from"] == r or s["to"] == r for s in seam_rows)
        op_clear = (r not in coupled_repos) if coupling_checked else False
        examined = sum(1 for m in ast_all.get(r, []) if not m.get("error"))

        # A certificate is a promise that we looked. Without both checks run,
        # or without having read anything at all, there is nothing to promise.
        if not coupling_checked:
            denied.append({"repo": r, "reason":
                           "the operational check could not run (no coupling "
                           "data was supplied), so independence cannot be asserted"})
            continue
        if not forks_checked:
            denied.append({"repo": r, "reason":
                           "the content check could not run (no fork data was "
                           "supplied), so independence cannot be asserted"})
            continue
        if examined == 0:
            subs = repo_records[r]["submodules"]
            reason = ("no source files could be parsed, so nothing was "
                      "actually examined — you cannot certify what you "
                      "did not read")
            if subs:
                reason += (f". It is an aggregator of {len(subs)} submodule(s) "
                           f"({', '.join(x['name'] for x in subs[:6])}"
                           f"{'…' if len(subs) > 6 else ''}), which tarballs do "
                           f"not include — examine those repositories directly")
            denied.append({"repo": r, "reason": reason,
                           "submodules": [x["url"] for x in subs]})
            continue
        if not (code_clear and op_clear):
            if code_clear and not op_clear:
                denied.append({"repo": r, "reason": "no code coupling, but shares "
                               "operational identifiers with other repos",
                               "partners": sorted(o for (x, y) in invisible
                                                  for o in ([y] if x == r else [x] if y == r else []))})
            continue
        if r in fork_coupled:
            partners = sorted(fork_coupled[r])
            denied.append({"repo": r,
                           "reason": "shares byte-identical files with "
                                     + ", ".join(f"{o} ({n})" for o, n in partners)
                                     + " — a possible fork or vendored copy",
                           "partners": [o for o, _ in partners]})
            continue
        mods = ast_all.get(r, [])
        imports = sum(len(m.get("imports", [])) for m in mods)
        certificates.append({
            "repo": r,
            "files_parsed": len(mods),
            "imports_examined": imports,
            "cross_repo_out": 0,
            "cross_repo_in": 0,
            "declared_dependencies": manifests.get(r, {}).get("deps", []),
            "external_packages": len(ext.get(r, ())),
            "shared_operational_identifiers": 0,
            "operational_check_ran": coupling_checked,
            "content_check_ran": forks_checked,
            "scope": ("three independent checks, all of which must have run: "
                      "(1) no code-level import crosses in either direction; "
                      "(2) no environment variable, database table, or bucket "
                      "name is shared with any other repository; "
                      "(3) no byte-identical files shared with any other "
                      "repository (fork/vendor detection)."),
        })

    # ---- blast radius ---------------------------------------------------
    # The inverse question: what breaks if I touch this?
    dependents = defaultdict(set)
    for s in seam_rows:
        dependents[s["to"]].add(s["from"])
    blast = {}
    for r in repos:
        seen, stack = set(), list(dependents.get(r, ()))
        while stack:
            d = stack.pop()
            if d in seen:
                continue
            seen.add(d)
            stack.extend(dependents.get(d, ()))
        # a repo inside a dependency cycle would otherwise list itself
        seen.discard(r)
        blast[r] = {"direct": sorted(dependents.get(r, ())),
                    "transitive": sorted(seen),
                    "count": len(seen)}

    # ---- posture (the 4D embedding, recomputed from the same graph) ----
    posture = {}
    for r in repos:
        out_ = sum(1 for s in seam_rows if s["from"] == r)
        in_ = sum(1 for s in seam_rows if s["to"] == r)
        dout = sum(1 for d in manifests.get(r, {}).get("deps", [])
                   if any(seams.dep_name(d) in {seams.norm(o), seams.norm(o.split("/")[-1])}
                          for o in repos if o != r))
        din = sum(1 for o in repos if o != r
                  for d in manifests.get(o, {}).get("deps", [])
                  if seams.dep_name(d) in {seams.norm(r), seams.norm(r.split("/")[-1])})
        if out_ == 0 and in_ == 0 and dout == 0 and din == 0:
            regime = "isolated"
        elif out_ > dout:
            regime = "clandestine"
        elif dout > out_:
            regime = "phantom"
        elif in_ > 0 and out_ == 0:
            regime = "library"
        elif out_ > 0 and in_ > 0:
            regime = "interdependent"
        else:
            regime = "balanced"
        posture[r] = {"code_out": out_, "code_in": in_,
                      "declared_out": dout, "declared_in": din, "regime": regime}

    doc = {
        "headline": result["headline"],
        "repos": repo_records,
        "posture": posture,
        "seams": seam_rows,
        "certificates": certificates,
        "certificate_denied": denied,
        "invisible_coupling": invisible_coupling,
        "forks": fork_pairs,
        "blast_radius": blast,
        "similar_pairs": predicted[:20],
        "orphans": sorted(result["orphans"]),
        "ambiguous_names": result.get("ambiguous_names", {}),
        "findings": result["findings"],
        "measured": {
            "repos_total": len(repos),
            "repos_with_seams": len({s["from"] for s in seam_rows} | {s["to"] for s in seam_rows}),
            "seam_count": len(seam_rows),
            "import_sites": sum(s["sites"] for s in seam_rows),
            "symbols_crossing": len({c["symbol"] for s in seam_rows for c in s["symbols"]}),
            "symbols_resolved": len({c["symbol"] for s in seam_rows for c in s["resolved_symbols"]}),
            "certificates": len(certificates),
            "certificate_denied": len(denied),
            "invisible_coupling_pairs": len(invisible_coupling),
            "fork_pairs": len(fork_pairs),
            "similar_pairs": len(predicted),
            "repos_with_dependents": sum(1 for r in blast if blast[r]["count"]),
        },
    }
    doc["risk"] = risk_model.evaluate(doc)
    doc["measured"]["risk_counts"] = doc["risk"]["counts"]
    doc["emergent"] = emergent_model.detect(doc, ast_all)
    doc["measured"]["emergent_claims"] = len(doc["emergent"])
    json.dump(doc, open(args.out, "w"), indent=1, sort_keys=True)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ast", default=DEFAULT_AST)
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    ap.add_argument("--owners", default=DEFAULT_OWNERS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--coupling", default=DEFAULT_COUPLING)
    a = ap.parse_args()
    owners = {}
    if os.path.exists(a.owners):
        for name, meta in json.load(open(a.owners)).items():
            full = meta.get("full_name", "")
            if "/" in full:
                owners[name] = full.split("/")[0]
    d = build(a, owners)
    m = d["measured"]
    print(f"repos {m['repos_total']}  with seams {m['repos_with_seams']}  "
          f"seams {m['seam_count']}  sites {m['import_sites']}")
    print(f"symbols crossing {m['symbols_crossing']}  "
          f"resolved {m['symbols_resolved']}  certificates {m['certificates']}")
    print(f"\nseams:")
    for s in d["seams"]:
        syms = ", ".join(sorted({c["symbol"] for c in s["symbols"]})) or "(none named)"
        print(f"  {s['from']:22} -> {s['to']:22} {s['sites']} site(s)  {syms}")
    print(f"\nisolation certificates: {len(d['certificates'])} repos proven independent")
    for c in d["certificates"][:4]:
        print(f"  {c['repo']:24} {c['files_parsed']:4} files, "
              f"{c['imports_examined']:5} imports examined, 0 crossings")
    print(f"\nblast radius (what breaks if you touch it):")
    for r, b in sorted(d["blast_radius"].items(), key=lambda x: -x[1]["count"])[:6]:
        print(f"  {r:24} breaks {b['count']:2}  {b['transitive']}")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
