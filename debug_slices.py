"""Print the slice between each pair of 'Posted' anchors so we can see
exactly which fields each card has and which leak across boundaries."""
from __future__ import annotations
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import observe
import uiautomation as uia

print("Focus the Upwork window in the next 3 seconds...")
time.sleep(3)
fg = uia.GetForegroundControl()
print(f"Foreground: {fg.ClassName!r} {fg.Name[:60]!r}\n")

obs = observe.observe(window_title="Upwork", include_unnamed=False, include_text=True)
els = obs.elements

posted = [i for i, e in enumerate(els) if e.role == "text" and e.name.strip() == "Posted"]
save = [(i, e.name) for i, e in enumerate(els) if e.role == "button" and e.name.lower().startswith("save job ")]

print(f"Total elements: {len(els)}")
print(f"'Posted' anchor indices: {posted}")
print(f"'Save job' button indices: {[i for i, _ in save]}")
print()

# Print each slice between consecutive Posted anchors
boundaries = posted + [len(els)]
for k in range(len(posted)):
    start, end = posted[k], boundaries[k+1]
    print(f"--- Slice {k+1}: indices [{start}, {end}) ---")
    for i in range(start, end):
        e = els[i]
        n = e.name.replace('\n', ' ')[:90]
        print(f"  {i:3} {e.role:9} y={e.bounds[1]:6} {n!r}")
    print()
