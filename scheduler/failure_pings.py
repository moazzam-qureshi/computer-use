"""Typed errors that route to Discord templates."""
from __future__ import annotations


class SystemFailure(Exception):
    template: str = "Unknown failure: {detail}"

    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class LoginExpired(SystemFailure):
    template = "Upwork login expired. Need re-auth: {detail}"


class CloudflareWall(SystemFailure):
    template = "Cloudflare wall hit on {detail}"


class OpenAIQuotaExceeded(SystemFailure):
    template = "OpenAI quota exceeded: {detail}"


class ComposioDown(SystemFailure):
    template = "Composio request failed: {detail}"


class PostgresUnavailable(SystemFailure):
    template = "Postgres unavailable: {detail}"


class ConnectsExhausted(SystemFailure):
    template = "Connects cap reached: {detail}"


class OrderAlreadySubmitted(SystemFailure):
    template = "Order already submitted (idempotency): {detail}"


class VisionModelFailed(SystemFailure):
    template = "Vision model failed: {detail}"


class ScreeningQuestionUnanswerable(SystemFailure):
    template = "Couldn't answer screening question: {detail}"


class ApplyFormChanged(SystemFailure):
    template = "Apply form structure shifted, parser needs update: {detail}"
