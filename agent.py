"""
LangChain ReAct agent that drives the computer using our tools.

Usage:
    uv run agent.py "Post 'hello world' on LinkedIn"
    uv run agent.py --model gpt-4o-mini "Open GitHub and find my repo named clarilo"
    uv run agent.py --max-steps 20 "Send 'Hi there' to my first contact in Gmail"

Requires OPENAI_API_KEY in .env (or environment).
"""
from __future__ import annotations

import argparse
import io
import sys

from dotenv import load_dotenv
from langchain.agents import create_agent

# UTF-8 console output (a11y trees can have emoji / non-latin)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from tools import ALL_TOOLS

SYSTEM_PROMPT = """\
You are a computer-use agent. You drive a real Windows desktop browser through OS-level
input (mouse clicks, keystrokes) and read the screen via the Windows accessibility tree.

The view_screen output groups elements by SPATIAL REGION:
  [top]    horizontal top bar — usually nav, search, profile menu
  [left]   left sidebar — usually nav menu or profile card
  [main]   primary content column — feed, document, conversation, the actual page
  [right]  right sidebar — usually recommendations, ads, related content
  [bottom] footer — links, copyright
  [modal]  a dialog/popup is open and dominating the page

Within each region, elements are in reading order (top-to-bottom, left-to-right).

When the user asks about page content ("the first post", "the message", "the article"),
they almost always mean [main]. [right] is sidebar widgets, not the content the user
is looking at. Don't confuse them.

Tools — use them precisely:
1. ALWAYS call set_target_window FIRST with the target site/app name (e.g. "LinkedIn").
2. Then call view_screen ONCE to see layout. Don't repeatedly re-view — that wastes tokens.
   Use find() for targeted lookups instead.
3. Element ids (like 'b7a3f2') come from view_screen or find. Pass them to click().
4. After an action that opens a dialog/modal/dropdown, call wait_for_change
   instead of view_screen — it returns ONLY new elements (much cheaper).
5. Most editors auto-focus you after a click. After clicking an editor, just type_text directly.
6. If a stored id no longer works, the page changed — call view_screen or find again.

CRITICAL — completing tasks correctly:
- A posting/sending/submitting task is NOT complete until you have CLICKED a submit
  button (look for the [SUBMIT] tag in view output) AND verified the form closed.
- Pressing Enter/Return inside a text editor usually does NOT submit — it adds a newline.
  In rich text editors (LinkedIn, Twitter, Slack composers, Gmail body), Enter never
  submits. You must click the submit button.
- After clicking the submit button, call wait_for_change to confirm the dialog closed
  or a success message appeared. If neither happened, you have NOT completed the task.
- Never claim success without observable evidence. "I typed the text" ≠ "the post was sent."

Be terse. Don't over-explain. Take the next action."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("goal", help="natural-language task description")
    ap.add_argument("--model", default="openai:gpt-4o-mini")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--verbose", action="store_true", help="print every step")
    args = ap.parse_args()

    load_dotenv()

    agent = create_agent(
        model=args.model,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )

    print(f"Goal: {args.goal}\n", file=sys.stderr)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.goal}]},
        config={"recursion_limit": args.max_steps * 2},  # each step = LLM turn + tool turn
    )

    messages = result["messages"]
    if args.verbose:
        for m in messages:
            role = getattr(m, "type", "?")
            content = getattr(m, "content", "")
            tool_calls = getattr(m, "tool_calls", None)
            print(f"\n--- {role} ---", file=sys.stderr)
            if tool_calls:
                for tc in tool_calls:
                    print(f"  TOOL CALL: {tc.get('name')}({tc.get('args')})", file=sys.stderr)
            if content:
                # content can be a string or a list of content blocks
                if isinstance(content, str):
                    print(content[:4000], file=sys.stderr)
                else:
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            print(block.get("text", "")[:4000], file=sys.stderr)

    final = messages[-1]
    final_content = getattr(final, "content", "")
    if isinstance(final_content, list):
        # Extract text blocks
        text_parts = []
        for block in final_content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        final_content = "\n".join(text_parts)
    print("\n=== FINAL ===")
    print(final_content)


if __name__ == "__main__":
    main()
