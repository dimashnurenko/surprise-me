"""Flow-focused, human-readable logging for the gift agent.

We only log the *flow*: the overall run, each graph node as it is invoked, and
each Claude (LLM) call — plus the ids that identify what happened on the happy
path. Infrastructure chatter (MCP round-trips, Rain/partner HTTP request and
response bodies, store writes) is deliberately kept off this INFO view and left
at ``DEBUG`` so the flow reads top-to-bottom like a story.

Format uses capital letters and rule lines so the important boundaries — a new
run, a new node — stand out at a glance.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("gift_agent")

_WIDTH = 70


def run(title: str) -> None:
    """Heavy banner marking the start/finish of a whole agent run."""
    rule = "=" * _WIDTH
    logger.info("%s\n  %s\n%s", rule, title.upper(), rule)


def node(name: str) -> None:
    """Banner marking a graph node being invoked."""
    rule = "-" * _WIDTH
    logger.info("%s\n  NODE  >  %s\n%s", rule, name.upper(), rule)


def llm(label: str) -> None:
    """A single Claude/LLM call within the current node."""
    logger.info("      * LLM  >  %s", label.upper())


def step(message: str) -> None:
    """A single flow fact — ids are enough on the happy path."""
    logger.info("      - %s", message)


def warn(message: str) -> None:
    """A flow problem that isn't fatal (e.g. nothing to do, missing config)."""
    logger.warning("      ! %s", message)
