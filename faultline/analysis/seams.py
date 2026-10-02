"""analysis/seams — whole-ecosystem seam analysis.

Answers the only question worth asking across many repos: how much of this is
actually connected, and where are the seams that exist?

Repo-agnostic: each repo's published names are resolved from whatever manifest it
has (pyproject, package.json, requirements.txt), then imports are checked against
those names to find boundary crossings.

Paths are parameters so the same code can run against the real ecosystem and
against the synthetic fixture in tests/.
"""

import argparse
import json
import os
import re
import sys as _sys
import tomllib
from collections import defaultdict

DEFAULT_REPOS = "data/repos"
DEFAULT_AST = "data/ast_all.json"
DEFAULT_OUT = "data/ecosystem.json"
DEFAULT_OWNERS = "data/all-trees.json"

NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events", "fs",
    "http", "http2", "https", "inspector", "module", "net", "os", "path",
    "perf_hooks", "process", "punycode", "querystring", "readline", "repl",
    "stream", "string_decoder", "sys", "timers", "tls", "tty", "url", "util",
    "v8", "vm", "wasi", "worker_threads", "zlib",
}

# Authoritative list from the interpreter, plus a manual fallback for names
# this hand-written set used to carry. Hand-maintaining it missed `__future__`,
# which then got normalised to `--future--` and counted as a third-party package.
STDLIB_HINT = set(getattr(_sys, "stdlib_module_names", set())) | {
    "os", "sys", "re", "json", "time", "datetime", "typing", "pathlib",
    "collections", "itertools", "functools", "dataclasses", "logging",
    "subprocess", "threading", "asyncio", "random", "math", "hashlib", "uuid",
    "abc", "enum", "copy", "io", "csv", "sqlite3", "unittest", "contextlib",
    "inspect", "warnings", "shutil", "tempfile", "traceback", "argparse",
    "base64", "hmac", "secrets", "struct", "socket", "ssl", "email", "http",
    "urllib", "xml", "html", "glob", "pickle", "ast", "textwrap", "string",
}


def is_runtime_builtin(name):
    """`node:assert` and `assert` are the same module — the prefix is syntax,
    not identity. Un-normalised, `node:assert` (355 real sites) ranked as a top
    external package."""
    n = name[5:] if name.startswith("node:") else name
    n = n.split("/")[0]                       # fs/promises -> fs
    return n in NODE_BUILTINS or n in STDLIB_HINT


def norm(name):
    """Normalise a package/import name for cross-ecosystem comparison."""
    if not name:
        return ""
    n = name.strip().lower()
    if n.startswith("@") and n.count("/") > 1:
        n = "/".join(n.split("/")[:2])
    return n.replace("_", "-")


def dep_name(spec):
    """'sia-v6-core @ git+https://...' -> 'sia-v6-core'.

    Exact names matter: substring matching makes 'sia-v6' match 'sia-v6-core',
    which silently turns an undeclared dependency into a declared one.
    """
    text = (spec or "").strip()
    # PEP 508 direct reference: "name @ url". The URL contains "://", so this
    # has to be handled before the URL branch or the name is lost.
    if " @ " in text:
        text = text.split(" @ ", 1)[0].strip()
    text = re.sub(r"^(?:-e|--editable)\s+", "", text)
    if "://" in text or text.startswith("git@"):
        # a bare VCS URL: the dependency's name is its last path component
        seg = re.split(r"[/:]", text.rstrip("/"))[-1]
        return norm(re.sub(r"\.git$", "", seg))
    text = re.sub(r"^(?:git|hg|svn|bzr)\+", "", text)
    m = re.match(r"^(@?[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)?)", text)
    return norm(m.group(1)) if m else ""


def import_root(spec, lang="python"):
    """The top-level package a specifier names.

    Python puts packages on a dotted path, so dots split. A JS/JVM bare
    specifier IS a package name where dots are literal — socket.io-client is
    one package; splitting it on the dot made it `socket`, a different
    package another repo may publish.
    """
    if spec.startswith("@"):
        parts = spec.split("/")
        return "/".join(parts[:2]) if len(parts) > 1 else spec
    if lang == "python":
        return spec.split(".")[0]
    return spec.split("/")[0]


MANIFEST_SKIP = {"node_modules", "__pycache__", ".git", "dist", "build",
                 ".venv", "venv", "vendor", "target", "site-packages",
                 ".next", ".gradle", "coverage"}


def _read_pyproject(path, names, deps):
    try:
        proj = tomllib.load(open(path, "rb")).get("project", {})
        if proj.get("name"):
            names.add(proj["name"])
        for d in proj.get("dependencies", []) or []:
            deps.add(d)
        for group in (proj.get("optional-dependencies") or {}).values():
            for d in group or []:
                deps.add(d)
    except Exception:
        pass


def _read_package_json(path, names, deps):
    try:
        # utf-8-sig strips a leading BOM, which otherwise makes json.load
        # throw and silently costs the repo its name
        j = json.load(open(path, encoding="utf-8-sig"))
        if j.get("name"):
            names.add(j["name"])
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            for d in (j.get(key) or {}):
                deps.add(d)
    except Exception:
        pass


def _read_requirements(path, deps):
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#"):
                deps.add(line)
    except Exception:
        pass


def _read_setup_py(path, names):
    try:
        m = re.search(r"""name\s*=\s*['"]([^'"]+)['"]""",
                      open(path, encoding="utf-8", errors="replace").read())
        if m:
            names.add(m.group(1))
    except Exception:
        pass


def _read_go_mod(path, names):
    try:
        m = re.search(r"^module\s+(\S+)",
                      open(path, encoding="utf-8", errors="replace").read(), re.M)
        if m:
            names.add(m.group(1))
    except Exception:
        pass


def _read_cargo(path, names, deps):
    try:
        j = tomllib.load(open(path, "rb"))
        pkg = j.get("package", {})
        if pkg.get("name"):
            names.add(pkg["name"])
        for key in ("dependencies", "dev-dependencies", "build-dependencies"):
            for d in (j.get(key) or {}):
                deps.add(d)
    except Exception:
        pass


GRADLE_DEP = re.compile(r'(?:implementation|api|compileOnly|runtimeOnly|'
                        r'testImplementation|kapt)\s*\(?\s*["\']([^"\']+)["\']')
GRADLE_NAME = re.compile(r'rootProject\.name\s*=\s*["\']([^"\']+)["\']')


def _read_gradle(path, names, deps):
    """build.gradle[.kts] contributes dependencies (group:artifact:version —
    the artifact id is the name that matters); settings.gradle[.kts]
    contributes the project's own name."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return
    for coords in GRADLE_DEP.findall(text):
        parts = coords.split(":")
        deps.add(parts[1] if len(parts) >= 2 else parts[0])
    m = GRADLE_NAME.search(text)
    if m:
        names.add(m.group(1))


def load_manifests(repos_dir):
    """Every manifest a repo carries, at any depth.

    Only reading the root is how a dependency declared in a workspace's
    nested package.json looked undeclared. Manifests under dependency
    directories (node_modules, vendor) belong to other packages, not this
    repo, so those trees are skipped.
    """
    out = {}
    if not os.path.isdir(repos_dir):
        return out
    three = {"pyproject.toml": _read_pyproject, "package.json": _read_package_json,
             "Cargo.toml": _read_cargo, "build.gradle": _read_gradle,
             "build.gradle.kts": _read_gradle, "settings.gradle": _read_gradle,
             "settings.gradle.kts": _read_gradle}
    two = {"setup.py": _read_setup_py, "go.mod": _read_go_mod}
    for repo in sorted(os.listdir(repos_dir)):
        base = os.path.join(repos_dir, repo)
        if not os.path.isdir(base):
            continue
        names, deps = set(), set()
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in MANIFEST_SKIP)
            for fn in sorted(filenames):
                path = os.path.join(dirpath, fn)
                try:
                    if fn == "requirements.txt":
                        _read_requirements(path, deps)
                    elif fn in three:
                        three[fn](path, names, deps)
                    elif fn in two:
                        two[fn](path, names)
                except Exception:
                    pass
        out[repo] = {"names": sorted(names), "deps": sorted(deps)}
    return out


def tails_index(ast_all):
    """repo -> every dotted path a module in it can be imported as.

    Python puts directories on sys.path, so `a/b/c.py` is importable as
    `a.b.c`, `b.c`, or `c` depending on which root is active. A repo whose code
    lives under a subdirectory (`sia-v6-platform/dat1/...`) therefore provides a
    bare `dat1` that a naive top-level model misses — and that bare name can
    collide with another repo's real top-level `dat1`, inventing a seam.
    """
    idx = {}
    for repo, mods in ast_all.items():
        s = set()
        for m in mods:
            d = m.get("dotted") or ""
            if d.startswith("src."):
                d = d[4:]
            if not d:
                continue
            parts = d.split(".")
            for i in range(len(parts)):
                s.add(".".join(parts[i:]))
        idx[repo] = s
    return idx


def module_index(ast_all):
    """repo -> set of dotted module paths it defines (src/ stripped)."""
    idx = {}
    for repo, mods in ast_all.items():
        s = set()
        for m in mods:
            d = m.get("dotted") or ""
            if d.startswith("src."):
                d = d[4:]
            if d:
                s.add(d)
        idx[repo] = s
    return idx



# ---------------------------------------------------------------- repo paths
# A cross-repo reference written as a *filesystem path* rather than a module
# name. `import(path.resolve(cwd, "../SIA-Foundry/orchestrators/x.js"))` is a
# real dependency on another repository that no import graph can see, because
# the specifier is a path. Missing this class let SIA-Foundry be certified
# independent while two repositories load it.
PATH_REF = re.compile(r"""['"`]([^'"`\n]{0,80}?)(?:\.\./)+([A-Za-z0-9][A-Za-z0-9._-]*)/""")

PATH_SCAN_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".kt",
                 ".kts", ".java", ".go", ".rs", ".rb", ".sh", ".json", ".yml",
                 ".yaml", ".toml", ".tf", ".bicep"}

PATH_SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", "build",
                  ".venv", "vendor", ".next", ".gradle", "target", "coverage"}


def repo_path_edges(repos_dir, repo_names):
    """Cross-repo edges implied by relative filesystem paths."""
    names = {r.lower(): r for r in repo_names}
    found = []
    for repo in repo_names:
        base = os.path.join(repos_dir, repo)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in PATH_SKIP_DIRS)
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in PATH_SCAN_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(full) > 400_000:
                        continue
                    text = open(full, encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                rel = os.path.relpath(full, base)
                for m in PATH_REF.finditer(text):
                    target = names.get(m.group(2).lower())
                    if target and target != repo:
                        found.append({
                            "from": repo, "to": target, "kind": "repo_path",
                            "via": m.group(0).strip("'\"`"), "names": [],
                            "file": rel, "line": text.count("\n", 0, m.start()) + 1,
                        })
    return found


def strongly_connected(graph):
    """Tarjan's SCCs, iterative — a 1,000-repo graph cannot blow the stack.
    Node sets are sorted at every step so output is deterministic."""
    index, low, on_stack, stack, out = {}, {}, set(), [], []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph.get(root, ()))))]
        while work:
            node, it = work[-1]
            if node not in index:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            descended = False
            for nxt in it:
                if nxt not in index:
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    descended = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                scc = set()
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.add(w)
                    if w == node:
                        break
                out.append(scc)
    return out


def analyze(repos_dir, ast_all, owners):
    manifests = load_manifests(repos_dir)
    modules = module_index(ast_all)
    tails = tails_index(ast_all)

    roots = {}
    for repo, mods in ast_all.items():
        s = set()
        for m in mods:
            d = m.get("dotted") or ""
            if d.startswith("src."):
                d = d[4:]
            root = d.split(".")[0]
            if root and root not in ("src", "test", "tests", "scripts"):
                s.add(root)
        roots[repo] = s

    # ------------------------------------------------------------------
    # Two different notions of "a name this repo owns". Conflating them caused
    # three separate false-seam bugs, so they are kept apart:
    #
    #   internal  — every name this repo can resolve *within itself*, at any
    #               depth. Used only to answer "is this import mine?".
    #   published — the names this repo is importable *by others* under: its
    #               manifest package name, plus directories that actually
    #               contain source. A bare file stem is NOT published. That is
    #               how `types.py` made a repo the owner of the stdlib, and
    #               `vite.config.ts` made one the owner of the npm package
    #               `vite`.
    # ------------------------------------------------------------------
    internal = {}
    for repo in ast_all:
        names = set()
        for n in manifests.get(repo, {}).get("names", []):
            names.add(norm(n))
            names.add(norm(n.split("/")[-1]))
        for r in roots.get(repo, ()):
            names.add(norm(r))
        names.discard("")
        internal[repo] = names

    # Directories that hold code but are not themselves importable packages.
    # A `tests/` tree used to publish the name `tests`; a workspace root used
    # to publish `packages`.
    NON_PACKAGE_DIRS = {"src", "test", "tests", "testing", "scripts", "docs",
                        "examples", "spec", "specs", "packages"}

    published = {}
    for repo, mods in ast_all.items():
        pub = set()
        for n in manifests.get(repo, {}).get("names", []):
            pub.add(norm(n))
            pub.add(norm(n.split("/")[-1]))
        for m in mods:
            # Directories come from the *path*, never from the dotted name.
            # `vite.config.ts` is a file whose stem contains a dot; reading its
            # dotted form as `vite.config` published a package called `vite`,
            # which is how a real npm import became a cross-repo edge.
            dirs = [d for d in (m.get("path") or "").split("/")[:-1] if d]
            for i in range(len(dirs)):
                if dirs[i].lower() in NON_PACKAGE_DIRS:
                    continue
                for j in range(i + 1, len(dirs) + 1):
                    pub.add(norm(".".join(dirs[i:j])))
        pub.discard("")
        published[repo] = pub

    pkg_names = {r: {norm(n) for n in manifests.get(r, {}).get("names", []) if n}
                 for r in ast_all}
    declared_by = {r: {dep_name(d) for d in manifests.get(r, {}).get("deps", [])}
                   for r in ast_all}

    def claim_map(names_by_repo):
        """Split claimed names into unambiguous owners and ambiguous ones.

        A name claimed by more than one repo must never be silently awarded to
        whichever sorts first.
        """
        claimants = defaultdict(set)
        for repo, names in names_by_repo.items():
            for n in names:
                if n:
                    claimants[n].add(repo)
        unique = {n: next(iter(rs)) for n, rs in claimants.items() if len(rs) == 1}
        amb = {n: sorted(rs) for n, rs in claimants.items() if len(rs) > 1}
        return unique, amb

    # Two owner maps, because the two language families resolve names
    # differently, and using one map for both produced false seams:
    #
    #   owner_py  — directory-derived names. Python puts directories on
    #               sys.path, so `sia-v6-platform/dat1/` really is importable
    #               as `dat1`.
    #   owner_pkg — manifest package names only. A JS/TS or JVM bare specifier
    #               is a *package* name, never a path. Using directories here
    #               let `overmind-os/src/adapters/apify/` capture all 70 real
    #               npm imports of `apify`.
    owner_py, amb_py = claim_map(published)
    owner_pkg, amb_pkg = claim_map(pkg_names)

    cross, external = [], defaultdict(int)
    ambiguous_hits = []
    for repo, mods in ast_all.items():
        for m in mods:
            if m.get("error"):
                continue
            lang = m.get("lang", "python")
            for imp in m.get("imports", []):
                spec = imp.get("module") or ""
                if not spec or spec.startswith(".") or imp.get("level"):
                    continue
                key = norm(import_root(spec, lang))

                # Standard library and runtime builtins are never another
                # repository, no matter what a repo named its files. Checking
                # this AFTER the owner lookup is how `import types` became a
                # seam to a repo with a types.ts.
                if is_runtime_builtin(key):
                    continue

                if lang == "python":
                    if (spec in tails.get(repo, ())
                            or key in internal.get(repo, ())
                            or key in published.get(repo, ())):
                        continue
                    owner, amb = owner_py, amb_py
                else:
                    if key in pkg_names.get(repo, ()):
                        continue
                    owner, amb = owner_pkg, amb_pkg

                target = owner.get(key)
                if target is None and key in amb:
                    # The importer's own manifest can disambiguate: if it
                    # declares exactly one of the candidates, that is the one
                    # it means.
                    declared = declared_by.get(repo, set())
                    narrowed = [c for c in amb[key]
                                if declared & (pkg_names.get(c, set()) | published.get(c, set()))]
                    if len(narrowed) == 1:
                        target = narrowed[0]
                    else:
                        ambiguous_hits.append({
                            "name": key, "importer": repo, "via": spec,
                            "candidates": amb[key],
                            "file": m["path"], "line": imp.get("line"),
                        })
                        continue

                if target and target != repo:
                    cross.append({"from": repo, "to": target, "kind": "import",
                                  "via": spec, "names": imp.get("names", []),
                                  "file": m["path"], "line": imp.get("line")})
                else:
                    external[key] += 1

    # cross-repo references written as relative filesystem paths
    cross.extend(repo_path_edges(repos_dir, sorted(ast_all.keys())))

    edges = defaultdict(list)
    for c in cross:
        edges[(c["from"], c["to"])].append(c)

    all_repos = sorted(ast_all.keys())
    connected = {r for pair in edges for r in pair}
    orphans = [r for r in all_repos if r not in connected]

    # ---------------------------------------------------------- checkers
    findings = []

    for (a, b), evs in sorted(edges.items()):
        deps = {dep_name(d) for d in manifests.get(a, {}).get("deps", [])}
        # compare against the target's real package names, not its folder names —
        # a folder called `vite` used to satisfy a dependency on the npm package
        cand = {n for n in pkg_names.get(b, ()) if len(n) > 2}
        if not (deps & cand):
            findings.append({
                "kind": "UNDECLARED_DEPENDENCY", "severity": "high",
                "summary": f"{a} imports {b} at {len(evs)} site(s) but declares no dependency on it",
                "witnesses": [{"repo": a, "file": e["file"], "line": e["line"],
                               "statement": e["via"]} for e in evs[:3]]
                             + [{"repo": a, "statement": f"declared: {manifests[a]['deps'] or '[]'}"}],
            })

    # imports a dotted module the target repo does not define
    for c in cross:
        spec = c["via"]
        if "." not in spec:
            continue
        tgt_mods = modules.get(c["to"], set())
        root = spec.split(".")[0]
        if not any(m == spec or m.startswith(spec + ".") for m in tgt_mods) \
           and spec not in tgt_mods and root in tgt_mods:
            findings.append({
                "kind": "UNRESOLVED_INTERFACE", "severity": "high",
                "summary": f"{c['from']} imports '{spec}' from {c['to']}, "
                           f"but {c['to']} defines no such module",
                "witnesses": [
                    {"repo": c["from"], "file": c["file"], "line": c["line"],
                     "statement": f"import {spec}"},
                    {"repo": c["to"], "statement": f"defines {sorted(tgt_mods)[:8]}"},
                ],
            })

    # declared git dependency pointing at an owner that does not match reality
    for repo, meta in manifests.items():
        for d in meta["deps"]:
            m = re.search(r"github\.com/([^/]+)/([^/.@]+)", d)
            if not m:
                continue
            declared_owner, dep_repo = m.group(1), m.group(2)
            actual = owners.get(dep_repo)
            if actual and actual != declared_owner:
                findings.append({
                    "kind": "DECLARATION_MISMATCH", "severity": "high",
                    "summary": f"{repo} declares a dependency on {dep_repo} at "
                               f"github.com/{declared_owner}/, but it is actually hosted at "
                               f"github.com/{actual}/",
                    "witnesses": [
                        {"repo": repo, "statement": d},
                        {"repo": actual, "statement": f"actual owner of {dep_repo}"},
                    ],
                })

    for h in ambiguous_hits:
        findings.append({
            "kind": "AMBIGUOUS_NAME", "severity": "medium",
            "summary": (f"{h['importer']} imports '{h['via']}', but "
                        f"{h['name']} is published by {len(h['candidates'])} "
                        f"repositories ({', '.join(h['candidates'])}) — which one "
                        f"is meant cannot be determined, so no seam is claimed"),
            "witnesses": [{"repo": h["importer"], "file": h["file"],
                           "line": h["line"], "statement": f"import {h['via']}"}]
                          + [{"repo": c, "statement": f"publishes {h['name']}"}
                             for c in h["candidates"]],
        })

    for repo in all_repos:
        if not any(k[1] == repo for k in edges):
            findings.append({
                "kind": "NO_CONSUMERS", "severity": "medium",
                "summary": f"nothing in the ecosystem imports from {repo}",
                "witnesses": [{"repo": repo, "statement": "no incoming cross-repo imports"}],
            })

    # A cycle is a strongly connected component, not a pair: A->B->C->A is one
    # cycle even though no two members import each other, and pairwise checks
    # are blind to it.
    graph = defaultdict(set)
    for (a, b) in edges:
        graph[a].add(b)
    for scc in strongly_connected(graph):
        if len(scc) < 2:
            continue
        members = sorted(scc)
        findings.append({
            "kind": "CYCLE", "severity": "medium",
            "summary": (f"cyclic coupling across {len(members)} repositories: "
                        + " -> ".join(members) + f" -> {members[0]}"),
            "witnesses": [{"repo": a, "file": evs[0]["file"],
                           "line": evs[0]["line"],
                           "statement": f"{a} -> {b}"}
                          for (a, b), evs in sorted(edges.items())
                          if a in scc and b in scc][:8],
        })

    symbols_crossing = {n for c in cross for n in c["names"]}

    return {
        "repos": all_repos,
        "edges": [{"from": a, "to": b, "sites": len(v),
                   "witnesses": [{"file": x["file"], "line": x["line"],
                                  "via": x["via"], "names": x["names"]} for x in v]}
                  for (a, b), v in sorted(edges.items())],
        "orphans": orphans,
        "published_names": {r: sorted(published[r]) for r in published},
        "ambiguous_names": {**amb_py, **amb_pkg},
        "findings": findings,
        "headline": {
            "repos_analyzed": len(all_repos),
            "repos_connected": len(connected),
            "repos_orphaned": len(orphans),
            "cross_repo_edges": len(edges),
            "import_sites": len(cross),
            "distinct_symbols_crossing": len(symbols_crossing),
            "external_packages": len(external),
        },
        "top_external": sorted(external.items(), key=lambda x: -x[1])[:25],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    ap.add_argument("--ast", default=DEFAULT_AST)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--owners", default=DEFAULT_OWNERS)
    a = ap.parse_args()

    ast_all = json.load(open(a.ast))
    owners = {}
    if os.path.exists(a.owners):
        for name, meta in json.load(open(a.owners)).items():
            full = meta.get("full_name", "")
            if "/" in full:
                owners[name] = full.split("/")[0]

    result = analyze(a.repos_dir, ast_all, owners)
    json.dump(result, open(a.out, "w"), indent=1)

    h = result["headline"]
    print("=" * 68)
    for k, v in h.items():
        print(f"  {k:26} {v}")
    print("=" * 68)
    print("\nedges:")
    for e in result["edges"]:
        print(f"  {e['from']:30} -> {e['to']:30} {e['sites']} site(s)")
    print(f"\norphans: {result['orphans']}")
    print(f"\nfindings: {len(result['findings'])}")
    for f in result["findings"][:15]:
        print(f"  [{f['severity']:6}] {f['kind']}: {f['summary']}")


if __name__ == "__main__":
    main()
