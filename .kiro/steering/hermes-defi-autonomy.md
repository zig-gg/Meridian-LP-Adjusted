# Hermes DeFi Autonomy Module — Steering

## Strategic Target

The Hermes DeFi Autonomy Module is an agentic Web3 swarm that can autonomously sign transactions, access an operator-funded wallet, execute selected DeFi/yield actions, and learn from outcomes. Autonomy is bounded by hard deterministic risk controls at every layer.

## Terminology

- **Operator-funded agent wallet**: A dedicated wallet created and controlled by the operator. The operator manually injects funds. The agent may eventually sign from this specific wallet. Maximum loss is bounded by the amount injected and by `risk_policy.json` caps.
- Do NOT use "sandbox wallet" — use "operator-funded agent wallet" everywhere.

## Signing Doctrine

Autonomous signing is allowed only when ALL of the following are enforced:

- Operator-funded agent wallet only
- Hard `risk_policy.json` caps
- Contract allowlist
- Token allowlist
- Pool allowlist
- Chain allowlist
- Transaction simulation (must pass before signing)
- Kill switch (STOP file halts all signing immediately)
- Macro gate (market-wide circuit breaker)
- Exact-amount approvals only (no unlimited approvals)

### Explicitly Forbidden

- No bridge automation unless explicitly approved in a later spec
- No borrowing unless explicitly approved in a later spec
- No leverage unless explicitly approved in a later spec
- No CEX credentials
- No access to other wallets
- No seed phrase storage
- No private-key leakage into prompts, logs, or ledgers
- No self-modification of risk caps, allowlists, autonomy level, blocked actions, wallet limits, or private-key handling

## Learning Doctrine

### The AI MAY learn from

- Bad FARM/WATCH/SKIP calls
- APR decay
- Gas cost errors
- Slippage errors
- Failed simulations
- Realized PnL
- Impermanent loss estimation errors
- Bad source reliability
- Bad pool quality assumptions

### The AI may NOT modify

- `max_wallet_value_usd`
- `max_tx_usd`
- `max_daily_spend_usd`
- `max_open_positions`
- Allowed chains
- Allowed tokens
- Allowed contracts
- Allowed pools
- Blocked actions
- `autonomy_level`
- Private-key path
- Kill switch
- `policy_engine` rules

## Compressed Sprint Roadmap

### Sprint 1: Data + Scanner Foundation

- NormalizedCandidate schema
- SourceAdapter / ReadOnlyHttpClient
- DeFiLlama adapter
- stablecoin_benchmark_adapter
- External_Data_Ingestion orchestrator
- Minimal source_health / normalized_yield_candidates output

### Sprint 2: Risk Engine + Policy Engine

- RiskScorer
- LearningMemory
- ActionDescriptor
- ApprovalToken
- PolicyEngine
- Append-only ExecutionLedger
- Allowlist digest checking
- FARM/WATCH/SKIP scoring

### Sprint 3: Wallet Executor for Operator-Funded Agent Wallet

- TxSimulator
- WalletExecutor
- Operator-funded agent wallet address check
- Exact-amount approval logic
- Simulation-required signing
- Kill-switch refusal
- Macro-gate refusal
- Max transaction cap
- Daily spend cap
- No bridge/borrow/leverage/unlimited approvals
- Level 2: human-approved signing first
- Level 3: capped autonomous signing later

### Sprint 4: Agentic Swarm Layer

- Coordinator Agent
- Scanner Agent
- Risk Agent
- Execution Agent
- Monitor Agent
- Learning Agent
- Telegram Guardian
- Cycle reports
- Emergency HALT/RESUME

### Sprint 5: Strategy Expansion

- xStocks adapter
- Meteora adapter
- xStocks points/yield tracking
- Pieverse/x402 sandbox
- AWS auxiliary backups/reports/dashboard

## Implementation Status

- Phase 0: complete
- Phase 1.1 (NormalizedCandidate): complete
- Phase 1.2 (SourceAdapter / ReadOnlyHttpClient): complete
- Phase 1.2b (compatibility patch): complete
- Phase 1.3 (DeFiLlama adapter): complete
- Phase 1.4 (stablecoin_benchmark_adapter): complete
- Phase 1.5 (External_Data_Ingestion orchestrator): complete
- Phase 2.1 (RiskScorer): complete
- Phase 2.2 (PolicyEngine): complete
- Phase 3.1 (TxSimulator): complete
- Phase 3.2A (WalletExecutor signing core): complete
- Phase 4.1 (Coordinator): complete
- Phase 4.2 (Telegram Guardian): complete
- Phase 4.3 (Integration test + dry-run harness): complete
- Phase 5.1 (xStocks adapter): complete
- Phase 5.1b (xStocks dynamic discovery): complete
- Phase 5.2 (Meteora adapter): complete
- Phase 5.3 (Registry wiring — all 4 adapters): complete
- Phase 5.4A (Provenance + Dynamic OutcomeEvent schema): complete
- Phase 5.4B (LearningMemory): complete
- Phase 5.4C (Coordinator + LearningMemory integration): complete
- Phase 5.4D (Outcome Event Generation + Pre-Broadcast Hardening): complete
- Phase 3.2B (BroadcastExecutor + Level 2 Telegram approval): complete
- Phase 3.2C (Position Lifecycle + broadcast ledger fix): complete
- Phase 8 (AWS Dry PM2 Observation): active
- Pre-expiry hardening (deps, deploy scripts, docs, agentic scaffold): complete
- Next: Live-readiness audit → Real RPC provider → PM2 deployment

## Implementation Priority (after Phase 1.4)

1. External_Data_Ingestion orchestrator
2. RiskScorer
3. PolicyEngine
4. TxSimulator
5. WalletExecutor for operator-funded agent wallet
6. Coordinator
7. Telegram Guardian

## Safety Invariants

- Default to `DRY_RUN=true` for all behavior verification.
- No signing code until PolicyEngine and TxSimulator exist and tests pass.
- No unrestricted autonomy at any phase.
- Existing Hermes daemons (`ecosystem.defi.cjs`) must not be modified or started until explicitly authorized.
- KILL_SWITCH.md `STOP` file halts all scanning and signing immediately.
