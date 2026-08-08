"""The ``search_products`` tool exposed to the search agent.

The tool schema is derived from :class:`partner_mock.models.SearchParams` so it
stays in sync with the real search core, and execution delegates straight to
:func:`partner_mock.search.search_products` — the same function the HTTP/MCP
transports use, so the agent sees the identical envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from partner_mock import search
from partner_mock.models import SearchParams

from . import cards

logger = logging.getLogger("gift_agent")

SEARCH_TOOL_NAME = "search_products"


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
) -> dict[str, Any]:
    """Buy a gift end-to-end: issue a scoped card, authorize, then settle.

    A plain function (not an agent tool) called by the purchase node after the
    gift-selection agent has chosen a product. It performs three sequential
    actions against the Rain API:

    1. **Create a scoped card** for exactly ``amount`` (USD cents), restricted to
       ``merchant_category_code``.
    2. **Authorize** ``amount`` on that card at ``merchant_name`` /
       ``merchant_category_code``.
    3. **Settle** the resulting transaction for the full authorized amount.

    Returns an envelope with the issued card id, the transaction id and the raw
    responses from each step. Raises ``ValueError`` for missing configuration or
    if an id cannot be resolved, and ``httpx.HTTPStatusError`` if Rain returns a
    non-2xx status.
    """
    logger.info(
        "[purchase] run_purchase started (amount=%d cents, merchant=%s, mcc=%s)",
        amount,
        merchant_name,
        merchant_category_code,
    )

    # 1. Create a scoped card sized to (and locked to the merchant category of) the gift.
    logger.info("[purchase] step 1/3: issuing scoped card for %d cents", amount)
    card = cards.issue_scoped_card(
        amount_in_usd_cents=amount,
        allowed_mccs=[merchant_category_code] if merchant_category_code else None,
    )
    card_id = cards.extract_card_id(card)
    if not card_id:
        logger.error("[purchase] step 1/3 failed: no card id in issue response")
        raise ValueError("Could not resolve the issued card id from the Rain response.")
    logger.info("[purchase] step 1/3 done: card issued (card_id=%s)", card_id)

    # 2. Authorize the payment against the freshly issued card.
    logger.info("[purchase] step 2/3: authorizing %d cents on card %s", amount, card_id)
    authorization = cards.authorize_transaction(
        card_id=card_id,
        amount=amount,
        merchant_name=merchant_name,
        merchant_category_code=merchant_category_code,
        currency=currency,
    )
    transaction_id = cards.extract_transaction_id(authorization)
    if not transaction_id:
        logger.error("[purchase] step 2/3 failed: no transaction id in authorize response")
        raise ValueError("Could not resolve the transaction id from the authorize response.")
    logger.info(
        "[purchase] step 2/3 done: authorized (transaction_id=%s)", transaction_id
    )

    # 3. Settle the payment for the full authorized amount.
    logger.info(
        "[purchase] step 3/3: settling transaction %s for %d cents", transaction_id, amount
    )
    settlement = cards.settle_transaction(transaction_id=transaction_id, amount=amount)
    logger.info("[purchase] step 3/3 done: transaction %s settled", transaction_id)

    logger.info(
        "[purchase] run_purchase finished: settled (card_id=%s, transaction_id=%s)",
        card_id,
        transaction_id,
    )
    return {
        "status": "settled",
        "card_id": card_id,
        "transaction_id": transaction_id,
        "card": card,
        "authorization": authorization,
        "settlement": settlement,
    }
