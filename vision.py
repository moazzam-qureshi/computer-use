"""
Vision LLM helper for UI element localization.

Used when UIA can't expose a control we need to click — most notably
contenteditable / virtualized form fields (Upwork's screening-question
textareas, which UIA doesn't surface even when in viewport).

Single function: find_textarea_for_question(question_text). Takes a full-screen
screenshot, asks gpt-4o-mini where the textarea associated with the given
question is, returns a sanity-checked BoundingBox or None.

Cost: ~$0.001 per call at 'high' detail. Within the project's compute-use
constraint (no browser automation, no DOM access — vision returns coords,
we click them via pyautogui SendInput like every other input).
"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass

import pyautogui
from openai import OpenAI


@dataclass
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """Same shape as observe.Element.bounds — left, top, right, bottom."""
        return (self.x1, self.y1, self.x2, self.y2)


def screenshot_png_bytes() -> bytes:
    """Full-screen screenshot, PNG-encoded."""
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_PROMPT_TEMPLATE = """You see a full-screen screenshot of a web form.

Find the empty textarea that the user should type their answer into for the
question below. The textarea is the large rectangular input box that appears
immediately below the question label.

QUESTION LABEL TEXT:
{question}

Return ONLY a JSON object with the bounding box of the textarea, in pixel
coordinates from the top-left of the screenshot:
{{"x1": int, "y1": int, "x2": int, "y2": int}}

If you cannot find the textarea (question not visible, or textarea is
off-screen, or you're not confident), return: {{"error": "not found"}}

No other text. No markdown fences. Just the JSON."""


def find_textarea_for_question(
    question_text: str,
    *,
    model: str = "gpt-4o-mini",
    safe_y_range: tuple[int, int] | None = None,
    debug_log=None,
) -> BoundingBox | None:
    """Take a screenshot, ask the vision model to locate the textarea below
    `question_text`. Returns a sanity-checked BoundingBox or None.

    `safe_y_range` is an optional (min_y, max_y) — bounding boxes whose center
    y is outside this range are rejected as suspicious (probably a misread).
    `debug_log` is an optional callable; if given, status messages are passed
    to it (e.g. log function from upwork_apply).
    """
    def _log(msg: str) -> None:
        if debug_log is not None:
            debug_log(msg)

    png = screenshot_png_bytes()
    b64 = base64.b64encode(png).decode("ascii")
    prompt = _PROMPT_TEMPLATE.format(question=question_text)

    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                ],
            }],
            temperature=0.0,
            max_tokens=200,
        )
    except Exception as ex:
        _log(f"  vision API error: {ex}")
        return None

    text = (resp.choices[0].message.content or "").strip()
    _log(f"  vision raw response: {text[:200]!r}")

    # Extract JSON object (model sometimes wraps in fences or adds text)
    match = re.search(r"\{[^{}]*\}", text)
    if not match:
        _log("  vision response had no JSON object")
        return None

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as ex:
        _log(f"  vision JSON parse failed: {ex}")
        return None

    if "error" in data:
        _log(f"  vision returned error: {data.get('error')}")
        return None

    try:
        x1, y1 = int(data["x1"]), int(data["y1"])
        x2, y2 = int(data["x2"]), int(data["y2"])
    except (KeyError, ValueError, TypeError) as ex:
        _log(f"  vision JSON missing fields: {ex}; data={data}")
        return None

    bb = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)

    # Sanity checks
    sw, sh = pyautogui.size()
    if bb.x1 < 0 or bb.y1 < 0:
        _log(f"  vision rejected: negative coords {bb.bounds}")
        return None
    if bb.x2 > sw or bb.y2 > sh:
        _log(f"  vision rejected: outside screen ({sw}x{sh}) — coords {bb.bounds}")
        return None
    if bb.x2 <= bb.x1 or bb.y2 <= bb.y1:
        _log(f"  vision rejected: invalid box {bb.bounds}")
        return None
    if safe_y_range is not None:
        cy = bb.center[1]
        if cy < safe_y_range[0] or cy > safe_y_range[1]:
            _log(f"  vision rejected: center y={cy} outside safe range {safe_y_range}")
            return None
    # Reject suspiciously tiny boxes (probably a single character / icon)
    width, height = bb.x2 - bb.x1, bb.y2 - bb.y1
    if width < 100 or height < 30:
        _log(f"  vision rejected: too small ({width}x{height})")
        return None

    _log(f"  vision returned bb={bb.bounds} center={bb.center}")
    return bb
