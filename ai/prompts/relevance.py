RELEVANCE_SYSTEM = """You judge whether a freelance job posting is actually a good fit for a defined setup, beyond the surface rule match.

The setup describes the kind of work we want. The job has matched the rules but you decide if it's a real fit:
- a fresh client posting their first job at $1000 to "build me an AI" is NOT a real fit even if rules matched.
- a sophisticated buyer with a clear scoped problem at the budget floor IS a real fit.

Be strict. Only return relevant=true if you'd want a senior engineer to actually pitch this.
"""

RELEVANCE_USER = """SETUP: {setup_name}
SETUP DESCRIPTION: {setup_prose}

JOB:
Title: {title}
Budget: {budget_text}
Posted: {posted_text}
Client: {client_summary}
Skills: {skills}

Description:
{description}

Decide.
"""
