"""LangGraph gift-selection agent.

A two-node graph on top of the partner marketplace search core:

* ``search`` node — runs the Product Search Agent system prompt with a
  ``search_products`` tool, letting the model map the user profile onto catalog
  filters and gather candidates.
* ``select`` node — runs the Gift Selection Agent system prompt over those
  candidates and returns the specified JSON decision.

See :mod:`gift_agent.graph` for the graph and :mod:`gift_agent.api` for the HTTP
endpoint that triggers it.
"""

from .graph import build_graph, run_agent

__all__ = ["build_graph", "run_agent"]
