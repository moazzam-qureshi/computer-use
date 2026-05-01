"""
End-to-end LinkedIn post flow as a single hardcoded recipe.

Uses the diff strategy: snapshot the tree before clicking 'Start a post',
snapshot after, and find the new editable surface in the diff. This is
robust to sites where the editor's a11y name doesn't match its visible
placeholder (which is the common case).

Usage:
    uv run post_linkedin.py "Your post text here"
    uv run post_linkedin.py "Your post text here" --dry-run
"""
from __future__ import annotations

import argparse
import io
import sys
import time

# UTF-8 console output so emoji/non-latin chars don't crash printing.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import act
import observe

WINDOW = "LinkedIn"


def step(msg: str) -> None:
    print(f"[step] {msg}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="post content")
    ap.add_argument("--dry-run", action="store_true", help="skip the final Post click")
    ap.add_argument("--window", default=WINDOW, help="window title substring")
    args = ap.parse_args()

    # 1. Focus the LinkedIn tab
    step(f"focusing window matching {args.window!r}")
    if not act.focus_window(args.window):
        print("Could not focus LinkedIn window. Is it open in our flagged Chrome?", file=sys.stderr)
        sys.exit(1)
    time.sleep(0.4)

    # 2. Snapshot feed, find 'Start a post', click
    step("snapshot 1 (feed)")
    before = observe.observe(window_title=args.window, include_unnamed=True)
    start_btn = observe.find_by_name(before.elements, "Start a post")
    if start_btn is None:
        print(f"'Start a post' not found in {len(before.elements)} elements.", file=sys.stderr)
        sys.exit(2)
    step(f"clicking 'Start a post' at {start_btn.bounds}")
    act.click(start_btn)

    # 3. Wait for the composer's editor to appear in the diff
    step("waiting for editor (role=edit) to appear in diff")
    try:
        new_edits = observe.wait_for_new(
            before.elements,
            window_title=args.window,
            role="edit",
            timeout=10,
        )
    except TimeoutError as ex:
        print(f"Editor didn't appear: {ex}", file=sys.stderr)
        sys.exit(3)

    # Pick the largest new edit surface (the composer body, not some tiny input)
    editor = max(new_edits, key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]))
    step(f"editor found: name={editor.name!r} bounds={editor.bounds}")

    # 4. Click into the editor (safety — most composers auto-focus, but not guaranteed)
    act.focus_window(args.window)
    act.click(editor)
    time.sleep(0.3)

    # 5. Type the post text
    step(f"typing {len(args.text)} chars")
    act.type_text(args.text)
    time.sleep(0.6)  # let LinkedIn enable the Post button

    # 6. Find the Post button in the current tree (exact button-role + name='Post')
    step("looking for 'Post' submit button")
    obs = observe.observe(window_title=args.window, include_unnamed=True)
    post_btn = None
    for e in obs.elements:
        if e.role == "button" and e.name.strip().lower() == "post":
            post_btn = e
            break
    if post_btn is None:
        print("'Post' button not found. Candidates with 'post' in name:", file=sys.stderr)
        for e in obs.elements:
            if e.role == "button" and "post" in e.name.lower():
                print(f"  {e.role:10}  {e.name!r}  bounds={e.bounds}", file=sys.stderr)
        sys.exit(4)
    step(f"Post button at {post_btn.bounds}")

    if args.dry_run:
        step("dry-run: NOT clicking Post. Composer left open with text typed.")
        return

    # 7. Click Post
    act.focus_window(args.window)
    act.click(post_btn)
    step("clicked Post; waiting to confirm composer dismissal")
    time.sleep(2.5)

    # 8. Confirm composer is gone
    final_obs = observe.observe(window_title=args.window, include_unnamed=True)
    still_there = [e for e in final_obs.elements if e.role == "edit" and "Text editor" in e.name]
    if not still_there:
        print("Success: composer dismissed. Post likely submitted.", file=sys.stderr)
    else:
        print("Composer still visible. Submission may have failed.", file=sys.stderr)
        sys.exit(5)


if __name__ == "__main__":
    main()
