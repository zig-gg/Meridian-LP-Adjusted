"""Stablecoin Benchmark SourceAdapter — Phase 1.4.

Normalizes conservative stablecoin lending and stable-stable LP yield data
into NormalizedCandidate instances. These serve as the baseline benchmark
against which future xStocks and other opportunities are compared.

Only emits candidates for:
- Conservative stablecoin assets (USDC, USDT, DAI, USDS, PYUSD, FDUSD).
- Allowed strategy types: stablecoin_lending, stable_stable_lp.
- Allowed chains: Base, BNB Chain, Solana.

Skips volatile, LSD, restaking, leveraged, recursive, memecoin, and
reward-only farms.

No network calls inside `normalize`. No signing. No key loading.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from defi_autonomy.schemas.normalized_candidate import (
    ALLOWED_CHAINS,
    NormalizedCandidate,
    NormalizedCandidateError,
    from_dict,
    validate_candidate,
)
from defi_autonomy.sources.base import (
    SourceAdapter,
    SourceAllowlistEntry,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

_CONSERVATIVE_STABLES: frozenset[str] = frozenset(
    {"USDC", "USDT", "DAI", "USDS", "PYUSD", "FDUSD"}
)

_CHAIN_MAP: dict[str, str] = {
    "Base": "Base",
    "BSC": "BNB Chain",
    "Binance": "BNB Chain",
    "BNB Chain": "BNB Chain",
    "Solana": "Solana",
}

# Keywords that indicate unsafe/volatile strategies to skip
_UNSAFE_KEYWORDS: frozenset[str] = frozenset(
    {
        "leveraged",
        "recursive",
        "restaking",
        "restaked",
        "lsd",
        "lst",
        "meme",
        "degen",
        "ponzi",
        "loop",
    }
)


# ============================================================================
# Helpers
# ============================================================================


def _extract_symbols(symbol: str) -> list[str]:
    """Split a pool symbol into individual token symbols."""
    # Common separators in yield pool symbols
    for sep in ("-", "/", "+", " "):
        if sep in symbol:
            return [s.strip().upper() for s in symbol.split(sep) if s.strip()]
    return [symbol.strip().upper()]


def _is_conservative_stable(symbol: str) -> bool:
    """Check if ALL tokens in the symbol are conservative stablecoins."""
    parts = _extract_symbols(symbol)
    if not parts:
        return False
    return all(
        any(stable in part for stable in _CONSERVATIVE_STABLES)
        for part in parts
    )


def _has_unsafe_keywords(pool: dict[str, Any]) -> bool:
    """Check if pool metadata contains unsafe strategy keywords."""
    fields_to_check = [
        str(pool.get("project", "")),
        str(pool.get("symbol", "")),
        str(pool.get("poolMeta", "")),
    ]
    combined = " ".join(fields_to_check).lower()
    return any(kw in combined for kw in _UNSAFE_KEYWORDS)


def _classify_strategy(pool: dict[str, Any]) -> str | None:
    """Classify pool into allowed strategy type or None to skip.

    stablecoin_lending: single-asset stablecoin supply/lending (no IL).
    stable_stable_lp: stable-stable pair LP (has IL but minimal).
    """
    il_risk = pool.get("ilRisk", "")
    symbol = str(pool.get("symbol", ""))
    parts = _extract_symbols(symbol)

    # Lending: single asset or no IL risk
    if il_risk == "no" or il_risk is False:
        return "stablecoin_lending"

    # LP: multiple tokens, all stable
    if len(parts) >= 2 and (il_risk == "yes" or il_risk is True):
        return "stable_stable_lp"

    # Heuristic: if symbol has multiple stable tokens separated by delimiter
    if len(parts) >= 2:
        return "stable_stable_lp"

    # Single token with IL risk somehow — treat as lending
    if len(parts) == 1:
        return "stablecoin_lending"

    return None


# ============================================================================
# StablecoinBenchmarkAdapter
# ============================================================================


class StablecoinBenchmarkAdapter(SourceAdapter):
    """Concrete adapter for stablecoin benchmark yield data.

    Produces NormalizedCandidate instances only for conservative stablecoin
    opportunities. Used as the baseline benchmark for opportunity comparison.
    """

    _SOURCE_ID = "stablecoin_benchmark"
    _ADAPTER_NAME = "stablecoin_benchmark_adapter"
    _DEFAULT_CONFIDENCE = 0.5

    def __init__(self, source_confidence_score: float | None = None) -> None:
        self._confidence = (
            source_confidence_score
            if source_confidence_score is not None
            else self._DEFAULT_CONFIDENCE
        )

    @property
    def source_id(self) -> str:
        return self._SOURCE_ID

    @property
    def adapter_name(self) -> str:
        return self._ADAPTER_NAME

    def build_urls(self, entry: SourceAllowlistEntry) -> list[str]:
        """Build benchmark yield endpoint URLs from allowlisted domains.

        Targets the /pools endpoint on yields.llama.fi or api.llama.fi domains.
        """
        urls: list[str] = []
        for domain in entry.domains:
            if "llama" in domain.lower():
                urls.append(f"https://{domain}/pools")
        return urls

    def normalize(
        self, raw: bytes | str | dict | list
    ) -> list[NormalizedCandidate]:
        """Convert raw yield pool data into conservative benchmark candidates.

        Accepts bytes, str (JSON), dict (with "data" key), or list of pools.
        Only emits candidates for conservative stablecoin opportunities.
        No network calls.
        """
        pools = self._parse_raw(raw)
        if pools is None:
            return []

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates: list[NormalizedCandidate] = []

        for pool in pools:
            candidate = self._normalize_pool(pool, now_utc)
            if candidate is not None:
                candidates.append(candidate)

        return candidates

    def _parse_raw(self, raw: bytes | str | dict | list) -> list[dict] | None:
        """Parse raw input into a list of pool dicts."""
        if isinstance(raw, bytes):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning(
                    "stablecoin_benchmark_adapter: failed to parse bytes as JSON"
                )
                return None
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "stablecoin_benchmark_adapter: failed to parse str as JSON"
                )
                return None
        elif isinstance(raw, (dict, list)):
            parsed = raw
        else:
            logger.warning(
                "stablecoin_benchmark_adapter: unsupported raw type %s",
                type(raw).__name__,
            )
            return None

        if isinstance(parsed, dict):
            if "data" in parsed and isinstance(parsed["data"], list):
                return parsed["data"]
            return [parsed]
        elif isinstance(parsed, list):
            return parsed
        return None

    def _normalize_pool(
        self, pool: dict, fetched_at: str
    ) -> NormalizedCandidate | None:
        """Normalize a single pool. Returns None if pool should be skipped."""
        if not isinstance(pool, dict):
            return None

        # Required fields
        chain_raw = pool.get("chain")
        project = pool.get("project")
        symbol = pool.get("symbol")
        tvl = pool.get("tvlUsd")

        if not chain_raw or not project or not symbol or tvl is None:
            return None

        # Chain mapping
        chain = _CHAIN_MAP.get(str(chain_raw))
        if chain is None or chain not in ALLOWED_CHAINS:
            return None

        # Must be conservative stablecoin
        if not _is_conservative_stable(str(symbol)):
            return None

        # Skip unsafe strategies
        if _has_unsafe_keywords(pool):
            return None

        # Stablecoin flag should be true for benchmark candidates
        if not pool.get("stablecoin", False):
            return None

        # Strategy classification
        strategy_type = _classify_strategy(pool)
        if strategy_type is None:
            return None

        # APY extraction
        try:
            apy_base = float(pool.get("apyBase") or 0)
            apy_reward = float(pool.get("apyReward") or 0)
            apy_total = pool.get("apy")
            if apy_total is not None:
                advertised_apy = float(apy_total)
            else:
                advertised_apy = apy_base + apy_reward
            fee_apr = apy_base
            reward_apr = apy_reward
        except (TypeError, ValueError):
            return None

        # Schema bounds enforcement
        if advertised_apy < -1 or advertised_apy > 100:
            return None
        if fee_apr < 0:
            fee_apr = 0.0
        if fee_apr > 100:
            return None
        if reward_apr < 0:
            reward_apr = 0.0
        if reward_apr > 100:
            return None

        # TVL
        try:
            tvl_usd = float(tvl)
        except (TypeError, ValueError):
            return None
        if tvl_usd <= 0:
            return None

        # Volume
        try:
            volume_24h = float(pool.get("volumeUsd1d") or 0)
        except (TypeError, ValueError):
            volume_24h = 0.0
        if volume_24h < 0:
            volume_24h = 0.0

        # Pool address
        pool_address = pool.get("pool")
        if pool_address is not None:
            pool_address = str(pool_address)
            if not pool_address:
                pool_address = None

        # Token addresses
        underlying = pool.get("underlyingTokens")
        if isinstance(underlying, list):
            token_addresses = [str(t) for t in underlying if t][:4]
        else:
            token_addresses = []

        # Venue ID
        venue_id = f"{chain.lower().replace(' ', '_')}:{project}:{symbol}"
        if len(venue_id) > 128:
            venue_id = venue_id[:128]

        # Source URL
        source_url = "https://yields.llama.fi/pools"

        # Source timestamp
        source_ts = pool.get("timestamp")
        if source_ts and isinstance(source_ts, str):
            source_timestamp_utc = source_ts
        else:
            source_timestamp_utc = fetched_at

        candidate_dict: dict[str, Any] = {
            "chain": chain,
            "protocol": str(project),
            "venue": str(project),
            "venue_id": venue_id,
            "pool_address": pool_address,
            "token_addresses": token_addresses,
            "strategy_type": strategy_type,
            "advertised_apy": advertised_apy,
            "fee_apr": fee_apr,
            "reward_apr": reward_apr,
            "tvl_usd": tvl_usd,
            "volume_24h_usd": volume_24h,
            "liquidity_depth_usd": tvl_usd,
            "source_id": self._SOURCE_ID,
            "source_url": source_url,
            "source_timestamp_utc": source_timestamp_utc,
            "fetched_at_utc": fetched_at,
            "adapter_name": self._ADAPTER_NAME,
            "data_freshness_seconds": 0,
            "source_confidence_score": self._confidence,
            "stale_data": False,
        }

        try:
            return from_dict(candidate_dict)
        except (NormalizedCandidateError, Exception) as e:
            logger.debug(
                "stablecoin_benchmark_adapter: skipping pool %s: %s",
                pool.get("pool", "unknown"),
                e,
            )
            return None


__all__ = ["StablecoinBenchmarkAdapter"]
