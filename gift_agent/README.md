# Gift Selection Agent (LangGraph)

A two-node LangGraph graph on top of the partner marketplace search core, plus an
HTTP endpoint to trigger it. Uses the cheapest Claude model (`claude-haiku-4-5`).

```
START ──▶ search ──▶ select ──▶ purchase ──▶ END
```

- **`search`** — runs the Product Search Agent system prompt
  (`prompts/search_agent_system_prompt.md`) with a `search_products` tool. The model
  maps the user's gift profile onto catalog filters and calls the tool (possibly
  several times); we collect the de-duplicated candidate products. The tool schema is
  derived from `partner_mock.models.SearchParams` and execution delegates to
  `partner_mock.search.search_products` — the same core used by the HTTP/MCP transports.
- **`select`** — runs the Gift Selection Agent system prompt
  (`prompts/gift_selection_agent_system_prompt.md`) over those candidates and returns
  the exact JSON decision specified in that prompt. This agent does not call tools.
- **`purchase`** — takes the selection as an order, resolves the chosen product back to
  its search candidate (for price + merchant), and calls `gift_agent.tools.run_purchase`,
  a plain function that buys the gift in three steps: **(1)** issue a scoped card sized to
  the gift, **(2)** authorize the payment, **(3)** settle it. The result (issued card id,
  transaction id, and raw responses) is returned under `purchase`. If nothing was selected
  it buys nothing.

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
