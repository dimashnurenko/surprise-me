"""The reusable product-search core.

:func:`search_products` is a pure function over the in-memory catalog. Both the HTTP
REST endpoint and the MCP tool delegate to it, so they always return the same envelope.
The response envelope mirrors a real storefront/search API (Algolia/Shopify style):
an echo of the normalized query, pagination metadata, facets over the full filtered
set, and the page of results with an embedded merchant object.
"""

from __future__ import annotations

import math
import re
from typing import Any

from . import db
from .models import SearchParams, SortOrder

# Base URL of the (mock) partner storefront, used to build canonical product URLs.
STOREFRONT_BASE_URL = "https://partner.example"

# Relevance weights per field where a query term is found.
_FIELD_WEIGHTS = {"name": 3.0, "tags": 2.0, "category": 2.0, "description": 1.0}
_MAX_TERM_WEIGHT = max(_FIELD_WEIGHTS.values())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _lower_list(values: list[str]) -> list[str]:
    return [v.strip().lower() for v in values if v and v.strip()]


def _product_tags(product: dict[str, Any]) -> list[str]:
    return [str(t).lower() for t in product.get("tags", [])]


def _searchable_text(product: dict[str, Any]) -> str:
    parts = [
        str(product.get("name", "")),
        str(product.get("description", "")),
        str(product.get("category", "")),
        " ".join(str(t) for t in product.get("tags", [])),
    ]
    return " ".join(parts).lower()


def _is_excluded(product: dict[str, Any], terms: list[str]) -> bool:
    """A product is excluded if, for ANY term, the term appears in its searchable text,
    equals one of its tags, or one of its tags is a substring of the term."""
    if not terms:
        return False
    text = _searchable_text(product)
    tags = _product_tags(product)
    for term in terms:
        if term in text:
            return True
        if any(tag == term or tag in term for tag in tags):
            return True
    return False


def _matches_brands(product: dict[str, Any], merchant: dict[str, Any] | None, brands: list[str]) -> bool:
    if not brands:
        return True
    haystack = str(product.get("name", "")).lower()
    if merchant:
        haystack += " " + str(merchant.get("name", "")).lower()
    return any(brand in haystack for brand in brands)


def _relevance(product: dict[str, Any], query_terms: list[str]) -> float:
    """Normalized 0..1 relevance for the query. Returns 0.0 when no term matches."""
    if not query_terms:
        return 1.0
    name = str(product.get("name", "")).lower()
    description = str(product.get("description", "")).lower()
    category = str(product.get("category", "")).lower()
    tags_text = " ".join(_product_tags(product))
    fields = {
        "name": name,
        "tags": tags_text,
        "category": category,
        "description": description,
    }
    raw = 0.0
    for term in query_terms:
        best = 0.0
        for field, weight in _FIELD_WEIGHTS.items():
            if term in fields[field]:
                best = max(best, weight)
        raw += best
    return round(raw / (len(query_terms) * _MAX_TERM_WEIGHT), 3)


def _merchant_matches(merchant: dict[str, Any] | None, params: SearchParams) -> bool:
    payment_methods = _lower_list(params.payment_methods)
    accepted_tokens = _lower_list(params.accepted_tokens)
    chains = _lower_list(params.chains)
    if not (payment_methods or accepted_tokens or chains):
        return True
    if merchant is None:
        return False
    if payment_methods:
        m_methods = {str(x).lower() for x in merchant.get("payment_methods", [])}
        if not (m_methods & set(payment_methods)):
            return False
    if accepted_tokens:
        m_tokens = {str(x).lower() for x in merchant.get("accepted_tokens", [])}
        if not (m_tokens & set(accepted_tokens)):
            return False
    if chains:
        if str(merchant.get("chain") or "").lower() not in chains:
            return False
    return True


def _public_merchant(merchant: dict[str, Any] | None) -> dict[str, Any] | None:
    if merchant is None:
        return None
    return {
        "id": merchant.get("id"),
        "name": merchant.get("name"),
        "payment_methods": merchant.get("payment_methods", []),
        "accepted_tokens": merchant.get("accepted_tokens", []),
        "wallet_address": merchant.get("wallet_address"),
        "chain": merchant.get("chain"),
    }


def _serialize(product: dict[str, Any], merchant: dict[str, Any] | None, score: float) -> dict[str, Any]:
    return {
        "id": product.get("id"),
        "name": product.get("name"),
        "category": product.get("category"),
        "tags": product.get("tags", []),
        "price_usd": product.get("price_usd"),
        "currency": "USD",
        "in_stock": product.get("in_stock"),
        "description": product.get("description"),
        "relevance_score": score,
        "url": f"{STOREFRONT_BASE_URL}/p/{product.get('id')}",
        "merchant": _public_merchant(merchant),
    }


def _count(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _facets(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Facets computed over the full filtered set (before pagination)."""
    categories: list[str] = []
    tags: list[str] = []
    merchant_names: list[str] = []
    payment_methods: list[str] = []
    prices: list[float] = []
    for product, merchant, _score in hits:  # type: ignore[misc]
        categories.append(str(product.get("category", "")))
        tags.extend(str(t) for t in product.get("tags", []))
        if merchant:
            merchant_names.append(str(merchant.get("name", "")))
            payment_methods.extend(str(m) for m in merchant.get("payment_methods", []))
        if product.get("price_usd") is not None:
            prices.append(float(product["price_usd"]))
    return {
        "categories": _count(categories),
        "tags": _count(tags),
        "merchants": _count(merchant_names),
        "payment_methods": _count(payment_methods),
        "price": {"min": min(prices), "max": max(prices)} if prices else {"min": None, "max": None},
    }


def _sort_key(sort: SortOrder):
    if sort is SortOrder.price_asc:
        return lambda item: (item[0].get("price_usd", 0),)
    if sort is SortOrder.price_desc:
        return lambda item: (-item[0].get("price_usd", 0),)
    if sort is SortOrder.name_asc:
        return lambda item: (str(item[0].get("name", "")).lower(),)
    # relevance: score desc, then price asc, then name
    return lambda item: (-item[2], item[0].get("price_usd", 0), str(item[0].get("name", "")).lower())


def search_products(params: SearchParams) -> dict[str, Any]:
    """Run a generic product search and return a real-search-API-style envelope."""
    query_terms = _tokens(params.q) if params.q else []
    categories = _lower_list(params.categories)
    want_tags = _lower_list(params.tags)
    exclude_terms = _lower_list(params.exclude_terms)
    brands = _lower_list(params.brands)
    merchant_ids = set(params.merchant_ids)

    hits: list[tuple[dict[str, Any], dict[str, Any] | None, float]] = []
    for product in db.products():
        merchant = db.get_merchant(product.get("merchant_id"))

        if params.in_stock is not None and bool(product.get("in_stock")) != params.in_stock:
            continue
        if categories and str(product.get("category", "")).lower() not in categories:
            continue
        if want_tags:
            p_tags = set(_product_tags(product))
            if params.match_all_tags:
                if not set(want_tags).issubset(p_tags):
                    continue
            elif not (set(want_tags) & p_tags):
                continue
        if _is_excluded(product, exclude_terms):
            continue
        if not _matches_brands(product, merchant, brands):
            continue
        price = product.get("price_usd")
        if params.min_price is not None and (price is None or price < params.min_price):
            continue
        if params.max_price is not None and (price is None or price > params.max_price):
            continue
        if merchant_ids and product.get("merchant_id") not in merchant_ids:
            continue
        if not _merchant_matches(merchant, params):
            continue

        score = _relevance(product, query_terms)
        if query_terms and score == 0.0:
            continue  # free-text query with no match acts as a filter

        hits.append((product, merchant, score))

    hits.sort(key=_sort_key(params.sort))

    total = len(hits)
    total_pages = math.ceil(total / params.page_size) if total else 0
    start = (params.page - 1) * params.page_size
    page_hits = hits[start : start + params.page_size]

    return {
        "query": params.model_dump(mode="json"),
        "pagination": {
            "page": params.page,
            "page_size": params.page_size,
            "total_results": total,
            "total_pages": total_pages,
        },
        "facets": _facets(hits),
        "results": [_serialize(p, m, s) for (p, m, s) in page_hits],
    }


def get_product(product_id: str) -> dict[str, Any] | None:
    """Full details for a single product, with the embedded merchant."""
    for product in db.products():
        if product.get("id") == product_id:
            merchant = db.get_merchant(product.get("merchant_id"))
            return _serialize(product, merchant, 1.0)
    return None


def list_merchants(
    payment_methods: list[str] | None = None,
    accepted_tokens: list[str] | None = None,
    chains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List merchants, optionally filtered by payment method / token / chain."""
    pm = _lower_list(payment_methods or [])
    tokens = _lower_list(accepted_tokens or [])
    ch = _lower_list(chains or [])
    out = []
    for merchant in db.merchants():
        if pm and not ({str(x).lower() for x in merchant.get("payment_methods", [])} & set(pm)):
            continue
        if tokens and not ({str(x).lower() for x in merchant.get("accepted_tokens", [])} & set(tokens)):
            continue
        if ch and str(merchant.get("chain") or "").lower() not in ch:
            continue
        out.append(_public_merchant(merchant))
    return out


def catalog_facets() -> dict[str, Any]:
    """The available search vocabulary: categories, tags, merchants, price range and
    payment options. Lets a caller discover valid filter values instead of hardcoding
    them — useful when mapping arbitrary profile fields onto this API."""
    hits = [(p, db.get_merchant(p.get("merchant_id")), 1.0) for p in db.products()]
    facets = _facets(hits)
    return {
        "categories": list(facets["categories"].keys()),
        "tags": list(facets["tags"].keys()),
        "merchants": list_merchants(),
        "payment_methods": list(facets["payment_methods"].keys()),
        "price": facets["price"],
        "product_count": len(hits),
    }
