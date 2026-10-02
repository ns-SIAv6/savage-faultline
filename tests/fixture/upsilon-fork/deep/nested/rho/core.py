def normalise(records):
    """Lowercase keys, drop empties, sort by id."""
    out = []
    for r in records:
        cleaned = {k.lower(): v for k, v in r.items() if v is not None}
        if cleaned:
            out.append(cleaned)
    return sorted(out, key=lambda r: r.get("id", ""))
