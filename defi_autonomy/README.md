# Hermes DeFi Autonomy Module

Phase 0 scaffolding only.

This directory will host the capital-preservation DeFi autonomy module described in `.kiro/specs/hermes-defi-autonomy/{requirements,design,tasks}.md`. v1 ships at `autonomy_level = 1` (Watch_Only): scan, score, simulate, and log only — no signing, no private-key load, no on-chain effects.

## Status (Phase 0)

- Directory layout created.
- Static config files in place (`manifesto.md`, `data/risk_policy.json`, four allowlists, six ledger/state JSON files).
- PM2 ecosystem entry declared (do **not** start it yet; `coordinator.py` does not exist until Phase 3.7).
- Dependency pins declared in `pyproject.toml` and `requirements.txt`.
- Test skeleton (`tests/unit/`, `tests/property/`, `tests/invariants/`) created.
- No production logic. No signing. No key loading.

## Cycle order (target, not yet implemented)

```
macro gate → kill switch → External_Data_Ingestion → Yield_Scanner → Risk_Scorer →
LLM_Proposer → Policy_Engine → Tx_Simulator → Wallet_Executor (disabled in v1) → ledger
```

## Kill switch

Presence of `STOP` at `kill_switch_file` (declared in `data/risk_policy.json`) halts all scanning and signing. See `KILL_SWITCH.md`.

## Production path

PM2 supervises this module from `/root/hermes-agent/defi_autonomy/` on the Hermes VPS (see `ecosystem.defi.cjs`). The development copy in this workspace mirrors that layout.
