# computer-use experiment

Goal: prove the UI Automation tree on Windows is rich enough that a small LLM could drive it, **without** Playwright/CDP and without sending screenshots on every step.

No agent, no LLM in this stage. Just two scripts to test the substrate.

## Setup

```bash
uv sync
```

## Experiment protocol

```bash
# 1) Focus Chrome on the page you want to inspect, then:
uv run dump.py
# -> writes last_dump.json, prints first 40 elements

# 2) Pick an id and click it:
uv run poke.py 12

# Or find by name substring:
uv run poke.py --find "Compose"

# Type into a focused textbox:
uv run poke.py --find "Search mail" --type "from:github"

# List everything captured:
uv run poke.py --list
```

`pyautogui` failsafe: fling the mouse to the top-left corner to abort any action.

## Sites to test (and what to look for)

| Site | Hypothesis |
|---|---|
| Gmail | Rich tree, clean names — should work great |
| GitHub | Standard semantic HTML — clean |
| Your bank | Real-world stress test, varies |
| Twitter/X | Heavy SPA — does the tree stay sane? |
| Figma / Docs editing surface | Canvas — expected empty (validates need for vision fallback) |
| Amazon | Huge DOM — does filtering keep it under ~500 nodes? |

For each: eyeball `last_dump.json`. Are names human-readable? Is the count manageable? Does `poke.py` actually land clicks?

## Stop conditions

- 4+ sites give clean, clickable trees -> theory holds, build the MVP agent.
- Most sites are garbage -> pivot to vision-first before sinking more time.

## Notes

- Modern Chrome enables the a11y tree on demand. If trees come back empty, launch Chrome with `--force-renderer-accessibility`.
- `dump.py` waits 3s before reading so you can switch focus to the target window.
- `poke.py` waits 2s before acting for the same reason.
