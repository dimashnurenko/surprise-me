# Gift Selection Agent (LangGraph)

A two-node LangGraph graph on top of the partner marketplace search core, plus an
HTTP endpoint to trigger it. Uses the cheapest Claude model (`claude-haiku-4-5`).

```
START ──▶ search ──▶ select ──▶ purchase ──▶ END
```

- **`search`** — runs the Product Search Agent system prompt
  (`prompts/search_agent_system_prompt.md`) with a `search_products` tool. The model
  maps the user's gift profile onto catalog filters and calls the tool (possibly
  several times); we collect the de-duplicated candidate products. The partner is
  treated as a **black-box third party**: we don't import its code. The tool schema is
  **discovered at runtime** from the partner's MCP server (`tools/list`) and cached, and
  execution goes through the same MCP server (`tools/call`). Configurable via
  `PARTNER_MCP_URL` (default `http://127.0.0.1:8000/mcp`), so the partner server
  (`python -m partner_mock.server`) must be running for the search step.
- **`select`** — runs the Gift Selection Agent system prompt
  (`prompts/gift_selection_agent_system_prompt.md`) over those candidates and returns
  the exact JSON decision specified in that prompt. This agent does not call tools.
- **`purchase`** — takes the selection as an order, resolves the chosen product back to
  its search candidate (for price + merchant) and reads the shopper's shipping address
  from the profile (`delivery.shipping_address`), then calls `gift_agent.tools.run_purchase`.
  The work is **split across the two sides**: our side **(1)** issues a scoped card sized to
  the gift, then the **partner** checkout API (`POST /api/checkout`) **(2)** processes the
  transaction (authorize) and **(3)** settles the order, building + logging an order JSON.
  The result (issued card id + the partner's checkout response) is returned under `purchase`.
  If nothing was selected it buys nothing.

  The partner endpoint is configurable via `PARTNER_CHECKOUT_URL` (default
  `http://127.0.0.1:8000/api/checkout`), so the partner server (`python -m partner_mock.server`)
  must be running for the purchase step to reach it.

## Setup

```bash
pip install -r requirements.txt          # langgraph, anthropic, fastapi, ...
export ANTHROPIC_API_KEY=...             # or put it in .env (auto-loaded)
```

The search core reads its catalog from `PARTNER_DB_PATH`; if unset, the agent falls
back to the committed `partner_mock/partner_web_site_db.json`.

## Run as an API

```bash
python -m gift_agent.api          # serves on 0.0.0.0:8010 (AGENT_PORT to override)
# or: uvicorn gift_agent.api:app --port 8010
```

```bash
# Uses the default user_profile.json:
curl -s -X POST http://127.0.0.1:8010/agent/gift \
  -H 'Content-Type: application/json' -d '{}'

# Or pass a profile + date explicitly:
curl -s -X POST http://127.0.0.1:8010/agent/gift \
  -H 'Content-Type: application/json' \
  -d '{"user_profile": { ...gift_profile... }, "current_date": "2026-08-08"}'
```

Response:

```jsonc
{
  "selection":   { ...gift-selection-agent JSON (status/selected_product/...)... },
  "candidates":  [ ...products the search step gathered... ],
  "search_calls":[ { "input": {...}, "total": N }, ... ]   // what the search agent queried
}
```

## Run in code

```python
import json
from gift_agent import run_agent

profile = json.load(open("user_profile.json"))   # the WHOLE object
result = run_agent(profile, "2026-08-08")
print(result["selection"])
```

## Notes

- The `user_profile` passed to the agent should be the **whole `user_profile.json`
  object** — both prompts reference `gift_profile.*` (interests, avoid, notes, ...) **and**
  `budget.*` (`per_gift_max_usd`, `monthly_cap_usd`, `frequency`), so those keys must be
  present at the top level. The API's default loader does this for you.
- Budget adherence depends on the model. Haiku sometimes omits `max_price` in the search
  step, so candidates over `per_gift_max_usd` can reach the selection step; the selection
  prompt is the backstop that discards them. For stricter guarantees, use a larger model
  or add a deterministic post-filter after the search node.
```
