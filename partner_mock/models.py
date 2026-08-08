"""Request/parameter models for the partner search API.

The parameters are intentionally *generic commerce filters* — free text, categories,
tags, a price range, exclusions, merchant/payment filters — and carry no knowledge of
any particular user-profile schema. A caller with a profile that uses arbitrary field
names (``interests`` vs ``likes`` vs ``hobbies``; ``avoid`` vs ``dislikes``;
``per_gift_max_usd`` vs ``budget``) maps those onto these fields itself, so the same
API works across different profiles and, in the real world, across different partner
sites.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SortOrder(str, Enum):
    relevance = "relevance"
    price_asc = "price_asc"
    price_desc = "price_desc"
    name_asc = "name_asc"


class SearchParams(BaseModel):
    """Generic product-search query. All fields optional."""

    q: Optional[str] = Field(
        default=None,
        description="Free-text query, matched against product name, tags, category and description.",
    )
    categories: list[str] = Field(
        default_factory=list, description="Restrict to these product categories (case-insensitive)."
    )
    tags: list[str] = Field(
        default_factory=list, description="Match products carrying these tags."
    )
    match_all_tags: bool = Field(
        default=False, description="Require ALL requested tags instead of ANY."
    )
    exclude_terms: list[str] = Field(
        default_factory=list,
        description=(
            "Drop products matching any of these terms (maps a profile 'avoid' list). "
            "A product is excluded if a term appears in its text, equals a tag, or a tag "
            "is a substring of the term (so 'novelty gag gifts' matches a 'gag gift' tag)."
        ),
    )
    brands: list[str] = Field(
        default_factory=list,
        description="Prefer/limit to these brands, matched against merchant and product names.",
    )
    min_price: Optional[float] = Field(default=None, description="Minimum price in USD.")
    max_price: Optional[float] = Field(
        default=None, description="Maximum price in USD (maps a budget / per-gift cap)."
    )
    merchant_ids: list[str] = Field(
        default_factory=list, description="Restrict to these merchant ids."
    )
    payment_methods: list[str] = Field(
        default_factory=list,
        description="Restrict to merchants supporting any of these payment methods (e.g. crypto, fiat_card).",
    )
    accepted_tokens: list[str] = Field(
        default_factory=list,
        description="Restrict to merchants accepting any of these tokens (e.g. USDC).",
    )
    chains: list[str] = Field(
        default_factory=list,
        description="Restrict to merchants settling on any of these chains (e.g. monad).",
    )
    in_stock: Optional[bool] = Field(
        default=True,
        description="true = only in-stock (default), false = only out-of-stock, null = no filter.",
    )
    sort: SortOrder = Field(default=SortOrder.relevance, description="Result ordering.")
    page: int = Field(default=1, ge=1, description="1-based page number.")
    page_size: int = Field(default=10, ge=1, le=100, description="Results per page.")
