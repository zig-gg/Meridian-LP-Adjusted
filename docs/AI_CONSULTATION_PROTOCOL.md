# AI CONSULTATION PROTOCOL

Applies to:

- Gemini
- Claude
- GPT
- Antigravity
- Codex
- Other agents

---

## Review Mode First

Before proposing changes:

1. State verified facts.
2. State assumptions.
3. State uncertainties.
4. State merge blockers.
5. State rollback plan.
6. State tests required.

---

## Evidence Requirements

Do not claim:

SAFE
MERGE
APPROVE

without evidence.

Must cite:

- code locations
- diff locations
- execution paths

---

## Safety Rules

Never:

- enable live execution
- enable autonomous trading
- weaken risk controls

without explicit instruction.

---

## Patch Philosophy

Smallest safe patch.

One objective per PR.

No opportunistic refactors.

---

## Preferred Workflow

1. Reconnaissance
2. Diagnosis
3. Contract
4. Implementation
5. Review
6. Testing
7. Merge

---

## Required Output Format

Verified Facts

Assumptions

Uncertainties

Merge Blockers

Required Fixes

Tests Needed

Final Verdict

APPROVE
SAFE WITH FIXES
DO NOT STAGE
DO NOT MERGE

---

## Project-Specific Constraints

Current Mode:

EXECUTION_MODE=scanner
DRY_RUN=true
ALLOW_LIVE_EXECUTION=false

Wallet:

0 SOL

No live trading.

No autonomous deployment.

Observation-first.