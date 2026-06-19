# MERIDIAN LP ARCHITECTURE

---

## System Overview

Meridian LP consists of:

1. Scanner
2. Risk Engine
3. Ledger
4. Telegram Interface
5. LP Decision Layer

---

## Scanner

Purpose:

Find candidate LP opportunities.

Current Output:

- candidate pools
- filtered pools
- screening evidence

No execution authority.

---

## Token Risk Module

Outputs:

PASS
WARN
BLOCK
UNKNOWN

UNKNOWN is treated conservatively.

---

## Decision Ledger

Append-only JSONL.

Purpose:

Audit trail.

Records:

- candidates
- reasons
- outcomes

Current outcomes:

- no_deploy
- filtered
- single_candidate_skipped

---

## Telegram Layer

Read-only.

Commands:

/status
/report
/ledger
/config
/wallet-ready

No trading authority.

---

## LP Strategy Layer

Future component.

Responsibilities:

- entry
- exit
- rebalance
- capital allocation

---

## Execution Layer

Currently disabled.

Requirements before activation:

- simulation complete
- risk engine complete
- human approval workflow complete

---

## Safety Boundaries

Never bypass:

DRY_RUN
ALLOW_LIVE_EXECUTION
TELEGRAM_MUTATIONS_ENABLED
LLM_ENABLED

without explicit approval.