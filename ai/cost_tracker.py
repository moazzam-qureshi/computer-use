"""LangChain callback that records every LLM call into agent_runs."""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler
from storage.agent_runs import AgentRunStore


class CostTracker(BaseCallbackHandler):
    def __init__(self, store: AgentRunStore, agent_name: str, trigger: str, trigger_context: dict, parent_run_id: Optional[int] = None):
        self._store = store
        self._agent_name = agent_name
        self._trigger = trigger
        self._trigger_context = trigger_context
        self._parent = parent_run_id
        self._run_id: Optional[int] = None
        self._steps: list[dict] = []
        self._total_tokens = 0
        self._total_cost = 0.0

    def __enter__(self) -> "CostTracker":
        self._run_id = self._store.start(
            agent_name=self._agent_name,
            trigger=self._trigger,
            trigger_context=self._trigger_context,
            parent_run_id=self._parent,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        status = "succeeded" if exc is None else "failed"
        self._store.finish(
            run_id=self._run_id,
            status=status,
            total_tokens=self._total_tokens,
            total_cost_usd=self._total_cost,
            output_summary=None,
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = getattr(response, "llm_output", None) or {}
        token_usage = usage.get("token_usage", {}) if isinstance(usage, dict) else {}
        self._total_tokens += int(token_usage.get("total_tokens", 0) or 0)
        # Cost calculation deferred to Phase 2 once we wire model-name -> $/token table

    @property
    def run_id(self) -> int:
        assert self._run_id is not None
        return self._run_id
