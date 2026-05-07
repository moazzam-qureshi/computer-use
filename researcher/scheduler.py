"""Researcher scheduler: daily 5pm local autonomous pass.

Async task spawned by scheduler.main alongside bidder, apply_executor,
and brief_watcher. Mirrors the brief_watcher pattern — runs forever,
fires once per day, errors in the loop body never kill the loop.

Sequence per fire:
1. Acquire ui_lock (substrate access — competes with bidder/apply)
2. Run run_researcher_pass in a worker thread (UIA needs COM init)
3. Release ui_lock
4. Run process_new_findings (LLM + Discord; no substrate, no lock)
5. Persist last-fire timestamp to system_config

Catch-up behavior: on startup, if last fire was >36h ago (or never),
fire immediately. After that, fire daily at the configured local hour.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional

from researcher.loop import run_researcher_pass
from researcher.nudge import process_new_findings
from storage.connection import Database
from storage.conversations import SystemConfigStore


_log = logging.getLogger(__name__)
_LAST_FIRE_KEY = "researcher_last_pass_at"
_CATCHUP_HOURS = 36
_DEFAULT_FIRE_HOUR_LOCAL = 17  # 5pm local


def _fire_hour_local() -> int:
    """Configurable via env so the operator can shift it without a redeploy."""
    raw = os.environ.get("RESEARCHER_FIRE_HOUR_LOCAL", str(_DEFAULT_FIRE_HOUR_LOCAL))
    try:
        h = int(raw)
        if 0 <= h <= 23:
            return h
    except (ValueError, TypeError):
        pass
    return _DEFAULT_FIRE_HOUR_LOCAL


def _next_fire_at(now_local: datetime, hour_local: int) -> datetime:
    """Next datetime at `hour_local` local time. If we've already passed
    that hour today, schedule tomorrow."""
    target_today = now_local.replace(
        hour=hour_local, minute=0, second=0, microsecond=0,
    )
    if now_local >= target_today:
        return target_today + timedelta(days=1)
    return target_today


def _last_fire(sysconfig: SystemConfigStore) -> Optional[datetime]:
    raw = sysconfig.get(_LAST_FIRE_KEY)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _should_catchup_on_startup(sysconfig: SystemConfigStore) -> bool:
    last = _last_fire(sysconfig)
    if last is None:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(hours=_CATCHUP_HOURS)


def _with_com_run(db: Database, max_jobs_per_query: int):
    """Run inside a UIAutomation-initialized worker thread."""
    import uiautomation as ua
    with ua.UIAutomationInitializerInThread():
        return run_researcher_pass(
            db, max_jobs_per_query=max_jobs_per_query,
        )


async def _fire_one(
    bot, settings, db: Database, ui_lock: asyncio.Lock,
    *, max_jobs_per_query: int = 30,
) -> dict:
    """Fire one Researcher pass + nudge dispatch. Returns the pass summary
    (with an extra 'nudge' key holding the nudge result)."""
    print("[researcher-sched] starting pass", flush=True)
    # Pick up operator-tunable pacing budget before the substrate-heavy
    # pass starts.
    from substrate import pacing as _pacing
    _pacing.apply_from_sysconfig(SystemConfigStore(db))
    async with ui_lock:
        summary = await asyncio.to_thread(
            _with_com_run, db, max_jobs_per_query,
        )
    print(
        f"[researcher-sched] pass complete: "
        f"queries_run={summary.get('queries_run', 0)} "
        f"queries_failed={summary.get('queries_failed', 0)} "
        f"jobs_ingested={summary.get('jobs_ingested', 0)} "
        f"findings_persisted={summary.get('findings_persisted', 0)}",
        flush=True,
    )

    # Nudge dispatch — no ui_lock needed (LLM + Discord, no substrate).
    async def _send(text: str) -> None:
        user = await bot.fetch_user(settings.discord_owner_user_id)
        await user.send(text)

    nudge_result = await process_new_findings(db, _send)
    summary["nudge"] = nudge_result.to_dict()
    print(
        f"[researcher-sched] nudge dispatch: "
        f"immediate_sent={nudge_result.immediate_sent} "
        f"digest_sent={nudge_result.digest_sent} "
        f"errors={len(nudge_result.errors)}",
        flush=True,
    )
    return summary


async def run_researcher_scheduler(
    bot, settings, db: Database, ui_lock: asyncio.Lock,
) -> None:
    """Forever-loop scheduler. Wait for bot ready, then fire daily at the
    configured local hour. Errors in any pass never kill the loop.
    """
    await bot.wait_until_ready()
    sysconfig = SystemConfigStore(db)
    fire_hour = _fire_hour_local()
    print(
        f"[researcher-sched] started; will fire daily at {fire_hour:02d}:00 local",
        flush=True,
    )

    if _should_catchup_on_startup(sysconfig):
        print("[researcher-sched] last pass was >36h ago (or never); "
              "firing catch-up now", flush=True)
        try:
            await _fire_one(bot, settings, db, ui_lock)
            sysconfig.set(_LAST_FIRE_KEY, datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001 — never let scheduler die
            _log.exception("researcher pass failed (catchup)")
            try:
                user = await bot.fetch_user(settings.discord_owner_user_id)
                await user.send(
                    f"Researcher pass failed (catch-up): {type(e).__name__}: {e}"
                )
            except Exception:
                pass

    while True:
        # Compute next fire-at in local time. Use naive local to anchor on the
        # OS clock the operator sees (no TZ confusion across systems).
        now_local = datetime.now()
        next_at = _next_fire_at(now_local, fire_hour)
        wait_s = (next_at - now_local).total_seconds()
        print(
            f"[researcher-sched] next pass at {next_at.isoformat()} "
            f"(in {wait_s/3600:.1f}h)",
            flush=True,
        )
        try:
            await asyncio.sleep(wait_s)
        except asyncio.CancelledError:
            raise

        try:
            await _fire_one(bot, settings, db, ui_lock)
            sysconfig.set(_LAST_FIRE_KEY, datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001
            _log.exception("researcher pass failed")
            try:
                user = await bot.fetch_user(settings.discord_owner_user_id)
                await user.send(
                    f"Researcher pass failed: {type(e).__name__}: {e}"
                )
            except Exception:
                pass
            # Even on failure, advance the clock to avoid retry storm.
            sysconfig.set(_LAST_FIRE_KEY, datetime.now(timezone.utc).isoformat())
