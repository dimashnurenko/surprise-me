"""Flow-focused, human-readable logging for the gift agent.

We only log the *flow*: the overall run, each graph node as it is invoked, and
each Claude (LLM) call — plus the ids that identify what happened on the happy
path. Infrastructure chatter (MCP round-trips, Rain/partner HTTP request and
response bodies, store writes) is deliberately kept off this INFO view and left
at ``DEBUG`` so the flow reads top-to-bottom like a story.

Each boundary is a single line so it sits cleanly next to the log prefix
(``timestamp LEVEL [gift_agent]``) instead of trailing ragged, unprefixed
rule lines. A label is centred inside a rule of box-drawing characters so a
new run or node stands out at a glance while staying one line tall.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("gift_agent")

_WIDTH = 60


def _banner(label: str, fill: str) -> str:
    """A single-line rule with ``label`` centred inside it."""
    text = f" {label.upper()} "
    pad = max(_WIDTH - len(text), 2)
    left = pad // 2
    right = pad - left
    return f"{fill * left}{text}{fill * right}"


def run(title: str) -> None:
    """Heavy banner marking the start/finish of a whole agent run."""
    logger.info(_banner(f"RUN · {title}", "═"))


def node(name: str) -> None:
    """Banner marking a graph node being invoked."""
    logger.info(_banner(f"NODE · {name}", "─"))


def llm(label: str) -> None:
    """A single Claude/LLM call within the current node."""
    logger.info("   ◆ LLM · %s", label.upper())


def step(message: str) -> None:
    """A single flow fact — ids are enough on the happy path."""
    logger.info("   · %s", message)


def warn(message: str) -> None:
    """A flow problem that isn't fatal (e.g. nothing to do, missing config)."""
    logger.warning("   ! %s", message)
