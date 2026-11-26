from __future__ import annotations

from os import getenv

from .action_planner import ActionPlanner, RuleBasedActionPlanner, VlaActionPlanner
from .openai_action_provider import OpenAIResponsesActionProvider


ACTION_PLANNER_ENV = "BACKEND_ACTION_PLANNER"


def action_planner_from_environment() -> ActionPlanner:
    provider = getenv(ACTION_PLANNER_ENV, "rule-based").strip().lower()
    if provider in {"rule", "rule-based"}:
        return RuleBasedActionPlanner()
    if provider == "openai":
        return VlaActionPlanner(OpenAIResponsesActionProvider.from_environment())
    raise RuntimeError(
        f"{ACTION_PLANNER_ENV}는 rule-based 또는 openai여야 합니다: {provider}"
    )
