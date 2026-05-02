"""
Google Docs creation via Composio.

Two actions per proposal:
  1. GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN — creates a doc from markdown.
  2. GOOGLEDRIVE_CREATE_PERMISSION — sets sharing to "anyone with link, reader".

Returns a public URL like https://docs.google.com/document/d/<doc_id>/edit
that the cover letter can include.

Reads:
  COMPOSIO_API_KEY  — your Composio key
  COMPOSIO_USER_ID  — the user_id you used when connecting Docs/Drive

Both must be in .env.
"""
from __future__ import annotations

import os
from typing import Optional

from composio import Composio


import tempfile


def _client() -> Composio:
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        raise RuntimeError("COMPOSIO_API_KEY not set in environment / .env")
    # Allow auto-staging of local files in temp dirs so GOOGLEDRIVE_UPLOAD_FILE
    # can take a local path instead of a manually-staged S3 key.
    return Composio(
        api_key=api_key,
        dangerously_allow_auto_upload_download_files=True,
        file_upload_dirs=[tempfile.gettempdir()],
    )


def _user_id() -> str:
    uid = os.getenv("COMPOSIO_USER_ID")
    if not uid:
        raise RuntimeError("COMPOSIO_USER_ID not set in environment / .env")
    return uid


def _extract_doc_id(create_result: dict) -> Optional[str]:
    """Composio returns nested response data; the doc_id can be in several
    places depending on response shape. Try the common ones."""
    if not isinstance(create_result, dict):
        return None
    # Top-level
    for k in ("documentId", "document_id", "id"):
        v = create_result.get(k)
        if isinstance(v, str) and len(v) > 10:
            return v
    # Inside `data` / `response_data`
    for wrapper_key in ("data", "response_data", "result"):
        wrapper = create_result.get(wrapper_key)
        if isinstance(wrapper, dict):
            for k in ("documentId", "document_id", "id"):
                v = wrapper.get(k)
                if isinstance(v, str) and len(v) > 10:
                    return v
    return None


_DIAGRAM_ANCHOR_HEADING = "Solution at a glance"


def _upload_image_to_drive(composio, user_id: str, local_path: str, name: str = "diagram.png") -> Optional[str]:
    """Upload a local PNG to Google Drive, make it public, return a direct-content URL.

    Returns a URL suitable for INSERT_INLINE_IMAGE (something Google Docs can fetch
    as image bytes), or None on failure.
    """
    # Composio's auto-staging (dangerously_allow_auto_upload_download_files=True)
    # expects file_to_upload to be a raw local PATH STRING. The SDK auto-stages
    # it to S3 and constructs the {name,mimetype,s3key} object internally.
    # Try the path-string form first; fall back to the explicit dict form.
    up_res = None
    last_err: Optional[str] = None
    for variant_name, args in [
        ("path-string", {"file_to_upload": local_path}),
        ("explicit-dict", {"file_to_upload": {"name": name, "mimetype": "image/png", "s3key": local_path}}),
    ]:
        try:
            r = composio.tools.execute(
                "GOOGLEDRIVE_UPLOAD_FILE",
                arguments=args,
                user_id=user_id,
                version="20260429_00",
            )
            if isinstance(r, dict):
                data = r.get("data") if isinstance(r.get("data"), dict) else {}
                err = r.get("error") or data.get("error") or data.get("message")
                if r.get("successful") is False or err:
                    last_err = f"{variant_name}: {err}"
                    continue
            up_res = r
            print(f"[gdocs] UPLOAD_FILE ok ({variant_name})", flush=True)
            break
        except Exception as ex:
            last_err = f"{variant_name}: {ex}"
            continue
    if up_res is None:
        print(f"[gdocs] UPLOAD_FILE all variants failed. Last: {last_err}", flush=True)
        return None

    # Extract Drive file_id from the response
    file_id = None
    if isinstance(up_res, dict):
        # Walk for an id-shaped value
        def _find_id(d, depth=0):
            if depth > 5 or not isinstance(d, dict):
                return None
            for k in ("id", "file_id", "fileId"):
                v = d.get(k)
                if isinstance(v, str) and len(v) >= 20:
                    return v
            for v in d.values():
                if isinstance(v, dict):
                    r = _find_id(v, depth + 1)
                    if r:
                        return r
            return None
        file_id = _find_id(up_res)
    if not file_id:
        print(f"[gdocs] could not extract file_id from UPLOAD_FILE response: {str(up_res)[:300]}", flush=True)
        return None

    # Step 2: set "anyone with link can view" so Google Docs can fetch the image
    try:
        composio.tools.execute(
            "GOOGLEDRIVE_CREATE_PERMISSION",
            arguments={
                "file_id": file_id,
                "role": "reader",
                "type": "anyone",
            },
            user_id=user_id,
            version="20260429_00",
        )
    except Exception as ex:
        print(f"[gdocs] image CREATE_PERMISSION failed: {ex}", flush=True)
        # Non-fatal; URL might still work if Drive defaults allow it

    # Step 3: build a direct-content URL. The classic public form is:
    #   https://drive.google.com/uc?export=view&id=<file_id>
    # which serves the raw image bytes (Google Docs can ingest this).
    return f"https://drive.google.com/uc?export=view&id={file_id}"


def create_proposal_doc(title: str, markdown: str, diagram_url: Optional[str] = None) -> Optional[str]:
    """Create a Google Doc from markdown content, optionally insert a diagram
    image right after the "Solution at a glance" heading, make it shareable,
    and return URL.

    Returns the canonical /edit URL, or None on failure.
    """
    composio = _client()
    user_id = _user_id()

    # Step 1: create the doc from markdown
    try:
        create_res = composio.tools.execute(
            "GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN",
            arguments={
                "title": title[:200],
                "markdown_text": markdown,
            },
            user_id=user_id,
            version="20260501_01",
        )
    except Exception as ex:
        print(f"[gdocs] CREATE_DOCUMENT_MARKDOWN failed: {ex}", flush=True)
        return None

    doc_id = _extract_doc_id(create_res)
    if not doc_id:
        print(f"[gdocs] could not extract doc_id from response: {str(create_res)[:300]}", flush=True)
        return None

    # Step 2: download mermaid PNG, upload to Drive, get a Drive image URL,
    # then INSERT_INLINE_IMAGE it into the doc.
    drive_image_url = None
    if diagram_url:
        # mermaid.ink is occasionally flaky (503s under load). Retry with
        # backoff. If still failing, give up on the diagram for this proposal
        # and ship the doc without it — better than blocking the whole flow.
        png_bytes = None
        try:
            import requests as _requests
            backoff = [0, 2, 5]  # seconds
            for i, wait in enumerate(backoff):
                if wait:
                    import time as _time
                    _time.sleep(wait)
                try:
                    r = _requests.get(diagram_url, timeout=15)
                    if r.status_code == 200 and r.content and len(r.content) >= 200:
                        png_bytes = r.content
                        break
                    print(f"[gdocs] mermaid.ink attempt {i+1} returned status={r.status_code} bytes={len(r.content)}", flush=True)
                except Exception as ex:
                    print(f"[gdocs] mermaid.ink attempt {i+1} failed: {ex}", flush=True)
            if png_bytes is None:
                print(f"[gdocs] mermaid.ink unavailable after {len(backoff)} attempts; shipping doc without diagram", flush=True)
        except Exception as ex:
            print(f"[gdocs] unexpected error fetching diagram: {ex}", flush=True)

        if png_bytes and len(png_bytes) > 200:
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="diagram_")
                os.close(fd)
                # Normalize Windows short-name (MOAZZA~1) to long form so
                # Composio's path-allowlist check matches.
                tmp_path = os.path.realpath(tmp_path)
                with open(tmp_path, "wb") as f:
                    f.write(png_bytes)
                # Resize the PNG so Google Docs renders it at a sane page-fitting
                # size. Google Docs honors native pixel dimensions, so we cap
                # the longest side at 720px (~ 7.5 inches at 96 DPI), which
                # fits comfortably on a Letter page without forcing a page break.
                try:
                    from PIL import Image
                    with Image.open(tmp_path) as img:
                        max_side = 720
                        w, h = img.size
                        if max(w, h) > max_side:
                            ratio = max_side / max(w, h)
                            new_size = (int(w * ratio), int(h * ratio))
                            resized = img.resize(new_size, Image.LANCZOS)
                            resized.save(tmp_path, "PNG", optimize=True)
                            print(f"[gdocs] resized diagram from {w}x{h} to {new_size[0]}x{new_size[1]}", flush=True)
                except Exception as ex:
                    print(f"[gdocs] resize failed (continuing with original): {ex}", flush=True)
                drive_image_url = _upload_image_to_drive(composio, user_id, tmp_path)
                print(f"[gdocs] drive image url: {drive_image_url}", flush=True)
            except Exception as ex:
                print(f"[gdocs] drive upload failed: {ex}", flush=True)
            finally:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    if drive_image_url:
        # Doc ends with "How the pieces fit together" heading. Insert the
        # image at end-of-doc so it lands directly under that heading as the
        # final visual.
        insert_index = _get_actual_end_index(composio, user_id, doc_id)
        if insert_index is None:
            print("[gdocs] no insert index resolved; skipping image insert", flush=True)
        else:
            ins_res = None
            ins_err = None
            for id_field in ("id", "documentId"):
                args = {
                    id_field: doc_id,
                    "uri": drive_image_url,
                    "location": {"index": insert_index},
                }
                try:
                    r = composio.tools.execute(
                        "GOOGLEDOCS_INSERT_INLINE_IMAGE",
                        arguments=args,
                        user_id=user_id,
                        version="20260501_01",
                    )
                    if isinstance(r, dict):
                        data = r.get("data") if isinstance(r.get("data"), dict) else {}
                        err = r.get("error") or data.get("error") or data.get("message")
                        if r.get("successful") is False or err or data.get("http_error"):
                            ins_err = f"{id_field}: {err or data.get('http_error')}"
                            continue
                    ins_res = r
                    print(f"[gdocs] INSERT_INLINE_IMAGE ok at index {insert_index} (param={id_field})", flush=True)
                    break
                except Exception as ex:
                    ins_err = f"{id_field}: {ex}"
                    continue
            if ins_res is None:
                print(f"[gdocs] INSERT_INLINE_IMAGE all attempts failed. Last: {ins_err}", flush=True)

    # Step 3: set "anyone with link can view"
    try:
        composio.tools.execute(
            "GOOGLEDRIVE_CREATE_PERMISSION",
            arguments={
                "file_id": doc_id,
                "role": "reader",
                "type": "anyone",
            },
            user_id=user_id,
            version="20260429_00",
        )
    except Exception as ex:
        print(f"[gdocs] CREATE_PERMISSION failed (doc not shared publicly): {ex}", flush=True)

    return f"https://docs.google.com/document/d/{doc_id}/edit"


def _get_index_before_heading(composio, user_id: str, doc_id: str, heading_text: str) -> Optional[int]:
    """Fetch the doc and find the index just before a paragraph that starts
    with `heading_text`. Used to insert content (e.g. an image) at a specific
    mid-document location anchored to a known heading.

    Returns startIndex of that paragraph minus 1 (so the image lands right
    above it). Returns None if heading not found.
    """
    res = _get_doc_structure(composio, user_id, doc_id)
    if not res:
        return None

    def _find_body(d, depth=0):
        if depth > 6 or not isinstance(d, dict):
            return None
        if "body" in d and isinstance(d["body"], dict):
            return d["body"]
        for v in d.values():
            if isinstance(v, dict):
                r = _find_body(v, depth + 1)
                if r:
                    return r
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        r = _find_body(item, depth + 1)
                        if r:
                            return r
        return None

    body = _find_body(res) if isinstance(res, dict) else None
    if not body:
        return None
    content = body.get("content")
    if not isinstance(content, list):
        return None

    # Walk paragraphs; find one whose text starts with heading_text
    for elem in content:
        if not isinstance(elem, dict):
            continue
        para = elem.get("paragraph")
        if not isinstance(para, dict):
            continue
        # Concatenate text runs to get the paragraph's text
        text = ""
        for run in para.get("elements", []):
            if isinstance(run, dict):
                tr = run.get("textRun")
                if isinstance(tr, dict):
                    text += tr.get("content", "")
        if text.strip().startswith(heading_text):
            start = elem.get("startIndex")
            if isinstance(start, int) and start > 1:
                return start - 1
    return None


def _get_doc_structure(composio, user_id: str, doc_id: str) -> Optional[dict]:
    """Single-call doc fetcher. Tries action variants until one works."""
    res = None
    last_err: Optional[str] = None
    attempts = [
        ("GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"id": doc_id}),
        ("GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"documentId": doc_id}),
        ("GOOGLEDOCS_GET_DOCUMENT", {"id": doc_id}),
        ("GOOGLEDOCS_GET_DOCUMENT", {"documentId": doc_id}),
    ]
    for action, args in attempts:
        try:
            r = composio.tools.execute(
                action,
                arguments=args,
                user_id=user_id,
                version="20260501_01",
            )
            if isinstance(r, dict):
                data = r.get("data") if isinstance(r.get("data"), dict) else {}
                err = r.get("error") or data.get("error") or data.get("message")
                if r.get("successful") is False or err:
                    last_err = f"{action}: {err}"
                    continue
            res = r
            break
        except Exception as ex:
            last_err = f"{action}: {ex}"
            continue
    if res is None:
        print(f"[gdocs] GET_DOCUMENT all attempts failed. Last: {last_err}", flush=True)
        return None
    return res


def _get_actual_end_index(composio, user_id: str, doc_id: str) -> Optional[int]:
    """Fetch the doc and return its real last valid insertion index."""
    res = None
    last_err: Optional[str] = None
    # Try a few action / param combinations — both names have appeared in
    # different Composio toolkit versions.
    attempts = [
        ("GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"id": doc_id}),
        ("GOOGLEDOCS_GET_DOCUMENT_BY_ID", {"documentId": doc_id}),
        ("GOOGLEDOCS_GET_DOCUMENT", {"id": doc_id}),
        ("GOOGLEDOCS_GET_DOCUMENT", {"documentId": doc_id}),
    ]
    for action, args in attempts:
        try:
            r = composio.tools.execute(
                action,
                arguments=args,
                user_id=user_id,
                version="20260501_01",
            )
            # Composio sometimes returns a structured-error response (200 OK)
            # rather than raising. Treat successful=False as failure too.
            if isinstance(r, dict):
                data = r.get("data") if isinstance(r.get("data"), dict) else {}
                err = r.get("error") or data.get("error") or data.get("message")
                if r.get("successful") is False or err:
                    last_err = f"{action} {list(args.keys())[0]} -> {err}"
                    continue
            res = r
            break
        except Exception as ex:
            last_err = f"{action} {list(args.keys())[0]} -> {ex}"
            continue
    if res is None:
        print(f"[gdocs] GET_DOCUMENT all attempts failed. Last error: {last_err}", flush=True)
        return None

    # ALWAYS dump the response shape so we can verify the parser logic
    # against real data (not assumptions).
    try:
        import json as _json
        # Pretty-print but cap to ~1500 chars so logs stay readable
        preview = _json.dumps(res, default=str, indent=2)
        if len(preview) > 1500:
            preview = preview[:1500] + "\n  ... (truncated)"
    except Exception:
        preview = str(res)[:1500]
    print(f"[gdocs] GET_DOCUMENT response preview:\n{preview}", flush=True)

    # The Composio response shape is typically {data: {documentId, body: {...}, ...}}
    # but sometimes the body is at top level or wrapped further. Walk to find it.
    def _find_body(d, depth: int = 0) -> Optional[dict]:
        if depth > 6 or not isinstance(d, dict):
            return None
        if "body" in d and isinstance(d["body"], dict):
            return d["body"]
        for v in d.values():
            if isinstance(v, dict):
                r = _find_body(v, depth + 1)
                if r:
                    return r
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        r = _find_body(item, depth + 1)
                        if r:
                            return r
        return None

    body = _find_body(res) if isinstance(res, dict) else None
    if not body:
        print(f"[gdocs] could not find 'body' anywhere in response", flush=True)
        return None
    content = body.get("content")
    if not isinstance(content, list) or not content:
        print(f"[gdocs] body has no content array. Body keys: {list(body.keys())}", flush=True)
        return None
    # The last element with endIndex is the doc's last paragraph.
    last_end_idx = None
    for elem in content:
        if isinstance(elem, dict) and isinstance(elem.get("endIndex"), int):
            last_end_idx = elem["endIndex"]
    if last_end_idx is None:
        print(f"[gdocs] no endIndex found in any content element", flush=True)
        return None
    print(f"[gdocs] resolved endIndex={last_end_idx}, will insert at {last_end_idx - 1}", flush=True)
    return max(1, last_end_idx - 1)


def _proposal_to_markdown(p) -> str:
    """Render a ProposalDraft (ai.schemas.ProposalDraft) into the locked Doc
    structure. The diagram section is left as a trailing heading; the actual
    image is inserted by create_proposal_doc via diagram_url.
    """
    lines: list[str] = []
    lines.append(f"# {p.title}")
    lines.append("")
    lines.append(p.opener)
    lines.append("")
    if p.approach:
        lines.append("## How I'd approach it")
        for item in p.approach:
            lines.append(f"- {item}")
        lines.append("")
    if p.deliverables:
        lines.append("## What you'd get")
        for item in p.deliverables:
            lines.append(f"- {item}")
        lines.append("")
    if p.timeline:
        lines.append("## Timeline")
        for item in p.timeline:
            lines.append(f"- {item}")
        lines.append("")
    if p.about_me:
        lines.append("## A bit about me")
        lines.append(p.about_me)
        lines.append("")
    if p.clarifying_questions:
        lines.append("## Questions I'd want to clarify")
        for item in p.clarifying_questions:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## How the pieces fit together")
    lines.append("")
    return "\n".join(lines)


def create_doc_with_diagram(proposal, *, mermaid_source: str) -> str:
    """Thin wrapper: render proposal markdown, build a mermaid.ink image URL,
    then call create_proposal_doc which uploads the image to Drive and inserts
    it at end-of-doc. Returns the public Doc URL.

    Raises RuntimeError if create_proposal_doc returns None.
    """
    from external import mermaid as _mermaid

    body = _proposal_to_markdown(proposal)
    diagram_url = _mermaid.diagram_to_url(mermaid_source) if mermaid_source else None
    if diagram_url and not _mermaid.is_safe_url_size(diagram_url):
        print(f"[gdocs] diagram URL too large ({len(diagram_url.encode())} bytes), skipping", flush=True)
        diagram_url = None
    url = create_proposal_doc(title=proposal.title, markdown=body, diagram_url=diagram_url)
    if url is None:
        raise RuntimeError("create_doc_with_diagram returned no URL")
    return url
