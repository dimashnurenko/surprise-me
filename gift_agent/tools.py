"""The ``search_products`` tool exposed to the search agent.

The partner is treated as a black-box third party: we never import its code.
Instead we talk to its MCP server (Streamable HTTP) — discovering the tool's
schema at runtime via ``tools/list`` and executing searches via ``tools/call``.
This keeps us decoupled from the partner's internals and automatically picks up
schema changes they publish.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Callable, Coroutine, Optional, TypeVar

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from . import cards

logger = logging.getLogger("gift_agent")

SEARCH_TOOL_NAME = "search_products"

# The partner's MCP endpoint (Streamable HTTP). Overridable for staging/prod.
# Schema discovery and search execution both go through here, so the agent's
# catalog lookups behave like a real third-party MCP integration.
_PARTNER_MCP_URL = os.environ.get("PARTNER_MCP_URL", "http://127.0.0.1:8000/mcp")

# The partner's checkout endpoint. Overridable for staging/prod. Card *issuing*
# stays on our side; the partner *processes and settles* the transaction.
_PARTNER_CHECKOUT_URL = os.environ.get(
    "PARTNER_CHECKOUT_URL", "http://127.0.0.1:8000/api/checkout"
)

# Cache the discovered tool definition — tools/list rarely changes and we don't
# want a round trip on every agent turn. Only successful discoveries are cached,
# so a transient partner outage can recover on the next call.
_search_tool_schema_cache: Optional[dict[str, Any]] = None

_T = TypeVar("_T")


def _run_async(make_coro: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run an async MCP interaction from sync code (LangGraph nodes are sync).

    The coroutine is executed in a dedicated thread with its own event loop, so
    this works whether or not the caller already has a running loop.
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(make_coro())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            box["error"] = exc

    thread = threading.Thread(target=runner, name="mcp-client")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


async def _discover_search_tool_schema() -> dict[str, Any]:
    """Fetch the ``search_products`` definition from the partner's MCP server.

    Returns an Anthropic tool definition (``name``/``description``/``input_schema``)
    built from the schema the partner publishes over ``tools/list``.
    """
    logger.info("[search] discovering tool schema via MCP %s", _PARTNER_MCP_URL)
    async with streamable_http_client(_PARTNER_MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
    tool = next((t for t in listing.tools if t.name == SEARCH_TOOL_NAME), None)
    if tool is None:
        available = ", ".join(t.name for t in listing.tools) or "<none>"
        raise RuntimeError(
            f"Partner MCP server does not expose a {SEARCH_TOOL_NAME!r} tool "
            f"(available: {available})."
        )
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema,
    }


def search_tool_schema(force_refresh: bool = False) -> dict[str, Any]:
    """Anthropic tool definition for the catalog search, discovered over MCP.

    The schema is fetched from the partner's MCP server (``tools/list``) and cached.
    Pass ``force_refresh=True`` to re-fetch. Raises ``RuntimeError`` if the partner
    MCP server can't be reached or doesn't publish the tool — we deliberately don't
    fall back to a hand-rolled schema, since the partner owns that contract.
    """
    global _search_tool_schema_cache
    if _search_tool_schema_cache is not None and not force_refresh:
        return _search_tool_schema_cache
    try:
        schema = _run_async(_discover_search_tool_schema)
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message
        raise RuntimeError(
            f"Could not discover the {SEARCH_TOOL_NAME!r} tool schema from the partner "
            f"MCP server at {_PARTNER_MCP_URL}. Is the partner server running? "
            f"Underlying error: {exc}"
        ) from exc
    _search_tool_schema_cache = schema
    return schema


def _extract_tool_payload(result: Any) -> dict[str, Any]:
    """Pull the JSON envelope out of an MCP ``CallToolResult``.

    Prefers the structured content (the tool returns a dict), falling back to the
    first text content block parsed as JSON. Raises ``RuntimeError`` if the partner
    reported a tool error.
    """
    if getattr(result, "is_error", False):
        detail = " ".join(
            block.text
            for block in getattr(result, "content", [])
            if getattr(block, "type", None) == "text"
        )
        raise RuntimeError(f"Partner {SEARCH_TOOL_NAME} call failed: {detail or '<no detail>'}")

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured

    for block in getattr(result, "content", []):
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    raise RuntimeError(f"Partner {SEARCH_TOOL_NAME} call returned no usable content.")


async def _call_search_tool(tool_input: dict[str, Any]) -> dict[str, Any]:
    async with streamable_http_client(_PARTNER_MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(SEARCH_TOOL_NAME, tool_input)
    return _extract_tool_payload(result)


def run_search_tool(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a ``search_products`` tool call and return the search envelope.

    Delegates to the partner's MCP server (``tools/call``) rather than calling the
    search core in-process, so the agent's catalog lookups behave like a real
    third-party MCP request. The ``tool_input`` is passed through as-is — it already
    conforms to the schema the partner published via :func:`search_tool_schema`.
    Raises ``RuntimeError`` if the partner is unreachable or reports a tool error.
    """
    logger.info("[search] MCP call %s -> %s (q=%r)", _PARTNER_MCP_URL, SEARCH_TOOL_NAME, tool_input.get("q"))
    try:
        envelope = _run_async(lambda: _call_search_tool(tool_input))
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap transport errors with context
        raise RuntimeError(
            f"Partner MCP search call to {_PARTNER_MCP_URL} failed: {exc}"
        ) from exc
    total = envelope.get("pagination", {}).get("total_results")
    logger.info("[search] partner MCP returned %s total result(s)", total)
    return envelope


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
    logger.info(
        "[purchase] partner checkout response: %d %s",
        response.status_code,
        response.text,
    )
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
