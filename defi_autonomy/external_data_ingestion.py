"""External_Data_Ingestion orchestrator — Phase 1.5.

Loads the source allowlist, runs approved SourceAdapters through
ReadOnlyHttpClient, records raw snapshots, normalizes candidates,
records source health, and writes normalized_yield_candidates.json.

No signing. No key loading. No wallet access. No PolicyEngine/RiskScorer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from defi_autonomy.schemas.normalized_candidate import (
    NormalizedCandidate,
    NormalizedCandidateError,
    validate_candidate,
)
from defi_autonomy.sources.base import (
    ReadOnlyHttpClient,
    SourceAdapter,
    SourceAdapterError,
    SourceAllowlistEntry,
    load_source_allowlist,
)
from defi_autonomy.sources.defillama_adapter import DeFiLlamaAdapter
from defi_autonomy.sources.meteora_adapter import MeteoraAdapter
from defi_autonomy.sources.stablecoin_benchmark_adapter import (
    StablecoinBenchmarkAdapter,
)
from defi_autonomy.sources.xstocks_adapter import XStocksAdapter

logger = logging.getLogger(__name__)

# ============================================================================
# Adapter Registry
# ============================================================================


def load_adapter_registry() -> dict[str, SourceAdapter]:
    """Return the explicit adapter registry.

    Only registered adapters are run. No auto-discovery.
    """
    return {
        "defillama": DeFiLlamaAdapter(),
        "stablecoin_benchmark": StablecoinBenchmarkAdapter(),
        "xstocks": XStocksAdapter(),
        "meteora": MeteoraAdapter(),
    }


# ============================================================================
# Atomic JSON writer
# ============================================================================


def write_json_atomic(path: Path | str, data: Any) -> None:
    """Write JSON data atomically via temp file then replace.

    Ensures no partial/corrupt JSON on disk even if the process is killed
    mid-write.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    # Write to temp file in same directory, then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(p.parent), suffix=".tmp", prefix=".ingestion_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # On Windows, target must not exist for os.replace to work reliably
        # os.replace handles this correctly on both platforms
        os.replace(tmp_path, str(p))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ============================================================================
# Ingestion cycle
# ============================================================================


def _generate_cycle_id() -> str:
    """Generate a unique cycle ID based on timestamp."""
    now = datetime.now(timezone.utc)
    return f"cycle_{now.strftime('%Y%m%dT%H%M%SZ')}_{int(time.time() * 1000) % 100000:05d}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(s: str, max_len: int = 500) -> str:
    """Safely truncate a string."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "...[truncated]"


def _load_source_health(path: Path) -> dict[str, Any]:
    """Load existing source_health.json or return empty dict."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _update_source_health(
    health: dict[str, Any],
    source_id: str,
    success: bool,
    error_type: str | None = None,
    error_message: str | None = None,
    latency_ms: float | None = None,
) -> None:
    """Update source health tracking for a source."""
    if source_id not in health:
        health[source_id] = {
            "last_success_utc": None,
            "last_failure_utc": None,
            "consecutive_failures": 0,
            "total_successes": 0,
            "total_failures": 0,
            "last_error_type": None,
            "last_error_message": None,
            "last_latency_ms": None,
        }
    entry = health[source_id]
    now = _now_utc()
    if success:
        entry["last_success_utc"] = now
        entry["consecutive_failures"] = 0
        entry["total_successes"] = entry.get("total_successes", 0) + 1
    else:
        entry["last_failure_utc"] = now
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["total_failures"] = entry.get("total_failures", 0) + 1
        entry["last_error_type"] = error_type
        entry["last_error_message"] = _truncate(error_message or "")
    if latency_ms is not None:
        entry["last_latency_ms"] = latency_ms


def run_ingestion_cycle(
    base_dir: Path | str,
    _client_factory: Any = None,
) -> dict[str, Any]:
    """Run a full ingestion cycle.

    Args:
        base_dir: Path to the defi_autonomy directory (contains data/ subdir).
        _client_factory: Optional factory for ReadOnlyHttpClient (for testing).
            Signature: (entry: SourceAllowlistEntry) -> ReadOnlyHttpClient-like.

    Returns:
        dict with keys:
        - cycle_id: str
        - status: "OK" | "PARTIAL" | "NO_VALID_DATA"
        - candidate_count: int
        - sources_succeeded: list[str]
        - sources_failed: list[str]
        - sources_missing_adapter: list[str]
    """
    base = Path(base_dir)
    data_dir = base / "data"
    allowlist_path = data_dir / "source_allowlist.json"

    cycle_id = _generate_cycle_id()
    now = _now_utc()

    # Load allowlist
    try:
        allowlist = load_source_allowlist(allowlist_path)
    except Exception as e:
        logger.error("Failed to load source allowlist: %s", e)
        # Write empty candidates and return
        empty_candidates = {
            "cycle_id": cycle_id,
            "generated_at_utc": now,
            "candidate_count": 0,
            "candidates": [],
        }
        write_json_atomic(data_dir / "normalized_yield_candidates.json", empty_candidates)
        return {
            "cycle_id": cycle_id,
            "status": "NO_VALID_DATA",
            "candidate_count": 0,
            "sources_succeeded": [],
            "sources_failed": [],
            "sources_missing_adapter": [],
        }

    # Load adapter registry
    registry = load_adapter_registry()

    # Load existing source health
    health_path = data_dir / "source_health.json"
    source_health = _load_source_health(health_path)

    # Track results
    all_candidates: list[dict[str, Any]] = []
    raw_snapshots: list[dict[str, Any]] = []
    sources_succeeded: list[str] = []
    sources_failed: list[str] = []
    sources_missing_adapter: list[str] = []

    for source_id, entry in allowlist.items():
        # Check if adapter exists in registry
        if source_id not in registry:
            logger.info(
                "SOURCE_ADAPTER_MISSING: no adapter registered for source_id=%r",
                source_id,
            )
            sources_missing_adapter.append(source_id)
            continue

        adapter = registry[source_id]

        # Build URLs
        urls = adapter.build_urls(entry)
        if not urls:
            logger.debug("No URLs for source %s, skipping", source_id)
            continue

        # Create client
        if _client_factory is not None:
            client = _client_factory(entry)
        else:
            client = ReadOnlyHttpClient(entry=entry)

        # Fetch from each URL
        source_success = False
        source_raw_bytes: bytes | None = None

        for url in urls:
            start_ms = time.time() * 1000
            snapshot: dict[str, Any] = {
                "source_id": source_id,
                "adapter_name": entry.adapter_name,
                "url": url,
                "fetched_at_utc": _now_utc(),
            }
            try:
                raw_bytes = client.request("GET", url)
                elapsed_ms = time.time() * 1000 - start_ms

                response_hash = hashlib.sha256(raw_bytes).hexdigest()
                snapshot["response_sha256"] = response_hash
                snapshot["response_size_bytes"] = len(raw_bytes)
                snapshot["status"] = "SUCCESS"
                snapshot["latency_ms"] = round(elapsed_ms, 1)

                raw_snapshots.append(snapshot)
                source_raw_bytes = raw_bytes
                source_success = True
                # Use first successful URL
                break

            except SourceAdapterError as e:
                elapsed_ms = time.time() * 1000 - start_ms
                error_type = type(e).__name__
                error_msg = str(e)
                snapshot["status"] = "FAILURE"
                snapshot["error_type"] = error_type
                snapshot["error_message"] = _truncate(error_msg)
                snapshot["latency_ms"] = round(elapsed_ms, 1)
                snapshot["response_sha256"] = None
                snapshot["response_size_bytes"] = 0
                raw_snapshots.append(snapshot)
                logger.warning(
                    "Fetch failed for %s url=%s: %s: %s",
                    source_id, url, error_type, error_msg,
                )
                continue

            except Exception as e:
                elapsed_ms = time.time() * 1000 - start_ms
                error_type = type(e).__name__
                error_msg = str(e)
                snapshot["status"] = "FAILURE"
                snapshot["error_type"] = error_type
                snapshot["error_message"] = _truncate(error_msg)
                snapshot["latency_ms"] = round(elapsed_ms, 1)
                snapshot["response_sha256"] = None
                snapshot["response_size_bytes"] = 0
                raw_snapshots.append(snapshot)
                logger.warning(
                    "Unexpected error for %s url=%s: %s: %s",
                    source_id, url, error_type, error_msg,
                )
                continue

        # Update source health
        if source_success:
            _update_source_health(
                source_health, source_id, success=True,
                latency_ms=raw_snapshots[-1].get("latency_ms"),
            )
            sources_succeeded.append(source_id)
        else:
            last_snapshot = next(
                (s for s in reversed(raw_snapshots) if s["source_id"] == source_id),
                None,
            )
            _update_source_health(
                source_health, source_id, success=False,
                error_type=last_snapshot.get("error_type") if last_snapshot else None,
                error_message=last_snapshot.get("error_message") if last_snapshot else None,
                latency_ms=last_snapshot.get("latency_ms") if last_snapshot else None,
            )
            sources_failed.append(source_id)
            continue

        # Normalize
        if source_raw_bytes is not None:
            try:
                # Pass confidence from allowlist entry
                if hasattr(adapter, "_confidence"):
                    adapter._confidence = entry.source_confidence_score
                candidates = adapter.normalize(source_raw_bytes)
            except Exception as e:
                logger.warning(
                    "Normalization failed for %s: %s: %s",
                    source_id, type(e).__name__, e,
                )
                candidates = []

            for candidate in candidates:
                if not isinstance(candidate, NormalizedCandidate):
                    continue
                try:
                    validate_candidate(candidate)
                except NormalizedCandidateError:
                    continue
                c_dict = candidate.to_dict()
                c_dict["_candidate_hash"] = candidate.hash_sha256()
                all_candidates.append(c_dict)

    # Determine status
    if sources_succeeded:
        status = "OK" if not sources_failed else "PARTIAL"
    else:
        status = "NO_VALID_DATA"

    # Write outputs
    candidates_output = {
        "cycle_id": cycle_id,
        "generated_at_utc": _now_utc(),
        "candidate_count": len(all_candidates),
        "candidates": all_candidates,
    }
    write_json_atomic(data_dir / "normalized_yield_candidates.json", candidates_output)
    write_json_atomic(data_dir / "raw_snapshots.json", raw_snapshots)
    write_json_atomic(data_dir / "source_health.json", source_health)

    return {
        "cycle_id": cycle_id,
        "status": status,
        "candidate_count": len(all_candidates),
        "sources_succeeded": sources_succeeded,
        "sources_failed": sources_failed,
        "sources_missing_adapter": sources_missing_adapter,
    }


__all__ = [
    "load_adapter_registry",
    "run_ingestion_cycle",
    "write_json_atomic",
]
