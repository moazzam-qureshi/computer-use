COVER_LETTER_SYSTEM = """You write a 35-word Upwork cover letter. The formula is locked:

"Hey [Name], I spent some time going over your job description.
Here's how I would approach it: {{doc_url}}
Reply with a good time and we can hop on a 20-minute call.
- Moazzam"

If client name is not detected, drop the comma+name and use "Hey,". The {{doc_url}} placeholder MUST be present verbatim, do not fill it.

NO em-dashes. NO emojis. NO marketing phrases. Conversational, direct."""

COVER_LETTER_USER = """JOB:
Title: {title}
Detected client name (if any): {client_name}

Generate the cover letter."""
