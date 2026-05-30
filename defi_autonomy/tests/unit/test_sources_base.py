"""Unit tests for defi_autonomy.sources.base — Phase 1.2.

Tests cover:
- SourceAllowlistEntry construction and validation
- load_source_allowlist: valid load, duplicate rejection, missing/unknown fields
- ReadOnlyHttpClient: method gating, domain validation, scheme enforcement,
  redirect blocking, response size cap, no cookies/auth headers
- SourceAdapter ABC: cannot instantiate without required methods

All tests are deterministic and offline. No real network calls. No signing.
No key loading.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from defi_autonomy.sources.base import (
    DomainBlockedError,
    MalformedSourceAllowlistError,
    MethodBlockedError,
    ReadOnlyHttpClient,
    RedirectBlockedError,
    ResponseTooLargeError,
    SourceAdapter,
    SourceAdapterError,
    SourceAllowlistEntry,
    SourceTimeoutError,
    load_source_allowlist,
)


# ============================================================================
# Fixtures
# ============================================================================


def _valid_allowlist_doc(entries: list[dict[str, Any]] | None = None) -> dict:
    """Return a minimal valid source_allowlist document."""
    if entries is None:
        entries = [
            {
                "source_id": "test_source",
                "adapter_name": "test_adapter",
                "domains": ["api.example.com", "cdn.example.com"],
                "max_freshness_seconds": 600,
                "fetch_timeout_seconds": 10,
            }
        ]
    return {"version": 1, "entries": entries}


def _write_allowlist(tmp_path: Path, doc: dict) -> Path:
    """Write a JSON allowlist doc to a temp file and return the path."""
    p = tmp_path / "source_allowlist.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _make_entry(**overrides) -> SourceAllowlistEntry:
    """Build a valid SourceAllowlistEntry with optional overrides."""
    defaults = {
        "source_id": "test_source",
        "adapter_name": "test_adapter",
        "domains": ("api.example.com",),
        "max_freshness_seconds": 600,
        "fetch_timeout_seconds": 10,
    }
    defaults.update(overrides)
    return SourceAllowlistEntry(**defaults)


def _make_client(
    domains: tuple[str, ...] = ("api.example.com",),
    max_bytes: int = 1_000_000,
    allow_non_https: bool = False,
    **kwargs,
) -> ReadOnlyHttpClient:
    """Build a ReadOnlyHttpClient with a valid entry."""
    entry = _make_entry(domains=domains)
    return ReadOnlyHttpClient(
        entry=entry, max_bytes=max_bytes, allow_non_https=allow_non_https, **kwargs
    )


# ============================================================================
# Tests: load_source_allowlist — valid load
# ============================================================================


class TestLoadSourceAllowlistValid:
    """Valid source allowlist loads correctly."""

    def test_valid_single_entry(self, tmp_path: Path) -> None:
        doc = _valid_allowlist_doc()
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert "test_source" in result
        entry = result["test_source"]
        assert entry.source_id == "test_source"
        assert entry.adapter_name == "test_adapter"
        assert entry.max_freshness_seconds == 600
        assert entry.fetch_timeout_seconds == 10

    def test_domains_converted_to_tuple(self, tmp_path: Path) -> None:
        doc = _valid_allowlist_doc()
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        entry = result["test_source"]
        assert isinstance(entry.domains, tuple)
        assert entry.domains == ("api.example.com", "cdn.example.com")

    def test_multiple_entries(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "src_a",
                "adapter_name": "adapter_a",
                "domains": ["a.example.com"],
                "max_freshness_seconds": 100,
                "fetch_timeout_seconds": 5,
            },
            {
                "source_id": "src_b",
                "adapter_name": "adapter_b",
                "domains": ["b.example.com"],
                "max_freshness_seconds": 200,
                "fetch_timeout_seconds": 8,
            },
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert len(result) == 2
        assert "src_a" in result
        assert "src_b" in result

    def test_notes_field_optional(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "with_notes",
                "adapter_name": "adapter",
                "domains": ["x.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "notes": "some note",
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert result["with_notes"].notes == "some note"


# ============================================================================
# Tests: load_source_allowlist — duplicate source_id rejected
# ============================================================================


class TestLoadSourceAllowlistDuplicate:
    """Duplicate source_id is rejected."""

    def test_duplicate_source_id_raises(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "dup",
                "adapter_name": "a1",
                "domains": ["a.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
            },
            {
                "source_id": "dup",
                "adapter_name": "a2",
                "domains": ["b.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
            },
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="duplicate source_id"):
            load_source_allowlist(p)


# ============================================================================
# Tests: load_source_allowlist — missing required fields rejected
# ============================================================================


class TestLoadSourceAllowlistMissingFields:
    """Missing required fields are rejected."""

    @pytest.mark.parametrize(
        "field",
        ["source_id", "adapter_name", "domains", "max_freshness_seconds", "fetch_timeout_seconds"],
    )
    def test_missing_required_field(self, tmp_path: Path, field: str) -> None:
        entry = {
            "source_id": "x",
            "adapter_name": "y",
            "domains": ["z.com"],
            "max_freshness_seconds": 60,
            "fetch_timeout_seconds": 5,
        }
        del entry[field]
        doc = _valid_allowlist_doc([entry])
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="missing required fields"):
            load_source_allowlist(p)


# ============================================================================
# Tests: load_source_allowlist — unknown fields rejected
# ============================================================================


class TestLoadSourceAllowlistUnknownFields:
    """Unknown fields are rejected."""

    def test_unknown_field_raises(self, tmp_path: Path) -> None:
        entry = {
            "source_id": "x",
            "adapter_name": "y",
            "domains": ["z.com"],
            "max_freshness_seconds": 60,
            "fetch_timeout_seconds": 5,
            "surprise_field": "bad",
        }
        doc = _valid_allowlist_doc([entry])
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="unknown fields"):
            load_source_allowlist(p)

    def test_multiple_unknown_fields_raises(self, tmp_path: Path) -> None:
        entry = {
            "source_id": "x",
            "adapter_name": "y",
            "domains": ["z.com"],
            "max_freshness_seconds": 60,
            "fetch_timeout_seconds": 5,
            "bogus_field_a": "bad",
            "bogus_field_b": 1024,
        }
        doc = _valid_allowlist_doc([entry])
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="unknown fields"):
            load_source_allowlist(p)


# ============================================================================
# Tests: ReadOnlyHttpClient — method gating
# ============================================================================


class TestMethodGating:
    """GET and HEAD allowed; POST, DELETE, etc. blocked."""

    def test_get_allowed(self) -> None:
        client = _make_client()
        client.validate_method("GET")  # must not raise

    def test_head_allowed(self) -> None:
        client = _make_client()
        client.validate_method("HEAD")  # must not raise

    def test_get_case_insensitive(self) -> None:
        client = _make_client()
        client.validate_method("get")  # must not raise

    def test_head_case_insensitive(self) -> None:
        client = _make_client()
        client.validate_method("head")  # must not raise

    def test_post_blocked(self) -> None:
        client = _make_client()
        with pytest.raises(MethodBlockedError):
            client.validate_method("POST")

    def test_delete_blocked(self) -> None:
        client = _make_client()
        with pytest.raises(MethodBlockedError):
            client.validate_method("DELETE")

    def test_put_blocked(self) -> None:
        client = _make_client()
        with pytest.raises(MethodBlockedError):
            client.validate_method("PUT")

    def test_patch_blocked(self) -> None:
        client = _make_client()
        with pytest.raises(MethodBlockedError):
            client.validate_method("PATCH")


# ============================================================================
# Tests: ReadOnlyHttpClient — domain validation
# ============================================================================


class TestDomainValidation:
    """Exact domain allowed, valid subdomain allowed, lookalike/nested blocked."""

    def test_exact_domain_allowed(self) -> None:
        client = _make_client(domains=("api.example.com",))
        client.validate_url("https://api.example.com/v1/data")  # must not raise

    def test_valid_subdomain_allowed(self) -> None:
        client = _make_client(domains=("example.com",))
        client.validate_url("https://api.example.com/v1/data")  # must not raise

    def test_deeper_subdomain_allowed(self) -> None:
        client = _make_client(domains=("example.com",))
        client.validate_url("https://deep.sub.example.com/path")  # must not raise

    def test_lookalike_domain_blocked(self) -> None:
        client = _make_client(domains=("api.example.com",))
        with pytest.raises(DomainBlockedError):
            client.validate_url("https://evil-api.example.com.attacker.io/steal")

    def test_nested_evil_domain_blocked(self) -> None:
        client = _make_client(domains=("api.example.com",))
        with pytest.raises(DomainBlockedError):
            client.validate_url("https://api.example.com.evil.net/phish")

    def test_unrelated_domain_blocked(self) -> None:
        client = _make_client(domains=("api.example.com",))
        with pytest.raises(DomainBlockedError):
            client.validate_url("https://totally-different.org/data")

    def test_suffix_match_not_substring(self) -> None:
        """notexample.com must NOT match allowlist entry 'example.com'."""
        client = _make_client(domains=("example.com",))
        with pytest.raises(DomainBlockedError):
            client.validate_url("https://notexample.com/data")


# ============================================================================
# Tests: ReadOnlyHttpClient — scheme enforcement
# ============================================================================


class TestSchemeEnforcement:
    """Public HTTP URL blocked; HTTPS allowed."""

    def test_https_allowed(self) -> None:
        client = _make_client(domains=("api.example.com",))
        client.validate_url("https://api.example.com/data")  # must not raise

    def test_http_blocked_by_default(self) -> None:
        client = _make_client(domains=("api.example.com",))
        with pytest.raises(DomainBlockedError):
            client.validate_url("http://api.example.com/data")

    def test_http_allowed_when_opted_in(self) -> None:
        client = _make_client(domains=("api.example.com",), allow_non_https=True)
        client.validate_url("http://api.example.com/data")  # must not raise

    def test_ftp_blocked(self) -> None:
        client = _make_client(domains=("api.example.com",))
        with pytest.raises(DomainBlockedError):
            client.validate_url("ftp://api.example.com/data")


# ============================================================================
# Tests: ReadOnlyHttpClient — redirect blocking (mocked)
# ============================================================================


class TestRedirectBlocking:
    """Off-allowlist redirect blocked using mocks."""

    def test_redirect_to_off_allowlist_domain_blocked(self) -> None:
        client = _make_client(domains=("api.example.com",))

        # Mock _open to simulate a redirect to an off-allowlist final URL
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://evil.attacker.io/stolen"
        mock_resp.read.return_value = b"data"

        with patch.object(client, "_open", return_value=mock_resp):
            with pytest.raises(RedirectBlockedError):
                client.request("GET", "https://api.example.com/redirect")

    def test_redirect_to_allowlisted_domain_allowed(self) -> None:
        client = _make_client(domains=("api.example.com", "cdn.example.com"))

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://cdn.example.com/cached"
        mock_resp.read.return_value = b"ok"

        with patch.object(client, "_open", return_value=mock_resp):
            result = client.request("GET", "https://api.example.com/data")
            assert result == b"ok"


# ============================================================================
# Tests: ReadOnlyHttpClient — response size cap (mocked)
# ============================================================================


class TestResponseSizeCap:
    """Response larger than max_bytes blocked using mocks."""

    def test_oversized_response_raises(self) -> None:
        client = _make_client(domains=("api.example.com",), max_bytes=100)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://api.example.com/big"
        # Return 101 bytes (exceeds max_bytes=100)
        mock_resp.read.return_value = b"x" * 101

        with patch.object(client, "_open", return_value=mock_resp):
            with pytest.raises(ResponseTooLargeError):
                client.request("GET", "https://api.example.com/big")

    def test_exact_max_bytes_allowed(self) -> None:
        client = _make_client(domains=("api.example.com",), max_bytes=100)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://api.example.com/exact"
        mock_resp.read.return_value = b"x" * 100

        with patch.object(client, "_open", return_value=mock_resp):
            result = client.request("GET", "https://api.example.com/exact")
            assert result == b"x" * 100


# ============================================================================
# Tests: ReadOnlyHttpClient — no cookies/auth headers (mock inspection)
# ============================================================================


class TestNoCookiesOrAuthHeaders:
    """No cookies or authorization headers sent using mock inspection."""

    def test_no_auth_or_cookie_headers_on_request(self) -> None:
        client = _make_client(domains=("api.example.com",))

        captured_requests: list[urllib.request.Request] = []

        def mock_open(request_obj, timeout):
            captured_requests.append(request_obj)
            resp = MagicMock()
            resp.geturl.return_value = request_obj.full_url
            resp.read.return_value = b"response"
            return resp

        with patch.object(client, "_open", side_effect=mock_open):
            client.request("GET", "https://api.example.com/data")

        assert len(captured_requests) == 1
        req = captured_requests[0]
        headers = req.headers
        # Check no cookie or authorization headers
        header_keys_lower = {k.lower() for k in headers.keys()}
        assert "cookie" not in header_keys_lower
        assert "authorization" not in header_keys_lower
        assert "proxy-authorization" not in header_keys_lower

    def test_no_headers_added_at_all(self) -> None:
        """The client should not add any custom headers."""
        client = _make_client(domains=("api.example.com",))

        captured_requests: list[urllib.request.Request] = []

        def mock_open(request_obj, timeout):
            captured_requests.append(request_obj)
            resp = MagicMock()
            resp.geturl.return_value = request_obj.full_url
            resp.read.return_value = b"ok"
            return resp

        with patch.object(client, "_open", side_effect=mock_open):
            client.request("GET", "https://api.example.com/path")

        req = captured_requests[0]
        # urllib.request.Request adds no headers by default unless explicitly set
        # The client must not have added any
        assert len(req.headers) == 0


# ============================================================================
# Tests: SourceAdapter ABC — cannot instantiate without required methods
# ============================================================================


class TestSourceAdapterABC:
    """SourceAdapter cannot be instantiated without required methods."""

    def test_cannot_instantiate_bare_abc(self) -> None:
        with pytest.raises(TypeError):
            SourceAdapter()  # type: ignore[abstract]

    def test_cannot_instantiate_partial_implementation(self) -> None:
        class PartialAdapter(SourceAdapter):
            @property
            def source_id(self) -> str:
                return "partial"

            # Missing adapter_name and normalize

        with pytest.raises(TypeError):
            PartialAdapter()  # type: ignore[abstract]

    def test_cannot_instantiate_missing_normalize(self) -> None:
        class MissingNormalize(SourceAdapter):
            @property
            def source_id(self) -> str:
                return "x"

            @property
            def adapter_name(self) -> str:
                return "y"

            # Missing normalize

        with pytest.raises(TypeError):
            MissingNormalize()  # type: ignore[abstract]

    def test_full_implementation_instantiates(self) -> None:
        class FullAdapter(SourceAdapter):
            @property
            def source_id(self) -> str:
                return "full"

            @property
            def adapter_name(self) -> str:
                return "full_adapter"

            def normalize(self, raw):
                return []

        adapter = FullAdapter()
        assert adapter.source_id == "full"
        assert adapter.adapter_name == "full_adapter"
        assert adapter.normalize(b"") == []
        # Default build_urls returns empty list
        entry = _make_entry()
        assert adapter.build_urls(entry) == []


# ============================================================================
# Phase 1.2b Tests: Extended SourceAllowlistEntry fields
# ============================================================================


class TestRealSourceAllowlistLoads:
    """The real defi_autonomy/data/source_allowlist.json loads successfully."""

    def test_real_allowlist_loads(self) -> None:
        real_path = Path(__file__).resolve().parents[2] / "data" / "source_allowlist.json"
        result = load_source_allowlist(real_path)
        assert len(result) > 0
        # Verify known entries exist
        assert "meteora" in result
        assert "coingecko" in result
        # Verify domains are tuples
        for entry in result.values():
            assert isinstance(entry.domains, tuple)
            assert isinstance(entry.methods, tuple)


class TestMethodsFieldValidation:
    """methods field: converted to uppercase tuple, invalid methods rejected."""

    def test_methods_converted_to_uppercase_tuple(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "methods": ["get", "head"],
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert result["x"].methods == ("GET", "HEAD")

    def test_methods_defaults_when_absent(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert result["x"].methods == ("GET", "HEAD")

    def test_invalid_method_post_rejected(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "methods": ["GET", "POST"],
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="not in allowed set"):
            load_source_allowlist(p)

    def test_empty_methods_rejected(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "methods": [],
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="must not be empty"):
            load_source_allowlist(p)


class TestMaxResponseBytesValidation:
    """max_response_bytes: must be > 0."""

    def test_max_response_bytes_zero_rejected(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "max_response_bytes": 0,
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="max_response_bytes must be > 0"):
            load_source_allowlist(p)

    def test_max_response_bytes_negative_rejected(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "max_response_bytes": -100,
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="max_response_bytes must be > 0"):
            load_source_allowlist(p)

    def test_max_response_bytes_valid(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "max_response_bytes": 2097152,
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert result["x"].max_response_bytes == 2097152


class TestSourceConfidenceScoreValidation:
    """source_confidence_score: must be between 0 and 1."""

    def test_score_below_zero_rejected(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "source_confidence_score": -0.1,
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="source_confidence_score must be between 0 and 1"):
            load_source_allowlist(p)

    def test_score_above_one_rejected(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "x",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "source_confidence_score": 1.1,
            }
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        with pytest.raises(MalformedSourceAllowlistError, match="source_confidence_score must be between 0 and 1"):
            load_source_allowlist(p)

    def test_score_at_boundaries_valid(self, tmp_path: Path) -> None:
        entries = [
            {
                "source_id": "zero",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "source_confidence_score": 0.0,
            },
            {
                "source_id": "one",
                "adapter_name": "y",
                "domains": ["z.com"],
                "max_freshness_seconds": 60,
                "fetch_timeout_seconds": 5,
                "source_confidence_score": 1.0,
            },
        ]
        doc = _valid_allowlist_doc(entries)
        p = _write_allowlist(tmp_path, doc)
        result = load_source_allowlist(p)
        assert result["zero"].source_confidence_score == 0.0
        assert result["one"].source_confidence_score == 1.0


class TestClientUsesEntryMaxResponseBytes:
    """ReadOnlyHttpClient uses entry.max_response_bytes by default."""

    def test_defaults_to_entry_max_response_bytes(self) -> None:
        entry = _make_entry(max_response_bytes=500)
        client = ReadOnlyHttpClient(entry=entry)
        assert client._max_bytes == 500

    def test_explicit_max_bytes_overrides_entry(self) -> None:
        entry = _make_entry(max_response_bytes=500)
        client = ReadOnlyHttpClient(entry=entry, max_bytes=200)
        assert client._max_bytes == 200

    def test_entry_max_response_bytes_enforced_in_request(self) -> None:
        entry = _make_entry(domains=("api.example.com",), max_response_bytes=50)
        client = ReadOnlyHttpClient(entry=entry)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://api.example.com/data"
        mock_resp.read.return_value = b"x" * 51  # exceeds 50

        with patch.object(client, "_open", return_value=mock_resp):
            with pytest.raises(ResponseTooLargeError):
                client.request("GET", "https://api.example.com/data")

    def test_entry_max_response_bytes_allows_within_limit(self) -> None:
        entry = _make_entry(domains=("api.example.com",), max_response_bytes=50)
        client = ReadOnlyHttpClient(entry=entry)

        mock_resp = MagicMock()
        mock_resp.geturl.return_value = "https://api.example.com/data"
        mock_resp.read.return_value = b"x" * 50

        with patch.object(client, "_open", return_value=mock_resp):
            result = client.request("GET", "https://api.example.com/data")
            assert result == b"x" * 50


class TestClientEnforcesEntryMethods:
    """ReadOnlyHttpClient enforces entry.methods, not hardcoded GET/HEAD."""

    def test_head_only_entry_blocks_get(self) -> None:
        entry = _make_entry(methods=("HEAD",))
        client = ReadOnlyHttpClient(entry=entry)
        with pytest.raises(MethodBlockedError):
            client.validate_method("GET")

    def test_head_only_entry_allows_head(self) -> None:
        entry = _make_entry(methods=("HEAD",))
        client = ReadOnlyHttpClient(entry=entry)
        client.validate_method("HEAD")  # must not raise

    def test_get_only_entry_allows_get(self) -> None:
        entry = _make_entry(methods=("GET",))
        client = ReadOnlyHttpClient(entry=entry)
        client.validate_method("GET")  # must not raise

    def test_get_only_entry_blocks_head(self) -> None:
        entry = _make_entry(methods=("GET",))
        client = ReadOnlyHttpClient(entry=entry)
        with pytest.raises(MethodBlockedError):
            client.validate_method("HEAD")
