$ErrorActionPreference = 'Stop'
$path = '.kiro\specs\hermes-defi-autonomy\requirements.md'
$content = @'


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
'@
Add-Content -Path $path -Value $content -NoNewline
'OK req24'
