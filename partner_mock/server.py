"""Partner marketplace server: one search core, two transports on the same domain.

Running ``python -m partner_mock.server`` starts a single ASGI app (uvicorn) that serves:

* the MCP endpoint at ``/mcp`` (Streamable HTTP) with the search tools, and
* a plain REST API under ``/api/...`` for the demo front-end / any HTTP client.

Both delegate to :mod:`partner_mock.search`, so they return an identical envelope.
"""

from __future__ import annotations

import os
from typing import Any, Optional

try:  # mcp >= 2.0 renamed the high-level server class
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import search
from .models import SearchParams, SortOrder

mcp = _Server(
    name="partner-marketplace",
    instructions=(
        "Search a partner storefront for gift/product candidates. Use `search_products` "
        "with generic commerce filters (free-text q, categories, tags, price range, "
        "exclude_terms, payment_methods, etc.). Call `get_catalog_facets` first to learn "
        "the available categories/tags/merchants, then map your own profile fields onto "
        "these filters. Use `get_product` for full details incl. merchant payment/wallet "
        "info, and `list_merchants` to discover payment options."
    ),
)


# --------------------------------------------------------------------------------------
# MCP tools (thin wrappers over the reusable core)
# --------------------------------------------------------------------------------------
@mcp.tool()
def search_products(
    q: Optional[str] = None,
    categories: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    match_all_tags: bool = False,
    exclude_terms: Optional[list[str]] = None,
    brands: Optional[list[str]] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    merchant_ids: Optional[list[str]] = None,
    payment_methods: Optional[list[str]] = None,
    accepted_tokens: Optional[list[str]] = None,
    chains: Optional[list[str]] = None,
    in_stock: Optional[bool] = True,
    sort: SortOrder = SortOrder.relevance,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    """Search the partner catalog with generic commerce filters.

    All filters are optional and combine with AND. `q` is free-text (relevance-scored
    over name/tags/category/description). `exclude_terms` drops products matching a
    profile "avoid" list. Returns a paginated envelope with facets and embedded
    merchant details (incl. payment method, accepted tokens, wallet address, chain).
    """
    params = SearchParams(
        q=q,
        categories=categories or [],
        tags=tags or [],
        match_all_tags=match_all_tags,
        exclude_terms=exclude_terms or [],
        brands=brands or [],
        min_price=min_price,
        max_price=max_price,
        merchant_ids=merchant_ids or [],
        payment_methods=payment_methods or [],
        accepted_tokens=accepted_tokens or [],
        chains=chains or [],
        in_stock=in_stock,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return search.search_products(params)


@mcp.tool()
def get_product(product_id: str) -> dict[str, Any]:
    """Fetch full details for a single product id, including its merchant."""
    result = search.get_product(product_id)
    if result is None:
        return {"error": "not_found", "product_id": product_id}
    return result


@mcp.tool()
def list_merchants(
    payment_methods: Optional[list[str]] = None,
    accepted_tokens: Optional[list[str]] = None,
    chains: Optional[list[str]] = None,
) -> dict[str, Any]:
    """List storefront merchants, optionally filtered by payment method, token or chain."""
    merchants = search.list_merchants(payment_methods, accepted_tokens, chains)
    return {"merchants": merchants, "count": len(merchants)}


@mcp.tool()
def get_catalog_facets() -> dict[str, Any]:
    """Discover the searchable vocabulary: categories, tags, merchants, price range and
    payment options. Call this to map arbitrary profile fields onto valid filter values."""
    return search.catalog_facets()


# --------------------------------------------------------------------------------------
# HTTP REST API (same app / same domain), delegating to the same core
# --------------------------------------------------------------------------------------
def _csv(request: Request, key: str) -> list[str]:
    """Collect a list param: repeated (?tags=a&tags=b) or comma-separated (?tags=a,b)."""
    values: list[str] = []
    for raw in request.query_params.getlist(key):
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    return values


def _opt_float(request: Request, key: str) -> Optional[float]:
    raw = request.query_params.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{key} must be a number")


def _opt_bool(request: Request, key: str, default: Optional[bool]) -> Optional[bool]:
    raw = request.query_params.get(key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    if lowered in {"any", "all", "null", "none", ""}:
        return None
    raise ValueError(f"{key} must be true, false or any")


@mcp.custom_route("/api/products/search", methods=["GET"])
async def http_search(request: Request) -> JSONResponse:
    try:
        params = SearchParams(
            q=request.query_params.get("q"),
            categories=_csv(request, "categories"),
            tags=_csv(request, "tags"),
            match_all_tags=_opt_bool(request, "match_all_tags", False) or False,
            exclude_terms=_csv(request, "exclude_terms"),
            brands=_csv(request, "brands"),
            min_price=_opt_float(request, "min_price"),
            max_price=_opt_float(request, "max_price"),
            merchant_ids=_csv(request, "merchant_ids"),
            payment_methods=_csv(request, "payment_methods"),
            accepted_tokens=_csv(request, "accepted_tokens"),
            chains=_csv(request, "chains"),
            in_stock=_opt_bool(request, "in_stock", True),
            sort=request.query_params.get("sort", SortOrder.relevance.value),
            page=int(request.query_params.get("page", 1)),
            page_size=int(request.query_params.get("page_size", 10)),
        )
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)
    return JSONResponse(search.search_products(params))


@mcp.custom_route("/api/products/{product_id}", methods=["GET"])
async def http_get_product(request: Request) -> JSONResponse:
    product_id = request.path_params["product_id"]
    result = search.get_product(product_id)
    if result is None:
        return JSONResponse(
            {"error": "not_found", "product_id": product_id}, status_code=404
        )
    return JSONResponse(result)


@mcp.custom_route("/api/merchants", methods=["GET"])
async def http_merchants(request: Request) -> JSONResponse:
    merchants = search.list_merchants(
        _csv(request, "payment_methods"),
        _csv(request, "accepted_tokens"),
        _csv(request, "chains"),
    )
    return JSONResponse({"merchants": merchants, "count": len(merchants)})


@mcp.custom_route("/api/catalog/facets", methods=["GET"])
async def http_facets(request: Request) -> JSONResponse:
    return JSONResponse(search.catalog_facets())


@mcp.custom_route("/healthz", methods=["GET"])
async def http_health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(mcp.streamable_http_app(), host=host, port=port)


if __name__ == "__main__":
    main()
