// PM2 ecosystem entry for the Hermes DeFi Autonomy Module.
//
// Phase 0 scaffolding only: this file declares how the module would be
// supervised by PM2 in production. The script `coordinator.py` does not yet
// exist (it lands in Phase 3.7). Do NOT `pm2 start` this file in v1's
// Phase 0 — it will fail because the script is absent.
//
// Process name `Hermes-DeFi-Autonomy-Watch` is intentionally distinct from
// the existing daemons (`CeFi-Engine-Shadow`, `CeFi-Engine-Bitget-Shadow`,
// `CeFi-Structural-Shadow`, Market Sentinel) per R1.3 and tasks 8.1.
//
// v1 ships at autonomy_level = 1 (Watch_Only). No signing. No key loading.

module.exports = {
  apps: [
    {
      name: "Hermes-DeFi-Autonomy-Watch",
      script: "coordinator.py",
      interpreter: "python3",
      cwd: "/root/hermes-agent/defi_autonomy",
      args: "",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_memory_restart: "512M",
      kill_timeout: 10000,
      env: {
        PYTHONUNBUFFERED: "1",
        HERMES_DEFI_MODULE_ROOT: "/root/hermes-agent/defi_autonomy"
        // HERMES_DEFI_SANDBOX_KEY is intentionally NOT set in v1.
        // It MUST remain unset until autonomy_level >= 2 (Phase 9).
      },
      out_file: "/root/.pm2/logs/hermes-defi-autonomy-watch-out.log",
      error_file: "/root/.pm2/logs/hermes-defi-autonomy-watch-err.log",
      merge_logs: true,
      time: true
    }
  ]
};
