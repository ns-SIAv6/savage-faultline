import hashlib


def fingerprint(payload):
    """Stable content hash for dedupe across runs."""
    raw = repr(sorted(payload.items())).encode()
    return hashlib.sha1(raw).hexdigest()
