"""extract/operational — coupling that is not an import.

Two repos can be fatally joined without a single line of shared code: they read
the same environment variable, write the same database table, or point at the
same bucket. No import graph sees this, which is exactly why it is the coupling
people are actually afraid of.

Everything here is extracted from source text with witnesses. Nothing inferred.
"""

import argparse
import json
import math
import os
import re
from collections import defaultdict

DEFAULT_REPOS = "data/repos"
DEFAULT_OUT = "data/coupling.json"

SCAN_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".kt", ".kts",
            ".java", ".go", ".rs", ".rb", ".swift", ".sql", ".yml", ".yaml",
            ".toml", ".json", ".env", ".sh", ".tf", ".bicep", ".properties"}

SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv",
             "venv", ".next", "vendor", "Pods", ".gradle", "target",
             "coverage", ".idea", "site-packages"}

MAX_BYTES = 400_000

# ---------------------------------------------------------------- patterns

# Explicit env access in code — these APIs unambiguously read the environment.
ENV_PATTERNS = [
    re.compile(r"""os\.environ(?:\.get)?\s*[\[(]\s*['"]([A-Za-z][A-Za-z0-9_]{3,})['"]"""),
    re.compile(r"""os\.getenv\s*\(\s*['"]([A-Za-z][A-Za-z0-9_]{3,})['"]"""),
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]{3,})"""),
    re.compile(r"""process\.env\s*\[\s*['"]([A-Z][A-Z0-9_]{3,})['"]\s*\]"""),
    re.compile(r"""import\.meta\.env\.([A-Z][A-Z0-9_]{3,})"""),
]

# `${VAR}` means "environment" only where the shell or a config file is the
# interpreter. In a .ts file it is template-literal interpolation of a local —
# that mismatch is what reported DESCRIPTION and JOB_NAME as shared env.
SHELLISH_EXT = {".sh", ".yml", ".yaml", ".env", ".tf", ".bicep",
                ".properties"}
ENV_BRACE = re.compile(r"""\$\{([A-Z][A-Z0-9_]{3,})\}""")
# A variable assigned in the script and never exported is a shell local,
# not environment — child processes cannot see it.
SHELL_LOCAL = re.compile(r"""^([A-Z][A-Z0-9_]{3,})=""", re.M)
ENV_EXPORT = re.compile(r"""^export\s+([A-Z][A-Z0-9_]{3,})""", re.M)
# A .env file IS environment definition; plain assignments count there.
ENV_DOTENV = re.compile(r"""^([A-Z][A-Z0-9_]{3,})\s*=""", re.M)

# API-style table references are precise; raw SQL is not, so it needs context.
API_TABLE = [
    re.compile(r"""\.from\(\s*['"]([a-z_][a-z0-9_]{3,})['"]\s*\)"""),
    re.compile(r"""\.collection\(\s*['"]([a-z_][a-z0-9_]{3,})['"]\s*\)"""),
    re.compile(r"""\.table\(\s*['"]([a-z_][a-z0-9_]{3,})['"]\s*\)"""),
    re.compile(r"""\.sheet\(\s*['"]([A-Za-z_][A-Za-z0-9_ ]{3,})['"]\s*\)"""),
]

# matching `FROM Epoch` inside prose produced 271 fake "tables", so a raw-SQL
# match now has to sit on a line that also carries real SQL syntax
RAW_SQL = re.compile(r"""\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z][a-z0-9_]{3,})\b""")
SQL_CTX = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|VALUES|SET|JOIN|"
                     r"ORDER\s+BY|GROUP\s+BY|LIMIT|CREATE\s+TABLE|PRIMARY\s+KEY)\b")

BUCKET_PATTERNS = [
    re.compile(r"""s3://([a-z0-9][a-z0-9.\-]{3,})"""),
    re.compile(r"""gs://([a-z0-9][a-z0-9.\-]{3,})"""),
]


def env_is_meaningful(name):
    """`AUTH` and `BODY` are noise; `DB_HOST` is not."""
    return len(name) >= 4 and ("_" in name or len(name) >= 8)


# names that appear everywhere and therefore mean nothing
UBIQUITOUS = {
    "PATH", "HOME", "PWD", "USER", "SHELL", "LANG", "TERM", "HOSTNAME", "TZ",
    "NODE_ENV", "PORT", "HOST", "DEBUG", "CI", "TMPDIR", "PWD", "OLDPWD",
    "PYTHONPATH", "VIRTUAL_ENV", "LD_LIBRARY_PATH", "USERPROFILE", "TEMP", "TMP",
    "SELECT", "WHERE", "VALUES", "SET", "USERS", "USER", "DATA", "ID", "NAME",
    "TABLE", "INDEX", "DUAL", "NOW", "COUNT", "LIMIT", "ORDER", "GROUP",
}


def scan_repo(base):
    """-> {(kind, name): [ {file, line} ]}"""
    found = defaultdict(list)

    def add(kind, name, text, pos, rel):
        found[(kind, name)].append(
            {"file": rel, "line": text.count("\n", 0, pos) + 1})

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            # splitext reads ".env" as a name with no extension at all, so
            # the file that most literally defines environment was never
            # scanned
            ext = ".env" if fn == ".env" or fn.endswith(".env")                 else os.path.splitext(fn)[1].lower()
            if ext not in SCAN_EXT:
                continue
            full = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(full) > MAX_BYTES:
                    continue
                text = open(full, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            rel = os.path.relpath(full, base)

            for pat in ENV_PATTERNS:
                for m in pat.finditer(text):
                    name = m.group(1)
                    if env_is_meaningful(name) and name.upper() not in UBIQUITOUS:
                        add("env", name, text, m.start(), rel)

            if ext in SHELLISH_EXT:
                locals_ = (set(SHELL_LOCAL.findall(text))
                           - set(ENV_EXPORT.findall(text))) if ext == ".sh" else set()
                for m in ENV_BRACE.finditer(text):
                    name = m.group(1)
                    if name in locals_:
                        continue
                    if env_is_meaningful(name) and name.upper() not in UBIQUITOUS:
                        add("env", name, text, m.start(), rel)
                if ext == ".sh":
                    pats = (ENV_EXPORT,)
                elif ext == ".env":
                    pats = (ENV_DOTENV,)
                else:
                    pats = ()
                for pat in pats:
                    for m in pat.finditer(text):
                        name = m.group(1)
                        if env_is_meaningful(name) and name.upper() not in UBIQUITOUS:
                            add("env", name, text, m.start(), rel)

            for pat in API_TABLE:
                for m in pat.finditer(text):
                    name = m.group(1).strip().lower()
                    if len(name) >= 4 and name.upper() not in UBIQUITOUS:
                        add("table", name, text, m.start(), rel)

            for m in RAW_SQL.finditer(text):
                ls = text.rfind("\n", 0, m.start()) + 1
                le = text.find("\n", m.start())
                line = text[ls: le if le != -1 else len(text)]
                if len(SQL_CTX.findall(line)) >= 2:
                    name = m.group(1).lower()
                    if name.upper() not in UBIQUITOUS:
                        add("table", name, text, m.start(), rel)

            for pat in BUCKET_PATTERNS:
                for m in pat.finditer(text):
                    add("bucket", m.group(1), text, m.start(), rel)

    return found


def build(repos_dir, out_path):
    """Scan every repo under repos_dir; write the shared-identifier document."""
    repos = sorted(d for d in os.listdir(repos_dir)
                   if os.path.isdir(os.path.join(repos_dir, d)))

    # (kind, name) -> repo -> witnesses
    index = defaultdict(lambda: defaultdict(list))
    for r in repos:
        for key, wits in scan_repo(os.path.join(repos_dir, r)).items():
            index[key][r] = wits

    # only identifiers appearing in 2+ repos are coupling
    shared = {k: v for k, v in index.items() if len(v) >= 2}

    idf = {k: math.log(len(repos) / len(v)) for k, v in shared.items()}

    by_kind = defaultdict(list)
    for (kind, name), v in shared.items():
        by_kind[kind].append({
            "kind": kind,
            "identifier": name,
            "repos": sorted(v.keys()),
            "repo_count": len(v),
            "weight": round(idf[(kind, name)], 3),
            "witnesses": [{"repo": r, "file": w[0]["file"], "line": w[0]["line"],
                           "occurrences": len(w)}
                          for r, w in sorted(v.items())],
        })
    for kind in by_kind:
        by_kind[kind].sort(key=lambda x: (-x["weight"], -x["repo_count"], x["identifier"]))

    doc = {
        "repos_scanned": len(repos),
        "shared_identifiers": sum(len(v) for v in by_kind.values()),
        "by_kind": {k: len(v) for k, v in by_kind.items()},
        # no truncation: identifiers are sorted by rarity, so slicing here
        # dropped the *most widely shared* ones first — exactly the ones that
        # matter most for deciding whether a repo is really independent
        "identifiers": {k: v for k, v in by_kind.items()},
    }
    json.dump(doc, open(out_path, "w"), indent=1, sort_keys=True)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()

    doc = build(a.repos_dir, a.out)
    by_kind = doc["identifiers"]

    print(f"repos scanned: {doc['repos_scanned']}")
    print(f"shared identifiers: {doc['shared_identifiers']}  {doc['by_kind']}\n")
    for kind in ("env", "table", "bucket"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        print(f"--- {kind.upper()} ({len(items)} shared) ---")
        for it in items[:12]:
            w = it["witnesses"][0]
            print(f"  {it['identifier']:34} in {it['repo_count']} repos  "
                  f"{it['repos'][:3]}  e.g. {w['repo']}/{w['file']}:{w['line']}")
        print()
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
