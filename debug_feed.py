"""Quick diagnostic — show what parse_feed_cards sees on the live feed."""
from __future__ import annotations
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import act
import observe

import time
print("Focus the Upwork window in the next 3 seconds...")
time.sleep(3)

import uiautomation as uia
fg = uia.GetForegroundControl()
print(f"Foreground window class: {fg.ClassName!r}, title: {fg.Name[:60]!r}")

obs = observe.observe(window_title="Upwork", include_unnamed=False, include_text=True)
print(f"Total elements: {len(obs.elements)}")

# Print every text element so we see the actual structure
print("\nAll text/listitem elements with their bounds, in tree order:")
for i, e in enumerate(obs.elements):
    if e.role in ("text", "listitem"):
        n = e.name.replace('\n', ' ')[:80]
        print(f"  {i:3} {e.role:9} y={e.bounds[1]:5} {n!r}")
print()

# Count "Posted" anchors
posted_anchors = [
    (i, e) for i, e in enumerate(obs.elements)
    if e.role == "text" and e.name.strip() == "Posted"
]
print(f"'Posted' anchor matches (exact): {len(posted_anchors)}")
for i, e in posted_anchors[:5]:
    print(f"  index={i} bounds={e.bounds} repr={e.name!r}")

# Look for anything containing 'Posted'
print("\nAll text elements containing 'posted':")
for i, e in enumerate(obs.elements):
    if e.role == "text" and "posted" in e.name.lower():
        print(f"  index={i} role={e.role} repr={e.name!r}")

# Save job buttons (these should still work)
print("\n'Save job ...' buttons:")
for e in obs.elements:
    if e.role == "button" and e.name.lower().startswith("save job "):
        print(f"  {e.name!r}")

# include_text status check — does the description text show up?
long_texts = [e for e in obs.elements if e.role == "text" and len(e.name) >= 200]
print(f"\nLong text elements (>=200 chars, descriptions): {len(long_texts)}")
for e in long_texts[:3]:
    print(f"  len={len(e.name)} starts={e.name[:80]!r}")
