"""analysis/narrative — the ecosystem's story, derived from the model only.

No domain knowledge and no hardcoded names: every noun in the output is a
repo, identifier, or number taken from the dataset document, and every claim
traces to witnesses the dataset carries. If a check did not run, the story
says so — an omission is a finding too. If this reads correctly on someone
else's four-repo project, it is correct.
"""

from collections import defaultdict


def tell(doc, date):
    """dataset document -> markdown report. `date` is passed in, never read
    from a clock, so the output is a pure function of the model."""
    L = []
    m = doc["measured"]
    repos = sorted(doc["repos"])
    total = m["repos_total"]

    L.append(f"# Faultline report — {date}")
    L.append("")
    L.append(f"{total} repositories were examined. "
             f"{m['repos_with_seams']} are joined by code seams, "
             f"{m['invisible_coupling_pairs']} pairs share operational state, "
             f"and {m.get('fork_pairs', 0)} pairs share byte-identical content. "
             f"{m['certificates']} repositories are certified independent; "
             f"{m['certificate_denied']} could not be certified.")

    # ---- the connected core ------------------------------------------------
    if doc["seams"]:
        L.append("\n## Where the boundaries are crossed")
        by_target = defaultdict(list)
        for s in doc["seams"]:
            by_target[s["to"]].append(s)
        for tgt, ss in sorted(by_target.items(), key=lambda x: -len(x[1])):
            who = ", ".join(f"`{s['from']}`" for s in ss)
            sites = sum(s["sites"] for s in ss)
            L.append(f"- `{tgt}` is depended on by {who} "
                     f"({sites} import site{'s' if sites != 1 else ''}).")
        hub = max(doc["blast_radius"].items(), key=lambda x: x[1]["count"])
        if hub[1]["count"]:
            L.append(f"\nThe largest blast radius belongs to `{hub[0]}`: "
                     f"changing it reaches "
                     f"{', '.join(f'`{r}`' for r in hub[1]['transitive'][:6])}"
                     f"{'…' if hub[1]['count'] > 6 else ''}.")

    # ---- what the import graph cannot see ----------------------------------
    inv = doc.get("invisible_coupling", [])
    if inv:
        L.append("\n## Coupling that no code shows")
        for p in inv[:5]:
            kinds = "/".join(p["kinds"])
            L.append(f"- `{p['a']}` and `{p['b']}` share {p['count']} "
                     f"{kinds} identifier(s), including "
                     f"`{p['identifiers'][0]}`.")
    elif not doc.get("certificates") and not doc.get("certificate_denied"):
        pass
    if not _ran(doc, "operational"):
        L.append("\n*The operational-coupling check did not run, so shared "
                 "databases, env vars and buckets are unknown — and no "
                 "certificate below can claim otherwise.*")

    # ---- forks -------------------------------------------------------------
    forks = doc.get("forks", [])
    real_forks = [f for f in forks if f["verdict"] == "fork"]
    if real_forks:
        L.append("\n## Copies of each other")
        for f in real_forks:
            L.append(f"- `{f['a']}` and `{f['b']}` share "
                     f"{f['identical_files']} byte-identical files "
                     f"({f['pct_of_smaller_repo']}% of the smaller repo). "
                     f"The copies will drift; which one is canonical is "
                     f"already unclear.")
    if not _ran(doc, "content"):
        L.append("\n*The content check did not run, so forks and vendored "
                 "copies are unknown.*")

    # ---- risks, in consequence order ---------------------------------------
    risk = doc.get("risk", {})
    reg = risk.get("register", [])
    act = [r for r in reg if r["severity"] in ("critical", "high")]
    if act:
        L.append("\n## What to act on first")
        for r in act[:8]:
            L.append(f"- **{r['severity']}** — {r['summary']}. "
                     f"{r['consequence'].capitalize()}.")

    # ---- what only the whole can see -----------------------------------------
    em = doc.get("emergent", [])
    if em:
        L.append("\n## What only the whole can see")
        for c in em[:6]:
            L.append(f"- {c['claim']}.")
        if len(em) > 6:
            L.append(f"- …and {len(em) - 6} more emergent claims, each with "
                     f"its evidence in the dataset.")

    # ---- certificates -------------------------------------------------------
    certs = doc.get("certificates", [])
    denied = doc.get("certificate_denied", [])
    L.append("\n## What is safe to archive")
    if certs:
        names = ", ".join(f"`{c['repo']}`" for c in certs)
        L.append(f"Proven independent — no code seam in either direction, no "
                 f"shared operational identifier, no shared content: {names}.")
    else:
        L.append("No repository could be certified independent"
                 + (" — see the checks that did not run, above."
                    if not (_ran(doc, "operational") and _ran(doc, "content"))
                    else "."))
    if denied:
        reasons = defaultdict(int)
        for d in denied:
            key = ("shared operational state" if "operational" in d["reason"]
                   else "shared content" if "identical" in d["reason"]
                   else "incomplete checks" if "could not" in d["reason"]
                   or "did not run" in d["reason"]
                   else "code coupling")
            reasons[key] += 1
        L.append(f"{len(denied)} repos were denied — "
                 + ", ".join(f"{v} for {k}"
                             for k, v in sorted(reasons.items())) + ".")

    # ---- open questions ------------------------------------------------------
    amb = doc.get("ambiguous_names") or {}
    if amb:
        # a name published by two repos that share byte-identical content is
        # explained by the fork, not a question — the copies carry the same
        # directories. Only unexplained ambiguity is worth a reader's time.
        fork_pairs = {frozenset((f["a"], f["b"])) for f in doc.get("forks", [])}
        genuine = {n: c for n, c in amb.items()
                   if not (len(c) == 2 and frozenset(c) in fork_pairs)}
        L.append("\n## Open questions")
        explained = len(amb) - len(genuine)
        if explained:
            L.append(f"{explained} ambiguous name(s) are explained by shared "
                     f"content between fork partners — the copies carry the "
                     f"same directories — and are excluded.")
        for name, cands in sorted(genuine.items())[:8]:
            L.append(f"- `{name}` is published by {len(cands)} repositories "
                     f"({', '.join(f'`{c}`' for c in cands)}); which one an "
                     f"importer means cannot be determined from the code.")
        if len(genuine) > 8:
            L.append(f"- …and {len(genuine) - 8} more genuinely ambiguous names.")
    return "\n".join(L) + "\n"


def _ran(doc, check):
    """Did a check actually execute? Inferred from denial reasons — a check
    that could not run leaves its reason on every repo."""
    needle = {"operational": "operational check could not run",
              "content": "content check could not run"}[check]
    return not any(needle in d["reason"]
                   for d in doc.get("certificate_denied", []))
