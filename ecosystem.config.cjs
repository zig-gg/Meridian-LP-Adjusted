module.exports = {
  apps: [
    {
      name: "meridian",
      script: "index.js",
      cwd: __dirname,
      interpreter: "node",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      restart_delay: 5000,
      kill_timeout: 10000,
      max_restarts: 10,
      min_uptime: "10s",
      env: {
        NODE_ENV: "production",
        // ── Scanner/dry-run daemon safety defaults ─────────────────
        // These mirror `npm run daemon` and ensure pm2:start is safe
        // even without explicit env injection from the command line.
        // ALLOW_LIVE_EXECUTION and DRY_RUN are belt-and-suspenders:
        // live execution requires both to be overridden simultaneously.
        DRY_RUN: "true",
        EXECUTION_MODE: "scanner",
        HEADLESS: "true",
        ALLOW_LIVE_EXECUTION: "false",
      },
    },
  ],
};
