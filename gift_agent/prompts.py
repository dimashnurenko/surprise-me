"""Load and render the agent system prompts from ``prompts/``.

The markdown templates use ``{{placeholder}}`` tokens for injected context and
double braces ``{{`` / ``}}`` to escape the literal JSON example. We substitute
the known placeholders first, then collapse any remaining double braces so the
model sees clean single-brace JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _render(template: str, replacements: dict[str, str]) -> str:
    out = template
    for key, value in replacements.items():
        out = out.replace("{{" + key + "}}", value)
    # Collapse the escaped braces of the literal JSON example. Injected JSON
    # uses single braces, so this only touches leftover template escapes.
    out = out.replace("{{", "{").replace("}}", "}")
    return out


def _dumps(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def search_system_prompt(user_profile: dict[str, Any], current_date: str) -> str:
    return _render(
        _load("search_agent_system_prompt.md"),
        {
            "user_profile_json": _dumps(user_profile),
            "current_date": current_date,
        },
    )


def gift_selection_system_prompt(
    user_profile: dict[str, Any],
    search_results: Any,
    current_date: str,
) -> str:
    return _render(
        _load("gift_selection_agent_system_prompt.md"),
        {
            "user_profile_json": _dumps(user_profile),
            "search_results_json": _dumps(search_results),
            "current_date": current_date,
        },
    )
