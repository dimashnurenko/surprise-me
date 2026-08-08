"""Unit tests for the reusable search core."""

from partner_mock import db
from partner_mock.models import SearchParams, SortOrder
from partner_mock.search import (
    catalog_facets,
    get_product,
    list_merchants,
    search_products,
)


def _ids(response) -> list[str]:
    return [r["id"] for r in response["results"]]


def test_free_text_query_matches_interest():
    resp = search_products(SearchParams(q="coffee", page_size=50))
    ids = _ids(resp)
    assert "p001" in ids and "p002" in ids
    # unrelated products excluded
    assert "p008" not in ids
    # relevance scored and sorted descending
    scores = [r["relevance_score"] for r in resp["results"]]
    assert scores == sorted(scores, reverse=True)


def test_max_price_cap():
    resp = search_products(SearchParams(max_price=20, page_size=50))
    for r in resp["results"]:
        assert r["price_usd"] <= 20
    # splurge items above cap are gone
    assert "p014" not in _ids(resp) and "p015" not in _ids(resp)


def test_exclude_terms_drops_avoid_items():
    resp = search_products(
        SearchParams(
            exclude_terms=["alcohol", "candles", "novelty gag gifts"],
            page_size=50,
        )
    )
    ids = set(_ids(resp))
    for filtered_out in ["p016", "p017", "p018", "p019", "p020"]:
        assert filtered_out not in ids


def test_payment_method_filter():
    resp = search_products(SearchParams(payment_methods=["crypto"], page_size=50))
    for r in resp["results"]:
        assert "crypto" in [m.lower() for m in r["merchant"]["payment_methods"]]
    # a fiat-only merchant's product is excluded
    assert "p003" not in _ids(resp)  # Rapha = fiat_card


def test_accepted_token_and_chain_filter():
    resp = search_products(
        SearchParams(accepted_tokens=["USDC"], chains=["monad"], page_size=50)
    )
    for r in resp["results"]:
        assert r["merchant"]["chain"] == "monad"
        assert "USDC" in r["merchant"]["accepted_tokens"]


def test_tags_any_vs_all():
    any_resp = search_products(SearchParams(tags=["cycling", "coffee"], page_size=50))
    all_resp = search_products(
        SearchParams(tags=["specialty coffee", "gear"], match_all_tags=True, page_size=50)
    )
    assert len(_ids(any_resp)) > 0
    assert _ids(all_resp) == ["p002"]  # only the dripper set has both tags


def test_pagination():
    page1 = search_products(SearchParams(sort=SortOrder.name_asc, page=1, page_size=5))
    page2 = search_products(SearchParams(sort=SortOrder.name_asc, page=2, page_size=5))
    assert page1["pagination"]["total_results"] == 20
    assert page1["pagination"]["total_pages"] == 4
    assert len(page1["results"]) == 5
    assert set(_ids(page1)).isdisjoint(_ids(page2))


def test_sort_orders():
    asc = search_products(SearchParams(sort=SortOrder.price_asc, page_size=50))
    prices = [r["price_usd"] for r in asc["results"]]
    assert prices == sorted(prices)
    desc = search_products(SearchParams(sort=SortOrder.price_desc, page_size=50))
    assert [r["price_usd"] for r in desc["results"]] == sorted(prices, reverse=True)


def test_facets_over_filtered_set():
    resp = search_products(SearchParams(page_size=1))
    facets = resp["facets"]
    # facets reflect the whole filtered set, not just the single returned page item
    assert facets["categories"]["cycling"] == 4
    assert facets["payment_methods"]["crypto"] > 0
    assert facets["price"]["min"] == 12 and facets["price"]["max"] == 145


def test_get_product_and_merchant_embedding():
    product = get_product("p001")
    assert product is not None
    assert product["merchant"]["id"] == "m_bluebottle"
    assert product["url"].endswith("/p/p001")
    assert get_product("does_not_exist") is None


def test_list_merchants_filter():
    crypto = list_merchants(payment_methods=["crypto"])
    assert all("crypto" in m["payment_methods"] for m in crypto)
    assert len(crypto) == 4


def test_catalog_facets_vocabulary():
    facets = catalog_facets()
    assert facets["product_count"] == 20
    assert "cycling" in facets["categories"]
    assert "USDC" not in facets["categories"]
    assert facets["price"]["max"] == 145


def test_db_loaded():
    assert len(db.products()) == 20
    assert len(db.merchants()) == 8
