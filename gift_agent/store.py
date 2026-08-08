"""In-memory order store.

Persists the order details returned by the partner checkout, keyed by user id.
The fulfillment node writes here after a successful purchase, and the HTTP API
reads back a user's orders.

Storage is a TinyDB backed by :class:`~tinydb.storages.MemoryStorage`, so orders
live purely in RAM for the lifetime of the process — restarting the server clears
them. Because the graph runs in-process with the API, writes from the fulfillment
node are immediately visible to the ``GET /orders`` endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from tinydb import Query, TinyDB
from tinydb.storages import MemoryStorage

logger = logging.getLogger("gift_agent")

# Purely in RAM. Passing the class (not an instance) lets TinyDB construct it.
_db = TinyDB(storage=MemoryStorage)
_Record = Query()


def save_order(user_id: str, order: dict[str, Any]) -> None:
    """Store one order's details for ``user_id``.

    Each order is stored as its own record so a user can accumulate many orders.
    """
    _db.insert({"user_id": user_id, "order": order})
    logger.info(
        "[store] saved order %s for user %s",
        order.get("order_id", "<no id>"),
        user_id,
    )


def get_orders(user_id: str) -> list[dict[str, Any]]:
    """Return every stored order for ``user_id`` (most-recently inserted last)."""
    return [record["order"] for record in _db.search(_Record.user_id == user_id)]


def clear() -> None:
    """Drop all stored orders. Useful in tests."""
    _db.truncate()
