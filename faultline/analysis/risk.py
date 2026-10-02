"""analysis/risk — rank findings by consequence, not by kind.

A flat findings list is noise: on the real ecosystem 25 of 27 entries were
NO_CONSUMERS, which answers a question nobody is asking. The questions with
consequences are: what breaks, what leaks, and what lies.

Consequence order:
  critical — a credential-looking identifier shared across repos, above all
             when no code relationship admits the sharing
  high     — undeclared dependency, unresolved interface, cyclic coupling,
             a full fork (which copy is canonical? drift is guaranteed)
  medium   — declaration drift, ambiguous names, shared non-secret state,
             vendored content
  low      — structural notes (no consumers, isolated repos)

Every risk carries its evidence. A risk model that cannot point is astrology.
"""

import re
from collections import defaultdict

SECRETISH = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|API_?KEY|ACCESS_?KEY|"
    r"CREDENTIAL|AUTH|SESSION|COOKIE)", re.I)

WEIGHT = {"critical": 10, "high": 5, "medium": 2, "low": 1}

# findings pass through with consequence severity; the kind determines the
# question the risk answers
FINDING_SEVERITY = {
    "UNDECLARED_DEPENDENCY": "high",
    "UNRESOLVED_INTERFACE": "high",
    "CYCLE": "high",
    "DECLARATION_MISMATCH": "medium",
    "AMBIGUOUS_NAME": "medium",
    "NO_CONSUMERS": "low",
}


def evaluate(doc):
    """dataset document -> ordered risk register + per-repo exposure."""
    risks = []
    seam_pairs = {(s["from"], s["to"]) for s in doc["seams"]}
    seam_pairs |= {(b, a) for a, b in seam_pairs}

    # ---- shared operational state; credentials are the emergency ----------
    # group by credential, not by pair: GITHUB_TOKEN in eleven repos is ONE
    # risk with a blast list, not ten pairwise entries
    cred_repos = defaultdict(set)
    cred_witnesses = defaultdict(list)
    cred_related = defaultdict(bool)
    for pair in doc["invisible_coupling"]:
        for w in pair["witnesses"]:
            if w["kind"] == "env" and SECRETISH.search(w["identifier"]):
                k = w["identifier"]
                cred_repos[k].update((pair["a"], pair["b"]))
                cred_witnesses[k].append(w)
                cred_related[k] |= (pair["a"], pair["b"]) in seam_pairs
    for ident in sorted(cred_repos):
        repos = sorted(cred_repos[ident])
        risks.append({
            "severity": "critical", "kind": "SHARED_CREDENTIAL",
            "repos": repos,
            "summary": (f"{ident} is read by {len(repos)} repositories "
                        f"({', '.join(repos[:5])}{'…' if len(repos) > 5 else ''})"
                        + ("" if cred_related[ident] else
                           " — none of them declare a relationship admitting it")),
            "consequence": ("rotating or revoking it breaks every listed repo "
                            "silently; archiving any of them strands a live "
                            "credential in it"),
            "witnesses": cred_witnesses[ident][:8],
        })

    for pair in doc["invisible_coupling"]:
        a, b = pair["a"], pair["b"]
        secrets = sorted({w["identifier"] for w in pair["witnesses"]
                          if w["kind"] == "env"
                          and SECRETISH.search(w["identifier"])})
        if secrets:
            continue          # already reported per-credential above
        if pair["count"]:
            risks.append({
                "severity": "medium", "kind": "SHARED_OPERATIONAL_STATE",
                "repos": [a, b],
                "summary": (f"{a} and {b} share {pair['count']} operational "
                            f"identifier(s): {', '.join(pair['identifiers'][:4])}"),
                "consequence": ("schema or config changes on one side reach "
                                "the other with no deploy signal"),
                "witnesses": pair["witnesses"],
            })

    # ---- forks: drift is guaranteed, canonical copy unknown ----------------
    for fp in doc.get("forks", []):
        if fp["verdict"] == "fork":
            risks.append({
                "severity": "high", "kind": "FORK_DRIFT",
                "repos": [fp["a"], fp["b"]],
                "summary": (f"{fp['a']} and {fp['b']} share "
                            f"{fp['identical_files']} byte-identical files "
                            f"({fp['pct_of_smaller_repo']}% of the smaller repo)"),
                "consequence": ("the copies will drift; every future fix lands "
                                "in one and rots in the other, and nobody can "
                                "say which copy is canonical"),
                "witnesses": fp["witnesses"],
            })
        elif fp["identical_files"] >= 2:
            risks.append({
                "severity": "medium", "kind": "VENDORED_CONTENT",
                "repos": [fp["a"], fp["b"]],
                "summary": (f"{fp['a']} and {fp['b']} share "
                            f"{fp['identical_files']} byte-identical files"),
                "consequence": "copied content drifts from its source",
                "witnesses": fp["witnesses"],
            })

    # ---- the checkers' findings, consequence-ranked -------------------------
    for f in doc["findings"]:
        risks.append({
            "severity": FINDING_SEVERITY.get(f["kind"], "low"),
            "kind": f["kind"],
            "repos": sorted({w["repo"] for w in f.get("witnesses", []) if w.get("repo")}),
            "summary": f["summary"],
            "consequence": _consequence_for(f),
            "witnesses": f.get("witnesses", []),
        })

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda r: (order[r["severity"]], r["kind"], r["repos"]))

    exposure = defaultdict(int)
    for r in risks:
        for repo in r["repos"]:
            exposure[repo] += WEIGHT[r["severity"]]

    counts = defaultdict(int)
    for r in risks:
        counts[r["severity"]] += 1

    return {
        "register": risks,
        "counts": dict(sorted(counts.items(), key=lambda x: order[x[0]])),
        "exposure": dict(sorted(exposure.items(), key=lambda x: (-x[1], x[0]))),
    }


def _consequence_for(finding):
    return {
        "UNDECLARED_DEPENDENCY": "the dependency is invisible to installers "
                                 "and auditors; removing the target looks safe",
        "UNRESOLVED_INTERFACE": "the import fails at runtime against the "
                                "target as it actually exists",
        "CYCLE": "neither side can be built, tested, or archived independently",
        "DECLARATION_MISMATCH": "the manifest points at a stale owner; installs "
                                "fetch from the wrong place or fail",
        "AMBIGUOUS_NAME": "resolution depends on tool ordering, not intent",
        "NO_CONSUMERS": "nothing uses it — a candidate for archival, if the "
                        "certificate also holds",
    }.get(finding["kind"], "")
