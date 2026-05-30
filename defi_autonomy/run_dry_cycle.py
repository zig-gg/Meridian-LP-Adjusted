"""Dry-run harness for Hermes DeFi Autonomy — Phase 4.3.

Runs one Coordinator cycle locally with mocked data sources.
Does not require PM2, real network, private keys, or broadcast.

Usage:
    python -m defi_autonomy.run_dry_cycle
    # or
    python defi_autonomy/run_dry_cycle.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure the project root is on sys.path for direct script execution
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from defi_autonomy.coordinator import CycleReport, cycle_report_to_dict, run_cycle
from defi_autonomy.sources.base import SourceAllowlistEntry


def _mock_pool_response() -> bytes:
    """Realistic mock DeFiLlama response."""
    data = {
        "status": "success",
        "data": [
            {
                "chain": "Base",
                "project": "aave-v3",
                "symbol": "USDC",
                "tvlUsd": 80_000_000.0,
                "apy": 4.2,
                "apyBase": 3.5,
                "apyReward": 0.7,
                "pool": "0x" + "a1" * 20,
                "underlyingTokens": ["0x" + "b1" * 20],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 2_000_000.0,
            },
            {
                "chain": "Base",
                "project": "compound-v3",
                "symbol": "USDT",
                "tvlUsd": 40_000_000.0,
                "apy": 3.8,
                "apyBase": 3.2,
                "apyReward": 0.6,
                "pool": "0x" + "c1" * 20,
                "underlyingTokens": ["0x" + "d1" * 20],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 1_000_000.0,
            },
            {
                "chain": "BNB Chain",
                "project": "venus",
                "symbol": "USDC",
                "tvlUsd": 25_000_000.0,
                "apy": 5.1,
                "apyBase": 4.0,
                "apyReward": 1.1,
                "pool": "0x" + "e1" * 20,
                "underlyingTokens": ["0x" + "f1" * 20],
                "stablecoin": True,
                "ilRisk": "no",
                "volumeUsd1d": 500_000.0,
            },
        ],
    }
    return json.dumps(data).encode("utf-8")


def _mock_xstocks_response() -> bytes:
    """Mock xStocks response with points and LP entries."""
    data = {
        "data": [
            {
                "symbol": "TSLAx",
                "chain": "Base",
                "type": "points",
                "protocol": "xstocks",
                "venue": "xstocks",
                "apy": 0,
                "fee_apr": 0,
                "reward_apr": 0,
                "tvl_usd": 0,
                "volume_24h_usd": 250_000,
                "contract_address": "0x" + "e1" * 20,
                "is_trading_halted": False,
                "source_url": "https://defi.xstocks.fi/points/TSLAx",
            },
            {
                "symbol": "NVDAx-USDC",
                "chain": "Base",
                "type": "lp",
                "protocol": "xstocks",
                "venue": "xstocks",
                "apy": 14.0,
                "fee_apr": 9.0,
                "reward_apr": 5.0,
                "tvl_usd": 400_000.0,
                "volume_24h_usd": 60_000,
                "pool_address": "0x" + "f1" * 20,
                "token_addresses": ["0x" + "e2" * 20, "0x" + "e3" * 20],
                "is_trading_halted": False,
                "source_url": "https://defi.xstocks.fi/pools/NVDAx-USDC",
            },
        ]
    }
    return json.dumps(data).encode("utf-8")


def _mock_meteora_response() -> bytes:
    """Mock Meteora response with stable-stable and xStocks LP pools."""
    data = [
        {
            "pair_name": "USDC-USDT",
            "address": "So1anaAddr111111111111111111111111111111",
            "mint_x": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "mint_y": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "tvl_usd": 2_000_000.0,
            "fee_apy": 8.5,
            "reward_apy": 2.0,
            "apy": 10.5,
            "volume_24h_usd": 500_000.0,
        },
        {
            "pair_name": "TSLAx-USDC",
            "address": "So1anaAddr222222222222222222222222222222",
            "mint_x": "TSLAxMint111111111111111111111111111111111",
            "mint_y": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "tvl_usd": 300_000.0,
            "fee_apy": 15.0,
            "reward_apy": 5.0,
            "apy": 20.0,
            "volume_24h_usd": 80_000.0,
        },
    ]
    return json.dumps(data).encode("utf-8")


def _mock_client_factory():
    """Client factory that returns mocked responses per source."""
    llama_resp = _mock_pool_response()
    xstocks_resp = _mock_xstocks_response()
    meteora_resp = _mock_meteora_response()

    def factory(entry: SourceAllowlistEntry):
        client = MagicMock()

        def mock_request(method: str, url: str) -> bytes:
            if "xstocks" in url.lower():
                return xstocks_resp
            if "meteora" in url.lower():
                return meteora_resp
            return llama_resp

        client.request = mock_request
        return client

    return factory


def main() -> None:
    """Run a single dry cycle and print the report."""
    # Use the real defi_autonomy directory as base
    base_dir = Path(__file__).resolve().parent

    print("=" * 60)
    print("  Hermes DeFi Autonomy — Dry-Run Cycle")
    print("=" * 60)
    print(f"\nBase directory: {base_dir}")
    print(f"Data directory: {base_dir / 'data'}")
    print()

    # Check if data directory exists
    data_dir = base_dir / "data"
    if not data_dir.exists():
        print("ERROR: data/ directory not found.")
        sys.exit(1)

    # Check risk policy
    policy_path = data_dir / "risk_policy.json"
    if policy_path.exists():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        print(f"Risk Policy loaded (autonomy_level={policy.get('autonomy_level', 0)})")
    else:
        print("WARNING: risk_policy.json not found, using defaults.")

    print("\nRunning cycle with mocked data sources...")
    print("-" * 60)

    # Run cycle with mocked client (no real network)
    report = run_cycle(base_dir, _client_factory=_mock_client_factory())

    print("\n" + "=" * 60)
    print("  Cycle Report")
    print("=" * 60)
    print(json.dumps(cycle_report_to_dict(report), indent=2))

    print("\n" + "-" * 60)
    print(f"Status: {report.status}")
    print(f"Candidates: {report.candidate_count}")
    print(f"Risk Assessments: {report.risk_assessment_count}")
    print(f"Approved: {report.approved_count}")
    print(f"Denied: {report.denied_count}")
    print(f"Simulations Passed: {report.simulation_passed_count}")
    print(f"Signing Prepared: {report.signing_prepared_count}")
    if report.errors:
        print(f"Errors: {len(report.errors)}")
    if report.warnings:
        print(f"Warnings: {len(report.warnings)}")
    print("-" * 60)
    print("\nDry-run complete. No transactions signed or broadcast.")


if __name__ == "__main__":
    main()
