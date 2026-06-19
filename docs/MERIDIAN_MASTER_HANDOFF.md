# MERIDIAN LP — MASTER HANDOFF

Last Updated: 2026-06-19

---

## Project Mission

Build a risk-adjusted LP allocation agent for Meteora on Solana.

Primary objectives:

1. Safe LP entry
2. Safe LP exit
3. Risk-adjusted capital allocation
4. Telegram reporting and evidence
5. Human-supervised execution

The project prioritizes capital preservation over yield.

---

## Current Deployment State

Repository:
Meridian-LP-Adjusted

Current Mode:

EXECUTION_MODE=scanner
DRY_RUN=true
ALLOW_LIVE_EXECUTION=false
HEADLESS=true
LLM_ENABLED=false
TELEGRAM_MUTATIONS_ENABLED=false
HIVEMIND_ENABLED=false

Wallet Funding:
0 SOL

Status:
Observation-only

---

## Safety Principles

No autonomous capital deployment.

No live execution until:

- Scanner proven stable
- Reporting proven stable
- Risk model proven stable
- Paper LP simulation completed
- Human approval workflow implemented

UNKNOWN token risk classifications are treated conservatively.

---

## Completed Work

### Phase A — Observation Scanner

Completed:

- Screening engine
- Telegram read-only commands
- Decision ledger
- Token risk module
- Reporting framework
- PM2 deployment
- AWS deployment

---

### PR 17

Token risk reporting improvements.

---

### PR 18

Ledger evidence enhancements.

---

### PR 19

Ops-8.1 Cache Truthfulness

Change:

setLatestCandidates(candidates)

added inside automated runScreeningCycle().

Result:

Automated cycles now refresh candidate cache consistently with manual workflows.

No execution behavior changed.

---

## Current Production Behavior

Observed:

- 128 ledger entries
- 100% no_deploy outcomes
- Scanner operating conservatively

Typical reasons:

- filtered
- deterministic_no_llm
- single_candidate_skipped

This is expected in observation mode.

---

## Known Issues

### Ops-8.2

Need clearer distinction between:

- NOT_QUERIED
- UNAVAILABLE
- MISSING
- NEGATIVE_SIGNAL

Current telemetry collapses multiple states together.

---

## Non-Negotiable Constraints

Do not:

- enable live execution
- enable wallet deployment
- enable LLM trading decisions
- weaken risk filters

without explicit human approval.

---

## Success Definition

The project succeeds when:

1. Entry decisions are evidence-based
2. Exit decisions are evidence-based
3. Telegram reporting is trustworthy
4. LP allocations are risk-adjusted
5. Human oversight remains intact