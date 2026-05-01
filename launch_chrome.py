"""
Launch Chrome with renderer accessibility enabled, into a chosen profile.

Why: the agent stack relies on the UI Automation tree exposing real web content.
Chrome only does that reliably when launched with --force-renderer-accessibility.
Programmatic wake-up via LegacyIAccessible alone is not consistent on modern Chrome.

Usage:
    uv run launch_chrome.py --list                   # show available profiles
    uv run launch_chrome.py --profile "Moazzam"      # launch with that profile
    uv run launch_chrome.py --profile "Moazzam" --url "https://github.com"
    uv run launch_chrome.py --profile "Moazzam" --kill-existing

Notes:
    - --kill-existing closes ALL running Chrome processes before launch.
      Without it, if Chrome is already running, the new launch may attach to
      the existing instance and the flag is silently ignored.
    - Profile match is case-insensitive substring on the profile's display name.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHROME_EXE_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

USER_DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"


def find_chrome_exe() -> str:
    for p in CHROME_EXE_CANDIDATES:
        if Path(p).exists():
            return p
    raise FileNotFoundError("chrome.exe not found in standard install paths")


def list_profiles() -> list[tuple[str, str]]:
    """Return list of (folder_name, display_name) for every profile."""
    out: list[tuple[str, str]] = []
    if not USER_DATA_DIR.exists():
        return out
    for folder in sorted(USER_DATA_DIR.iterdir()):
        if not folder.is_dir():
            continue
        name = folder.name
        if name != "Default" and not name.startswith("Profile "):
            continue
        prefs = folder / "Preferences"
        display = name
        if prefs.exists():
            try:
                data = json.loads(prefs.read_text(encoding="utf-8", errors="ignore"))
                display = data.get("profile", {}).get("name") or name
            except Exception:
                pass
        out.append((name, display))
    return out


def resolve_profile(needle: str) -> str:
    """Map a display-name substring to a profile folder name."""
    needle_l = needle.lower()
    candidates = list_profiles()
    for folder, display in candidates:
        if needle_l == display.lower() or needle_l == folder.lower():
            return folder
    for folder, display in candidates:
        if needle_l in display.lower():
            return folder
    raise ValueError(f"No profile matched {needle!r}. Available: {[d for _, d in candidates]}")


def kill_chrome() -> int:
    """Terminate every chrome.exe process. Returns count killed."""
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            killed = result.stdout.count("SUCCESS")
            return killed
        return 0
    except FileNotFoundError:
        print("taskkill not available; cannot kill existing Chrome.", file=sys.stderr)
        return 0


def launch(profile_dir: str, url: str | None) -> subprocess.Popen:
    chrome = find_chrome_exe()
    args = [
        chrome,
        "--force-renderer-accessibility",
        f"--profile-directory={profile_dir}",
    ]
    if url:
        args.append(url)
    return subprocess.Popen(args, close_fds=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list profiles and exit")
    ap.add_argument("--profile", help="profile display-name substring or folder name")
    ap.add_argument("--url", help="URL to open after launch")
    ap.add_argument("--kill-existing", action="store_true", help="kill running Chrome first")
    ap.add_argument("--wait", type=float, default=2.0, help="seconds to wait after launch")
    args = ap.parse_args()

    if args.list:
        for folder, display in list_profiles():
            print(f"  {folder:12}  {display}")
        return

    if not args.profile:
        ap.error("--profile is required (or --list)")

    profile_dir = resolve_profile(args.profile)
    print(f"Resolved profile -> {profile_dir!r}", file=sys.stderr)

    if args.kill_existing:
        n = kill_chrome()
        print(f"Killed {n} chrome.exe process(es).", file=sys.stderr)
        time.sleep(1.0)

    proc = launch(profile_dir, args.url)
    print(f"Launched chrome (pid {proc.pid}) with --force-renderer-accessibility", file=sys.stderr)
    time.sleep(args.wait)
    print("Ready. The renderer a11y tree will be exposed for any tab in this Chrome.", file=sys.stderr)


if __name__ == "__main__":
    main()
