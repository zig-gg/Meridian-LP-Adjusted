"""DeFiLlama SourceAdapter — Phase 1.3.

Converts DeFiLlama yield pool data into NormalizedCandidate instances.
No network calls inside `normalize`. No signing. No key loading.

DeFiLlama /pools endpoint returns a JSON array of pool objects with fields
like: chain, project, symbol, tvlUsd, apy, apyBase, apyReward, pool (address),
underlyingTokens, stablecoin, ilRisk, etc.

This adapter:
- Filters to allowed chains (Base, BNB Chain, Solana).
- Maps pool characteristics to allowed strategy types.
- Skips pools that don't fit the v1 strategy universe.
- Produces validated NormalizedCandidate instances.
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
# Chain name mapping: DeFiLlama chain names → NormalizedCandidate chain names
# ============================================================================

_CHAIN_MAP: dict[str, str] = {
    "Base": "Base",
    "BSC": "BNB Chain",
    "Binance": "BNB Chain",
    "BNB Chain": "BNB Chain",
    "Solana": "Solana",
}

# ============================================================================
# Strategy classification helpers
# ============================================================================


def _is_stablecoin_lending(pool: dict[str, Any]) -> bool:
    """Heuristic: pool is a stablecoin lending pool.

    DeFiLlama marks stablecoin pools with `stablecoin: true` and lending
    pools typically have `ilRisk: "no"` or project names containing "lend"
    or "aave" or "compound".
    """
    if not pool.get("stablecoin", False):
        return False
    # Lending pools typically have no IL risk
    il_risk = pool.get("ilRisk", "")
    if il_risk == "no" or il_risk is False:
        return True
    # Also classify by project name patterns
    project = str(pool.get("project", "")).lower()
    lending_keywords = ("aave", "compound", "lend", "morpho", "spark", "venus")
    if any(kw in project for kw in lending_keywords):
        return True
    return False


def _is_stable_stable_lp(pool: dict[str, Any]) -> bool:
    """Heuristic: pool is a stable-stable LP pair.

    DeFiLlama marks these with `stablecoin: true` and typically `ilRisk: "yes"`
    (since it's an LP, not lending). The symbol usually contains two stable
    tokens separated by a delimiter.
    """
    if not pool.get("stablecoin", False):
        return False
    # Must have IL risk (it's an LP, not lending)
    il_risk = pool.get("ilRisk", "")
    if il_risk == "yes" or il_risk is True:
        return True
    # Check symbol for stable-stable patterns
    symbol = str(pool.get("symbol", "")).upper()
    stable_tokens = ("USDC", "USDT", "DAI", "FRAX", "BUSD", "TUSD", "LUSD", "PYUSD")
    parts = [p.strip() for p in symbol.replace("/", "-").replace("+", "-").split("-")]
    if len(parts) >= 2 and all(
        any(st in part for st in stable_tokens) for part in parts[:2]
    ):
        return True
    return False


def _classify_strategy(pool: dict[str, Any]) -> str | None:
    """Return the strategy_type string or None if pool should be skipped."""
    if _is_stablecoin_lending(pool):
        return "stablecoin_lending"
    if _is_stable_stable_lp(pool):
        return "stable_stable_lp"
    return None


# ============================================================================
# DeFiLlamaAdapter
# ============================================================================


class DeFiLlamaAdapter(SourceAdapter):
    """Concrete adapter for DeFiLlama yield pool data.

    Implements the SourceAdapter ABC. `normalize` accepts raw DeFiLlama
    /pools response data and produces NormalizedCandidate instances for
    pools that match the v1 strategy universe.
    """

    _SOURCE_ID = "defillama"
    _ADAPTER_NAME = "defillama_adapter"
    _DEFAULT_CONFIDENCE = 0.5

    def __init__(self, source_confidence_score: float | None = None) -> None:
        """Initialize with optional source confidence score.

        If not provided, defaults to 0.5 (conservative).
        """
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
        """Build DeFiLlama yield endpoint URLs from allowlisted domains.

        Returns URLs for the /pools endpoint on each allowlisted domain.
        """
        urls: list[str] = []
        for domain in entry.domains:
            if "llama" in domain.lower():
                urls.append(f"https://{domain}/pools")
        return urls

    def normalize(
        self, raw: bytes | str | dict | list
    ) -> list[NormalizedCandidate]:
        """Convert raw DeFiLlama pool data into NormalizedCandidate list.

        Accepts:
        - bytes: JSON-encoded pool data
        - str: JSON string of pool data
        - dict: expects {"data": [...]} wrapper or a single pool dict
        - list: list of pool dicts

        Skips malformed pools, unsupported chains, unsupported strategies,
        and pools that fail schema validation. No network calls.
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
                logger.warning("defillama_adapter: failed to parse bytes as JSON")
                return None
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("defillama_adapter: failed to parse str as JSON")
                return None
        elif isinstance(raw, (dict, list)):
            parsed = raw
        else:
            logger.warning(
                "defillama_adapter: unsupported raw type %s", type(raw).__name__
            )
            return None

        # DeFiLlama /pools returns {"status": "success", "data": [...]}
        if isinstance(parsed, dict):
            if "data" in parsed and isinstance(parsed["data"], list):
                return parsed["data"]
            # Single pool dict — wrap in list
            return [parsed]
        elif isinstance(parsed, list):
            return parsed
        return None

    def _normalize_pool(
        self, pool: dict, fetched_at: str
    ) -> NormalizedCandidate | None:
        """Attempt to normalize a single pool dict. Returns None on skip."""
        if not isinstance(pool, dict):
            return None

        # Required fields check
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

        # Strategy classification
        strategy_type = _classify_strategy(pool)
        if strategy_type is None:
            return None

        # APY extraction with safe float conversion
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

        # Clamp to schema bounds: advertised_apy [-1, 100], fee/reward [0, 100]
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
        if tvl_usd < 0:
            return None

        # Volume (may not be present)
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
        source_url = f"https://yields.llama.fi/pools"

        # Source timestamp — use pool's timestamp if available
        source_ts = pool.get("timestamp")
        if source_ts and isinstance(source_ts, str):
            source_timestamp_utc = source_ts
        else:
            source_timestamp_utc = fetched_at

        # Build candidate dict
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
            "liquidity_depth_usd": tvl_usd,  # best proxy available
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
                "defillama_adapter: skipping pool %s: %s",
                pool.get("pool", "unknown"),
                e,
            )
            return None


__all__ = ["DeFiLlamaAdapter"]
