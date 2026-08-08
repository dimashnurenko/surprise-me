"""In-memory user-profile store.

Persists a single user profile keyed by user id, mirroring
:mod:`gift_agent.store` (the order store). Where orders accumulate many records
per user, a profile is one-per-user, so writes use ``upsert`` and there is a
single-record getter.

Storage is a TinyDB backed by :class:`~tinydb.storages.MemoryStorage`, so
profiles live purely in RAM for the lifetime of the process — restarting the
server clears them. Because the graph runs in-process with the API, a profile
written via ``PUT /profile`` is immediately visible to the next agent run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB
from tinydb.storages import MemoryStorage

logger = logging.getLogger("gift_agent")

# Purely in RAM. Passing the class (not an instance) lets TinyDB construct it.
_db = TinyDB(storage=MemoryStorage)
_Record = Query()

# Seed profile used when the store has nothing for a user yet (first run before any
# PUT /profile). Lives at the repo root alongside the packages.
_DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "user_profile.json"


def save_profile(user_id: str, profile: dict[str, Any]) -> None:
    """Store (overwrite) the profile for ``user_id``.

    Uses ``upsert`` so each user has exactly one profile record — a later write
    replaces the earlier one.
    """
    _db.upsert({"user_id": user_id, "profile": profile}, _Record.user_id == user_id)
    logger.debug("[profile_store] saved profile for user %s", user_id)


def get_profile(user_id: str) -> dict[str, Any] | None:
    """Return the stored profile for ``user_id``, or ``None`` if none is stored."""
    records = _db.search(_Record.user_id == user_id)
    return records[0]["profile"] if records else None


def default_profile() -> dict[str, Any]:
    """The seed profile from ``user_profile.json`` (used when nothing is stored)."""
    return json.loads(_DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))


def load_profile(user_id: str | None) -> dict[str, Any]:
    """The effective profile for ``user_id``: the stored one, else the seed default.

    This is the single source of truth for the profile the agent runs against, so
    that editing the profile via ``PUT /profile`` changes the agent's results.
    """
    if user_id:
        stored = get_profile(user_id)
        if stored is not None:
            return stored
    return default_profile()


def clear() -> None:
    """Drop all stored profiles. Useful in tests."""
    _db.truncate()
