# PM2 Deployment

The bidder runs as a long-lived PM2 process so it survives terminal closure
and auto-restarts on crash. Chrome (with renderer-accessibility) is a
one-shot PM2 process that runs at login.

## Prerequisites

- Windows 10/11 with your user logged in. The bidder cannot run as a
  service-level account because UIA + SendInput require an interactive
  desktop session.
- Node.js + PM2 installed globally:
  ```
  npm install -g pm2 pm2-windows-startup
  ```
- Postgres running in Docker (`docker compose up -d postgres`).
- `.env` populated with OPENAI / COMPOSIO / DISCORD keys (same file the
  manual `uv run` invocations read).

## One-time install

From the project root (`d:\Personal\Projects\computer-use`):

```powershell
# 1. Start the two PM2 processes defined in ecosystem.config.js.
pm2 start ecosystem.config.js

# 2. Save the current process list. Without this, pm2 resurrect after a
#    reboot will start nothing.
pm2 save

# 3. Register PM2 itself with Windows so it auto-starts at login.
#    pm2-windows-startup is the shim that does this; it adds a Windows
#    Startup entry that runs `pm2 resurrect` on login.
pm2-startup install
```

After that, the scheduler is running, Chrome was launched, and the system
will come back up automatically the next time you log in.

## Day-to-day operations

```powershell
pm2 status              # show both processes (scheduler running, chrome stopped after one-shot)
pm2 logs scheduler      # tail bidder + bot logs
pm2 logs scheduler --lines 200    # last 200 lines
pm2 restart scheduler   # redeploy code changes (full process restart)
pm2 stop scheduler      # stop bot + bidder together
pm2 start scheduler     # start it back up
pm2 start chrome        # relaunch Chrome if it died or you closed it
```

The Discord-side controls run *inside* the scheduler process and don't
require PM2 actions:

- `/bidder status`   current state, last cycle, connects budget
- `/bidder pause`    pause the bidder loop, bot keeps responding
- `/bidder resume`   resume cycling
- `/bidder run-now`  fire a cycle immediately, bypass diurnal envelope
- `/bidder logs N`   tail last N lines of logs from inside Discord

Use PM2 controls for code changes / hard restarts. Use Discord controls
for runtime ops without taking the bot offline.

## After a reboot

1. Log in to Windows.
2. PM2 should auto-resurrect. Verify:
   ```
   pm2 status
   ```
   You should see `scheduler` in `online` state.
3. Chrome should have launched. If `pm2 status` shows it as `stopped`
   (expected) but no Chrome window opened, run:
   ```
   pm2 start chrome
   ```
4. Sanity-check via Discord: `/bidder status`.

## When something goes wrong

**Scheduler keeps restarting (high restart count in `pm2 status`).**
Check `pm2 logs scheduler --err`. Likely causes: bad migration, missing
.env key, Postgres unreachable. After fixing, `pm2 restart scheduler`.

**Chrome didn't launch on reboot.**
`pm2 start chrome` to run the one-shot again. If it keeps failing, run
`uv run python launch_chrome.py --profile Moazzam --url https://www.upwork.com/nx/find-work/ --kill-existing`
manually to see the error.

**Bot disconnected from Discord.** discord.py auto-reconnects. If it
doesn't, `pm2 restart scheduler`.

**Made code changes.** `pm2 restart scheduler` picks up the new code.
The bidder loop reinitializes from a clean state; in-flight cycle is
killed mid-action (don't do this during an apply-executor run).

**Want to take the system offline.** `pm2 stop scheduler chrome`. To
bring it back: `pm2 start scheduler chrome` or `pm2 resurrect`.

**Disable PM2 auto-startup.** `pm2-startup uninstall`. Then `pm2 kill`
to terminate the daemon completely.

## Notes

- The `chrome` PM2 process exits after launching Chrome (it's a one-shot).
  This is intentional. PM2 status will show it as `stopped` after the
  initial run, which is correct.
- `pm2 save` writes the current process list to `~/.pm2/dump.pm2`.
  Re-run `pm2 save` after any `pm2 start` / `pm2 stop` you want to
  persist across reboots.
- Logs are in `logs/scheduler-out.log` and `logs/scheduler-err.log`.
  The `logs/` directory is gitignored.
- For long-running deployments, install `pm2-logrotate`:
  ```
  pm2 install pm2-logrotate
  pm2 set pm2-logrotate:max_size 50M
  pm2 set pm2-logrotate:retain 10
  ```
  Without rotation, the bidder's verbose debug prints will eventually
  fill the disk.
