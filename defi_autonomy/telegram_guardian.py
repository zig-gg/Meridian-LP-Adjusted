"""Telegram Guardian — Sprint 4, Phase 4.2.

Operator-control layer for Hermes DeFi Autonomy. Provides status reporting,
emergency HALT/RESUME/PAUSE, cycle report summaries, and a stubbed Level 2
approval flow for future signing.

No private-key loading. No signing. No broadcast. No real Telegram API calls
in tests. No wallet access. No modification to existing Hermes daemons.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================================
# Exceptions
# ============================================================================


class TelegramGuardianError(Exception):
    """Base for all Telegram Guardian errors."""


class UnauthorizedChatError(TelegramGuardianError):
    """Chat ID is not authorized."""


class InvalidCommandError(TelegramGuardianError):
    """Command is not recognized or malformed."""


class ResumeRefusedError(TelegramGuardianError):
    """Resume was refused due to safety conditions."""


class ApprovalFlowDisabled(TelegramGuardianError):
    """Approval flow is not enabled at current autonomy level."""


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True, slots=True)
class GuardianCommand:
    """Parsed operator command."""

    command: str
    args: tuple[str, ...]
    chat_id: str
    user_id: str | None
    received_at_utc: str


@dataclass(frozen=True, slots=True)
class GuardianResponse:
    """Response to an operator command."""

    ok: bool
    command: str
    message: str
    created_at_utc: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


# ============================================================================
# Utility
# ============================================================================


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _response(ok: bool, command: str, message: str, warnings=(), errors=()) -> GuardianResponse:
    return GuardianResponse(
        ok=ok,
        command=command,
        message=message,
        created_at_utc=_now_utc(),
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


# ============================================================================
# Authorization
# ============================================================================


def load_authorized_chat_ids() -> set[str]:
    """Load authorized Telegram chat IDs from environment or config.

    Reads HERMES_TELEGRAM_AUTHORIZED_CHATS (comma-separated) from env.
    Returns empty set if not configured.
    """
    raw = os.environ.get("HERMES_TELEGRAM_AUTHORIZED_CHATS", "")
    if not raw.strip():
        return set()
    return {cid.strip() for cid in raw.split(",") if cid.strip()}


# ============================================================================
# Command parsing
# ============================================================================

_VALID_COMMANDS: frozenset[str] = frozenset(
    {"/status", "/halt", "/resume", "/pause", "/cycle",
     "/positions", "/policy", "/help", "/approve", "/deny"}
)


def parse_command(
    text: str, chat_id: str, user_id: str | None = None
) -> GuardianCommand:
    """Parse a raw text message into a GuardianCommand.

    Raises InvalidCommandError if the command is not recognized.
    """
    text = text.strip()
    if not text.startswith("/"):
        raise InvalidCommandError(f"not a command: {text!r}")

    parts = text.split()
    cmd = parts[0].lower()
    args = tuple(parts[1:])

    if cmd not in _VALID_COMMANDS:
        raise InvalidCommandError(f"unknown command: {cmd!r}")

    return GuardianCommand(
        command=cmd,
        args=args,
        chat_id=chat_id,
        user_id=user_id,
        received_at_utc=_now_utc(),
    )


# ============================================================================
# Command handlers
# ============================================================================


def handle_command(
    command: GuardianCommand,
    base_dir: Path | str,
    authorized_chat_ids: set[str] | None = None,
) -> GuardianResponse:
    """Handle a parsed command. Returns GuardianResponse.

    Checks authorization first. Dispatches to specific handlers.
    """
    base = Path(base_dir)

    # Authorization check
    if authorized_chat_ids is not None and command.chat_id not in authorized_chat_ids:
        raise UnauthorizedChatError(
            f"chat_id={command.chat_id!r} not authorized"
        )

    handlers = {
        "/status": lambda: _handle_status(base),
        "/halt": lambda: _handle_halt(base),
        "/resume": lambda: _handle_resume(base),
        "/pause": lambda: _handle_pause(base, command.args),
        "/cycle": lambda: _handle_cycle(base),
        "/positions": lambda: _handle_positions(base),
        "/policy": lambda: _handle_policy(base),
        "/help": lambda: _handle_help(),
        "/approve": lambda: _handle_approve(base, command.args),
        "/deny": lambda: _handle_deny(base, command.args),
    }

    handler = handlers.get(command.command)
    if handler is None:
        return _response(False, command.command, "Unknown command.", errors=("unknown command",))

    try:
        return handler()
    except TelegramGuardianError as e:
        return _response(False, command.command, str(e), errors=(str(e),))
    except Exception as e:
        return _response(
            False, command.command,
            f"Internal error: {type(e).__name__}",
            errors=(f"{type(e).__name__}: {str(e)[:100]}",),
        )


# ============================================================================
# Individual handlers
# ============================================================================


def format_status(base_dir: Path | str) -> str:
    """Format a status summary string."""
    base = Path(base_dir)
    data_dir = base / "data"
    policy = _load_json(data_dir / "risk_policy.json")
    report = _load_json(data_dir / "cycle_report.json")

    autonomy_level = policy.get("autonomy_level", 0) if isinstance(policy, dict) else 0
    kill_switch = _is_kill_switch_active(base, policy if isinstance(policy, dict) else {})

    lines = [
        "=== Hermes DeFi Autonomy Status ===",
        f"Autonomy Level: {autonomy_level}",
        f"Kill Switch: {'ACTIVE' if kill_switch else 'inactive'}",
    ]

    if isinstance(report, dict) and report:
        lines.append(f"Last Cycle: {report.get('status', 'unknown')}")
        lines.append(f"Candidates: {report.get('candidate_count', 0)}")
        lines.append(f"Approved: {report.get('approved_count', 0)}")
        lines.append(f"Denied: {report.get('denied_count', 0)}")
        lines.append(f"Simulations Passed: {report.get('simulation_passed_count', 0)}")
        lines.append(f"Signing Prepared: {report.get('signing_prepared_count', 0)}")
    else:
        lines.append("Last Cycle: no report available")

    return "\n".join(lines)


def _handle_status(base: Path) -> GuardianResponse:
    msg = format_status(base)
    return _response(True, "/status", msg)


def _handle_halt(base: Path) -> GuardianResponse:
    activate_kill_switch(base)
    return _response(True, "/halt", "Kill switch ACTIVATED. All operations halted.")


def _handle_resume(base: Path) -> GuardianResponse:
    if not resume_allowed(base):
        raise ResumeRefusedError("resume not allowed: safety conditions not met")
    clear_kill_switch(base)
    return _response(True, "/resume", "Kill switch cleared. Operations may resume.")


def _handle_pause(base: Path, args: tuple[str, ...]) -> GuardianResponse:
    if not args:
        return _response(False, "/pause", "Usage: /pause <minutes>", errors=("missing duration",))
    try:
        minutes = int(args[0])
    except ValueError:
        return _response(False, "/pause", "Invalid duration. Usage: /pause <minutes>",
                         errors=("invalid duration",))
    if minutes <= 0 or minutes > 1440:
        return _response(False, "/pause", "Duration must be 1-1440 minutes.",
                         errors=("duration out of range",))
    pause_for_duration(base, minutes)
    return _response(True, "/pause", f"Paused for {minutes} minutes. Kill switch activated.")


def format_cycle_report(base_dir: Path | str) -> str:
    """Format the latest cycle report as a summary string."""
    base = Path(base_dir)
    report = _load_json(base / "data" / "cycle_report.json")
    if not isinstance(report, dict) or not report:
        return "No cycle report available."

    lines = [
        "=== Latest Cycle Report ===",
        f"Cycle ID: {report.get('cycle_id', 'unknown')}",
        f"Status: {report.get('status', 'unknown')}",
        f"Started: {report.get('started_at_utc', 'unknown')}",
        f"Finished: {report.get('finished_at_utc', 'unknown')}",
        f"Autonomy Level: {report.get('autonomy_level', 0)}",
        f"Ingestion: {report.get('ingestion_status', 'unknown')}",
        f"Candidates: {report.get('candidate_count', 0)}",
        f"Risk Assessments: {report.get('risk_assessment_count', 0)}",
        f"Approved: {report.get('approved_count', 0)}",
        f"Denied: {report.get('denied_count', 0)}",
        f"Simulations Passed: {report.get('simulation_passed_count', 0)}",
        f"Simulations Failed: {report.get('simulation_failed_count', 0)}",
        f"Signing Prepared: {report.get('signing_prepared_count', 0)}",
    ]
    errors = report.get("errors", [])
    if errors:
        lines.append(f"Errors: {len(errors)}")
    warnings = report.get("warnings", [])
    if warnings:
        lines.append(f"Warnings: {len(warnings)}")
    return "\n".join(lines)


def _handle_cycle(base: Path) -> GuardianResponse:
    msg = format_cycle_report(base)
    return _response(True, "/cycle", msg)


def _handle_positions(base: Path) -> GuardianResponse:
    from defi_autonomy.position_lifecycle import get_open_positions
    positions_path = base / "data" / "positions.jsonl"
    open_pos = get_open_positions(positions_path)
    if not open_pos:
        return _response(True, "/positions", "No active positions. (Watch-only mode)")
    lines = ["=== Active Positions ==="]
    for p in open_pos[:10]:
        lines.append(
            f"  {p.get('position_id','?')} | {p.get('chain','?')} | "
            f"{p.get('protocol','?')} | {p.get('strategy_type','?')} | "
            f"${p.get('entry_estimated_tx_usd',0):.2f} | {p.get('opened_at_utc','?')}"
        )
    if len(open_pos) > 10:
        lines.append(f"  ... and {len(open_pos)-10} more")
    return _response(True, "/positions", "\n".join(lines))


def _handle_policy(base: Path) -> GuardianResponse:
    data_dir = base / "data"
    policy = _load_json(data_dir / "risk_policy.json")
    if not isinstance(policy, dict):
        return _response(True, "/policy", "No policy loaded.")

    # Only show non-secret fields
    lines = [
        "=== Risk Policy Summary ===",
        f"Autonomy Level: {policy.get('autonomy_level', 0)}",
        f"Max Wallet Value: ${policy.get('max_wallet_value_usd', 0)}",
        f"Max TX: ${policy.get('max_tx_usd', 0)}",
        f"Max Daily Spend: ${policy.get('max_daily_spend_usd', 0)}",
        f"Max Slippage: {policy.get('max_slippage_bps', 0)} bps",
        f"Allowed Chains: {', '.join(policy.get('allowed_chains', []))}",
        f"Allowed Strategies: {', '.join(policy.get('allowed_strategy_types', []))}",
        f"Blocked Actions: {', '.join(policy.get('blocked_actions', []))}",
    ]
    return _response(True, "/policy", "\n".join(lines))


def _handle_help() -> GuardianResponse:
    msg = (
        "=== Hermes Guardian Commands ===\n"
        "/status - Current system status\n"
        "/halt - Emergency stop all operations\n"
        "/resume - Clear kill switch (if allowed)\n"
        "/pause <minutes> - Pause for N minutes\n"
        "/cycle - Latest cycle report\n"
        "/positions - Active positions\n"
        "/policy - Risk policy summary\n"
        "/approve <id> - Approve action (Level 2+)\n"
        "/deny <id> - Deny action (Level 2+)\n"
        "/help - This message"
    )
    return _response(True, "/help", msg)


def _handle_approve(base: Path, args: tuple[str, ...]) -> GuardianResponse:
    """Handle /approve command — create OperatorApprovalRecord for Level 2 broadcast."""
    data_dir = base / "data"
    policy = _load_json(data_dir / "risk_policy.json")
    autonomy_level = policy.get("autonomy_level", 0) if isinstance(policy, dict) else 0

    if autonomy_level < 2:
        raise ApprovalFlowDisabled(
            "approval flow disabled at autonomy_level < 2"
        )

    if not args:
        return _response(False, "/approve", "Usage: /approve <envelope_id>",
                         errors=("missing envelope_id",))

    envelope_id = args[0]

    # Load wallet execution ledger to find the envelope
    ledger_path = data_dir / "wallet_execution_ledger.jsonl"
    envelope_record = None
    if ledger_path.exists():
        try:
            for line in ledger_path.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("envelope_id") == envelope_id:
                    envelope_record = record
                    break
        except (json.JSONDecodeError, OSError):
            pass

    if envelope_record is None:
        return _response(False, "/approve", f"Envelope {envelope_id} not found.",
                         errors=("unknown envelope_id",))

    if not envelope_record.get("broadcast_allowed", False):
        return _response(False, "/approve",
                         f"Envelope {envelope_id} does not have broadcast_allowed=true.",
                         errors=("broadcast_allowed is false",))

    if envelope_record.get("broadcasted", False):
        return _response(False, "/approve",
                         f"Envelope {envelope_id} already broadcasted.",
                         errors=("already broadcasted",))

    # Create OperatorApprovalRecord
    from datetime import timedelta
    timeout_seconds = int(
        policy.get("telegram_approval_timeout_seconds", 300) if isinstance(policy, dict) else 300
    )
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    approval_record = {
        "approval_record_id": f"appr_rec_{int(time.time() * 1000) % 1_000_000:06d}",
        "action_id": envelope_record.get("action_id", ""),
        "envelope_id": envelope_id,
        "simulation_id": envelope_record.get("simulation_id", ""),
        "candidate_hash": envelope_record.get("candidate_hash", ""),
        "signed_payload_hash": envelope_record.get("signed_payload_hash", ""),
        "approved_by_chat_id": "",  # Set by caller
        "approved_by_user_id": None,
        "approved_at_utc": now_str,
        "expires_at_utc": expires,
        "approval_message": f"Approved by operator via Telegram at {now_str}",
    }

    # Write to operator_approvals.jsonl
    approvals_path = data_dir / "operator_approvals.jsonl"
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(approval_record, sort_keys=True, separators=(",", ":"))
    with open(approvals_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    return _response(
        True, "/approve",
        f"Envelope {envelope_id} approved. Expires: {expires}",
        warnings=("approval valid for broadcast within timeout period",),
    )


def _handle_deny(base: Path, args: tuple[str, ...]) -> GuardianResponse:
    data_dir = base / "data"
    policy = _load_json(data_dir / "risk_policy.json")
    autonomy_level = policy.get("autonomy_level", 0) if isinstance(policy, dict) else 0
    if autonomy_level < 2:
        raise ApprovalFlowDisabled(
            "approval flow disabled at autonomy_level < 2"
        )
    if not args:
        return _response(False, "/deny", "Usage: /deny <action_id>", errors=("missing action_id",))
    return _response(True, "/deny", f"Action {args[0]} denied. (Stub — no effect in Phase 4.2)")


# ============================================================================
# Kill switch management
# ============================================================================


def _is_kill_switch_active(base: Path, risk_policy: dict) -> bool:
    ks_file = risk_policy.get("kill_switch_file")
    if ks_file and Path(ks_file).exists():
        return True
    local_stop = base / "KILL_SWITCH.md"
    if local_stop.exists():
        content = local_stop.read_text(encoding="utf-8").strip()
        if content.upper().startswith("STOP"):
            return True
    return False


def activate_kill_switch(base_dir: Path | str) -> None:
    """Create the STOP kill switch file."""
    base = Path(base_dir)
    ks_path = base / "KILL_SWITCH.md"
    ks_path.write_text(
        f"STOP\nActivated by Telegram Guardian at {_now_utc()}\n",
        encoding="utf-8",
    )


def clear_kill_switch(base_dir: Path | str) -> None:
    """Remove the STOP kill switch file."""
    base = Path(base_dir)
    ks_path = base / "KILL_SWITCH.md"
    if ks_path.exists():
        content = ks_path.read_text(encoding="utf-8")
        if content.strip().upper().startswith("STOP"):
            ks_path.write_text(
                f"CLEARED\nCleared by Telegram Guardian at {_now_utc()}\n",
                encoding="utf-8",
            )


def pause_for_duration(base_dir: Path | str, duration_minutes: int) -> None:
    """Activate kill switch with pause metadata."""
    base = Path(base_dir)
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    expires = (now + timedelta(minutes=duration_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ks_path = base / "KILL_SWITCH.md"
    ks_path.write_text(
        f"STOP\nPaused by Telegram Guardian at {_now_utc()}\n"
        f"Expires: {expires}\nDuration: {duration_minutes} minutes\n",
        encoding="utf-8",
    )


def resume_allowed(base_dir: Path | str) -> bool:
    """Check if resume is allowed.

    Currently always returns True. Future versions may check for recent
    policy violations or require cooldown periods.
    """
    return True


# ============================================================================
# Approval stub
# ============================================================================


def approve_transaction_stub(
    base_dir: Path | str, args: tuple[str, ...] = ()
) -> GuardianResponse:
    """Stub for transaction approval. Raises ApprovalFlowDisabled at level < 2."""
    base = Path(base_dir)
    data_dir = base / "data"
    policy = _load_json(data_dir / "risk_policy.json")
    autonomy_level = policy.get("autonomy_level", 0) if isinstance(policy, dict) else 0

    if autonomy_level < 2:
        raise ApprovalFlowDisabled(
            "approval flow disabled at autonomy_level < 2"
        )

    if not args:
        return _response(False, "/approve", "Usage: /approve <action_id>",
                         errors=("missing action_id",))

    # Stub: acknowledge but do not actually approve
    return _response(
        True, "/approve",
        f"Action {args[0]} acknowledged. (Stub — approval flow not yet active in Phase 4.2)",
        warnings=("approval flow is stubbed; no signing will occur",),
    )


__all__ = [
    "ApprovalFlowDisabled",
    "GuardianCommand",
    "GuardianResponse",
    "InvalidCommandError",
    "ResumeRefusedError",
    "TelegramGuardianError",
    "UnauthorizedChatError",
    "activate_kill_switch",
    "approve_transaction_stub",
    "clear_kill_switch",
    "format_cycle_report",
    "format_status",
    "handle_command",
    "load_authorized_chat_ids",
    "parse_command",
    "pause_for_duration",
    "resume_allowed",
]
