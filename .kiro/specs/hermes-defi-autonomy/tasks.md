# Implementation Plan — Hermes DeFi Autonomy Module

This is a phased Kanban. v1 ships at `autonomy_level = 1` (Watch_Only). Phases 9 and 10 are **future** — listed here so the architecture stays consistent, but they are not part of v1 implementation.

`[Reqs: …]` lists the requirements each task satisfies.

## Phase 0 — Scaffolding and static safety files

- [ ] 0.1 Create directory structure under `/root/hermes-agent/defi_autonomy/` matching the file tree in design.md (no Python logic yet, just empty `__init__.py` and folder placeholders). [Reqs: R1.1]
- [ ] 0.2 Add `manifesto.md` with the constitutional text (capital preservation, propose/decide/sign separation, prohibited actions). [Reqs: R18.1]
- [ ] 0.3 Add initial `data/risk_policy.json` with the defaults from R19.1. [Reqs: R19.1, R19.2]
- [ ] 0.4 Add empty `data/contract_allowlist.json`, `data/token_allowlist.json`, `data/pool_allowlist.json` with `version: 1, entries: []` skeleton. [Reqs: R16, R19.1]
- [ ] 0.5 Add initial `data/source_allowlist.json` with the seven adapters in design.md (xstocks, meteora, stablecoin_benchmark, defillama, coingecko, protocol_status, manual). [Reqs: R24.2]
- [ ] 0.6 Add empty `data/positions.json`, `data/raw_snapshots.json`, `data/normalized_yield_candidates.json`, `data/source_health.json`, `data/execution_ledger.json`, `data/lessons_learned.json`. [Reqs: R15, R24.10–R24.12]
- [ ] 0.7 Add `STOP` file path convention; do NOT create the file (presence-only kill switch). [Reqs: R14.1]
- [ ] 0.8 Add `ecosystem.defi.cjs` PM2 entry pointing at `coordinator.py` (script not yet implemented; entry just defined). [Reqs: R23.1]
- [ ] 0.9 Add `pyproject.toml` or `requirements.txt` with pinned versions of: requests/httpx, jsonschema, hypothesis, pytest, pydantic (or dataclasses-only), ulid-py, eth-account, web3, solders/solana-py. [Reqs: R23.2]
- [ ] 0.10 Add `tests/` skeleton (`unit/`, `property/`, `invariants/` with empty `conftest.py`).

## Phase 1 — External_Data_Ingestion (watch-only first)

- [ ] 1.1 Implement `schemas/normalized_candidate.py`: `NormalizedCandidate` frozen dataclass + canonical_json + hash + JSON Schema constant. [Reqs: R24.11]
- [ ] 1.2 Implement `sources/base.py`: `SourceAdapter` ABC + `ReadOnlyHttpClient` enforcing method+domain allowlist, no cookies, no off-allowlist redirects, max-bytes, timeout. [Reqs: R24.4, R24.5]
- [ ] 1.3 Implement `sources/xstocks_adapter.py` (read-only). [Reqs: R24.1, R24.3]
- [ ] 1.4 Implement `sources/meteora_adapter.py` (source-side, public API only).
- [ ] 1.5 Implement `sources/stablecoin_benchmark_adapter.py`.
- [ ] 1.6 Implement `sources/defillama_adapter.py`.
- [ ] 1.7 Implement `sources/coingecko_adapter.py`.
- [ ] 1.8 Implement `sources/protocol_status_adapter.py` with HTML/script strip step. [Reqs: R24.19]
- [ ] 1.9 Implement `sources/manual_source_adapter.py` reading `data/manual_source_overrides.json`.
- [ ] 1.10 Implement `external_data_ingestion.py`: registers adapters, enforces source_allowlist registration (R24.3), runs adapters with per-source timeout boundary, computes `attestation`, persists `SOURCE_FETCH`/`SOURCE_FAILURE`, updates `raw_snapshots.json` (ring buffer), `normalized_yield_candidates.json` (atomic), `source_health.json`. [Reqs: R24.1, R24.8–R24.15, R24.21, R24.22]
- [ ] 1.11 Implement `data_freshness_seconds` calculation and `stale_data` flagging per source's `max_freshness_seconds`. [Reqs: R24.13]
- [ ] 1.12 Wire ingestion into a thin standalone harness (no Coordinator yet) that runs `ingest()` once and prints a report. Run manually to verify each adapter.

## Phase 2 — Yield_Scanner + Risk_Scorer

- [ ] 2.1 Implement `adapters/base.py`: `VenueAdapter` ABC and `Candidate`/`Quote`/`RawTx`/`TxReceipt`/`Outcome` dataclasses (no signing). [Reqs: R9, R10]
- [ ] 2.2 Implement `adapters/stable_lending_adapter.py` and `adapters/stable_stable_lp_adapter.py` as benchmark venues (always loaded). [Reqs: DR-001]
- [ ] 2.3 Implement `adapters/meteora_adapter.py` (venue-side, distinct from source-side). [Reqs: DR-002]
- [ ] 2.4 Implement `yield_scanner.py`: joins ingestion candidates with on-chain venue queries on `(chain, venue_id, pool_address)`; corroboration check; allowlist filter; `OUT_OF_UNIVERSE` drops; `SCAN_BATCH` ledger record. [Reqs: R9, R24.23]
- [ ] 2.5 Implement `learning_memory.py`: append-only writer + read-only bias reader; clamp at `learning_bias_clamp_points`. [Reqs: R17]
- [ ] 2.6 Implement `risk_scorer.py` with the extended component set (TVL, volume, APR sustainability, age, liquidity depth, volatility, IL estimate, benchmark comparison, warning flags, source confidence). Deterministic. No LLM. [Reqs: R10]

## Phase 3 — Policy_Engine + schemas + ledger

- [ ] 3.1 Implement `schemas/action_descriptor.py`: `ActionDescriptor` frozen dataclass with `ingestion_attestation` and `candidate_hash` fields. canonical_json + hash. [Reqs: R3.6]
- [ ] 3.2 Implement `schemas/approval_token.py`: HMAC-SHA256 single-use, per-cycle key. `verify()` raises on MAC mismatch / expiry / hash mismatch / replay. [Reqs: R3.6]
- [ ] 3.3 Implement `schemas/ledger_records.py`: `LedgerRecord` dataclass; serializer/parser with round-trip property; closed `RecordType` enum including new ingestion records (`SOURCE_FETCH`, `SOURCE_FAILURE`, `NO_VALID_DATA`, `SOURCE_ALLOWLIST_DIGEST`). [Reqs: R20]
- [ ] 3.4 Implement `policy_engine.py`: deterministic evaluation of every R4 rule plus R-EXT.1, R-EXT.2, R-EXT.3, R5 invariants, R3.5 injection-pattern detection. Issues single-use ApprovalTokens. Never imports any LLM client. [Reqs: R3, R4, R5, R24.14, R24.16, R24.17]
- [ ] 3.5 Implement allowlist loaders with SHA-256 digest computation; cooldown enforcement; `ALLOWLIST_DIGEST` record per cycle. [Reqs: R16, R24.24]
- [ ] 3.6 Implement append-only ledger writer with write-temp-then-rename atomic semantics. [Reqs: R15.2, R15.3]
- [ ] 3.7 Implement `Coordinator` skeleton (cycle order: macro → kill switch → ingestion → scan → score → propose → policy → simulate → executor (disabled) → ledger). Placeholder LLM pass-through for now. [Reqs: R8.5]

## Phase 4 — LLM_Proposer + manifesto injection

- [ ] 4.1 Implement `schemas/proposal_schema.py`: strict JSON Schema with `additionalProperties: false`, including required `ingestion_attestation` and per-proposal `candidate_hash`. [Reqs: R18.2, R24.16]
- [ ] 4.2 Implement `llm_proposer.py`: OpenRouter client; prompt builder includes manifesto + risk_policy snapshot + scored non-stale candidates' normalized fields ONLY (no source_url, no raw text, no secrets). Strict schema validation; freeform discard. [Reqs: R18, R24.20]
- [ ] 4.3 Wire `LLM_Proposer` into `Coordinator.run_cycle`. [Reqs: R8.5]
- [ ] 4.4 Verify `POLICY_INJECTION_ATTEMPT` detection on adversarial fixtures. [Reqs: R3.5]

## Phase 5 — Tx_Simulator stubs (no signing)

- [ ] 5.1 Implement `tx_simulator.py`: EVM `eth_call`-based simulator and Solana `simulateTransaction` simulator. Tolerance check against `simulation_value_tolerance_bps`. Returns `OK` / `FAILED` / `DEVIATION` / `UNAVAILABLE`. [Reqs: R11]
- [ ] 5.2 Implement `wallet_executor.py` skeleton with `sign()` that ALWAYS raises `AutonomyLevelTooLow` when `autonomy_level < 2`. Define exception types for `KillSwitchActive`, `MacroBlocked`, `ApprovalTokenInvalid`, `ApprovalTokenReplayed`, `SimulationMissing`, `SandboxAddressMismatch`. **Do NOT implement key loading or signing logic in v1.** [Reqs: R6.2, R12, DR-006]
- [ ] 5.3 Wire `Tx_Simulator` and `Wallet_Executor` into `Coordinator.run_cycle`. Confirm via test that no `SIGN_BROADCAST` ever occurs. [Reqs: CP-1, CP-5]

## Phase 6 — Telegram_Guardian

- [ ] 6.1 Implement `telegram_guardian.py`: authorized-chat-ID gate; commands `STATUS`, `HALT`, `PAUSE <duration>`, `RESUME`. [Reqs: R13.1, R13.4–R13.7]
- [ ] 6.2 Implement `RESUME_REFUSED` logic when a policy violation occurred in trailing 24 h. [Reqs: R13.5]
- [ ] 6.3 `STATUS` reply: autonomy_level, macro_state, open positions count, wallet value USD (paper at v1), trailing 24h PnL. [Reqs: R13.7]
- [ ] 6.4 Stub the per-tx approval flow behind an `autonomy_level >= 2` guard (does nothing in v1). [Reqs: R13.2]
- [ ] 6.5 Confirm Telegram_Guardian cannot mutate caps, allowlists, source_allowlist, or autonomy_level. [Reqs: R13.8]

## Phase 7 — Tests and safety invariants

- [ ] 7.1 Unit tests for every R4 rule (positive + negative case each). [Reqs: R4]
- [ ] 7.2 Unit tests for R-EXT.1, R-EXT.2, R-EXT.3. [Reqs: R24.14, R24.16, R24.17]
- [ ] 7.3 Property tests: adversarial-descriptor fuzzer; ledger round-trip; descriptor hashing determinism + collision resistance; NormalizedCandidate normalization round-trip.
- [ ] 7.4 Allowlist digest tests (stability, change detection, cooldown). [Reqs: R16]
- [ ] 7.5 Source allowlist enforcement tests (off-allowlist source_id, off-allowlist domain, blocked method). [Reqs: R24.3, R24.4]
- [ ] 7.6 Stale data rejection test. [Reqs: R24.14]
- [ ] 7.7 Malformed API response, adapter timeout, NormalizedCandidate schema validation tests. [Reqs: R24.9, R24.11]
- [ ] 7.8 Prompt-injection-in-source-text test (the injection string never reaches the LLM prompt). [Reqs: R24.19, R24.20]
- [ ] 7.9 Proof: scraped text cannot modify policy. Static + runtime. [Reqs: CP-2]
- [ ] 7.10 Proof: source data cannot bypass pool/token/contract allowlists. [Reqs: R4.11–R4.13, R24.23]
- [ ] 7.11 Kill-switch tests (presence blocks scan + sign; absence permits cycle). [Reqs: R14.1, R14.2]
- [ ] 7.12 Macro HALT tests (Risk-Off and HALT both block; macro re-read every cycle). [Reqs: R14.3, R14.5]
- [ ] 7.13 LLM schema validation tests (non-conforming output rejected; freeform discarded; secrets never in prompt). [Reqs: R18]
- [ ] 7.14 No-signing tests under autonomy_level = 1: end-to-end Coordinator run, assert zero `SIGN_BROADCAST`. [Reqs: CP-5, R6.2]
- [ ] 7.15 Invariant test: no `autonomy_level == 5` literal anywhere; loader rejects > 4. [Reqs: R5.7]
- [ ] 7.16 Import-graph test: External_Data_Ingestion ⇏ Wallet_Executor; sources/* ⇏ Wallet_Executor. [Reqs: R24.21, R24.25, CP-2]
- [ ] 7.17 Static tests: zero occurrences of `bridge`, `borrow`, `leverage`, `mnemonic`, `seed_phrase`, `binance_client`, `bitget_client`, unbounded approval. [Reqs: R5.1–R5.6, CP-10]
- [ ] 7.18 Atomic-write chaos test (kill -9 mid-write does not corrupt JSON files). [Reqs: R15.3]
- [ ] 7.19 Add a pre-commit hook that runs the invariant + import-graph tests.

## Phase 8 — PM2 deployment

- [ ] 8.1 Verify `ecosystem.defi.cjs` starts the Coordinator as `Hermes-DeFi-Autonomy-Watch` distinct from existing daemons (`CeFi-Engine-Shadow`, `CeFi-Engine-Bitget-Shadow`, `CeFi-Structural-Shadow`, Market Sentinel). [Reqs: R1.3]
- [ ] 8.2 Confirm autonomy_level = 1 in `risk_policy.json` on the deployment host.
- [ ] 8.3 Confirm `STOP` file is absent at start; confirm Telegram_Guardian can create/remove it.
- [ ] 8.4 Run for 100 successful cycles in observation mode. Verify ledger size, JSON validity, no unexpected record types.
- [ ] 8.5 Verify that an existing daemon crash does not affect the autonomy module and vice versa. [Reqs: R1.4]
- [ ] 8.6 Operator review of `READY_FOR_LEVEL_2` advisory before any consideration of promotion.

## Phase 9 — Future Level 2 signing preparation (NOT enabled in v1)

- [ ] 9.1 Implement key loading in `Wallet_Executor` (env var or encrypted at-rest file). Behind autonomy_level >= 2 gate. [Reqs: R2.2]
- [ ] 9.2 Implement address-match verification (sandbox_wallet_address vs derived address). [Reqs: R2.7]
- [ ] 9.3 Implement exact-amount ERC-20 approvals; never unbounded. [Reqs: R5.6]
- [ ] 9.4 Implement Telegram per-tx approval flow with `telegram_approval_timeout_seconds` and `APPROVAL_TIMEOUT` ledger record. [Reqs: R13.2, R13.3]
- [ ] 9.5 Add tests for: ApprovalToken HMAC verification, replay rejection, address mismatch, kill-switch sign refusal, macro sign refusal, simulation-missing sign refusal. [Reqs: R12]
- [ ] 9.6 Operator decision gate: separate spec amendment required to flip autonomy_level to 2.

## Phase 10 — Future Pieverse / x402 sandbox (NOT enabled in v1)

- [ ] 10.1 Author a separate spec under `.kiro/specs/hermes-defi-pieverse-sandbox/`. [Reqs: DR-003]
- [ ] 10.2 Implement Pieverse adapter in isolation. Cannot hold sandbox wallet key. Cannot import `wallet_executor.py`. Cannot mutate any allowlist or policy. Same import-graph guarantee as External_Data_Ingestion.
- [ ] 10.3 Operator decision gate: Pieverse sandbox enabled only after v2 has run cleanly for the L3 promotion window.
