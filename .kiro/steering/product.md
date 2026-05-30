# Product

**Meridian** is an autonomous Meteora DLMM liquidity provider agent for Solana, powered by LLMs.

It runs continuous screening and management cycles to deploy capital into high-quality Meteora DLMM pools and close positions based on live PnL, yield, and range data. The agent learns from every closed position.

## Core Capabilities

- **Pool screening** — scans Meteora DLMM pools against configurable thresholds (fee/TVL ratio, organic score, holders, mcap, bin step) and surfaces high-quality opportunities.
- **Position management** — monitors, claims fees, and closes LP positions; decides STAY, CLOSE, or REDEPLOY.
- **Learning loop** — studies top LPers, saves structured lessons, and evolves screening thresholds from closed-position history.
- **Discord signals** — optional selfbot listener watches LP Army channels for Solana token calls and queues them as priority screening candidates.
- **Telegram chat & alerts** — full agent chat, cycle reports, OOR alerts, and command/control (`/positions`, `/close`, `/set`).
- **Claude Code integration** — slash commands (`/screen`, `/manage`, `/balance`, etc.) and sub-agents (`screener`, `manager`).

## Agent Architecture

A **ReAct loop** wraps every autonomous cycle. Two specialized agents run on independent cron schedules:

| Agent | Default interval | Role |
|---|---|---|
| **Screening Agent** | 30 min | Find and deploy into the best candidate pool |
| **Management Agent** | 10 min | Evaluate each open position and act |

A third **GENERAL** role serves the REPL/Telegram chat and exposes all tools.

## Sub-modules

- **`defi_autonomy/`** — separate Python "Hermes DeFi Autonomy" capital-preservation module (Phase 0 scaffold). Watch-only, no signing, no on-chain effects in v1. Tracked via a dedicated spec under `.kiro/specs/hermes-defi-autonomy/`.

## Risk Posture

This is autonomous trading software with real financial risk. Always default to `DRY_RUN=true` for behavior verification. Never load production keys into example configs. Private keys live only in `.env`, never in `user-config.json`.
