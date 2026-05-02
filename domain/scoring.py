"""Filter-DSL evaluation. Pure function: (Job, Setup) -> MatchResult."""
from __future__ import annotations

import re
from typing import Any

from domain.types import Job, Setup, MatchResult, FilterDsl


def _eval_rule(rule: dict, job: Job) -> tuple[str, bool]:
    """Evaluate a single-key rule against a job. Returns (rule_name, matched)."""
    if len(rule) != 1:
        raise ValueError(f"Rule must have exactly one key: {rule!r}")
    [(key, val)] = rule.items()

    if key == "skill_in":
        wanted = {s.lower() for s in val}
        have = {s.lower() for s in (job.skills or [])}
        return key, bool(wanted & have)

    if key == "budget_min_at_least":
        b = job.budget_min_usd if job.budget_min_usd is not None else job.budget_max_usd
        return key, (b is not None and b >= val)

    if key == "budget_kind_in":
        return key, (job.budget_kind in val)

    if key == "client_payment_verified":
        return key, (job.client_payment_verified == val)

    if key == "client_country_in":
        return key, (job.client_country in val)

    if key == "description_matches":
        if not job.description:
            return key, False
        return key, bool(re.search(val, job.description, re.I))

    raise ValueError(f"Unknown rule key: {key!r}")


def score_job_against_setup(job: Job, setup: Setup) -> MatchResult:
    spec = setup.filter_dsl.spec
    matched_rules: list[str] = []
    unmet_rules: list[str] = []

    if "all_of" in spec:
        all_ok = True
        for rule in spec["all_of"]:
            name, ok = _eval_rule(rule, job)
            if ok:
                matched_rules.append(name)
            else:
                unmet_rules.append(name)
                all_ok = False
        return MatchResult(matched=all_ok, matched_rules=matched_rules, unmet_rules=unmet_rules)

    if "any_of" in spec:
        any_ok = False
        for rule in spec["any_of"]:
            name, ok = _eval_rule(rule, job)
            if ok:
                matched_rules.append(name)
                any_ok = True
            else:
                unmet_rules.append(name)
        return MatchResult(matched=any_ok, matched_rules=matched_rules, unmet_rules=unmet_rules)

    # Single rule
    name, ok = _eval_rule(spec, job)
    if ok:
        matched_rules.append(name)
    else:
        unmet_rules.append(name)
    return MatchResult(matched=ok, matched_rules=matched_rules, unmet_rules=unmet_rules)
