"""extract/forks — shared content across repositories.

A repo can be bound to another without a single import or shared variable:
it can BE the other one, copied. Full relative-path matching is blind to it —
`example-repo` and `example-style` share 3 full paths but 252 path *suffixes*, of
which 162 are byte-identical, because nested layout hides the relationship.

So matching is by path SUFFIX (two segments or more — `index.js` alone is not
evidence), and identity is the SHA-1 of the bytes. Boilerplate (LICENSEs,
READMEs, gitignores) and near-empty files are excluded: identical MIT texts
are not coupling.
"""

import argparse
import hashlib
import json
import os
from collections import defaultdict

DEFAULT_REPOS = "data/repos"
DEFAULT_OUT = "data/forks.json"

SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv",
             "venv", ".next", "vendor", "Pods", ".gradle", "target",
             "coverage", ".idea", "site-packages"}

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
              ".zip", ".tar", ".gz", ".whl", ".jar", ".aar", ".apk", ".so",
              ".dylib", ".dll", ".exe", ".bin", ".woff", ".woff2", ".ttf",
              ".otf", ".mp3", ".mp4", ".mov", ".wav", ".psd", ".sketch",
              ".lock", ".keystore", ".jks", ".pb", ".onnx", ".pt", ".pkl",
              ".h5", ".wasm", ".class", ".pyc"}

# identical boilerplate is not coupling; tiny files cannot be evidence
BOILERPLATE = {"license", "license.md", "license.txt", "copying", "readme",
               "readme.md", "readme.txt", "changelog", "changelog.md",
               "notice", "notice.md", ".gitignore", ".dockerignore",
               ".gitattributes", "authors", "contributors"}
MIN_BYTES = 64
MAX_BYTES = 400_000


def hash_repo(base):
    """-> {relpath: sha1} for every evidentiary file in the repo."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.lower() in BOILERPLATE:
                continue
            if os.path.splitext(fn)[1].lower() in BINARY_EXT:
                continue
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
                if size < MIN_BYTES or size > MAX_BYTES:
                    continue
                with open(full, "rb") as f:
                    digest = hashlib.sha1(f.read()).hexdigest()
            except Exception:
                continue
            out[os.path.relpath(full, base)] = digest
    return out


def suffixes(rel):
    """'a/b/c.py' -> ['a/b/c.py', 'b/c.py']. One segment is not evidence."""
    parts = rel.split("/")
    return ["/".join(parts[i:]) for i in range(len(parts) - 1)]


def build(repos_dir, out_path):
    repos = sorted(d for d in os.listdir(repos_dir)
                   if os.path.isdir(os.path.join(repos_dir, d)))
    hashed = {r: hash_repo(os.path.join(repos_dir, r)) for r in repos}

    # suffix -> [(repo, relpath, sha1)]
    by_suffix = defaultdict(list)
    for r in repos:
        for rel, sha in hashed[r].items():
            for sfx in suffixes(rel):
                by_suffix[sfx].append((r, rel, sha))

    # pair -> evidence. Counts are DISTINCT FILES, not suffix matches — one
    # file yields several suffixes, and counting suffixes inflated a 459-file
    # repo into "847 identical" (184%). The claim is about files.
    pairs = defaultdict(lambda: {"shared_paths": 0,
                                 "a_identical": set(), "b_identical": set(),
                                 "witnesses": []})
    for sfx in sorted(by_suffix):
        entries = by_suffix[sfx]
        repos_here = sorted({e[0] for e in entries})
        if len(repos_here) < 2:
            continue
        for i in range(len(repos_here)):
            for j in range(i + 1, len(repos_here)):
                a, b = repos_here[i], repos_here[j]
                ea = next(e for e in entries if e[0] == a)
                eb = next(e for e in entries if e[0] == b)
                p = pairs[(a, b)]
                p["shared_paths"] += 1
                identical = ea[2] == eb[2]
                if identical:
                    p["a_identical"].add(ea[1])
                    p["b_identical"].add(eb[1])
                p["witnesses"].append({
                    "suffix": sfx, "a_path": ea[1], "b_path": eb[1],
                    "identical": identical,
                })

    out_pairs = []
    for (a, b), p in sorted(pairs.items()):
        # distinct files identical on BOTH sides (the counts can differ if one
        # repo carries the same content twice)
        identical = max(len(p["a_identical"]), len(p["b_identical"]))
        if identical < 1:
            # path-shape overlap without one byte-identical file is conjecture,
            # not evidence — every python repo shares src/pkg/__init__.py
            continue
        smaller = min(len(hashed[a]), len(hashed[b])) or 1
        pct = round(identical / smaller * 100, 1)
        verdict = ("fork" if identical >= 20 and pct >= 25
                   else "shared_content")
        out_pairs.append({
            "a": a, "b": b,
            "shared_paths": p["shared_paths"],
            "identical_files": identical,
            "pct_of_smaller_repo": pct,
            "verdict": verdict,
            "witnesses": sorted(p["witnesses"],
                                key=lambda w: (not w["identical"], w["suffix"]))[:12],
        })
    out_pairs.sort(key=lambda x: (-x["identical_files"], x["a"], x["b"]))

    doc = {
        "repos_scanned": len(repos),
        "files_hashed": sum(len(v) for v in hashed.values()),
        "pairs": out_pairs,
    }
    json.dump(doc, open(out_path, "w"), indent=1, sort_keys=True)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    doc = build(a.repos_dir, a.out)
    print(f"repos scanned: {doc['repos_scanned']}  "
          f"files hashed: {doc['files_hashed']}  pairs: {len(doc['pairs'])}")
    for p in doc["pairs"][:10]:
        print(f"  {p['a']:26} <-> {p['b']:26} {p['identical_files']:4} identical "
              f"({p['pct_of_smaller_repo']}% of smaller)  {p['verdict']}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
