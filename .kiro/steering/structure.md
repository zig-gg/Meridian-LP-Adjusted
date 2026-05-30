# Project Structure

## Top-level layout

```
meridian/
├─ index.js              # Entry: REPL + cron orchestration + Telegram polling
├─ agent.js              # ReAct loop (LLM ⇄ tool calls); defines role tool sets
├─ cli.js                # `meridian` CLI — every tool as a subcommand, JSON output
├─ config.js             # Loads user-config.json + .env into the runtime config object
├─ prompt.js             # System prompt builder per role (SCREENER / MANAGER / GENERAL)
├─ setup.js              # Interactive wizard for .env + user-config.json
├─ envcrypt.js           # Load encrypted .env values written via scripts/envrypt.js
│
├─ state.js              # Position registry (state.json): bin ranges, OOR timestamps, notes
├─ decision-log.js       # Structured decision log (decision-log.json)
├─ lessons.js            # Records perf, derives lessons, evolves thresholds (lessons.json)
├─ pool-memory.js        # Per-pool deploy history + snapshots (pool-memory.json)
├─ strategy-library.js   # Saved LP strategies (strategy-library.json)
├─ smart-wallets.js      # KOL/alpha wallet tracker (smart-wallets.json)
├─ token-blacklist.js    # Permanent token blacklist (token-blacklist.json)
├─ dev-blocklist.js      # Deployer blocklist helpers (deployer-blacklist.json)
├─ signal-tracker.js     # Discord signal tracker
├─ signal-weights.js     # Signal scoring weights
├─ briefing.js           # Daily Telegram briefing (HTML)
├─ telegram.js           # Telegram bot: polling + notifications
├─ hivemind.js           # Agent Meridian HiveMind sync
├─ logger.js             # Daily-rotating logs + action audit trail
│
├─ tools/                # LLM-callable tools (role-gated)
│  ├─ definitions.js     # OpenAI-format tool schemas (what the LLM sees)
│  ├─ executor.js        # Dispatch + safety checks + WRITE_TOOLS gating
│  ├─ dlmm.js            # Meteora DLMM SDK wrapper (deploy/close/claim/PnL)
│  ├─ screening.js       # Pool discovery (Meteora API)
│  ├─ wallet.js          # SOL/token balances (Helius) + Jupiter swap
│  ├─ token.js           # Token info / holders / narrative (Jupiter)
│  ├─ study.js           # Top-LPer study (LPAgent API)
│  ├─ chart-indicators.js
│  ├─ okx.js             # OKX OnchainOS smart-money signals
│  └─ agent-meridian.js  # Agent Meridian endpoints
│
├─ utils/
│  └─ number.js
│
├─ scripts/
│  ├─ envrypt.js         # Encrypt .env.raw with .envrypt key
│  └─ patch-anchor.js    # Runs via npm postinstall
│
├─ test/                 # Minimal Node harness scripts (no Jest/Vitest)
│  ├─ test-agent.js
│  └─ test-screening.js
│
├─ discord-listener/     # Standalone selfbot package (own package.json)
│  ├─ index.js
│  └─ pre-checks.js      # Dedup → blacklist → pool resolution → rug check → fees
│
├─ defi_autonomy/        # Python sub-module (Hermes DeFi Autonomy, Phase 0+)
│  ├─ adapters/  data/  schemas/  sources/
│  ├─ tests/             # unit/, property/, invariants/
│  ├─ pyproject.toml     # pinned deps; pytest config
│  ├─ requirements.txt   # mirrors pyproject deps
│  ├─ ecosystem.defi.cjs # PM2 entry (do not start until Phase 3.7)
│  ├─ KILL_SWITCH.md     # `STOP` file halts all scanning + signing
│  └─ manifesto.md
│
├─ .claude/
│  ├─ agents/            # screener.md, manager.md sub-agents
│  └─ commands/          # /screen, /manage, /balance, /positions, /candidates, ...
│
├─ .kiro/
│  ├─ specs/             # Feature specs (requirements.md, design.md, tasks.md)
│  └─ steering/          # This folder — product.md, tech.md, structure.md
│
├─ ecosystem.config.cjs  # PM2 entry for the main agent
├─ package.json          # type: module, bin: meridian → cli.js
└─ user-config.example.json, .env.example
```

## Where state lives

JSON files at the repo root, each owned by exactly one module. Treat them as live state — do not edit while the agent is running.

| File | Owner | Purpose |
|---|---|---|
| `state.json` | `state.js` | Open positions, bin ranges, OOR timestamps, notes |
| `decision-log.json` | `decision-log.js` | Deploy/close/skip/no-deploy rationale |
| `lessons.json` | `lessons.js` | Derived lessons + closed-position performance |
| `pool-memory.json` | `pool-memory.js` | Per-pool deploy history and snapshots |
| `strategy-library.json` | `strategy-library.js` | Saved LP strategies |
| `smart-wallets.json` | `smart-wallets.js` | Tracked KOL/alpha wallets |
| `token-blacklist.json` | `token-blacklist.js` | Permanent token-mint blacklist |
| `deployer-blacklist.json` | `dev-blocklist.js` | Rug/farm deployer wallets |
| `discord-signals.json` | `discord-listener/` + `signal-tracker.js` | Queued screening signals |
| `user-config.json` | `config.js` (read), `update_config` tool (write) | Runtime config (gitignored) |
| `.env` / `.env.raw` / `.envrypt` | `envcrypt.js` | Secrets (gitignored) |

## Agent roles & where to wire them

Three roles in `agent.js` filter which tools the LLM can call:

- `SCREENER` — finds and deploys new positions
- `MANAGER` — manages existing positions
- `GENERAL` — REPL / Telegram chat (all tools)

When adding a tool:
1. Define the schema in `tools/definitions.js`.
2. Dispatch it in `tools/executor.js` (`toolMap`).
3. Add it to `MANAGER_TOOLS` / `SCREENER_TOOLS` in `agent.js` if role-restricted.
4. If it writes on-chain state, add it to `WRITE_TOOLS` in `executor.js` so safety checks run.

## Spec workflow

Specs live under `.kiro/specs/<feature>/` and follow the standard trio:

```
.kiro/specs/<feature>/
├─ requirements.md
├─ design.md
└─ tasks.md
```

Active specs in this repo:
- `hermes-defi-autonomy/` — drives the Python `defi_autonomy/` sub-module.

## Naming & code-organization conventions

- **One concern per file** at the repo root (e.g. `lessons.js` owns lessons + persistence).
- **Tools** are kebab-case file names under `tools/`, but tool names (the strings the LLM calls) are `snake_case`.
- **State files** are snake-case JSON at the repo root, mirroring the module name (`pool-memory.js` ↔ `pool-memory.json`).
- **ES modules only** in Node code. Use `import`/`export`. The Python sub-module is the only place you'll see a different module system.
- **Do not introduce a new persistence file from inline code** — add a dedicated module that owns read/write, like the existing pattern.
