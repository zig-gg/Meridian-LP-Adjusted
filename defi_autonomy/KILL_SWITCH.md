# Kill Switch Convention (Phase 0.7)

The kill switch is a **presence-only** filesystem flag. The path is declared in
`data/risk_policy.json` under `kill_switch_file`:

```
/root/hermes-agent/defi_autonomy/STOP
```

## Behavior

- WHILE the file at `kill_switch_file` exists, the Wallet_Executor MUST refuse
  all signing requests (R14.1) and the Coordinator MUST skip Yield_Scanner,
  Risk_Scorer, and LLM_Proposer invocations (R14.2).
- WHEN the file transitions from existing to absent, the Coordinator MUST log a
  `KILL_SWITCH_CLEARED` record before the next cycle (R14.4).
- The Telegram_Guardian MAY create the file via the `HALT` command and MAY
  delete it via the `RESUME` command (subject to R13.5: refusal if a policy
  violation occurred in the trailing 24 hours).

## Phase 0 invariant

The `STOP` file is **not created** as part of Phase 0 scaffolding. The
Wallet_Executor will be implemented as a stub in Phase 5 and will refuse to
sign regardless (autonomy_level = 1 raises `AutonomyLevelTooLow`); the kill
switch is the secondary gate that becomes load-bearing at autonomy_level >= 2.

## Operator usage

Create:    `touch /root/hermes-agent/defi_autonomy/STOP`
Remove:    `rm /root/hermes-agent/defi_autonomy/STOP`

Either action is logged in the next cycle's ledger record.
