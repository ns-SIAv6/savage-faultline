"""Savage Faultline — cross-repository coupling analysis.

The package is layered so that each layer depends only on the one below:

    sources/   fetch repositories from somewhere (GitHub, a local path, ...)
    model/     persist and query what was fetched, incrementally
    extract/   pull structure and coupling out of the fetched files
    analysis/  seams, checks, risk, narrative
    views/     how it is shown (2D instrument, 3D cosmos, markdown report)

Nothing in `analysis` may reach for a network or a filesystem directly; it works
off the model. That is what makes the tool portable — it can run against a
GitHub org, a single local directory, or a synthetic fixture with no changes.
"""

__version__ = "0.1.0"
