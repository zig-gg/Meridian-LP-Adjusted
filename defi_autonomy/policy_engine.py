"""PolicyEngine — Sprint 2, Phase 2.2.

Deterministic gate that converts RiskAssessments into approved or denied
ActionDescriptors. Core safety layer before any future transaction simulation
or wallet signing.

No LLM calls. No network calls. No wallet calls. No signing/key-loading.
No mutation of risk_policy.json or allowlists.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from defi_autonomy.risk_scorer import RiskAssessment
from defi_autonomy.schemas.normalized_candidate import (
    ALLOWED_CHAINS,
    ALLOWED_STRATEGY_TYPES,
    NormalizedCandidate,
)

# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    """Describes a proposed action for policy evaluation."""

    action_id: str
    candidate_hash: str
    source_id: str
    adapter_name: str
    chain: str
    protocol: str
    strategy_type: str
    action_type: str  # WATCH / FARM / EXIT / CLAIM / REBALANCE
    pool_address: str | None
    token_addresses: tuple[str, ...]
    estimated_tx_usd: float
    estimated_wallet_value_usd: float
    estimated_daily_spend_usd: float
    slippage_bps: int
    risk_score: int
    risk_decision: str
    created_at_utc: str
    metadata: dict


@dataclass(frozen=True, slots=True)
class ApprovalToken:
    """Token issued when all policy rules pass. Not a transaction signature."""

    approval_id: str
    action_id: str
    candidate_hash: str
    policy_digest: str
    allowlist_digest: str
    approved: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    expires_at_utc: str
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of policy evaluation for an action."""

    action_id: str
    approved: bool
    approval_token: ApprovalToken | None
    denial_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    created_at_utc: str


# ============================================================================
# Utility functions
# ============================================================================


def load_json(path: Path | str) -> dict | list:
    """Load a JSON file. Returns empty dict if file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def canonical_digest(data: Any) -> str:
    """Compute SHA-256 digest of canonical JSON representation."""
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_allowlist_digest(
    contract_allowlist: dict | list,
    token_allowlist: dict | list,
    pool_allowlist: dict | list,
) -> str:
    """Compute a combined digest of all three allowlists."""
    combined = {
        "contract": contract_allowlist,
        "pool": pool_allowlist,
        "token": token_allowlist,
    }
    return canonical_digest(combined)


def risk_assessment_to_action(
    risk_assessment: RiskAssessment,
    candidate: NormalizedCandidate,
    risk_policy: dict,
    estimated_tx_usd: float = 0.0,
    estimated_wallet_value_usd: float = 0.0,
    estimated_daily_spend_usd: float = 0.0,
    slippage_bps: int = 0,
) -> ActionDescriptor:
    """Convert a RiskAssessment + candidate into an ActionDescriptor."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    action_type = risk_assessment.decision  # FARM / WATCH / SKIP
    action_id = f"act_{candidate.hash_sha256()[:16]}_{int(time.time() * 1000) % 100000:05d}"

    return ActionDescriptor(
        action_id=action_id,
        candidate_hash=risk_assessment.candidate_hash,
        source_id=risk_assessment.source_id,
        adapter_name=risk_assessment.adapter_name,
        chain=risk_assessment.chain,
        protocol=risk_assessment.protocol,
        strategy_type=risk_assessment.strategy_type,
        action_type=action_type,
        pool_address=candidate.pool_address,
        token_addresses=candidate.token_addresses,
        estimated_tx_usd=estimated_tx_usd,
        estimated_wallet_value_usd=estimated_wallet_value_usd,
        estimated_daily_spend_usd=estimated_daily_spend_usd,
        slippage_bps=slippage_bps,
        risk_score=risk_assessment.score,
        risk_decision=risk_assessment.decision,
        created_at_utc=now,
        metadata={},
    )


# ============================================================================
# PolicyEngine
# ============================================================================


_ALLOWED_ACTION_TYPES: frozenset[str] = frozenset(
    {"WATCH", "FARM", "EXIT", "CLAIM", "REBALANCE"}
)

# Autonomy levels required per action type
_ACTION_AUTONOMY_LEVELS: dict[str, int] = {
    "WATCH": 0,
    "FARM": 2,
    "EXIT": 2,
    "CLAIM": 2,
    "REBALANCE": 3,
}

_MIN_RISK_SCORE = 50
_APPROVAL_VALIDITY_SECONDS = 300  # 5 minutes


class PolicyEngine:
    """Deterministic policy gate for action approval.

    Loads risk_policy.json and allowlists from base_dir/data/.
    Evaluates ActionDescriptors against hard policy rules.
    Appends decisions to execution_ledger.jsonl.
    """

    def __init__(self, base_dir: Path | str) -> None:
        self._base = Path(base_dir)
        self._data_dir = self._base / "data"

        # Load policy and allowlists
        self._risk_policy = load_json(self._data_dir / "risk_policy.json")
        self._contract_allowlist = load_json(self._data_dir / "contract_allowlist.json")
        self._token_allowlist = load_json(self._data_dir / "token_allowlist.json")
        self._pool_allowlist = load_json(self._data_dir / "pool_allowlist.json")

        # Compute digests
        self._policy_digest = canonical_digest(self._risk_policy)
        self._allowlist_digest = compute_allowlist_digest(
            self._contract_allowlist,
            self._token_allowlist,
            self._pool_allowlist,
        )

        # Extract policy values with safe defaults
        self._autonomy_level = int(self._risk_policy.get("autonomy_level", 0))
        self._max_tx_usd = float(self._risk_policy.get("max_tx_usd", 0))
        self._max_wallet_value_usd = float(
            self._risk_policy.get("max_wallet_value_usd", 0)
        )
        self._max_daily_spend_usd = float(
            self._risk_policy.get("max_daily_spend_usd", 0)
        )
        self._max_slippage_bps = int(self._risk_policy.get("max_slippage_bps", 50))
        self._allowed_chains = set(
            self._risk_policy.get("allowed_chains", list(ALLOWED_CHAINS))
        )
        self._allowed_strategy_types = set(
            self._risk_policy.get(
                "allowed_strategy_types", list(ALLOWED_STRATEGY_TYPES)
            )
        )
        self._blocked_actions = set(
            self._risk_policy.get("blocked_actions", [])
        )
        self._require_contract_allowlist = bool(
            self._risk_policy.get("require_contract_allowlist", True)
        )
        self._require_token_allowlist = bool(
            self._risk_policy.get("require_token_allowlist", True)
        )
        self._require_pool_allowlist = bool(
            self._risk_policy.get("require_pool_allowlist", True)
        )
        self._kill_switch_file = self._risk_policy.get("kill_switch_file")

        # Build allowlist entry sets for fast lookup
        self._pool_entries = self._extract_entries(self._pool_allowlist)
        self._token_entries = self._extract_entries(self._token_allowlist)
        self._contract_entries = self._extract_entries(self._contract_allowlist)

        # Ledger path
        self._ledger_path = self._data_dir / "execution_ledger.jsonl"

    @staticmethod
    def _extract_entries(allowlist: dict | list) -> set[str]:
        """Extract entry identifiers from an allowlist document.

        Supports both EVM-style fields (address, contract_address, token_address)
        and Solana-style fields (mint, program_id).
        """
        if isinstance(allowlist, dict):
            entries = allowlist.get("entries", [])
        elif isinstance(allowlist, list):
            entries = allowlist
        else:
            return set()
        result: set[str] = set()
        for e in entries:
            if isinstance(e, str):
                result.add(e.lower())
            elif isinstance(e, dict):
                for key in (
                    "address",
                    "pool_address",
                    "token_address",
                    "contract_address",
                    "id",
                    "mint",
                    "program_id",
                ):
                    if key in e and isinstance(e[key], str):
                        result.add(e[key].lower())
                        break
        return result

    @property
    def policy_digest(self) -> str:
        return self._policy_digest

    @property
    def allowlist_digest(self) -> str:
        return self._allowlist_digest

    def evaluate(self, action: ActionDescriptor) -> PolicyDecision:
        """Evaluate an ActionDescriptor against all policy rules.

        Returns PolicyDecision with approved=True only if ALL checks pass.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        denial_reasons: list[str] = []
        warnings: list[str] = []

        # 1. Kill switch
        if self._check_kill_switch():
            denial_reasons.append("kill switch active")

        # 2. Macro state HALT
        if self._check_macro_halt():
            denial_reasons.append("macro state HALT")

        # 3. Autonomy level
        required_level = _ACTION_AUTONOMY_LEVELS.get(action.action_type, 99)
        if self._autonomy_level < required_level:
            denial_reasons.append(
                f"autonomy_level={self._autonomy_level} < required={required_level} for {action.action_type}"
            )

        # 4. Action type allowed
        if action.action_type not in _ALLOWED_ACTION_TYPES:
            denial_reasons.append(f"action_type={action.action_type!r} not allowed")

        # 5. Chain allowed
        if action.chain not in self._allowed_chains:
            denial_reasons.append(f"chain={action.chain!r} not in allowed_chains")

        # 6. Strategy type allowed
        if action.strategy_type not in self._allowed_strategy_types:
            denial_reasons.append(
                f"strategy_type={action.strategy_type!r} not in allowed_strategy_types"
            )

        # 7. Risk decision SKIP
        if action.risk_decision == "SKIP":
            denial_reasons.append("risk_decision=SKIP")

        # 8. Minimum risk score
        if action.risk_score < _MIN_RISK_SCORE:
            denial_reasons.append(
                f"risk_score={action.risk_score} < minimum={_MIN_RISK_SCORE}"
            )

        # 9. Transaction cap
        if action.estimated_tx_usd > self._max_tx_usd > 0:
            denial_reasons.append(
                f"estimated_tx_usd={action.estimated_tx_usd} > max_tx_usd={self._max_tx_usd}"
            )

        # 10. Wallet value cap
        if action.estimated_wallet_value_usd > self._max_wallet_value_usd > 0:
            denial_reasons.append(
                f"estimated_wallet_value_usd={action.estimated_wallet_value_usd} > max={self._max_wallet_value_usd}"
            )

        # 11. Daily spend cap
        if action.estimated_daily_spend_usd > self._max_daily_spend_usd > 0:
            denial_reasons.append(
                f"estimated_daily_spend_usd={action.estimated_daily_spend_usd} > max={self._max_daily_spend_usd}"
            )

        # 12. Slippage cap
        if action.slippage_bps > self._max_slippage_bps:
            denial_reasons.append(
                f"slippage_bps={action.slippage_bps} > max={self._max_slippage_bps}"
            )

        # 13. Blocked actions
        metadata_actions = action.metadata.get("actions", []) if isinstance(action.metadata, dict) else []
        action_labels = [action.action_type.lower()] + [
            str(a).lower() for a in metadata_actions
        ]
        for blocked in self._blocked_actions:
            if blocked.lower() in action_labels:
                denial_reasons.append(f"blocked action: {blocked}")

        # Check metadata for specific blocked patterns
        if isinstance(action.metadata, dict):
            if action.metadata.get("bridge"):
                denial_reasons.append("blocked action: bridge")
            if action.metadata.get("borrow"):
                denial_reasons.append("blocked action: borrow")
            if action.metadata.get("leverage"):
                denial_reasons.append("blocked action: leverage")
            if action.metadata.get("unlimited_approval"):
                denial_reasons.append("blocked action: unlimited_approval")

        # 14. Pool allowlist
        if self._require_pool_allowlist and action.pool_address:
            if action.pool_address.lower() not in self._pool_entries:
                denial_reasons.append(
                    f"pool_address={action.pool_address!r} not in pool allowlist"
                )

        # 15. Token allowlist
        if self._require_token_allowlist and action.token_addresses:
            for token in action.token_addresses:
                if token.lower() not in self._token_entries:
                    denial_reasons.append(
                        f"token={token!r} not in token allowlist"
                    )
                    break  # One denial is enough

        # 16. Contract allowlist
        if self._require_contract_allowlist:
            protocol_lower = action.protocol.lower() if action.protocol else ""
            if protocol_lower and protocol_lower not in self._contract_entries:
                denial_reasons.append(
                    f"protocol={action.protocol!r} not in contract allowlist"
                )

        # 17. Allowlist digest consistency
        current_digest = compute_allowlist_digest(
            self._contract_allowlist,
            self._token_allowlist,
            self._pool_allowlist,
        )
        if current_digest != self._allowlist_digest:
            denial_reasons.append("allowlist digest changed during evaluation")

        # --- Decision ---
        approved = len(denial_reasons) == 0
        approval_token: ApprovalToken | None = None

        if approved:
            expires = datetime.now(timezone.utc)
            from datetime import timedelta

            expires_str = (expires + timedelta(seconds=_APPROVAL_VALIDITY_SECONDS)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            approval_id = f"appr_{action.action_id}_{int(time.time() * 1000) % 100000:05d}"
            approval_token = ApprovalToken(
                approval_id=approval_id,
                action_id=action.action_id,
                candidate_hash=action.candidate_hash,
                policy_digest=self._policy_digest,
                allowlist_digest=self._allowlist_digest,
                approved=True,
                reasons=("all policy checks passed",),
                warnings=tuple(warnings),
                expires_at_utc=expires_str,
                created_at_utc=now,
            )

        decision = PolicyDecision(
            action_id=action.action_id,
            approved=approved,
            approval_token=approval_token,
            denial_reasons=tuple(denial_reasons),
            warnings=tuple(warnings),
            created_at_utc=now,
        )

        # Append to ledger
        self.append_ledger(
            {
                "event_type": "POLICY_APPROVED" if approved else "POLICY_DENIED",
                "action_id": action.action_id,
                "approved": approved,
                "reasons": list(approval_token.reasons) if approval_token else [],
                "denial_reasons": list(denial_reasons),
                "warnings": list(warnings),
                "policy_digest": self._policy_digest,
                "allowlist_digest": self._allowlist_digest,
                "created_at_utc": now,
            }
        )

        return decision

    def append_ledger(self, record: dict) -> None:
        """Append a record to the execution ledger (JSONL format)."""
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with open(self._ledger_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _check_kill_switch(self) -> bool:
        """Check if kill switch file exists."""
        if not self._kill_switch_file:
            # Also check local KILL_SWITCH.md for STOP content
            local_stop = self._base / "KILL_SWITCH.md"
            if local_stop.exists():
                content = local_stop.read_text(encoding="utf-8").strip()
                if content.upper().startswith("STOP"):
                    return True
            return False
        return Path(self._kill_switch_file).exists()

    def _check_macro_halt(self) -> bool:
        """Check if macro_state.json indicates HALT."""
        macro_path = self._data_dir / "macro_state.json"
        if not macro_path.exists():
            return False
        try:
            data = json.loads(macro_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state = data.get("state", "").upper()
                action = data.get("action", "").upper()
                return state == "HALT" or action == "HALT"
        except (json.JSONDecodeError, OSError):
            pass
        return False


__all__ = [
    "ActionDescriptor",
    "ApprovalToken",
    "PolicyDecision",
    "PolicyEngine",
    "canonical_digest",
    "compute_allowlist_digest",
    "load_json",
    "risk_assessment_to_action",
]
