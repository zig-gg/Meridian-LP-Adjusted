"""Meteora SourceAdapter — Sprint 5, Phase 5.2.

Normalizes Meteora Solana DLMM / LP yield data into NormalizedCandidate
instances for cross-venue comparison.

Supported strategies:
- stable-stable Meteora pools → stable_stable_lp
- xStock/stablecoin pools → xstocks_lp
- volatile LPs → skip for v1

Chain: Solana only.

No network calls inside normalize. No signing. No key loading. No broadcast.
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

_STABLECOINS: frozenset[str] = frozenset({
    "USDC", "USDT", "PYUSD", "FDUSD", "USDS",
})

# xStocks alias map (same dynamic approach as xstocks_adapter)
_XSTOCKS_ALIASES: dict[str, str] = {
    "TSLAX": "TSLAX", "TSLAON": "TSLAX",
    "NVDAX": "NVDAX", "NVDAON": "NVDAX",
    "AMDX": "AMDX", "AMDON": "AMDX",
    "GOOGLX": "GOOGLX", "GOOGLON": "GOOGLX",
    "MSFTX": "MSFTX", "MSFTON": "MSFTX",
    "AAPLX": "AAPLX", "AAPLON": "AAPLX",
    "METAX": "METAX", "METAON": "METAX",
    "AMZNX": "AMZNX", "AMZNON": "AMZNX",
    "PREOPAI": "PREOPAI", "PRESPAX": "PRESPAX",
}

# Suffixes that indicate an xStocks-style tokenized asset
_XSTOCKS_SUFFIXES: tuple[str, ...] = ("X", "ON")


# ============================================================================
# Helpers
# ============================================================================


def _is_stablecoin(symbol: str) -> bool:
    """Check if a symbol is a known stablecoin."""
    return symbol.upper().strip() in _STABLECOINS


def _is_xstocks_like(symbol: str) -> bool:
    """Check if a symbol looks like an xStocks tokenized asset.

    Dynamic model: accepts known aliases AND any symbol ending in 'x' or 'ON'
    that looks like a tokenized equity ticker (uppercase letters + suffix).
    Does NOT reject unknown symbols.
    """
    upper = symbol.upper().strip()
    # Known alias
    if upper in _XSTOCKS_ALIASES:
        return True
    # Heuristic: uppercase letters ending in X (e.g. TSLAx, COINBASEx, SPYx)
    if len(upper) >= 3 and upper.endswith("X") and upper[:-1].isalpha():
        return True
    # Heuristic: uppercase letters ending in ON (e.g. TSLAON, NVDAON)
    if len(upper) >= 4 and upper.endswith("ON") and upper[:-2].isalpha():
        return True
    return False


def _extract_pair_symbols(pair_name: str) -> list[str]:
    """Extract individual token symbols from a pair name."""
    # Common separators in Meteora pool names
    for sep in ("-", "/", "_", " "):
        if sep in pair_name:
            return [s.strip().upper() for s in pair_name.split(sep) if s.strip()]
    return [pair_name.strip().upper()]


def _classify_strategy(pool: dict[str, Any]) -> str | None:
    """Classify a Meteora pool into strategy_type or None to skip.

    - Both tokens are stablecoins → stable_stable_lp
    - One token is xStocks-like and other is stablecoin → xstocks_lp
    - Otherwise → skip (volatile LP, not supported in v1)
    """
    # Try pair_name / name / symbol field
    pair_name = str(pool.get("pair_name", "") or pool.get("name", "") or pool.get("symbol", ""))
    if not pair_name:
        return None

    parts = _extract_pair_symbols(pair_name)
    if len(parts) < 2:
        # Single token pool — not an LP
        return None

    token_a = parts[0]
    token_b = parts[1]

    a_stable = _is_stablecoin(token_a)
    b_stable = _is_stablecoin(token_b)
    a_xstock = _is_xstocks_like(token_a)
    b_xstock = _is_xstocks_like(token_b)

    # Both stablecoins → stable_stable_lp
    if a_stable and b_stable:
        return "stable_stable_lp"

    # xStock + stablecoin → xstocks_lp
    if (a_xstock and b_stable) or (b_xstock and a_stable):
        return "xstocks_lp"

    # Volatile LP — skip for v1
    return None


# ============================================================================
# MeteoraAdapter
# ============================================================================


class MeteoraAdapter(SourceAdapter):
    """Concrete adapter for Meteora Solana DLMM / LP yield data.

    Normalizes Meteora pool opportunities into NormalizedCandidate instances.
    Solana chain only. Does not fabricate APY or reward values.
    """

    _SOURCE_ID = "meteora"
    _ADAPTER_NAME = "meteora_adapter"
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
        """Build Meteora endpoint URLs from allowlisted domains."""
        urls: list[str] = []
        for domain in entry.domains:
            if "meteora" in domain.lower():
                urls.append(f"https://{domain}/pair/all")
        return urls

    def normalize(
        self, raw: bytes | str | dict | list
    ) -> list[NormalizedCandidate]:
        """Convert raw Meteora pool data into NormalizedCandidate list.

        Accepts bytes, str (JSON), dict, or list.
        Does not fabricate APY or reward values.
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
                logger.warning("meteora_adapter: failed to parse bytes as JSON")
                return None
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("meteora_adapter: failed to parse str as JSON")
                return None
        elif isinstance(raw, (dict, list)):
            parsed = raw
        else:
            return None

        if isinstance(parsed, dict):
            if "data" in parsed and isinstance(parsed["data"], list):
                return parsed["data"]
            if "pairs" in parsed and isinstance(parsed["pairs"], list):
                return parsed["pairs"]
            if "pools" in parsed and isinstance(parsed["pools"], list):
                return parsed["pools"]
            return [parsed]
        elif isinstance(parsed, list):
            return parsed
        return None

    def _normalize_pool(
        self, pool: dict, fetched_at: str
    ) -> NormalizedCandidate | None:
        """Normalize a single Meteora pool. Returns None to skip."""
        if not isinstance(pool, dict):
            return None

        # Meteora is Solana only
        chain = "Solana"

        # Pool address required
        pool_address = pool.get("address") or pool.get("pool_address")
        if not pool_address or not isinstance(pool_address, str):
            return None
        pool_address = str(pool_address).strip()
        if not pool_address:
            return None

        # Token addresses required
        mint_x = pool.get("mint_x") or pool.get("token_x_address")
        mint_y = pool.get("mint_y") or pool.get("token_y_address")
        token_addresses_raw = pool.get("token_addresses")

        if isinstance(token_addresses_raw, list) and len(token_addresses_raw) >= 2:
            token_addresses = [str(t) for t in token_addresses_raw if t][:4]
        elif mint_x and mint_y:
            token_addresses = [str(mint_x), str(mint_y)]
        else:
            return None

        if len(token_addresses) < 2:
            return None

        # Strategy classification
        strategy_type = _classify_strategy(pool)
        if strategy_type is None:
            return None

        # TVL required and positive
        try:
            tvl_usd = float(pool.get("tvl_usd", 0) or pool.get("liquidity", 0) or 0)
        except (TypeError, ValueError):
            return None
        if tvl_usd <= 0:
            return None

        # APY — do NOT fabricate
        try:
            fee_apy = pool.get("fee_apy") or pool.get("fees_24h_apy") or pool.get("fee_apr")
            reward_apy = pool.get("reward_apy") or pool.get("reward_apr")
            total_apy = pool.get("apy") or pool.get("total_apy")

            fee_apr = float(fee_apy) if fee_apy is not None else 0.0
            reward_apr = float(reward_apy) if reward_apy is not None else 0.0

            if total_apy is not None:
                advertised_apy = float(total_apy)
            else:
                advertised_apy = fee_apr + reward_apr
        except (TypeError, ValueError):
            return None

        # If APY is completely unknown (all zero and no explicit field), skip LP
        if advertised_apy == 0 and fee_apr == 0 and reward_apr == 0:
            # Only skip if there was no explicit apy field at all
            if pool.get("apy") is None and pool.get("total_apy") is None and pool.get("fee_apy") is None:
                return None

        # Schema bounds
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

        # Volume
        try:
            volume_24h = float(pool.get("volume_24h_usd", 0) or pool.get("trade_volume_24h", 0) or 0)
        except (TypeError, ValueError):
            volume_24h = 0.0
        if volume_24h < 0:
            volume_24h = 0.0

        # Pair name for venue_id
        pair_name = str(pool.get("pair_name", "") or pool.get("name", "") or pool.get("symbol", ""))
        venue_id = f"solana:meteora:{pair_name}" if pair_name else f"solana:meteora:{pool_address[:16]}"
        if len(venue_id) > 128:
            venue_id = venue_id[:128]

        # Source URL
        source_url = pool.get("source_url")
        if not source_url or not isinstance(source_url, str):
            source_url = f"https://app.meteora.ag/dlmm/{pool_address}"

        # Source timestamp
        source_ts = pool.get("timestamp")
        if source_ts and isinstance(source_ts, str):
            source_timestamp_utc = source_ts
        else:
            source_timestamp_utc = fetched_at

        candidate_dict: dict[str, Any] = {
            "chain": chain,
            "protocol": "meteora",
            "venue": "meteora",
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
                "meteora_adapter: skipping pool %s: %s",
                pool_address, e,
            )
            return None


__all__ = ["MeteoraAdapter"]
