"""analysis/emergent — properties of the whole that no part can tell you.

The definition is inherited and kept strict:

    A claim is emergent iff it cannot be established by reading any single
    repository.

So every claim here is computed from at least two repos, and each carries a
`because` line stating exactly why no single repo could have said it. If a
detector can fire from one repo alone, it does not belong in this module.

Detectors:
  topology       — the shape of the whole seam graph (hubs, layers, cycles)
  fanout         — a credential read by N repos is a fact about the N, not any
  convergence    — two repos independently built on the same rare stack
  surface        — what a repo offers the ecosystem vs what the ecosystem reads
  constellation  — content sharing that spans 3+ repos is a family, not a pair
"""

from collections import defaultdict


def detect(doc, ast_all=None):
    out = []
    out += _topology(doc)
    out += _fanout(doc)
    out += _convergence(doc)
    out += _surface(doc, ast_all)
    out += _constellations(doc)
    return out


def _claim(kind, claim, evidence, because):
    return {"kind": kind, "claim": claim, "evidence": evidence,
            "because": because}


# ---------------------------------------------------------------- topology

def _topology(doc):
    seams = doc.get("seams", [])
    repos = doc.get("repos", {})
    if not seams:
        n = len(repos)
        return [_claim("topology",
                       f"the ecosystem is an archipelago: {n} repositories, "
                       f"no code seam between any two of them",
                       {"repos": len(repos), "seams": 0},
                       "no single repo can know that nothing imports it — "
                       "only the whole graph carries that")] if n else []

    indeg = defaultdict(int)
    adj = defaultdict(set)
    for s in seams:
        indeg[s["to"]] += 1
        adj[s["from"]].add(s["to"])
    claims = []

    hubs = sorted(indeg.items(), key=lambda x: -x[1])
    if hubs and hubs[0][1] >= 2 and hubs[0][1] >= len(seams) / 2:
        claims.append(_claim(
            "topology",
            f"hub-and-spoke: `{hubs[0][0]}` absorbs {hubs[0][1]} of "
            f"{len(seams)} seam(s) — the ecosystem reorganises around it",
            {"hub": hubs[0][0], "in_degree": hubs[0][1], "seams": len(seams)},
            "the hub repo cannot see its consumers from inside; each consumer "
            "sees only its own edge"))

    findings = doc.get("findings", [])
    cycles = [f for f in findings if f["kind"] == "CYCLE"]
    if cycles:
        claims.append(_claim(
            "topology",
            f"{len(cycles)} cyclic cluster(s): "
            + "; ".join(c["summary"] for c in cycles[:3]),
            {"cycles": len(cycles)},
            "a cycle has no member that can observe it — each repo sees one "
            "edge of the loop"))

    # strata of the seam DAG, cycles condensed away
    depth = _dag_depth(adj)
    if depth >= 3:
        claims.append(_claim(
            "topology",
            f"the ecosystem is layered {depth} strata deep — changes at the "
            f"foundation propagate {depth - 1} hops",
            {"depth": depth},
            "no repo can count the strata below it; depth is a whole-graph "
            "measurement"))
    return claims


def _dag_depth(adj):
    """Longest path over the condensation (each SCC collapses to one node).
    Iterative throughout: no recursion limit, no nondeterminism."""
    from . import seams as _seams
    sccs = _seams.strongly_connected(adj)
    node2c = {}
    for i, scc in enumerate(sccs):
        for n in scc:
            node2c[n] = i
    nodes = set(adj) | {n for vs in adj.values() for n in vs}
    nxt = len(sccs)
    for n in sorted(nodes):
        if n not in node2c:
            node2c[n] = nxt
            nxt += 1
    cadj = defaultdict(set)
    for a, bs in adj.items():
        for b in bs:
            if node2c[a] != node2c[b]:
                cadj[node2c[a]].add(node2c[b])
    # longest path in a DAG by Kahn-order relaxation, deterministic order
    indeg = defaultdict(int)
    for u in cadj:
        for v in cadj[u]:
            indeg[v] += 1
    order = sorted({u for u in cadj} | set(node2c.values()))
    queue = sorted(u for u in order if indeg[u] == 0)
    dist = {u: 1 for u in order}
    while queue:
        u = queue.pop(0)
        for v in sorted(cadj.get(u, ())) :
            if dist[u] + 1 > dist.get(v, 1):
                dist[v] = dist[u] + 1
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return max(dist.values(), default=0)


# ---------------------------------------------------------------- fanout

def _fanout(doc):
    claims = []
    for r in (doc.get("risk", {}).get("register", [])):
        if r["kind"] != "SHARED_CREDENTIAL":
            continue
        ident = r["summary"].split(" is read by")[0]
        claims.append(_claim(
            "fanout",
            f"`{ident}` fans out to {len(r['repos'])} repositories — one "
            f"rotation event reaches all of them at once",
            {"identifier": ident, "repos": r["repos"]},
            "each repo sees only its own read of the credential; the fan-out "
            "shape exists nowhere but across the set"))
    return claims


# ------------------------------------------------------------- convergence

def _convergence(doc):
    claims = []
    for p in doc.get("similar_pairs", []):
        claims.append(_claim(
            "convergence",
            f"`{p['a']}` and `{p['b']}` independently built on the same rare "
            f"stack ({', '.join(p['shared_rare'][:3])}) yet share nothing — "
            f"hypothesis, not finding",
            {"shared_rare": p["shared_rare"], "score": p["score"]},
            "each repo's manifest is visible from inside; the overlap of two "
            "manifests is not"))
    return claims


# ---------------------------------------------------------------- surface

def _surface(doc, ast_all):
    """Offered vs read: the repo's public symbols against the names that
    actually cross its boundary. Skipped entirely without parse data —
    an absent detector is stated, not faked."""
    if not ast_all:
        return []
    consumed = defaultdict(set)
    for s in doc.get("seams", []):
        for c in s.get("symbols", []):
            consumed[s["to"]].add(c["symbol"])
    claims = []
    for repo in sorted(consumed):
        if not consumed[repo]:
            continue
        offered = sorted({s["name"]
                          for m in ast_all.get(repo, []) if not m.get("error")
                          for s in m.get("symbols", [])
                          if not s["name"].startswith("_")})
        if not offered:
            continue
        got = sorted(consumed[repo] & set(offered))
        claims.append(_claim(
            "surface",
            f"the ecosystem reads {len(got)} of {len(offered)} public "
            f"symbol(s) `{repo}` offers"
            + (f" — `{repo}`'s unread surface is the rest" if len(got) < len(offered) else ""),
            {"repo": repo, "read": got, "offered": offered},
            "a repo cannot know which of its exports are read elsewhere; "
            "the consumers cannot know what else it offers"))
    return claims


# ------------------------------------------------------------ constellations

def _constellations(doc):
    pairs = doc.get("forks", [])
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        a, b = find(p["a"]), find(p["b"])
        if a != b:
            parent[max(a, b)] = min(a, b)
    comps = defaultdict(list)
    for p in pairs:
        for r in (p["a"], p["b"]):
            root = find(r)
            if r not in comps[root]:
                comps[root].append(r)
    claims = []
    for members in sorted((sorted(v) for v in comps.values()), key=lambda m: -len(m)):
        if len(members) < 3:
            continue
        claims.append(_claim(
            "constellation",
            f"{len(members)} repositories share byte-identical content: "
            + ", ".join(f"`{m}`" for m in members)
            + " — a family, not a pair",
            {"members": members},
            "each pair relationship is visible pairwise; the connected family "
            "only exists when all pairs are known at once"))
    return claims
