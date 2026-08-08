"""Storefront data source.

Loads the mock partner database (merchants + products) from JSON once and caches it in
memory, exposing a merchant index for embedding merchant details into product results.

In a real integration this module would be swapped for a client that talks to the
partner site's backend; everything downstream only depends on the shape returned here.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# Default location of the mock DB. Resolved relative to the repo root so it works
# regardless of the current working directory. Override with PARTNER_DB_PATH.
_DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent
    / ".context"
    / "attachments"
    / "khs51I"
    / "partner_web_site_db.json"
)


def db_path() -> Path:
    """Path to the storefront JSON, honouring the ``PARTNER_DB_PATH`` override."""
    override = os.environ.get("PARTNER_DB_PATH")
    return Path(override).expanduser().resolve() if override else _DEFAULT_DB_PATH


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Partner storefront DB not found at {path}. "
            "Set PARTNER_DB_PATH to point at the partner_web_site_db.json file."
        )
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    merchants: list[dict[str, Any]] = raw.get("merchants", [])
    products: list[dict[str, Any]] = raw.get("products", [])
    merchant_index = {m["id"]: m for m in merchants}
    return {
        "merchants": merchants,
        "products": products,
        "merchant_index": merchant_index,
    }


def merchants() -> list[dict[str, Any]]:
    """All merchants in the catalog."""
    return _load()["merchants"]


def products() -> list[dict[str, Any]]:
    """All products in the catalog."""
    return _load()["products"]


def merchant_index() -> dict[str, dict[str, Any]]:
    """Mapping of ``merchant_id -> merchant`` for quick lookups."""
    return _load()["merchant_index"]


def get_merchant(merchant_id: str | None) -> dict[str, Any] | None:
    """Return a merchant by id, or ``None`` if unknown."""
    if merchant_id is None:
        return None
    return merchant_index().get(merchant_id)


def reload() -> None:
    """Clear the in-memory cache (useful in tests when PARTNER_DB_PATH changes)."""
    _load.cache_clear()
