# Surprise Me — Gift Selection Agent

A LangGraph agent that picks and purchases a gift within a user's budget, exposed
over HTTP. It talks to a **partner mock** (product search + checkout) and to the
Rain card APIs. An interactive **demo page** is served from the agent so you can
drive the whole flow from a browser.

Two services:

| Service        | Command                     | Port  | Purpose                                   |
| -------------- | --------------------------- | ----- | ----------------------------------------- |
| `gift_agent`   | `python -m gift_agent.api`  | 8010  | The agent HTTP API + demo page at `/`     |
| `partner_mock` | `python -m partner_mock.server` | 8000 | Product search (MCP) and checkout backend |

## Prerequisites

- **Docker + Docker Compose** (recommended), or **Python 3.10+** for a local run.
- An **Anthropic API key** (the agent uses Claude to choose a gift).
- Rain card sandbox credentials (`API_KEY`, `TEAM_ID`, `COLLATERAL_CONTRACT_ID`).

## 1. Configure environment

Copy the template and fill in your values:

```bash
cp .env.example .env
# then edit .env
```

Required variables:

| Variable                       | Description                                              |
| ------------------------------ | -------------------------------------------------------- |
| `ANTHROPIC_API_KEY`            | Claude API key used by the agent.                        |
| `USER_ID`                      | The user the agent acts for; orders/profile are keyed by it. |
| `API_KEY`                      | Rain card API key.                                       |
| `TEAM_ID`                      | Rain team id.                                            |
| `COLLATERAL_CONTRACT_ID`       | Rain collateral contract id for card funding.            |
| `SESSION_ID_CRYPTO_PUBLIC_KEY` | Public key used to encrypt the session id.               |

Optional: `LANGSMITH_*` for tracing.

## 2. Run with Docker Compose (recommended)

```bash
docker compose up --build
```

This builds one image, starts `partner_mock` (`:8000`) and waits for it to be
healthy, then starts `gift_agent` (`:8010`). Inside the compose network the agent
reaches the partner automatically (`PARTNER_MCP_URL` / `PARTNER_CHECKOUT_URL` are
set for you).

## 3. Run without Docker

In two terminals (both need the `.env` values exported, e.g. `set -a; source .env; set +a`):

```bash
pip install -r requirements.txt

# terminal 1 — partner mock
python -m partner_mock.server            # :8000

# terminal 2 — gift agent (point it at the local partner)
export PARTNER_MCP_URL=http://localhost:8000/mcp
export PARTNER_CHECKOUT_URL=http://localhost:8000/api/checkout
python -m gift_agent.api                  # :8010
```

## 4. Open the demo

Go to **http://localhost:8010/**.

The page is served same-origin by the agent (no CORS setup needed) and walks
through the full demo, showing expected vs. actual JSON for each step:

0. **Check user state** — `GET /profile`, highlights the starting **$20** budget cap
1. **Clear orders** — `DELETE /orders`
2. **Run gift agent (1st, $20 cap)** — `POST /agent/gift`
3. **Check orders** — `GET /orders` → 1 order
4. **Raise cap to $50** — `PUT /profile`
5. **Run gift agent (2nd, $50 cap)** — `POST /agent/gift`
6. **Check orders** — `GET /orders` → 2 distinct orders

Click **▶ Run full demo** to run them in sequence, or run any step individually.
Health of the API is shown by the dot next to the API URL field.

## API quick reference

All on `http://localhost:8010`:

| Method | Path            | Description                                   |
| ------ | --------------- | --------------------------------------------- |
| GET    | `/`             | Interactive demo page                         |
| GET    | `/healthz`      | `{ "status": "ok" }`                           |
| POST   | `/agent/gift`   | Run the agent; `{ "status": "fulfilled" }`     |
| GET    | `/orders`       | List stored orders for `USER_ID`               |
| DELETE | `/orders`       | Clear all orders                               |
| GET    | `/profile`      | Read the current user profile                  |
| PUT    | `/profile`      | Overwrite the stored profile                   |

> Orders live in an in-memory store, so they reset when the agent restarts.

Ready-made requests are also available in the Bruno collection under `bruno/`.
