"""The LangGraph gift-selection graph.

Two nodes wired in sequence:

    search  ->  select

* ``search`` runs the Product Search Agent prompt with a ``search_products``
  tool-use loop and gathers de-duplicated candidate products.
* ``select`` runs the Gift Selection Agent prompt over those candidates and
  parses the specified JSON decision.

The state is a plain :class:`TypedDict`; :func:`run_agent` is a thin convenience
wrapper for callers (e.g. the HTTP API) that just want the final decision.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from anthropic import Anthropic
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from . import prompts
from .tools import SEARCH_TOOL_NAME, run_search_tool, search_tool_schema

load_dotenv()

logger = logging.getLogger("gift_agent")
if not logging.getLogger().handlers and not logger.handlers:
    # Provide a sensible default so the flow logs show up in the console even when
    # the app hasn't configured logging (e.g. running the module directly).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

# The partner search core resolves its DB from PARTNER_DB_PATH. When that isn't set
# (and the module's stale default doesn't exist), fall back to the DB committed in the
# repo so the agent works out of the box.
_REPO_DB = Path(__file__).resolve().parent.parent / "partner_mock" / "partner_web_site_db.json"
if not os.environ.get("PARTNER_DB_PATH") and _REPO_DB.exists():
    os.environ["PARTNER_DB_PATH"] = str(_REPO_DB)

# Cheapest current Claude model.
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2048
# Cap the search agent's tool-use loop so a misbehaving model can't spin forever.
MAX_SEARCH_STEPS = 6


@lru_cache(maxsize=1)
def _client() -> Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set (put it in .env or the environment).")
    return Anthropic()


class GiftState(TypedDict, total=False):
    # Inputs
    user_profile: dict[str, Any]
    current_date: str
    # Produced by the search node
    search_results: list[dict[str, Any]]
    search_calls: list[dict[str, Any]]
    # Produced by the select node
    selection: dict[str, Any]


# --------------------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------------------
def search_node(state: GiftState) -> GiftState:
    """Run the search agent: it maps the profile onto catalog filters, calls the
    ``search_products`` tool (possibly several times) and we collect the candidates."""
    logger.info("[search] node started (current_date=%s)", state.get("current_date"))
    system = prompts.search_system_prompt(state["user_profile"], state["current_date"])
    tools = [search_tool_schema()]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                "Decide the best catalog search for this profile and call search_products. "
                "You may search more than once to compare options; stop once you have good "
                "candidates within budget."
            ),
        }
    ]

    candidates: dict[str, dict[str, Any]] = {}
    search_calls: list[dict[str, Any]] = []

    for step in range(MAX_SEARCH_STEPS):
        logger.info("[search] LLM step %d/%d", step + 1, MAX_SEARCH_STEPS)
        response = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            logger.info("[search] no more tool calls; stopping loop at step %d", step + 1)
            break

        tool_results: list[dict[str, Any]] = []
        for block in tool_uses:
            if block.name == SEARCH_TOOL_NAME:
                logger.info("[search] calling %s with %s", SEARCH_TOOL_NAME, block.input)
                envelope = run_search_tool(block.input)
                total = envelope["pagination"]["total_results"]
                search_calls.append({"input": block.input, "total": total})
                for product in envelope.get("results", []):
                    candidates[product["id"]] = product
                logger.info(
                    "[search] tool returned %d results (%d total, %d unique candidates so far)",
                    len(envelope.get("results", [])),
                    total,
                    len(candidates),
                )
                payload = envelope
            else:
                logger.warning("[search] model requested unknown tool: %s", block.name)
                payload = {"error": "unknown_tool", "tool": block.name}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    logger.info(
        "[search] node finished: %d unique candidates from %d search call(s)",
        len(candidates),
        len(search_calls),
    )
    return {"search_results": list(candidates.values()), "search_calls": search_calls}


def select_node(state: GiftState) -> GiftState:
    """Run the gift-selection agent over the gathered candidates and parse its JSON."""
    candidates = state.get("search_results", [])
    logger.info("[select] node started with %d candidate(s)", len(candidates))
    system = prompts.gift_selection_system_prompt(
        state["user_profile"],
        candidates,
        state["current_date"],
    )
    response = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[
            {
                "role": "user",
                "content": "Select the single best gift now. Return only the JSON, no other text.",
            }
        ],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    selection = _parse_json(text)
    logger.info(
        "[select] node finished: selected %s",
        selection.get("product_id") or selection.get("id") or selection,
    )
    return {"selection": selection}


def _parse_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON output, tolerating markdown code fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first {...} block if the model wrapped it in prose.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


# --------------------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def build_graph():
    """Compile and cache the search -> select graph."""
    graph = StateGraph(GiftState)
    graph.add_node("search", search_node)
    graph.add_node("select", select_node)
    graph.add_edge(START, "search")
    graph.add_edge("search", "select")
    graph.add_edge("select", END)
    return graph.compile()


def run_agent(
    user_profile: dict[str, Any],
    current_date: str | None = None,
) -> GiftState:
    """Run the full graph and return the final state (selection + candidates)."""
    state: GiftState = {
        "user_profile": user_profile,
        "current_date": current_date or date.today().isoformat(),
    }
    logger.info("[graph] run_agent started (current_date=%s)", state["current_date"])
    result = build_graph().invoke(state)
    logger.info(
        "[graph] run_agent finished: %d candidates, selection=%s",
        len(result.get("search_results", [])),
        bool(result.get("selection")),
    )
    return result
