"""
Render a Mermaid diagram to a PNG URL using mermaid.ink (no API key needed).

mermaid.ink takes a base64-encoded Mermaid source and returns a PNG.
URL must stay under 2 KB for Composio's image-insert constraint, so we
cap our diagrams at ~6 nodes (Bohdan's spec).
"""
from __future__ import annotations

import base64
import zlib

MERMAID_INK_BASE = "https://mermaid.ink/img"


def diagram_to_url(
    mermaid_src: str,
    theme: str = "default",
    bg_color: str = "ffffff",
    width: int = 1200,
    scale: int = 2,
) -> str:
    """Convert Mermaid source code to a public PNG URL.

    theme="default" renders with proper colors on a white background which
    fits Google Docs (white pages) cleanly.
    width=1400 + scale=2 produces a large, sharp diagram that fills the
    Doc page width when embedded.
    """
    src = mermaid_src.strip()
    state = {
        "code": src,
        "mermaid": {"theme": theme},
    }
    qs = f"type=png&theme={theme}&bgColor={bg_color}&width={width}&scale={scale}"
    try:
        import json
        json_str = json.dumps(state, separators=(",", ":"))
        compressed = zlib.compress(json_str.encode("utf-8"), 9)
        encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
        return f"{MERMAID_INK_BASE}/pako:{encoded}?{qs}"
    except Exception:
        encoded = base64.urlsafe_b64encode(src.encode("utf-8")).decode("ascii").rstrip("=")
        return f"{MERMAID_INK_BASE}/{encoded}?{qs}"


def is_safe_url_size(url: str, max_kb: int = 2) -> bool:
    """mermaid.ink URLs that are too long break Composio's INSERT_INLINE_IMAGE."""
    return len(url.encode("utf-8")) <= max_kb * 1024


def render_to_png_file(mermaid_src: str, out_path: str | None = None, **render_kwargs) -> str | None:
    """Fetch the rendered PNG from mermaid.ink and save to a local file.

    Returns the local file path on success, or None on failure.
    Default out_path: a temp file. The caller is responsible for cleanup
    if they pass their own path; temp files are auto-cleaned by the OS.
    """
    import requests
    import tempfile

    url = diagram_to_url(mermaid_src, **render_kwargs)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        if not resp.content or len(resp.content) < 200:
            print(f"[mermaid] empty or tiny response from mermaid.ink ({len(resp.content)} bytes)", flush=True)
            return None
    except Exception as ex:
        print(f"[mermaid] mermaid.ink fetch failed: {ex}", flush=True)
        return None

    if out_path is None:
        fd, out_path = tempfile.mkstemp(suffix=".png", prefix="diagram_")
        import os as _os
        _os.close(fd)

    try:
        with open(out_path, "wb") as f:
            f.write(resp.content)
    except Exception as ex:
        print(f"[mermaid] could not write PNG to {out_path}: {ex}", flush=True)
        return None

    return out_path
