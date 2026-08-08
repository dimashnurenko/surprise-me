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
PURCHASE_TOOL_NAME = "purchase_gift"


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


def purchase_tool_schema() -> dict[str, Any]:
    """Anthropic tool definition for the authorize-then-settle purchase.

    The tool wraps :func:`gift_agent.cards.purchase_transaction`, which performs a
    card authorization immediately followed by a settlement, so a single tool call
    completes the whole charge for the chosen gift.
    """
    return {
        "name": PURCHASE_TOOL_NAME,
        "description": (
            "Charge the user's scoped card for the selected gift by authorizing and "
            "then settling a single transaction in one step. Call this exactly once, "
            "for the product you decided to buy, using its price and merchant. `amount` "
            "is the price in US cents (e.g. $42.99 -> 4299). `merchant_category_code` is "
            "the merchant's MCC. Omit `settle_amount` to settle the full authorized "
            "amount. Only set `decline_reason` to deliberately simulate a declined charge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "The scoped card id to charge (provided in the context).",
                },
                "amount": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Authorization amount in US cents (price_usd * 100).",
                },
                "merchant_name": {
                    "type": "string",
                    "description": "The merchant selling the chosen product.",
                },
                "merchant_category_code": {
                    "type": "string",
                    "description": 'The merchant category code (MCC), e.g. "5941".',
                },
                "currency": {
                    "type": "string",
                    "enum": ["USD"],
                    "default": "USD",
                    "description": 'Merchant currency. Only "USD" is supported.',
                },
                "settle_amount": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Optional settlement amount in US cents; omit to settle the full "
                        "authorized amount."
                    ),
                },
                "decline_reason": {
                    "type": "string",
                    "description": "Optional. Simulate a declined authorization with this reason.",
                },
            },
            "required": ["card_id", "amount", "merchant_name", "merchant_category_code"],
        },
    }


def run_purchase_tool(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a ``purchase_gift`` tool call (authorize + settle) and return the envelope."""
    return cards.purchase_transaction(
        card_id=tool_input["card_id"],
        amount=tool_input["amount"],
        merchant_name=tool_input["merchant_name"],
        merchant_category_code=tool_input["merchant_category_code"],
        currency=tool_input.get("currency", "USD"),
        settle_amount=tool_input.get("settle_amount"),
        decline_reason=tool_input.get("decline_reason"),
    )
