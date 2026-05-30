# Requirements Document

## Introduction

The Hermes DeFi Autonomy Module is a new, isolated subsystem added to the existing Hermes trading swarm at `/root/hermes-agent/` under the directory `/root/hermes-agent/defi_autonomy/`. The module enables an AI agentic swarm to scan, score, simulate, and (eventually) sign on-chain DeFi transactions from a strictly capped sandbox wallet, without touching the operator's main wallet or any centralized exchange account.

The module is designed for a small-capital operator running a single Linux VPS ("Hermes node") under PM2. Capital preservation is the primary design goal: downside is bounded by hard, code-enforced caps, and any AI-generated proposal must pass a deterministic policy gate before a private key signs anything.

The module operates as a staged autonomy ladder (Level 1 → Level 4). Level 5 (unrestricted agent wallet) is forbidden by construction and MUST be unreachable from any code path. The LLM may propose actions; the policy engine decides; the wallet executor signs only on a policy pass. Learning memory may bias scoring/ranking but MUST NOT modify risk caps, allowlists, or policy.

This module integrates alongside (not in place of) the existing Hermes daemons (`binance_daemon.py`, `bitget_spot_daemon.py`, `structural_hunter.py`, `sentinel_fanout.py`, Overseer). It reuses the existing `macro_state.json` Risk-Off/HALT gate and Telegram control surface.

This spec is for capital-preservation autonomy, not a dashboard, not a demo. The success criterion is verifiable safety plus measurable yield from a $10–$25 starting sandbox wallet.

## Glossary

- **Hermes_Node**: The single Linux VPS at `/root/hermes-agent/` running the existing Hermes trading swarm under PM2.
- **Autonomy_Module**: The new subsystem under `/root/hermes-agent/defi_autonomy/`.
- **Coordinator**: `coordinator.py`. Orchestrates one autonomy cycle: scan → score → propose → policy-check → simulate → execute → log.
- **Policy_Engine**: `policy_engine.py`. Deterministic, non-LLM gate that approves or rejects every proposed action against `risk_policy.json` and allowlists. Sole authority on whether a transaction may be signed.
- **Wallet_Executor**: `wallet_executor.py`. The only component that holds and uses the sandbox wallet private key and the only component that may produce a signed transaction.
- **Yield_Scanner**: `yield_scanner.py`. Discovers candidate yield opportunities on allowlisted chains and pools.
- **Risk_Scorer**: `risk_scorer.py`. Assigns a numeric risk score to each candidate; output is advisory input to the Coordinator and Policy_Engine.
- **Tx_Simulator**: `tx_simulator.py`. Performs a dry-run/simulation of a proposed transaction (e.g., eth_call, RPC simulation, or fork) before signing.
- **Learning_Memory**: `learning_memory.py`. Append-only structured store of outcomes that biases future scoring/ranking only.
- **Telegram_Guardian**: `telegram_guardian.py`. Telegram surface for approvals, kill commands, and status.
- **LLM_Proposer**: Any LLM-backed component (called via OpenRouter or equivalent) producing candidate actions or rankings. Output is advisory and never bypasses Policy_Engine.
- **Sandbox_Wallet**: A dedicated, value-capped EOA wallet whose private key is used only by Wallet_Executor. Has no relationship to the operator's main wallet.
- **Main_Wallet**: The operator's personal, full-value wallet. The Autonomy_Module MUST have no access to it under any condition.
- **Macro_State**: Existing `macro_state.json` regime file produced by `sentinel_fanout.py`. Values include at least `Risk-Off` and `HALT`.
- **Kill_Switch_File**: A filesystem flag at `/root/hermes-agent/defi_autonomy/STOP`. When present, Autonomy_Module MUST not sign or execute.
- **Risk_Policy**: `data/risk_policy.json`. Declarative caps and switches consumed by Policy_Engine.
- **Contract_Allowlist**: `data/contract_allowlist.json`. Set of contract addresses that may be interacted with.
- **Token_Allowlist**: `data/token_allowlist.json`. Set of token addresses that may be touched.
- **Pool_Allowlist**: `data/pool_allowlist.json`. Set of liquidity pools that may be entered.
- **Positions_Ledger**: `data/positions.json`. Current open positions held by Sandbox_Wallet.
- **Execution_Ledger**: `data/execution_ledger.json`. Append-only history of every proposed, simulated, approved, rejected, signed, and confirmed action.
- **Lessons_Learned**: `data/lessons_learned.json`. Append-only structured outcomes used by Learning_Memory.
- **Manifesto**: `manifesto.md`. The constitutional document re-injected to the LLM each cycle alongside Risk_Policy.
- **Autonomy_Level**: An integer 1–4 representing current stage on the ladder. Level 5 is forbidden.
- **Watch_Only**: Autonomy_Level = 1. Scan and log only; no signing.
- **Human_Approved**: Autonomy_Level = 2. Telegram approval required per transaction.
- **Capped_Autonomy**: Autonomy_Level = 3. Auto-execution within Risk_Policy caps.
- **Whitelisted_Farming**: Autonomy_Level = 4. Auto-farming on allowlisted protocols only, still under Risk_Policy.
- **Dry_Run**: A simulation execution that produces no on-chain state change.
- **xStocks**: Tokenized equity protocol referenced at https://docs.xstocks.fi/docs and https://defi.xstocks.fi/.
- **Pieverse_x402**: Agent payment rail referenced at https://docs.pieverse.io/getting-started.
- **Meridian_Framework**: Referenced harness at https://github.com/yunus-0x/meridian and https://agentmeridian.xyz/hivemind.html.

## Requirements

### Requirement 1: Module Isolation and Coexistence

**User Story:** As a small-capital operator, I want the new Autonomy_Module to live in its own directory and never modify existing Hermes daemons, so that capital preservation guarantees of the existing swarm are not regressed.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL reside entirely under `/root/hermes-agent/defi_autonomy/`.
2. THE Autonomy_Module SHALL NOT modify, import-mutate, or write to any file outside `/root/hermes-agent/defi_autonomy/` except for read-only consumption of `macro_state.json`.
3. THE Autonomy_Module SHALL run as a separate PM2 process distinct from `CeFi-Engine-Shadow`, `CeFi-Engine-Bitget-Shadow`, `CeFi-Structural-Shadow`, and Market Sentinel processes.
4. IF the Autonomy_Module process crashes or is stopped, THEN THE existing Hermes daemons SHALL continue operating without dependency on the Autonomy_Module.
5. THE Autonomy_Module SHALL NOT read or write any credential, key, or session associated with Binance, Bitget, or any centralized exchange.

### Requirement 2: Wallet Isolation and Key Handling

**User Story:** As an operator, I want Sandbox_Wallet to be the only wallet the agent can ever touch, so that catastrophic loss is bounded by sandbox value.

#### Acceptance Criteria

1. THE Wallet_Executor SHALL be the only component within the Autonomy_Module that loads or references the Sandbox_Wallet private key.
2. THE Wallet_Executor SHALL load the Sandbox_Wallet private key from an environment variable or an encrypted at-rest secrets file, and SHALL NOT load it from any plaintext file checked into version control.
3. THE Autonomy_Module SHALL NOT contain, read, derive, or transmit any seed phrase, mnemonic, or key material associated with Main_Wallet.
4. IF any component other than Wallet_Executor attempts to access the Sandbox_Wallet private key, THEN THE Wallet_Executor SHALL refuse and the Coordinator SHALL log a `KEY_ACCESS_VIOLATION` entry to Execution_Ledger.
5. THE Wallet_Executor SHALL NOT log, print, transmit, or persist the Sandbox_Wallet private key in any output, error message, or telemetry.
6. THE Autonomy_Module SHALL provide a documented, manual-only key rotation procedure that replaces the Sandbox_Wallet without modifying any other component.
7. WHEN the Autonomy_Module starts, THE Wallet_Executor SHALL verify that the loaded wallet address matches a value declared in Risk_Policy under `sandbox_wallet_address`, and IF the addresses do not match, THEN THE Wallet_Executor SHALL refuse to sign any transaction.

### Requirement 3: Propose / Decide / Sign Separation

**User Story:** As an operator, I want a hard architectural separation between LLM proposals, deterministic policy decisions, and signing, so that no LLM output can ever cause a transaction to be signed.

#### Acceptance Criteria

1. THE LLM_Proposer SHALL produce only structured proposals (candidate action descriptors) and SHALL NOT call Wallet_Executor directly.
2. THE Coordinator SHALL submit every LLM proposal to Policy_Engine before any simulation or signing.
3. THE Wallet_Executor SHALL refuse to sign any transaction that is not accompanied by a Policy_Engine approval token produced in the current cycle.
4. THE Policy_Engine SHALL be implemented as deterministic, non-LLM code and SHALL NOT call any LLM endpoint.
5. IF an LLM proposal contains instructions that attempt to modify Risk_Policy, allowlists, or autonomy level, THEN THE Policy_Engine SHALL reject the proposal and log `POLICY_INJECTION_ATTEMPT` to Execution_Ledger.
6. THE Policy_Engine SHALL produce a single-use approval token bound to a specific action descriptor hash, and THE Wallet_Executor SHALL reject any token whose bound hash does not match the transaction it is asked to sign.

### Requirement 4: Risk Policy Enforcement

**User Story:** As an operator, I want every cap from `risk_policy.json` enforced in code, so that LLM hallucinations or prompt injection cannot exceed risk limits.

#### Acceptance Criteria

1. THE Policy_Engine SHALL load Risk_Policy from `data/risk_policy.json` at the start of every cycle.
2. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF its post-execution wallet value would exceed `max_wallet_value_usd`.
3. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF its notional value exceeds `max_tx_usd`.
4. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF the cumulative spend in the current UTC day would exceed `max_daily_spend_usd`.
5. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF accepting it would cause the count of open positions to exceed `max_open_positions`.
6. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF the resulting stable-asset reserve would fall below `min_stable_reserve_pct` of total wallet value.
7. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF the chain is not contained in `allowed_chains`.
8. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF its strategy type is not contained in `allowed_strategy_types`.
9. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF its action category is contained in `blocked_actions`.
10. WHEN evaluating a proposed action, THE Policy_Engine SHALL reject the action IF its declared slippage tolerance exceeds `max_slippage_bps`.
11. WHERE `require_contract_allowlist` is true, THE Policy_Engine SHALL reject the action IF any contract address it touches is absent from Contract_Allowlist.
12. WHERE `require_token_allowlist` is true, THE Policy_Engine SHALL reject the action IF any token address it touches is absent from Token_Allowlist.
13. WHERE `require_pool_allowlist` is true, THE Policy_Engine SHALL reject the action IF any pool it enters is absent from Pool_Allowlist.
14. WHERE `require_tx_simulation` is true, THE Policy_Engine SHALL reject the action IF Tx_Simulator has not produced a successful Dry_Run for the action descriptor in the current cycle.
15. THE Policy_Engine SHALL emit a structured rejection record to Execution_Ledger for every rejected action including the rule identifier that caused rejection.

### Requirement 5: Absolute Prohibitions as Construction Invariants

**User Story:** As an operator, I want certain capabilities to be impossible by construction rather than merely disabled by config, so that no configuration error can unlock them.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL NOT contain any code path that reads, derives, or signs from Main_Wallet.
2. THE Autonomy_Module SHALL NOT contain any code path that authenticates against Binance, Bitget, or any centralized exchange.
3. THE Autonomy_Module SHALL NOT contain any code path that persists or transmits a seed phrase or mnemonic.
4. THE Autonomy_Module SHALL NOT contain any code path that initiates a cross-chain bridge transaction.
5. THE Autonomy_Module SHALL NOT contain any code path that initiates a borrowing or leverage operation.
6. WHEN the Wallet_Executor constructs an ERC-20 approval transaction, THE Wallet_Executor SHALL set the approval amount to the exact amount required for the immediately following action, and SHALL NOT use `type(uint256).max` or any unbounded approval.
7. THE Autonomy_Module SHALL NOT expose Autonomy_Level = 5 as a settable value, and the source code SHALL reject any configuration whose autonomy_level is greater than 4.
8. IF static analysis or runtime checks detect any of the prohibited capabilities listed in 5.1–5.7, THEN THE Coordinator SHALL refuse to start the cycle and SHALL log `INVARIANT_VIOLATION`.

### Requirement 6: Autonomy Ladder State Machine

**User Story:** As an operator, I want a staged autonomy ladder with explicit transitions, so that I can graduate the agent only after measurable safe operation.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL support exactly the autonomy levels Watch_Only (1), Human_Approved (2), Capped_Autonomy (3), and Whitelisted_Farming (4).
2. WHILE Autonomy_Level is Watch_Only, THE Coordinator SHALL produce candidate actions, score them, and log them, and THE Wallet_Executor SHALL NOT sign any transaction.
3. WHILE Autonomy_Level is Human_Approved, THE Wallet_Executor SHALL sign a transaction only after Telegram_Guardian receives an explicit approval from an allowlisted Telegram user for that specific action descriptor hash within an approval timeout window defined in Risk_Policy.
4. WHILE Autonomy_Level is Capped_Autonomy, THE Wallet_Executor SHALL sign transactions that pass Policy_Engine without per-transaction human approval, subject to all caps in Requirement 4.
5. WHILE Autonomy_Level is Whitelisted_Farming, THE Coordinator SHALL restrict candidate actions to strategies whose protocol is contained in Pool_Allowlist with a `farming_enabled: true` attribute.
6. THE Autonomy_Level SHALL be set by an operator-only manual edit to Risk_Policy, and the Autonomy_Module SHALL NOT modify Autonomy_Level programmatically.
7. WHEN Autonomy_Level is increased, THE Coordinator SHALL log `LEVEL_PROMOTION` to Execution_Ledger including the previous level, new level, and timestamp.

### Requirement 7: Graduation Criteria Between Levels

**User Story:** As an operator, I want quantitative criteria for promoting the agent from one level to the next, so that promotion is evidence-based.

#### Acceptance Criteria

1. THE Coordinator SHALL compute and persist to Execution_Ledger, on every cycle, a `graduation_metrics` record containing at least: cumulative successful cycles, consecutive policy passes, count of policy violations in trailing 7 days, cumulative simulated PnL, and cumulative realized PnL.
2. WHILE Autonomy_Level is Watch_Only, THE Coordinator SHALL emit a `READY_FOR_LEVEL_2` advisory IF cumulative successful cycles is at least 100 AND policy violations in trailing 7 days equals 0.
3. WHILE Autonomy_Level is Human_Approved, THE Coordinator SHALL emit a `READY_FOR_LEVEL_3` advisory IF the count of human-approved transactions confirmed on-chain is at least 20 AND policy violations in trailing 14 days equals 0 AND cumulative realized PnL is non-negative.
4. WHILE Autonomy_Level is Capped_Autonomy, THE Coordinator SHALL emit a `READY_FOR_LEVEL_4` advisory IF Capped_Autonomy has been active for at least 30 days AND policy violations in trailing 30 days equals 0 AND cumulative realized PnL is non-negative AND maximum drawdown over trailing 30 days is no more than 5 percent of peak wallet value.
5. THE Coordinator SHALL NOT alter Autonomy_Level when emitting any `READY_FOR_LEVEL_*` advisory.

### Requirement 8: Coordinator Cycle Behavior

**User Story:** As an operator, I want the Coordinator to run a deterministic cycle, so that each iteration is auditable.

#### Acceptance Criteria

1. WHEN a cycle begins, THE Coordinator SHALL load Macro_State from `/root/hermes-agent/macro_state.json`.
2. IF Macro_State indicates `Risk-Off` or `HALT`, THEN THE Coordinator SHALL skip scanning, skip signing, and log a `MACRO_HIBERNATE` entry to Execution_Ledger.
3. WHEN a cycle begins, THE Coordinator SHALL check for the existence of Kill_Switch_File.
4. IF Kill_Switch_File exists, THEN THE Coordinator SHALL skip scanning, skip signing, and log a `KILL_SWITCH_ACTIVE` entry to Execution_Ledger.
5. WHEN a cycle proceeds past the macro and kill-switch gates, THE Coordinator SHALL execute the steps in this order: invoke Yield_Scanner, invoke Risk_Scorer, invoke LLM_Proposer with Manifesto and Risk_Policy injected, invoke Policy_Engine, invoke Tx_Simulator, request Wallet_Executor signing if applicable, then update Positions_Ledger and Execution_Ledger.
6. THE Coordinator SHALL re-inject Manifesto and Risk_Policy into every LLM_Proposer call within the same cycle.
7. IF any step in the cycle raises an unhandled exception, THEN THE Coordinator SHALL abort the cycle, log a `CYCLE_FAILURE` entry, and SHALL NOT request any signing.

### Requirement 9: Yield Scanner Behavior

**User Story:** As an operator, I want yield candidates to come from a constrained, allowlisted universe, so that the agent cannot wander into unknown protocols.

#### Acceptance Criteria

1. THE Yield_Scanner SHALL produce only candidates whose chain is contained in `allowed_chains`.
2. THE Yield_Scanner SHALL produce only candidates whose strategy type is contained in `allowed_strategy_types`.
3. THE Yield_Scanner SHALL annotate each candidate with: chain, protocol, pool address, token addresses, advertised APY, TVL, and source URL.
4. IF a candidate references a contract, token, or pool not in the corresponding allowlist, THEN THE Yield_Scanner SHALL mark the candidate as `OUT_OF_UNIVERSE` and the Coordinator SHALL drop the candidate before scoring.
5. THE Yield_Scanner SHALL persist each scanning batch to Execution_Ledger as a `SCAN_BATCH` record including the count of candidates and the count dropped as `OUT_OF_UNIVERSE`.

### Requirement 10: Risk Scorer Behavior

**User Story:** As an operator, I want each candidate ranked by a numeric risk score, so that the LLM_Proposer receives prioritized inputs but cannot bypass scoring.

#### Acceptance Criteria

1. THE Risk_Scorer SHALL assign each candidate a numeric score in the closed interval `[0, 100]` where 0 is most risky and 100 is least risky.
2. THE Risk_Scorer SHALL include in the score components reflecting at least: TVL, contract age in days, audit status, historical exploit flag, and oracle dependency.
3. THE Risk_Scorer SHALL be deterministic given identical inputs.
4. THE Risk_Scorer SHALL NOT call any LLM endpoint.
5. THE Risk_Scorer output SHALL be advisory; THE Policy_Engine SHALL NOT use the score as a substitute for any cap or allowlist check.

### Requirement 11: Transaction Simulation

**User Story:** As an operator, I want every transaction simulated before signing, so that failed or malicious calls are caught off-chain.

#### Acceptance Criteria

1. WHEN Policy_Engine has approved an action, THE Tx_Simulator SHALL execute a Dry_Run of the transaction against a current chain state and record the simulated balance changes.
2. IF the simulation fails or reverts, THEN THE Tx_Simulator SHALL mark the action as `SIMULATION_FAILED` and the Coordinator SHALL NOT request signing.
3. IF the simulated post-execution wallet value differs from the predicted post-execution wallet value by more than a tolerance defined in Risk_Policy as `simulation_value_tolerance_bps`, THEN THE Tx_Simulator SHALL mark the action as `SIMULATION_DEVIATION` and the Coordinator SHALL NOT request signing.
4. THE Tx_Simulator SHALL persist a `SIMULATION` record to Execution_Ledger for every simulated action including success or failure status and simulated balance changes.

### Requirement 12: Wallet Executor Signing Behavior

**User Story:** As an operator, I want the Wallet_Executor to be the single signing chokepoint, so that all on-chain effects flow through one auditable path.

#### Acceptance Criteria

1. THE Wallet_Executor SHALL accept signing requests only from the Coordinator running in the current process.
2. WHEN a signing request arrives, THE Wallet_Executor SHALL verify that an unexpired Policy_Engine approval token bound to the action descriptor hash exists for the current cycle.
3. WHEN a signing request arrives, THE Wallet_Executor SHALL verify that a successful Tx_Simulator record exists for the action descriptor hash in the current cycle.
4. WHEN a signing request arrives, THE Wallet_Executor SHALL verify Kill_Switch_File does not exist.
5. WHEN a signing request arrives, THE Wallet_Executor SHALL verify that Macro_State is neither `Risk-Off` nor `HALT`.
6. IF any verification in 12.2 through 12.5 fails, THEN THE Wallet_Executor SHALL refuse to sign and SHALL log a structured `SIGN_REFUSED` record to Execution_Ledger including the failed check identifier.
7. WHEN a transaction is broadcast, THE Wallet_Executor SHALL persist a `SIGN_BROADCAST` record to Execution_Ledger including transaction hash, chain, gas used estimate, and action descriptor hash.
8. WHEN a transaction is confirmed, THE Wallet_Executor SHALL persist a `SIGN_CONFIRMED` or `SIGN_FAILED` record to Execution_Ledger including the on-chain receipt status.

### Requirement 13: Telegram Guardian and Approvals

**User Story:** As an operator, I want a Telegram surface for approvals and emergency commands, so that I can intervene from a phone.

#### Acceptance Criteria

1. THE Telegram_Guardian SHALL accept commands only from chat IDs declared in Risk_Policy under `telegram_authorized_chat_ids`.
2. WHEN Autonomy_Level is Human_Approved, THE Telegram_Guardian SHALL deliver each candidate action descriptor hash to authorized chats and accept a typed approval reply within an approval timeout window defined in Risk_Policy as `telegram_approval_timeout_seconds`.
3. IF the approval reply is not received within `telegram_approval_timeout_seconds`, THEN THE Coordinator SHALL drop the action and log `APPROVAL_TIMEOUT` to Execution_Ledger.
4. WHEN Telegram_Guardian receives the command `HALT`, THE Telegram_Guardian SHALL create Kill_Switch_File and reply with confirmation.
5. WHEN Telegram_Guardian receives the command `RESUME`, THE Telegram_Guardian SHALL delete Kill_Switch_File only IF no policy violation has occurred in the trailing 24 hours, and otherwise SHALL refuse and log `RESUME_REFUSED`.
6. WHEN Telegram_Guardian receives the command `PAUSE`, THE Telegram_Guardian SHALL set a `paused_until` timestamp in Risk_Policy runtime state for a duration declared in the command, and THE Coordinator SHALL skip signing while the pause is active.
7. WHEN Telegram_Guardian receives the command `STATUS`, THE Telegram_Guardian SHALL reply with current Autonomy_Level, Macro_State value, count of open positions, current wallet value in USD, and trailing 24-hour PnL.
8. THE Telegram_Guardian SHALL NOT execute any command that would modify Risk_Policy caps, allowlists, or Autonomy_Level.

### Requirement 14: Kill Switch and Macro Hibernation

**User Story:** As an operator, I want a filesystem kill switch and reuse of the existing Risk-Off macro gate, so that I can halt the agent without code changes.

#### Acceptance Criteria

1. WHILE Kill_Switch_File exists, THE Wallet_Executor SHALL refuse all signing requests.
2. WHILE Kill_Switch_File exists, THE Coordinator SHALL skip Yield_Scanner, Risk_Scorer, and LLM_Proposer invocations.
3. WHILE Macro_State equals `Risk-Off` or `HALT`, THE Wallet_Executor SHALL refuse all signing requests.
4. WHEN Kill_Switch_File transitions from existing to absent, THE Coordinator SHALL log a `KILL_SWITCH_CLEARED` entry to Execution_Ledger before the next cycle.
5. THE Coordinator SHALL re-read Macro_State at the start of every cycle and SHALL NOT cache the value across cycles.

### Requirement 15: Data File Invariants

**User Story:** As an operator, I want every JSON data file to remain valid and append-only where stated, so that the audit trail is trustworthy.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL ensure that after every write, `risk_policy.json`, `contract_allowlist.json`, `token_allowlist.json`, `pool_allowlist.json`, `positions.json`, `execution_ledger.json`, and `lessons_learned.json` parse as valid JSON.
2. THE Autonomy_Module SHALL write Execution_Ledger and Lessons_Learned in append-only fashion, and SHALL NOT mutate or delete prior records.
3. THE Autonomy_Module SHALL perform writes to data files using a write-temp-then-rename pattern so that no concurrent reader observes a partial file.
4. IF a data file fails to parse at startup, THEN THE Coordinator SHALL refuse to start the cycle and SHALL log `DATA_FILE_CORRUPT` to a sidecar error log.
5. THE Autonomy_Module SHALL retain at least 90 days of Execution_Ledger entries on disk.

### Requirement 16: Allowlist Update Process

**User Story:** As an operator, I want allowlist files to be modifiable only by humans, so that the agent cannot grow its own universe.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL NOT contain any code path that programmatically appends to or removes from Contract_Allowlist, Token_Allowlist, or Pool_Allowlist.
2. WHEN the Coordinator starts, THE Coordinator SHALL compute and log a SHA-256 digest of each allowlist file to Execution_Ledger as an `ALLOWLIST_DIGEST` record.
3. IF an allowlist digest changes between two consecutive cycles, THEN THE Coordinator SHALL log `ALLOWLIST_CHANGE_DETECTED` and SHALL pause signing for a cooldown period defined in Risk_Policy as `allowlist_change_cooldown_seconds`.

### Requirement 17: Learning Memory Constraints

**User Story:** As an operator, I want learning memory to bias scoring only, so that the agent cannot weaken its own safety rails.

#### Acceptance Criteria

1. THE Learning_Memory SHALL persist outcomes in append-only form to Lessons_Learned.
2. THE Learning_Memory SHALL provide read-only access to Risk_Scorer for the purpose of biasing component scores.
3. THE Learning_Memory SHALL NOT modify Risk_Policy, Contract_Allowlist, Token_Allowlist, Pool_Allowlist, or Autonomy_Level under any condition.
4. IF the Learning_Memory bias for a candidate would alter the final risk score by more than a clamp value defined in Risk_Policy as `learning_bias_clamp_points`, THEN THE Risk_Scorer SHALL clamp the bias to the maximum allowed value.
5. THE Learning_Memory SHALL NOT call any LLM endpoint.

### Requirement 18: Manifesto Injection

**User Story:** As an operator, I want the Manifesto re-injected to the LLM each cycle, so that the LLM is consistently anchored to the constitution.

#### Acceptance Criteria

1. WHEN the Coordinator invokes LLM_Proposer, THE Coordinator SHALL include the full text of Manifesto and a serialized snapshot of Risk_Policy in the prompt.
2. THE LLM_Proposer SHALL produce structured output conforming to a schema declared in code, and THE Coordinator SHALL reject any LLM output that fails schema validation.
3. IF the LLM output contains free-form instructions outside the declared schema, THEN THE Coordinator SHALL discard the free-form portion and log `LLM_FREEFORM_DISCARDED` to Execution_Ledger.
4. THE Coordinator SHALL NOT pass Sandbox_Wallet private key, environment secrets, or Telegram tokens into any LLM prompt.

### Requirement 19: Initial Risk Policy Defaults

**User Story:** As an operator, I want explicit initial caps so that the first deployment is safe by default.

#### Acceptance Criteria

1. THE Risk_Policy initial defaults SHALL be: `mode = "CAPPED_AUTONOMY"`, `autonomy_level = 1`, `max_wallet_value_usd = 25`, `max_tx_usd = 5`, `max_daily_spend_usd = 10`, `max_open_positions = 2`, `min_stable_reserve_pct = 50`, `allowed_chains = ["Base", "BNB Chain", "Solana"]`, `allowed_strategy_types = ["stablecoin_lending", "stable_stable_lp", "xstocks_points", "xstocks_lp"]`, `blocked_actions = ["bridge", "borrow", "leverage", "unknown_contract", "unlimited_approval", "main_wallet_access", "seed_phrase_storage"]`, `require_contract_allowlist = true`, `require_token_allowlist = true`, `require_pool_allowlist = true`, `require_tx_simulation = true`, `max_slippage_bps = 50`, `kill_switch_file = "/root/hermes-agent/defi_autonomy/STOP"`.
2. THE Risk_Policy SHALL declare `autonomy_level = 1` (Watch_Only) on first deployment and SHALL NOT be auto-promoted by the Autonomy_Module.
3. IF Risk_Policy is missing any field listed in 19.1, THEN THE Coordinator SHALL refuse to start and SHALL log `RISK_POLICY_INCOMPLETE`.

### Requirement 20: Execution Ledger Schema and Append-Only Round-Trip

**User Story:** As an operator, I want a structured, parseable execution ledger, so that audits and learning are reliable.

#### Acceptance Criteria

1. THE Execution_Ledger SHALL be a JSON array on disk, and every appended record SHALL contain at least the fields `timestamp_utc`, `cycle_id`, `record_type`, `payload`, and `digest_sha256`.
2. THE `record_type` field SHALL be one of: `SCAN_BATCH`, `SCORE`, `LLM_PROPOSAL`, `POLICY_APPROVAL`, `POLICY_REJECTION`, `SIMULATION`, `SIGN_REFUSED`, `SIGN_BROADCAST`, `SIGN_CONFIRMED`, `SIGN_FAILED`, `MACRO_HIBERNATE`, `KILL_SWITCH_ACTIVE`, `KILL_SWITCH_CLEARED`, `LEVEL_PROMOTION`, `ALLOWLIST_DIGEST`, `ALLOWLIST_CHANGE_DETECTED`, `LLM_FREEFORM_DISCARDED`, `POLICY_INJECTION_ATTEMPT`, `INVARIANT_VIOLATION`, `KEY_ACCESS_VIOLATION`, `RISK_POLICY_INCOMPLETE`, `DATA_FILE_CORRUPT`, `CYCLE_FAILURE`, `READY_FOR_LEVEL_2`, `READY_FOR_LEVEL_3`, `READY_FOR_LEVEL_4`, `APPROVAL_TIMEOUT`, `RESUME_REFUSED`, `GRADUATION_METRICS`.
3. THE Autonomy_Module SHALL provide a serializer that converts an in-memory ledger record to its on-disk JSON form and a parser that converts on-disk JSON back into the in-memory record.
4. FOR ALL valid in-memory ledger records, applying the serializer then the parser SHALL produce a record equivalent to the original under field-wise equality.
5. FOR ALL valid on-disk ledger JSON entries, applying the parser then the serializer SHALL produce JSON equivalent under canonicalization to the original.

### Requirement 21: Strategic Decisions to Resolve

**User Story:** As an operator, I want the unresolved strategic questions explicitly captured as decisions to be made before implementation, so that they are not silently embedded in code.

#### Acceptance Criteria

1. THE design phase SHALL produce a decision record selecting either `xstocks_focused` or `broader_defi_yield` as the initial strategy universe and SHALL constrain the initial Pool_Allowlist accordingly.
2. THE design phase SHALL produce a decision record selecting Meteora as either `primary_venue` or `one_of_many_venues` and SHALL define the venue adapter interface accordingly.
3. THE design phase SHALL produce a decision record selecting one of `pieverse_x402_now`, `pieverse_x402_later`, or `pieverse_x402_never` for the Pieverse_x402 integration scope.
4. THE design phase SHALL produce a decision record selecting one of `meridian_adopt_harness`, `meridian_borrow_ideas`, or `meridian_decline` for the Meridian_Framework integration scope.
5. THE design phase SHALL produce a decision record assigning AWS to `auxiliary_only` and listing the auxiliary uses (S3 backups, Lambda reports, Bedrock summaries, Amplify dashboard) and SHALL declare Hermes_Node as the primary execution host.
6. EACH decision record produced under 21.1 through 21.5 SHALL be persisted in `design.md` with a rationale referencing capital preservation impact.

### Requirement 22: Out of Scope

**User Story:** As an operator, I want explicit out-of-scope statements so that scope creep is rejected by reference.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL NOT integrate with the operator's main exchange accounts.
2. THE Autonomy_Module SHALL NOT integrate with Main_Wallet in any form.
3. THE Autonomy_Module SHALL NOT implement Autonomy_Level 4 farming behavior in the initial release; Autonomy_Level 4 capability SHALL be deferred until graduation criteria from Capped_Autonomy are met and a follow-up spec is approved.
4. THE Autonomy_Module SHALL NOT implement Autonomy_Level 5 in any release.
5. THE Autonomy_Module SHALL NOT migrate existing Hermes daemons (`binance_daemon.py`, `bitget_spot_daemon.py`, `structural_hunter.py`, `sentinel_fanout.py`, Overseer) to AWS as primary host.

### Requirement 23: Operational Footprint

**User Story:** As an operator, I want the Autonomy_Module to fit alongside existing PM2 processes on a single VPS, so that I do not incur new infrastructure cost.

#### Acceptance Criteria

1. THE Autonomy_Module SHALL run as a single PM2 process whose ecosystem entry references `coordinator.py` as the script.
2. THE Autonomy_Module SHALL NOT require AWS, Lambda, S3, Bedrock, or Amplify to be reachable in order to perform a cycle.
3. WHERE AWS auxiliary services are configured, THE Autonomy_Module SHALL use them only for read-only backups, summaries, or dashboards, and SHALL NOT delegate any policy decision or signing to them.
4. IF an AWS auxiliary call fails, THEN THE Coordinator SHALL log the failure and SHALL continue the cycle without aborting.


### Requirement 24: External Data Ingestion

**User Story:** As an operator, I want a single, sandboxed boundary that fetches yield/protocol/market data from a closed allowlist of public sources, normalizes it, flags staleness, and proves provenance, so that no scraped value can bypass safety rails or contaminate the LLM prompt.

#### Acceptance Criteria

1. THE External_Data_Ingestion component SHALL be implemented at `defi_autonomy/external_data_ingestion.py` and SHALL be the only component in the Autonomy_Module that performs outbound network calls for yield/protocol/market data.
2. THE External_Data_Ingestion component SHALL load the source allowlist from `data/source_allowlist.json` at the start of every cycle.
3. THE External_Data_Ingestion component SHALL invoke only source adapters whose `source_id` is present in `source_allowlist.json`, and IF an adapter is registered without a matching `source_id`, THEN THE Coordinator SHALL log `INVARIANT_VIOLATION{which="source_adapter"}` and refuse to start the cycle.
4. THE External_Data_Ingestion component SHALL restrict outbound HTTP requests to methods in `{"GET", "HEAD"}` and to domains declared in `source_allowlist.json` for the invoking adapter, and IF an adapter attempts a different method or off-allowlist domain, THEN THE component SHALL refuse the request and log `SOURCE_FAILURE{reason="METHOD_BLOCKED"}` or `SOURCE_FAILURE{reason="DOMAIN_BLOCKED"}` accordingly.
5. THE External_Data_Ingestion component SHALL NOT perform browser automation, headless-browser execution, or authenticated WebSocket subscriptions.
6. THE External_Data_Ingestion component SHALL NOT read or transmit any value from environment variables matching `*_API_KEY`, `*_SECRET`, `*_TOKEN`, or `HERMES_DEFI_SANDBOX_KEY`.
7. THE External_Data_Ingestion component SHALL NOT scrape, fetch, or otherwise access any of: the operator's main wallet, exchange accounts (Binance, Bitget, or other CEX), Discord direct messages, Telegram private groups, or any endpoint requiring user-bound credentials.
8. WHEN a source adapter completes a fetch, THE External_Data_Ingestion component SHALL persist a `SOURCE_FETCH` record to Execution_Ledger including `adapter_name`, `source_id`, `source_url`, `response_sha256`, `bytes`, `data_freshness_seconds`, `source_confidence_score`, and `status`.
9. WHEN a source adapter fails, times out, or returns an empty/malformed response, THE External_Data_Ingestion component SHALL log `SOURCE_FAILURE{adapter, reason}` and continue the cycle with remaining adapters.
10. THE External_Data_Ingestion component SHALL maintain `data/raw_snapshots.json` as a bounded ring buffer of raw responses with provenance, where the buffer capacity is declared in the file and SHALL NOT exceed the declared capacity.
11. THE External_Data_Ingestion component SHALL produce, for every successful fetch, one or more `NormalizedCandidate` records conforming to the `NormalizedCandidate` JSON Schema declared in design.md, and SHALL persist the latest cycle's normalized batch atomically to `data/normalized_yield_candidates.json` using a write-temp-then-rename pattern.
12. THE External_Data_Ingestion component SHALL maintain per-source rolling statistics in `data/source_health.json` including last fetch timestamp, last status, rolling success rate, rolling stale rate, and latency percentiles.
13. WHEN a candidate's `data_freshness_seconds` exceeds the source's `max_freshness_seconds`, THE External_Data_Ingestion component SHALL set the candidate's `stale_data` field to true.
14. THE Policy_Engine SHALL reject any action descriptor whose underlying candidate has `stale_data == true` (rule `R-EXT.1`), and the rejection SHALL be logged as `POLICY_REJECTION{rule_id="R-EXT.1"}`.
15. THE External_Data_Ingestion component SHALL compute an `attestation` value equal to the SHA-256 of the canonical JSON of `(cycle_id, sorted candidate hashes)` and SHALL include this value in the `IngestionResult`.
16. THE Policy_Engine SHALL reject any action descriptor whose `ingestion_attestation` does not equal the `IngestionResult.attestation` of the current cycle (rule `R-EXT.2`).
17. THE Policy_Engine SHALL reject any action descriptor whose underlying candidate's `adapter_name` is not present in `source_allowlist.json` (rule `R-EXT.3`).
18. WHEN every allowlisted source returns failure, empty, or stale-only output, THE Coordinator SHALL append a `NO_VALID_DATA` record to Execution_Ledger and SHALL end the cycle without invoking Yield_Scanner, Risk_Scorer, LLM_Proposer, Policy_Engine, Tx_Simulator, or Wallet_Executor.
19. THE External_Data_Ingestion component SHALL strip HTML tags, script tags, and embedded JavaScript from any free-form text fields before persisting them to `raw_snapshots.json`, and THE LLM_Proposer SHALL NOT include any such free-form text in any LLM prompt.
20. THE Coordinator SHALL include in the LLM prompt only normalized numeric and categorical fields drawn from `NormalizedCandidate`, and SHALL NOT include `source_url`, raw response bodies, or any text that originated from a scraped page.
21. THE External_Data_Ingestion component SHALL NOT contain any code path that imports, references, or invokes Wallet_Executor, Policy_Engine signing-token issuance, or any private-key handling code.
22. THE External_Data_Ingestion component SHALL NOT contain any write handle to `risk_policy.json`, `contract_allowlist.json`, `token_allowlist.json`, `pool_allowlist.json`, or `source_allowlist.json`.
23. THE Yield_Scanner SHALL drop any candidate whose contract, token, or pool address is absent from the corresponding execution allowlist, and SHALL mark the candidate `OUT_OF_UNIVERSE`, regardless of how confidently the source adapter reported the candidate.
24. WHEN `source_allowlist.json` digest changes between two consecutive cycles, THE Coordinator SHALL log `ALLOWLIST_CHANGE_DETECTED{which="source_allowlist"}` and SHALL pause signing for `allowlist_change_cooldown_seconds`.
25. THE External_Data_Ingestion component SHALL be statically verifiable by an import-graph test asserting that no module under `defi_autonomy/sources/` and no symbol in `external_data_ingestion.py` reaches `wallet_executor.py` through any import path.
