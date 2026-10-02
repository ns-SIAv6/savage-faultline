"""The zero-configuration entry point.

    faultline analyze ./my-projects     a directory of repos, no network
    faultline analyze owner/repo        one GitHub repository
    faultline analyze someorg           every visible repo of an org or user

The first run must produce something useful in under a minute with no config
file and no token. Auth (GITHUB_TOKEN) is a speed upgrade, never a gate.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from .analysis import narrative, seams
from .extract import forks, operational, structure
from .model import Store
from .sources import RateLimited, SourceError, open_source
from .views import dataset


def _owners_of(manifest: dict) -> dict:
    """repo name -> owning account, from fetch results."""
    out = {}
    for name, meta in manifest.items():
        full = meta.get("full_name", "")
        out[name] = full.split("/")[0] if "/" in full else "local"
    return out


def fetch(target: str, out: Path, store: Store | None = None,
          taken_at: str = "") -> dict:
    """Materialise every repo the target names under `out`. Returns the manifest.

    With a store, a repo whose HEAD has not moved since the recorded fetch is
    not re-downloaded — one small HEAD request instead of a full tarball.
    """
    src = open_source(target)
    refs = src.list_repos()
    if not src.authenticated:
        print("note: no credentials — public repos only, 60 requests/hour",
              file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for ref in refs:
        if ref.is_empty:
            print(f"  skip {ref.name}: empty", file=sys.stderr)
            continue
        if ref.name in manifest:
            print(f"  skip {ref.id}: name collides with "
                  f"{manifest[ref.name]['full_name']}", file=sys.stderr)
            continue
        # incremental: only worth a HEAD request when a previous fetch exists
        if store is not None and hasattr(src, "head_sha"):
            prev = store.known_sha(ref.name)
            if prev:
                try:
                    head = src.head_sha(ref)
                except Exception:
                    head = None
                if head and head == prev and (out / ref.name).exists():
                    row = store.fetch_record(ref.name)
                    manifest[ref.name] = {
                        "full_name": ref.id, "ref": ref.default_branch,
                        "files": row[0], "skipped": 0, "bytes": row[1],
                        "sha": prev, "desc": ref.description,
                        "lang": ref.language}
                    print(f"  {ref.name:34} unchanged ({prev[:7]})",
                          file=sys.stderr)
                    continue
        res = src.fetch(ref, out)
        if store is not None:
            store.upsert_repo(ref.name, ref.id, ref.id.split("/")[0],
                              ref.default_branch, ref.language, ref.description)
            store.record_fetch(ref.name, res.commit_sha, ref.default_branch,
                               res.files, res.bytes, taken_at)
        link = out / ref.name
        if res.path and Path(res.path).resolve() != link.resolve():
            # local sources analyse in place; present them under the same root
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(Path(res.path).resolve(), link)
        manifest[ref.name] = {
            "full_name": ref.id, "ref": ref.default_branch, "files": res.files,
            "skipped": res.skipped, "bytes": res.bytes, "sha": res.commit_sha,
            "desc": ref.description, "lang": ref.language}
        print(f"  {ref.name:34} {res.files:5} files", file=sys.stderr)
    return manifest


def analyze(target: str, workdir: Path, reuse: bool) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    repos_root = workdir / "repos"
    manifest_path = workdir / "all-trees.json"

    today = datetime.date.today().isoformat()
    store = Store(workdir / "faultline.db")
    if reuse and manifest_path.exists() and repos_root.is_dir():
        manifest = json.load(open(manifest_path))
    else:
        manifest = fetch(target, repos_root, store=store, taken_at=today)
        json.dump(manifest, open(manifest_path, "w"), indent=1, sort_keys=True)
    owners = _owners_of(manifest)

    ast_path = workdir / "ast_all.json"
    ast_all = structure.parse_ecosystem(str(repos_root), str(ast_path))

    coupling_path = workdir / "coupling.json"
    operational.build(str(repos_root), str(coupling_path))

    forks_path = workdir / "forks.json"
    forks.build(str(repos_root), str(forks_path))

    result = seams.analyze(str(repos_root), ast_all, owners)
    json.dump(result, open(workdir / "ecosystem.json", "w"), indent=1, sort_keys=True)

    args = argparse.Namespace(
        ast=str(ast_path), repos_dir=str(repos_root),
        owners=str(manifest_path), out=str(workdir / "viz.json"),
        coupling=str(coupling_path), forks=str(forks_path))
    doc = dataset.build(args, owners)

    m = doc["measured"]
    snap = store.snapshot(today, repos=m["repos_total"],
                          files_parsed=sum(r["files"] for r in doc["repos"].values()),
                          seams=m["seam_count"], certificates=m["certificates"],
                          detail={"denied": m["certificate_denied"],
                                  "fork_pairs": m["fork_pairs"],
                                  "risk": m.get("risk_counts", {})})
    for d in doc["certificate_denied"]:
        if "could not run" in d["reason"]:
            store.record_unchecked(snap, d["repo"], "certificate", d["reason"])

    report_dir = workdir / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"{today}.md"
    report_path.write_text(narrative.tell(doc, today))

    print("=" * 68)
    print(f"  repos {m['repos_total']}   seams {m['seam_count']}   "
          f"import sites {m['import_sites']}")
    print(f"  certificates {m['certificates']}   denied {m['certificate_denied']}   "
          f"invisible coupling pairs {m['invisible_coupling_pairs']}")
    print("=" * 68)
    risk = doc.get("risk", {})
    counts = risk.get("counts", {})
    if counts:
        print("  risk: " + "   ".join(f"{k} {v}" for k, v in counts.items()))
    print()
    for r in risk.get("register", [])[:12]:
        print(f"  [{r['severity']:8}] {r['kind']}: {r['summary']}")
    print(f"\nfull dataset: {args.out}")
    print(f"report: {report_path}")
    print("view: python3 -m http.server 8777, then open "
          "web/ecosystem.html (it reads data/viz.json)")
    return doc


def main():
    ap = argparse.ArgumentParser(prog="faultline", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="materialise a source to disk, nothing else")
    f.add_argument("target")
    f.add_argument("--out", default="data/repos")

    a = sub.add_parser("analyze", help="fetch + parse + seams + coupling + dataset")
    a.add_argument("target")
    a.add_argument("--workdir", default="data")
    a.add_argument("--reuse", action="store_true",
                   help="reuse a previous fetch in the workdir")

    args = ap.parse_args()
    try:
        if args.cmd == "fetch":
            manifest = fetch(args.target, Path(args.out))
            print(f"{len(manifest)} repos -> {args.out}")
        elif args.cmd == "analyze":
            analyze(args.target, Path(args.workdir), args.reuse)
    except RateLimited as e:
        print(f"rate limited: {e}", file=sys.stderr)
        sys.exit(2)
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
