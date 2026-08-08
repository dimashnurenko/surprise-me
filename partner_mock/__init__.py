"""Mock partner storefront search API.

Exposes a single, generic product-search engine (:func:`partner_mock.search.search_products`)
over both an HTTP REST API and MCP tools. Deliberately decoupled from any user-profile
schema: the caller maps whatever profile fields it has into generic commerce filters.
"""

from .models import SearchParams
from .search import search_products

__all__ = ["SearchParams", "search_products"]
