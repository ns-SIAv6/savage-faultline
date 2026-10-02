"""extract/structure — one module model across every language in the ecosystem.

Python gets a real AST. Everything else gets a regex pass, which is enough for
the only question the seam engine asks: what does this file import, and from
where. Symbol extraction for regex languages is best-effort and marked as such.
"""

import argparse
import ast as pyast
import json
import os
import re

REPOS = "data/repos"
OUT = "data/ast_all.json"

PY = {".py"}
JS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
JVM = {".kt", ".kts", ".java"}
OTHER = {".go", ".rs", ".rb", ".swift", ".php"}

SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv",
             "venv", ".next", "vendor", "Pods", ".gradle", "target",
             "coverage", ".idea", "site-packages", ".mypy_cache"}

# --- JS / TS ---------------------------------------------------------------
JS_IMPORT = re.compile(
    r"""^[ \t]*import\s+(?:(?P<names>[\w*{}\s,$]+?)\s+from\s+)?['"](?P<mod>[^'"]+)['"]""",
    re.M)
JS_EXPORT_FROM = re.compile(
    r"""^[ \t]*export\s+(?:\*|\{[^}]*\})\s+from\s+['"](?P<mod>[^'"]+)['"]""", re.M)
JS_REQUIRE = re.compile(r"""require\(\s*['"](?P<mod>[^'"]+)['"]\s*\)""")
JS_DYNAMIC = re.compile(r"""import\(\s*['"](?P<mod>[^'"]+)['"]\s*\)""")
JS_SYMBOL = re.compile(
    r"""^[ \t]*export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)""",
    re.M)

# --- JVM -------------------------------------------------------------------
JVM_IMPORT = re.compile(r"^[ \t]*import\s+(?P<mod>[\w.]+)(?:\.\*)?\s*;?", re.M)
JVM_PACKAGE = re.compile(r"^[ \t]*package\s+(?P<pkg>[\w.]+)", re.M)
JVM_SYMBOL = re.compile(r"^[ \t]*(?:public\s+|internal\s+|private\s+|abstract\s+|open\s+)*"
                        r"(?:class|interface|object|enum\s+class|fun|val|var)\s+(?P<name>\w+)", re.M)

# --- others ----------------------------------------------------------------
GO_IMPORT = re.compile(r'^[ \t]*(?:import\s+)?(?:[\w.]+\s+)?"(?P<mod>[^"]+)"', re.M)
RS_USE = re.compile(r"^[ \t]*use\s+(?P<mod>[\w:]+)", re.M)
RB_REQUIRE = re.compile(r"""^[ \t]*require(?:_relative)?\s+['"](?P<mod>[^'"]+)['"]""", re.M)


def split_names(raw):
    """'Foo, {Bar, Baz}, * as Qux' -> ['Foo','Bar','Baz','Qux']"""
    if not raw:
        return []
    out = []
    for tok in re.split(r"[,{}]", raw):
        tok = tok.strip().replace("* as ", "").strip()
        if tok and tok != "*" and re.fullmatch(r"[\w$]+", tok):
            out.append(tok)
    return out


def dotted_from_rel(rel):
    """src/beta/__init__.py -> 'src.beta' (an __init__ *is* its package)."""
    d = rel[:-3].replace("/", ".")
    if d.endswith(".__init__"):
        d = d[: -len(".__init__")]
    elif d == "__init__":
        d = ""
    return d


def parse_python(repo, path, rel):
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = pyast.parse(src, filename=rel)
    except SyntaxError as e:
        return {"repo": repo, "path": rel, "lang": "python", "error": f"SyntaxError: {e}"}

    imports = []
    for n in pyast.walk(tree):
        if isinstance(n, pyast.Import):
            for a in n.names:
                imports.append({"module": a.name, "names": [], "level": 0, "line": n.lineno})
        elif isinstance(n, pyast.ImportFrom):
            imports.append({"module": n.module or "", "names": [a.name for a in n.names],
                            "level": n.level, "line": n.lineno})

    symbols = []
    for n in tree.body:
        if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
            symbols.append({"name": n.name, "line": n.lineno,
                            "doc": (pyast.get_docstring(n) or "")[:200]})
    return {"repo": repo, "path": rel, "lang": "python", "dotted": dotted_from_rel(rel),
            "imports": imports, "symbols": symbols,
            "loc": len(src.splitlines())}


def parse_js(repo, path, rel):
    src = open(path, encoding="utf-8", errors="replace").read()
    imports = []
    for m in JS_IMPORT.finditer(src):
        imports.append({"module": m.group("mod"), "names": split_names(m.group("names")),
                        "level": 0, "line": src[:m.start()].count("\n") + 1})
    for rx in (JS_EXPORT_FROM, JS_REQUIRE, JS_DYNAMIC):
        for m in rx.finditer(src):
            imports.append({"module": m.group("mod"), "names": [],
                            "level": 0, "line": src[:m.start()].count("\n") + 1})
    symbols = [{"name": m.group("name"), "line": src[:m.start()].count("\n") + 1, "doc": ""}
               for m in JS_SYMBOL.finditer(src)]
    return {"repo": repo, "path": rel, "lang": "js", "dotted": rel.rsplit(".", 1)[0].replace("/", "."),
            "imports": imports, "symbols": symbols,
            "loc": len(src.splitlines())}


def parse_jvm(repo, path, rel):
    src = open(path, encoding="utf-8", errors="replace").read()
    pkg = JVM_PACKAGE.search(src)
    imports = [{"module": m.group("mod"), "names": [], "level": 0,
                "line": src[:m.start()].count("\n") + 1}
               for m in JVM_IMPORT.finditer(src)]
    symbols = [{"name": m.group("name"), "line": src[:m.start()].count("\n") + 1, "doc": ""}
               for m in JVM_SYMBOL.finditer(src)]
    return {"repo": repo, "path": rel, "lang": "jvm",
            "dotted": (pkg.group("pkg") if pkg else rel.rsplit(".", 1)[0].replace("/", ".")),
            "imports": imports, "symbols": symbols,
            "loc": len(src.splitlines())}


def parse_other(repo, path, rel, ext):
    src = open(path, encoding="utf-8", errors="replace").read()
    rx = {".go": GO_IMPORT, ".rs": RS_USE, ".rb": RB_REQUIRE}.get(ext)
    imports = ([{"module": m.group("mod"), "names": [], "level": 0,
                 "line": src[:m.start()].count("\n") + 1} for m in rx.finditer(src)]
               if rx else [])
    return {"repo": repo, "path": rel, "lang": ext.lstrip("."), "dotted": rel,
            "imports": imports, "symbols": [], "loc": len(src.splitlines())}


def parse_ecosystem(repos_dir, out_path, verbose=True):
    out = {}
    for repo in sorted(os.listdir(repos_dir)):
        base = os.path.join(repos_dir, repo)
        if not os.path.isdir(base):
            continue
        mods = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, base)
                try:
                    if ext in PY:
                        mods.append(parse_python(repo, full, rel))
                    elif ext in JS:
                        mods.append(parse_js(repo, full, rel))
                    elif ext in JVM:
                        mods.append(parse_jvm(repo, full, rel))
                    elif ext in OTHER:
                        mods.append(parse_other(repo, full, rel, ext))
                except Exception as e:
                    mods.append({"repo": repo, "path": rel, "lang": ext.lstrip("."),
                                 "error": str(e)[:120], "imports": [], "symbols": [], "loc": 0})
        out[repo] = mods
        if verbose:
            langs = {}
            for m in mods:
                langs[m.get("lang", "?")] = langs.get(m.get("lang", "?"), 0) + 1
            errs = sum(1 for m in mods if "error" in m)
            print(f"{repo:34} {len(mods):5} files  " +
                  " ".join(f"{k}:{v}" for k, v in sorted(langs.items(), key=lambda x: -x[1])) +
                  (f"  errors={errs}" if errs else ""))

    json.dump(out, open(out_path, "w"), indent=1)
    total = sum(len(v) for v in out.values())
    if verbose:
        print(f"\ntotal parsed: {total} files across {len(out)} repos -> {out_path}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", default=REPOS)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    parse_ecosystem(a.repos_dir, a.out)


if __name__ == "__main__":
    main()
