// PM2 process declarations for the Upwork bidder system.
//
// Two processes:
//   1. scheduler   long-running bot + bidder + apply-executor in one process.
//                  Auto-restarts on crash. Survives terminal closure.
//                  Logs to logs/scheduler-out.log and logs/scheduler-err.log.
//
//   2. chrome      one-shot at startup: launches Chrome with the
//                  --force-renderer-accessibility flag so UIA can read the
//                  page tree. Exits after launching. PM2 keeps it as a
//                  registered "stopped" process, ready to re-run after a
//                  reboot via `pm2 resurrect`.
//
// Usage:
//   pm2 start ecosystem.config.js          one-time install
//   pm2 save                               persist for reboot
//   pm2 status                             show both processes
//   pm2 logs scheduler                     tail bidder/bot logs
//   pm2 restart scheduler                  redeploy code changes
//   pm2 stop scheduler                     stop the bidder + bot together
//   pm2 start chrome                       relaunch Chrome (after a quit)
//
// For runtime control of the bidder loop without restarting the service,
// use Discord: /bidder pause, /bidder resume, /bidder run-now, /bidder status.

const path = require("path");
const cwd = __dirname;

module.exports = {
  apps: [
    {
      name: "scheduler",
      cwd: cwd,
      // `uv run` resolves the project venv every invocation. Adds ~100ms of
      // startup overhead; not material for a long-lived process. Keeps
      // dependency resolution identical to manual `uv run` invocations so
      // there is one source of truth for the runtime environment.
      script: "uv",
      args: ["run", "python", "-m", "scheduler.main"],
      // PM2 needs to know this is not a JS script.
      interpreter: "none",
      // Restart on crash, but back off if it crashes repeatedly so we don't
      // spin-loop on an unrecoverable error (e.g. a bad migration).
      autorestart: true,
      max_restarts: 10,
      min_uptime: "30s",
      restart_delay: 4000,
      // Don't kill on memory spikes; the bidder's working set is small. If
      // we ever leak, we'll see it before PM2 does.
      max_memory_restart: "1G",
      // Logs go in the project's logs/ folder for easy `tail -f` and so
      // /bidder logs can find them without crawling ~/.pm2/.
      out_file: path.join(cwd, "logs", "scheduler-out.log"),
      error_file: path.join(cwd, "logs", "scheduler-err.log"),
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      // Pass through environment from the parent (uv reads .env via
      // python-dotenv at import time, so PM2 doesn't need to inject env).
      env: {},
    },
    {
      name: "chrome",
      cwd: cwd,
      script: "uv",
      args: [
        "run",
        "python",
        "-m",
        "substrate.launch_chrome",
        "--profile",
        "Moazzam",
        "--url",
        "https://www.upwork.com/nx/find-work/",
        "--kill-existing",
      ],
      interpreter: "none",
      // One-shot: launches Chrome and exits. PM2 records it as "stopped".
      // After reboot, `pm2 resurrect` re-runs it once.
      autorestart: false,
      out_file: path.join(cwd, "logs", "chrome-out.log"),
      error_file: path.join(cwd, "logs", "chrome-err.log"),
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {},
    },
  ],
};
