# Tech Stack

## Runtime

- **Node.js >= 18** (ES modules — `"type": "module"` in `package.json`)
- **Python 3.11–3.12** (only for the `defi_autonomy/` sub-module)
- Process supervision: **PM2** (`ecosystem.config.cjs` for the main agent, `defi_autonomy/ecosystem.defi.cjs` for the Python module)

## Core Libraries (Node)

- `@meteora-ag/dlmm` (1.9.4) — Meteora DLMM SDK (deploy, close, claim, positions, PnL)
- `@solana/web3.js` (^1.95) — Solana RPC + transaction primitives
- `bn.js`, `bs58` — bigint math and base58 keys
- `openai` (^4.73) — OpenAI-compatible client used against **OpenRouter** (and any compatible endpoint, e.g. LM Studio)
- `node-cron` — schedules screening / management cycles
- `dotenv` — `.env` loader; pairs with the in-repo envrypt flow (`envcrypt.js`, `scripts/envrypt.js`)
- `jsonrepair` — repairs malformed LLM JSON tool calls

## LLM Integration

- Default endpoint: OpenRouter. Models are configured per role: `managementModel`, `screeningModel`, `generalModel` in `user-config.json`.
- Local LLMs supported via `LLM_BASE_URL` (e.g. LM Studio at `http://localhost:1234/v1`).
- `maxOutputTokens` minimum 2048; lower limits cause empty responses with free models.

## Python Sub-module (`defi_autonomy/`)

Pinned via `pyproject.toml` and mirrored in `requirements.txt`:
- `httpx`, `requests`, `jsonschema`, `pydantic`, `ulid-py`
- `web3`, `eth-account`, `solana`, `solders`
- Test-only: `pytest`, `hypothesis` (property-based testing)

## External APIs

Meteora DLMM (on-chain + PnL API), Jupiter (token info, holders, swaps), Helius (wallet balances, RPC), OKX OnchainOS (smart-money signals), LPAgent (top-LPer study). HiveMind syncs to `https://api.agentmeridian.xyz` by default.

## Persistence

JSON files at the repo root, written through dedicated modules. Never edit them by hand while the agent is live:
- `state.json` (positions), `decision-log.json`, `lessons.json`, `pool-memory.json`, `strategy-library.json`, `smart-wallets.json`, `token-blacklist.json`, `discord-signals.json`, `deployer-blacklist.json`
- Config: `user-config.json` (gitignored), template at `user-config.example.json`
- Secrets: `.env` (gitignored), template at `.env.example`; optional encrypted `.env.raw` + `.envrypt`

## Common Commands

### Setup

```bash
npm install               # also runs scripts/patch-anchor.js via postinstall
npm run setup             # interactive wizard for .env + user-config.json
npm run env:encrypt       # encrypt .env.raw using .envrypt key
```

### Run the agent

```bash
npm run dev               # dry-run mode (DRY_RUN=true), no on-chain txs
npm start                 # live mode
npm run pm2:start         # PM2 supervision (recommended for VPS)
npm run pm2:restart       # apply env/code changes
npm run pm2:logs          # tail logs
```

### Tests

```bash
npm test                  # currently aliases test:syntax
npm run test:syntax       # node --check on every *.js (excludes node_modules)
npm run test:screen       # test/test-screening.js
npm run test:agent        # test/test-agent.js with DRY_RUN=true
```

> The Node test suite is currently a syntax check + two harness scripts. There is no Jest/Vitest framework. When adding test infrastructure, match the project's existing minimalism unless the user requests otherwise.

### Direct CLI (every tool as a subcommand)

```bash
node cli.js <command> [flags]   # or `meridian <command>` after `npm install -g .`
# Examples:
node cli.js positions
node cli.js candidates --limit 5
node cli.js deploy --pool <addr> --amount 0.5 --dry-run
node cli.js screen --dry-run --silent
node cli.js config set screeningModel anthropic/claude-opus-4-5
```

Universal flags: `--dry-run` (skip on-chain), `--silent` (suppress Telegram).

### Python sub-module

```bash
cd defi_autonomy
python -m venv .venv && .venv\Scripts\activate    # Windows cmd
pip install -e .[test]
pytest                                            # uses pyproject.toml [tool.pytest.ini_options]
```

## Conventions

- **ES modules only** in Node code. Use `import`/`export`, not CommonJS.
- **Async/await** throughout. Avoid bare `.then()` chains.
- **Tools are role-gated.** When adding a tool: define schema in `tools/definitions.js`, dispatch in `tools/executor.js`, then add to `MANAGER_TOOLS` and/or `SCREENER_TOOLS` in `agent.js`. On-chain writes must also be added to `WRITE_TOOLS` for safety checks.
- **Config mutations go through `update_config`** (executor.js) so the live `config` object, `user-config.json`, and cron jobs stay in sync. Don't write `user-config.json` directly from tool code.
- **Never put private keys or API keys in `user-config.json`.** They belong in `.env` only. Both files are gitignored; keep it that way.
- **Default to `DRY_RUN=true`** when iterating on agent behavior. Live mode requires explicit unset.
- **Windows shell:** the workspace is `cmd` on Windows. Use `;` or `&` (not `&&`) when chaining commands; prefer Kiro file/search tools over `dir`/`type`/`find`.
