"""The ``search_products`` tool exposed to the search agent.

The tool schema is derived from :class:`partner_mock.models.SearchParams` so it
stays in sync with the real search core, and execution delegates straight to
:func:`partner_mock.search.search_products` — the same function the HTTP/MCP
transports use, so the agent sees the identical envelope.
"""

from __future__ import annotations

from typing import Any

from partner_mock import search
from partner_mock.models import SearchParams

from . import cards

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
    # 1. Create a scoped card sized to (and locked to the merchant category of) the gift.
    card = cards.issue_scoped_card(
        amount_in_usd_cents=amount,
        allowed_mccs=[merchant_category_code] if merchant_category_code else None,
    )
    card_id = cards.extract_card_id(card)
    if not card_id:
        raise ValueError("Could not resolve the issued card id from the Rain response.")

    # 2. Authorize the payment against the freshly issued card.
    authorization = cards.authorize_transaction(
        card_id=card_id,
        amount=amount,
        merchant_name=merchant_name,
        merchant_category_code=merchant_category_code,
        currency=currency,
    )
    transaction_id = cards.extract_transaction_id(authorization)
    if not transaction_id:
        raise ValueError("Could not resolve the transaction id from the authorize response.")

    # 3. Settle the payment for the full authorized amount.
    settlement = cards.settle_transaction(transaction_id=transaction_id, amount=amount)

    return {
        "status": "settled",
        "card_id": card_id,
        "transaction_id": transaction_id,
        "card": card,
        "authorization": authorization,
        "settlement": settlement,
    }
