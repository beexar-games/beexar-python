"""Test bootstrap: import paths and where the shared fixtures live."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "examples"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def conformance_root() -> Path:
    """Locate the shared conformance fixtures.

    Two layouts, because this package is developed in the Beexar monorepo and
    published as a standalone repository. In the published repository the
    fixtures sit at the root as ``conformance/``; in the monorepo they are the
    single copy under ``api/``, shared with the Node, Go and PHP SDKs. The tests
    that ship to operators therefore run unchanged in both places.
    """
    candidates = (
        ROOT / "conformance",  # published repository
        ROOT.parents[1] / "api" / "providers" / "softswiss" / "conformance",  # monorepo
    )
    for candidate in candidates:
        if (candidate / "manifest.json").is_file():
            return candidate
    raise RuntimeError(
        "conformance fixtures not found — looked in {}".format(
            " and ".join(str(c) for c in candidates)
        )
    )
