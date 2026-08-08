"""The ``search_products`` tool exposed to the search agent.

The tool schema is derived from :class:`partner_mock.models.SearchParams` so it
stays in sync with the real search core, and execution delegates straight to
:func:`partner_mock.search.search_products` — the same function the HTTP/MCP
transports use, so the agent sees the identical envelope.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from partner_mock import search
from partner_mock.models import SearchParams

from . import cards

logger = logging.getLogger("gift_agent")

SEARCH_TOOL_NAME = "search_products"

# The partner's checkout endpoint. Overridable for staging/prod. Card *issuing*
# stays on our side; the partner *processes and settles* the transaction.
_PARTNER_CHECKOUT_URL = os.environ.get(
    "PARTNER_CHECKOUT_URL", "http://127.0.0.1:8000/api/checkout"
)


def search_tool_schema() -> dict[str, Any]:
    """Anthropic tool definition for the catalog search."""
    schema = SearchParams.model_json_schema()
    return {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Search the partner catalog with generic commerce filters. All filters are "
            "optional and combine with AND. `q` is free-text over name/tags/category/"
            "description. `exclude_terms` drops products matching the profile 'avoid' list. "
            "`max_price` maps a per-gift budget cap. Returns a paginated envelope with facets "
            "and results (each with id, name, price_usd, in_stock, tags, description, merchant)."
        ),
        "input_schema": schema,
    }


def run_search_tool(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a ``search_products`` tool call and return the search envelope."""
    params = SearchParams(**tool_input)
    return search.search_products(params)


def run_purchase(
    *,
    amount: int,
    merchant_name: str,
    merchant_category_code: str,
    currency: str = "USD",
    shipping_address: Optional[dict[str, Any]] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Buy a gift by issuing a scoped card and handing checkout to the partner.

    A plain function (not an agent tool) called by the purchase node after the
    gift-selection agent has chosen a product. The work is split across the two
    sides of the system:

    1. **Our side — issue a scoped card** for exactly ``amount`` (USD cents),
       restricted to ``merchant_category_code``.
    2. **Partner side — checkout** by POSTing the card, amount, merchant and the
       shopper's ``shipping_address`` to the partner API, which processes the
       transaction (authorize) and settles the order.

    Returns an envelope with the issued card id and the partner's checkout result
    (order, authorization, settlement). Raises ``ValueError`` for missing
    configuration or if the card id can't be resolved, and
    ``httpx.HTTPStatusError`` if the card API or the partner returns a non-2xx
    status.
    """
    logger.info(
        "[purchase] run_purchase started (amount=%d cents, merchant=%s, mcc=%s)",
        amount,
        merchant_name,
        merchant_category_code,
    )

    # 1. Our side: create a scoped card sized to (and locked to the MCC of) the gift.
    logger.info("[purchase] step 1/2: issuing scoped card for %d cents", amount)
    card = cards.issue_scoped_card(
        amount_in_usd_cents=amount,
        allowed_mccs=[merchant_category_code] if merchant_category_code else None,
    )
    card_id = cards.extract_card_id(card)
    if not card_id:
        logger.error("[purchase] step 1/2 failed: no card id in issue response")
        raise ValueError("Could not resolve the issued card id from the Rain response.")
    logger.info("[purchase] step 1/2 done: card issued (card_id=%s)", card_id)

    # 2. Partner side: hand off checkout (transaction processing + settlement).
    checkout_body = {
        "cardId": card_id,
        "amount": amount,
        "currency": currency,
        "merchantName": merchant_name,
        "merchantCategoryCode": merchant_category_code,
        "shippingAddress": shipping_address,
    }
    logger.info(
        "[purchase] step 2/2: POST %s (card=%s, amount=%d cents)",
        _PARTNER_CHECKOUT_URL,
        card_id,
        amount,
    )
    response = httpx.post(_PARTNER_CHECKOUT_URL, json=checkout_body, timeout=timeout)
    logger.info("[purchase] partner checkout response: %d", response.status_code)
    response.raise_for_status()
    try:
        checkout_result = response.json()
    except ValueError:
        checkout_result = {"raw": response.text}

    logger.info(
        "[purchase] run_purchase finished: partner status=%s (card_id=%s)",
        checkout_result.get("status"),
        card_id,
    )
    return {
        "status": checkout_result.get("status", "unknown"),
        "card_id": card_id,
        "card": card,
        "checkout": checkout_result,
    }
