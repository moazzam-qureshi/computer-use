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


def ensure_chrome_running(
    *,
    profile: str = "Moazzam",
    url: str = "https://www.upwork.com/nx/find-work/most-recent",
    title_filter: tuple[str, ...] = ("Upwork", "Google Chrome"),
    settle_seconds: float = 5.0,
) -> bool:
    """Return True if Chrome with a matching window title is already running.

    If not, kill any orphan chrome.exe processes (which would prevent the
    --force-renderer-accessibility flag from taking effect) and launch a
    fresh instance with the given profile + URL. Wait `settle_seconds` for
    the window to register in the UIA tree before returning.

    Called from the bidder loop's pre-cycle check so the operator can close
    Chrome whenever they want without breaking the next cycle.

    Returns True if Chrome is up and a matching window was found within the
    settle window, False otherwise.
    """
    # UIA imports are heavy and only work on Windows; do them lazily here so
    # this module stays usable as a CLI on machines without uiautomation set
    # up (e.g. running --list to inspect profiles from a fresh checkout).
    from substrate import observe
    import uiautomation as ua

    # Already running? Then we're done.
    win = observe.find_window(title_filter)
    if win is not None:
        return True

    print(f"[launch_chrome] no window matching {title_filter} found; relaunching", flush=True)
    # Kill any orphan chrome.exe processes first. If the UIA-titled window
    # is gone but a chrome.exe is still alive (background tab cleanup, GPU
    # process, etc.), launching a fresh Chrome would silently attach to
    # that ghost instance and the renderer-accessibility flag would be
    # ignored. The bidder substrate would then read garbage.
    killed = kill_chrome()
    if killed:
        print(f"[launch_chrome] killed {killed} orphan chrome.exe process(es)", flush=True)
        time.sleep(1.0)  # let the OS reap the handles before we relaunch

    profile_dir = resolve_profile(profile)
    print(f"[launch_chrome] launching profile={profile_dir!r} url={url!r}", flush=True)
    launch(profile_dir, url)

    # Poll the UIA tree for the window; settles ~3-5s on a healthy laptop.
    deadline = time.time() + settle_seconds
    while time.time() < deadline:
        time.sleep(0.5)
        win = observe.find_window(title_filter)
        if win is not None:
            print(f"[launch_chrome] window ready: {win.Name!r}", flush=True)
            return True

    print(f"[launch_chrome] window did NOT appear within {settle_seconds}s", flush=True)
    return False


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
