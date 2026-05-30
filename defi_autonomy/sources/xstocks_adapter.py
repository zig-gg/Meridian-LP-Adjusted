"""xStocks SourceAdapter — Sprint 5, Phase 5.1 + 5.1b Dynamic Discovery.

Normalizes xStocks tokenized equity / points / LP opportunity data into
NormalizedCandidate instances. Supports dynamic asset discovery from
xStocks public API and aggregator-style responses.

Dynamic discovery model:
- Does NOT reject unknown symbols solely because they are absent from a
  hardcoded list. Any asset with valid API metadata (symbol, chain,
  contract_address, not halted) is accepted.
- Maintains an alias map for normalization of known tickers only.
- Ranks discovered assets by volume, market cap, chain support, and
  trading status.

Strategy mapping:
- xStocks points / holding / quest data → xstocks_points
- xStock / stablecoin LP candidate → xstocks_lp
- unsupported or unknown strategy → skip

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
# Alias map for known xStocks tickers (normalization only, not rejection)
# ============================================================================

# Maps variant spellings to a canonical symbol for venue_id generation.
# Assets NOT in this map are still accepted if API metadata is valid.
_XSTOCKS_ALIASES: dict[str, str] = {
    "TSLAX": "TSLAX",
    "TSLAON": "TSLAX",
    "NVDAX": "NVDAX",
    "NVDAON": "NVDAX",
    "AMDX": "AMDX",
    "AMDON": "AMDX",
    "GOOGLX": "GOOGLX",
    "GOOGLON": "GOOGLX",
    "MSFTX": "MSFTX",
    "MSFTON": "MSFTX",
    "AAPLX": "AAPLX",
    "AAPLON": "AAPLX",
    "METAX": "METAX",
    "METAON": "METAX",
    "AMZNX": "AMZNX",
    "AMZNON": "AMZNX",
    "PREOPAI": "PREOPAI",
    "PRESPAX": "PRESPAX",
}

_CHAIN_MAP: dict[str, str] = {
    "Base": "Base",
    "base": "Base",
    "BSC": "BNB Chain",
    "BNB Chain": "BNB Chain",
    "bnb": "BNB Chain",
    "Solana": "Solana",
    "solana": "Solana",
}

# LP indicator keywords
_LP_INDICATORS: frozenset[str] = frozenset({
    "lp", "pool", "pair", "liquidity", "amm",
})

# Stablecoin tokens that form LP pairs with xStocks
_STABLE_TOKENS: frozenset[str] = frozenset({
    "USDC", "USDT", "DAI", "USDS", "BUSD",
})


# ============================================================================
# Dynamic validation helpers
# ============================================================================


def _is_valid_xstocks_asset(entry: dict[str, Any]) -> bool:
    """Validate an xStocks asset entry for acceptance.

    Dynamic model: accepts any asset with valid metadata. Does NOT reject
    solely because the symbol is absent from the alias map.

    Rejects if:
    - symbol is missing or empty
    - is_trading_halted is True
    - no contract_address AND no token_addresses AND type is not points/quest
    """
    symbol = entry.get("symbol")
    if not symbol or not isinstance(symbol, str):
        return False

    # Reject halted assets
    if entry.get("is_trading_halted", False):
        return False

    # For non-points entries, require some form of contract/token reference
    entry_type = str(entry.get("type", "")).lower()
    if entry_type not in ("points", "quest", "holding", "staking"):
        has_contract = bool(entry.get("contract_address") or entry.get("contract_addresses"))
        has_tokens = bool(entry.get("token_addresses"))
        has_pool = bool(entry.get("pool_address"))
        if not (has_contract or has_tokens or has_pool):
            return False

    return True


def _is_known_alias(symbol: str) -> bool:
    """Check if a symbol is in the known alias map."""
    return symbol.upper().strip() in _XSTOCKS_ALIASES


def _canonicalize_symbol(symbol: str) -> str:
    """Return the canonical symbol if known, otherwise uppercase the input."""
    upper = symbol.upper().strip()
    return _XSTOCKS_ALIASES.get(upper, upper)


def _contains_known_xstock(text: str) -> bool:
    """Check if text contains any known xStocks alias."""
    upper = text.upper()
    return any(alias in upper for alias in _XSTOCKS_ALIASES)


# ============================================================================
# Ranking
# ============================================================================


def rank_xstocks_assets(raw_assets: list[dict]) -> list[dict]:
    """Rank xStocks assets by quality signals.

    Ranking prefers:
    - not trading halted
    - higher 24h volume
    - higher market cap
    - supported chain in allowed chains
    - contract address available
    - stablecoin support
    - DeFi/pool relevance

    Returns a new sorted list (best first). Does not mutate input.
    """
    scored: list[tuple[float, int, dict]] = []

    for i, asset in enumerate(raw_assets):
        if not isinstance(asset, dict):
            continue
        score = 0.0

        # Halted assets get lowest priority
        if asset.get("is_trading_halted", False):
            score -= 1000

        # Volume (log-scale bonus)
        try:
            vol = float(asset.get("volume_24h_usd", 0) or 0)
            if vol > 0:
                import math
                score += math.log10(vol + 1) * 10
        except (TypeError, ValueError):
            pass

        # Market cap (log-scale bonus)
        try:
            mcap = float(asset.get("market_cap_usd", 0) or 0)
            if mcap > 0:
                import math
                score += math.log10(mcap + 1) * 5
        except (TypeError, ValueError):
            pass

        # Chain support
        chain_raw = str(asset.get("chain", ""))
        if _CHAIN_MAP.get(chain_raw) in ALLOWED_CHAINS:
            score += 20

        # Supported networks bonus
        networks = asset.get("supported_networks")
        if isinstance(networks, list) and len(networks) > 0:
            score += min(len(networks) * 5, 15)

        # Contract address available
        if asset.get("contract_address") or asset.get("contract_addresses"):
            score += 15

        # Stablecoin support
        if asset.get("stablecoin_support"):
            score += 10

        # Pool/DeFi relevance
        if asset.get("pool_address") or asset.get("tvl_usd"):
            score += 10

        # Known alias bonus (established assets)
        symbol = str(asset.get("symbol", ""))
        if _is_known_alias(symbol):
            score += 5

        scored.append((score, i, asset))

    # Sort by score descending, then by original order for ties
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [item[2] for item in scored]


# ============================================================================
# Strategy classification
# ============================================================================


def _classify_strategy(entry: dict[str, Any]) -> str | None:
    """Classify an xStocks entry into strategy_type or None to skip."""
    entry_type = str(entry.get("type", "")).lower()
    symbol = str(entry.get("symbol", "")).upper()
    name = str(entry.get("name", "")).lower()

    # Explicit type field
    if entry_type in ("points", "quest", "holding", "staking"):
        return "xstocks_points"
    if entry_type in ("lp", "pool", "liquidity", "amm"):
        return "xstocks_lp"

    # Heuristic: check if it's an LP pair
    combined = f"{symbol} {name}".lower()
    if any(ind in combined for ind in _LP_INDICATORS):
        return "xstocks_lp"

    # Check if symbol contains a stable token (LP pair indicator)
    parts = [p.strip() for p in symbol.replace("/", "-").replace("+", "-").split("-")]
    if len(parts) >= 2:
        has_stable = any(p in _STABLE_TOKENS for p in parts)
        if has_stable:
            return "xstocks_lp"

    # Default: single token → points/holding
    return "xstocks_points"


# ============================================================================
# XStocksAdapter
# ============================================================================


class XStocksAdapter(SourceAdapter):
    """Concrete adapter for xStocks tokenized equity / points / LP data.

    Supports dynamic asset discovery: any valid xStocks asset from the API
    is accepted, not just hardcoded tickers. Normalizes into
    NormalizedCandidate instances. Does not fabricate APY or points value.
    """

    _SOURCE_ID = "xstocks"
    _ADAPTER_NAME = "xstocks_adapter"
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
        """Build xStocks endpoint URLs from allowlisted domains."""
        urls: list[str] = []
        for domain in entry.domains:
            if "xstocks" in domain.lower():
                urls.append(f"https://{domain}/api/v1/opportunities")
        return urls

    def normalize(
        self, raw: bytes | str | dict | list
    ) -> list[NormalizedCandidate]:
        """Convert raw xStocks data into NormalizedCandidate list.

        Accepts bytes, str (JSON), dict, or list.
        Dynamically validates assets — does not reject unknown symbols
        if API metadata is valid.
        """
        entries = self._parse_raw(raw)
        if entries is None:
            return []

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates: list[NormalizedCandidate] = []

        for entry in entries:
            candidate = self._normalize_entry(entry, now_utc)
            if candidate is not None:
                candidates.append(candidate)

        return candidates

    def _parse_raw(self, raw: bytes | str | dict | list) -> list[dict] | None:
        """Parse raw input into a list of entry dicts."""
        if isinstance(raw, bytes):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("xstocks_adapter: failed to parse bytes as JSON")
                return None
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("xstocks_adapter: failed to parse str as JSON")
                return None
        elif isinstance(raw, (dict, list)):
            parsed = raw
        else:
            return None

        if isinstance(parsed, dict):
            if "data" in parsed and isinstance(parsed["data"], list):
                return parsed["data"]
            if "opportunities" in parsed and isinstance(parsed["opportunities"], list):
                return parsed["opportunities"]
            if "assets" in parsed and isinstance(parsed["assets"], list):
                return parsed["assets"]
            return [parsed]
        elif isinstance(parsed, list):
            return parsed
        return None

    def _normalize_entry(
        self, entry: dict, fetched_at: str
    ) -> NormalizedCandidate | None:
        """Normalize a single xStocks entry. Returns None to skip."""
        if not isinstance(entry, dict):
            return None

        # Dynamic validation — accepts any valid asset, not just hardcoded list
        if not _is_valid_xstocks_asset(entry):
            return None

        symbol = entry["symbol"]

        # Chain mapping
        chain_raw = entry.get("chain", "Base")
        chain = _CHAIN_MAP.get(str(chain_raw))
        if chain is None or chain not in ALLOWED_CHAINS:
            return None

        # Strategy classification
        strategy_type = _classify_strategy(entry)
        if strategy_type is None:
            return None

        # Protocol / venue
        protocol = str(entry.get("protocol", "xstocks"))
        venue = str(entry.get("venue", "xstocks"))

        # APY extraction — do NOT fabricate
        try:
            advertised_apy = float(entry.get("apy", 0) or 0)
            fee_apr = float(entry.get("fee_apr", 0) or 0)
            reward_apr = float(entry.get("reward_apr", 0) or 0)
        except (TypeError, ValueError):
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

        # TVL
        try:
            tvl_usd = float(entry.get("tvl_usd", 0) or 0)
        except (TypeError, ValueError):
            tvl_usd = 0.0

        # For LP candidates, TVL must be positive
        if strategy_type == "xstocks_lp" and tvl_usd <= 0:
            return None

        if tvl_usd < 0:
            return None

        # Volume
        try:
            volume_24h = float(entry.get("volume_24h_usd", 0) or 0)
        except (TypeError, ValueError):
            volume_24h = 0.0
        if volume_24h < 0:
            volume_24h = 0.0

        # Pool address
        pool_address = entry.get("pool_address")
        if pool_address is not None:
            pool_address = str(pool_address)
            if not pool_address:
                pool_address = None

        # Token addresses
        tokens = entry.get("token_addresses")
        if isinstance(tokens, list):
            token_addresses = [str(t) for t in tokens if t][:4]
        elif entry.get("contract_address"):
            token_addresses = [str(entry["contract_address"])]
        elif isinstance(entry.get("contract_addresses"), list):
            token_addresses = [str(a) for a in entry["contract_addresses"] if a][:4]
        else:
            token_addresses = []

        # Venue ID — use canonical symbol if known, otherwise raw symbol
        canonical = _canonicalize_symbol(symbol)
        venue_id = f"{chain.lower().replace(' ', '_')}:xstocks:{canonical}"
        if len(venue_id) > 128:
            venue_id = venue_id[:128]

        # Source URL
        source_url = entry.get("source_url", "https://defi.xstocks.fi")
        if not source_url or not isinstance(source_url, str):
            source_url = "https://defi.xstocks.fi"

        # Source timestamp
        source_ts = entry.get("timestamp")
        if source_ts and isinstance(source_ts, str):
            source_timestamp_utc = source_ts
        else:
            source_timestamp_utc = fetched_at

        candidate_dict: dict[str, Any] = {
            "chain": chain,
            "protocol": protocol,
            "venue": venue,
            "venue_id": venue_id,
            "pool_address": pool_address,
            "token_addresses": token_addresses,
            "strategy_type": strategy_type,
            "advertised_apy": advertised_apy,
            "fee_apr": fee_apr,
            "reward_apr": reward_apr,
            "tvl_usd": tvl_usd,
            "volume_24h_usd": volume_24h,
            "liquidity_depth_usd": tvl_usd if tvl_usd > 0 else 0.0,
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
                "xstocks_adapter: skipping entry %s: %s",
                symbol, e,
            )
            return None


__all__ = ["XStocksAdapter", "rank_xstocks_assets"]
