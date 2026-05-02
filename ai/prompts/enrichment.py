ENRICHMENT_SYSTEM = """You extract structured data from an Upwork job posting. Return a JSON object matching the Enrichment schema. Be specific and conservative, empty arrays are fine if signals are absent."""

ENRICHMENT_USER = """JOB POSTING:
Title: {title}
Budget: {budget_text}
Skills: {skills}

Description:
{description}
"""

ENRICHMENT_PROMPT_VERSION = "2026-05-02-v1"
