# Partner Marketplace — mock search API (HTTP + MCP)

A mock **partner storefront search API**, shaped like a real e-commerce site's search
endpoint (Algolia/Shopify style: query echo, pagination, facets, embedded merchant).
It exists so a gift/recommendation agent can search partner catalogs for candidate
products.

The search parameters are **generic commerce filters** — free text, categories, tags,
price range, exclusions, merchant/payment filters. They carry **no knowledge of any
user-profile schema**: whatever profile you have (different field names for interests /
likes / budget / avoid), you map it onto these filters yourself. The same API shape
then works across different profiles and, in the real world, across different partner
sites.

One reusable core, `search_products()`, backs both transports below, so HTTP and MCP
always return the same envelope.

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m partner_mock.server           # serves on http://0.0.0.0:8000
```

Env overrides: `HOST`, `PORT`, and `PARTNER_DB_PATH` (path to the storefront JSON;
defaults to `.context/attachments/khs51I/partner_web_site_db.json`).

Everything is served on one host/port (same domain for the demo):

| Surface | Path |
| --- | --- |
| MCP (Streamable HTTP) | `POST /mcp` |
| REST: search | `GET /api/products/search` |
| REST: product detail | `GET /api/products/{id}` |
| REST: merchants | `GET /api/merchants` |
| REST: catalog facets | `GET /api/catalog/facets` |
| REST: checkout | `POST /api/checkout` |
| Health | `GET /healthz` |

### Checkout

`POST /api/checkout` processes a transaction and settles the order for a scoped
card the buyer platform already issued. It authorizes the card through the payment
processor (`partner_mock.payments`, the Rain simulate API), settles the amount, then
builds an order JSON (including the shipping address) and **logs it** — nothing is
persisted yet. Body:

```jsonc
{
  "cardId": "a75df5f9-aba4-42cd-9471-4f0f726d8275",
  "amount": 500,                       // USD cents
  "currency": "USD",
  "merchantName": "Coffee Shop",
  "merchantCategoryCode": "5814",
  "shippingAddress": { "line1": "221B Baker Street", "city": "New York", "state": "NY", "zip": "10001", "country": "US" }
}
```

## Search parameters

All optional; combine with AND. List params in the REST API accept repeated keys
(`?tags=a&tags=b`) or comma-separated values (`?tags=a,b`).

| Param | Type | Meaning |
| --- | --- | --- |
| `q` | string | Free text, relevance-scored over name/tags/category/description |
| `categories` | list | Restrict to these categories |
| `tags` | list | Match these tags |
| `match_all_tags` | bool | Require ALL tags instead of ANY (default false) |
| `exclude_terms` | list | Drop products matching an "avoid" list (term in text / tag equals / tag substring of term) |
| `brands` | list | Match against merchant + product names |
| `min_price`, `max_price` | number | Price range in USD (e.g. a budget cap) |
| `merchant_ids` | list | Restrict to these merchants |
| `payment_methods` | list | e.g. `crypto`, `fiat_card` |
| `accepted_tokens` | list | e.g. `USDC` |
| `chains` | list | e.g. `monad` |
| `in_stock` | bool | `true` = in stock only (default), `false` = out of stock, `any` = no filter |
| `sort` | enum | `relevance` (default), `price_asc`, `price_desc`, `name_asc` |
| `page`, `page_size` | int | Pagination (default 1 / 10, max page_size 100) |

## Example HTTP calls

```bash
# In-stock coffee under $50
curl 'http://localhost:8000/api/products/search?q=coffee&max_price=50'

# Cycling items, excluding an avoid list
curl 'http://localhost:8000/api/products/search?tags=cycling&exclude_terms=alcohol,candles,novelty%20gag%20gifts'

# Only crypto/USDC merchants on monad
curl 'http://localhost:8000/api/products/search?payment_methods=crypto&accepted_tokens=USDC&chains=monad'

# Discover the searchable vocabulary
curl 'http://localhost:8000/api/catalog/facets'
```

## MCP tools

Exposed at `/mcp`: `search_products`, `get_product`, `list_merchants`,
`get_catalog_facets`. Point an MCP client at the URL, e.g.:

```json
{
  "mcpServers": {
    "partner-marketplace": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Or inspect it: `npx @modelcontextprotocol/inspector` → connect to
`http://localhost:8000/mcp`.

## Tests

```bash
pip install pytest
pytest -q
```
