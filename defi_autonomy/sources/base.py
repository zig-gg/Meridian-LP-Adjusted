"""SourceAdapter ABC and ReadOnlyHttpClient - Phase 1.2.

This module is the bounded outbound-network boundary for the
External_Data_Ingestion layer (Phase 1.10). All future source adapters under
`defi_autonomy/sources/*` must use `ReadOnlyHttpClient` and must not bypass
its method/domain enforcement.

Constraints (R24.4 - R24.7, design.md Security Boundaries / Source ingestion):
- Methods restricted to {GET, HEAD}.
- Targets restricted to allowlisted domains (exact match or proper subdomain).
- HTTPS-only by default. HTTP requires explicit opt-in (used in tests only).
- No cookies, no authorization headers, no environment-secret reads.
- Off-allowlist redirects blocked at both pre-redirect and post-fetch stages.
- Per-request body size cap (max_bytes); oversized responses raise.
- Per-source timeout from the allowlist entry; timeouts raise.

Phase 1.2 deliberately does NOT implement:
- any concrete source adapter,
- External_Data_Ingestion orchestrator,
- Yield_Scanner / Risk_Scorer / Policy_Engine / Tx_Simulator / Wallet_Executor,
- any signing or private-key handling.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from defi_autonomy.schemas.normalized_candidate import NormalizedCandidate


# ============================================================================
# Exceptions
# ============================================================================


class SourceAdapterError(Exception):
    """Base for every source-adapter / read-only-client error."""


class MethodBlockedError(SourceAdapterError):
    """HTTP method outside {GET, HEAD}."""


class DomainBlockedError(SourceAdapterError):
    """Target domain or scheme not permitted by the allowlist."""


class RedirectBlockedError(SourceAdapterError):
    """Redirect chain crossed an off-allowlist domain."""


class ResponseTooLargeError(SourceAdapterError):
    """Response body exceeds the per-request max_bytes cap."""


class SourceTimeoutError(SourceAdapterError):
    """Per-source fetch timeout exceeded."""


class MalformedSourceAllowlistError(SourceAdapterError):
    """source_allowlist.json failed structural validation."""


# ============================================================================
# SourceAllowlistEntry + loader
# ============================================================================


_ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})


@dataclass(frozen=True, slots=True)
class SourceAllowlistEntry:
    """One entry in `data/source_allowlist.json`.

    Fields are deliberately the minimum needed by the read-only client and
    the ingestion orchestrator. `__post_init__` enforces non-negativity on
    integer bounds; the loader enforces field-set strictness and tuple
    conversion for `domains` and `methods`.
    """

    source_id: str
    adapter_name: str
    domains: tuple[str, ...]
    max_freshness_seconds: int
    fetch_timeout_seconds: int
    methods: tuple[str, ...] = ("GET", "HEAD")
    max_response_bytes: int = 1_000_000
    source_confidence_score: float = 0.5
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.source_id must be a non-empty string"
            )
        if not isinstance(self.adapter_name, str) or not self.adapter_name:
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.adapter_name must be a non-empty string"
            )
        if not isinstance(self.domains, tuple):
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.domains must be a tuple"
            )
        if not all(isinstance(d, str) and d for d in self.domains):
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.domains entries must be non-empty strings"
            )
        if not isinstance(self.max_freshness_seconds, int) or isinstance(
            self.max_freshness_seconds, bool
        ):
            raise MalformedSourceAllowlistError(
                "max_freshness_seconds must be an int"
            )
        if self.max_freshness_seconds < 0:
            raise MalformedSourceAllowlistError(
                "max_freshness_seconds must be >= 0"
            )
        if not isinstance(self.fetch_timeout_seconds, int) or isinstance(
            self.fetch_timeout_seconds, bool
        ):
            raise MalformedSourceAllowlistError(
                "fetch_timeout_seconds must be an int"
            )
        if self.fetch_timeout_seconds < 0:
            raise MalformedSourceAllowlistError(
                "fetch_timeout_seconds must be >= 0"
            )
        # methods validation
        if not isinstance(self.methods, tuple):
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.methods must be a tuple"
            )
        if not self.methods:
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.methods must not be empty"
            )
        if not all(isinstance(m, str) and m for m in self.methods):
            raise MalformedSourceAllowlistError(
                "SourceAllowlistEntry.methods entries must be non-empty strings"
            )
        for m in self.methods:
            if m.upper() not in _ALLOWED_METHODS:
                raise MalformedSourceAllowlistError(
                    f"method {m!r} not in allowed set {sorted(_ALLOWED_METHODS)}"
                )
        # max_response_bytes validation
        if not isinstance(self.max_response_bytes, int) or isinstance(
            self.max_response_bytes, bool
        ):
            raise MalformedSourceAllowlistError(
                "max_response_bytes must be an int"
            )
        if self.max_response_bytes <= 0:
            raise MalformedSourceAllowlistError(
                "max_response_bytes must be > 0"
            )
        # source_confidence_score validation
        if not isinstance(self.source_confidence_score, (int, float)) or isinstance(
            self.source_confidence_score, bool
        ):
            raise MalformedSourceAllowlistError(
                "source_confidence_score must be a number"
            )
        if self.source_confidence_score < 0 or self.source_confidence_score > 1:
            raise MalformedSourceAllowlistError(
                "source_confidence_score must be between 0 and 1"
            )
        if self.notes is not None and not isinstance(self.notes, str):
            raise MalformedSourceAllowlistError(
                "notes must be a string or null"
            )


_ENTRY_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in fields(SourceAllowlistEntry)
)
_OPTIONAL_ENTRY_FIELDS: frozenset[str] = frozenset(
    {"notes", "methods", "max_response_bytes", "source_confidence_score"}
)
_REQUIRED_ENTRY_FIELDS: frozenset[str] = _ENTRY_FIELD_NAMES - _OPTIONAL_ENTRY_FIELDS


def load_source_allowlist(
    path: str | Path,
) -> dict[str, SourceAllowlistEntry]:
    """Load and validate `data/source_allowlist.json`.

    Returns a dict keyed by `source_id`. Raises MalformedSourceAllowlistError
    on any structural defect:
      - file unreadable or not JSON,
      - missing top-level `version` or `entries`,
      - `entries` not a list,
      - any entry not a dict,
      - any entry missing a required field,
      - any entry containing an unknown field,
      - duplicate `source_id`,
      - any field with a value of the wrong type or out-of-range bound.
    """
    p = Path(path)
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise MalformedSourceAllowlistError(
            f"could not read source allowlist at {p}: {e}"
        ) from e
    try:
        doc = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise MalformedSourceAllowlistError(
            f"invalid JSON in {p}: {e}"
        ) from e
    if not isinstance(doc, dict):
        raise MalformedSourceAllowlistError(
            f"top-level of {p} must be a JSON object"
        )
    if "version" not in doc:
        raise MalformedSourceAllowlistError("missing top-level 'version'")
    if "entries" not in doc:
        raise MalformedSourceAllowlistError("missing top-level 'entries'")
    entries = doc["entries"]
    if not isinstance(entries, list):
        raise MalformedSourceAllowlistError("'entries' must be a list")

    out: dict[str, SourceAllowlistEntry] = {}
    for i, entry_data in enumerate(entries):
        if not isinstance(entry_data, dict):
            raise MalformedSourceAllowlistError(
                f"entries[{i}] must be a JSON object"
            )
        keys = set(entry_data.keys())
        unknown = keys - _ENTRY_FIELD_NAMES
        if unknown:
            raise MalformedSourceAllowlistError(
                f"entries[{i}] contains unknown fields: {sorted(unknown)}"
            )
        missing = _REQUIRED_ENTRY_FIELDS - keys
        if missing:
            raise MalformedSourceAllowlistError(
                f"entries[{i}] missing required fields: {sorted(missing)}"
            )
        domains = entry_data["domains"]
        if not isinstance(domains, list) or not all(
            isinstance(d, str) for d in domains
        ):
            raise MalformedSourceAllowlistError(
                f"entries[{i}].domains must be a list of strings"
            )
        # Handle optional methods field
        methods_raw = entry_data.get("methods")
        if methods_raw is not None:
            if not isinstance(methods_raw, list) or not all(
                isinstance(m, str) for m in methods_raw
            ):
                raise MalformedSourceAllowlistError(
                    f"entries[{i}].methods must be a list of strings"
                )
            methods = tuple(m.upper() for m in methods_raw)
        else:
            methods = ("GET", "HEAD")
        # Handle optional max_response_bytes field
        max_response_bytes = entry_data.get("max_response_bytes")
        if max_response_bytes is None:
            max_response_bytes = 1_000_000
        # Handle optional source_confidence_score field
        source_confidence_score = entry_data.get("source_confidence_score")
        if source_confidence_score is None:
            source_confidence_score = 0.5
        try:
            entry = SourceAllowlistEntry(
                source_id=entry_data["source_id"],
                adapter_name=entry_data["adapter_name"],
                domains=tuple(domains),
                max_freshness_seconds=entry_data["max_freshness_seconds"],
                fetch_timeout_seconds=entry_data["fetch_timeout_seconds"],
                methods=methods,
                max_response_bytes=max_response_bytes,
                source_confidence_score=source_confidence_score,
                notes=entry_data.get("notes"),
            )
        except MalformedSourceAllowlistError:
            raise
        except (TypeError, ValueError) as e:
            raise MalformedSourceAllowlistError(
                f"entries[{i}] failed to construct: {e}"
            ) from e
        if entry.source_id in out:
            raise MalformedSourceAllowlistError(
                f"duplicate source_id: {entry.source_id!r}"
            )
        out[entry.source_id] = entry
    return out


# ============================================================================
# SourceAdapter ABC
# ============================================================================


class SourceAdapter(ABC):
    """Abstract contract for every source adapter.

    Subclasses MUST implement `source_id`, `adapter_name`, and `normalize`.
    `normalize` MUST NOT perform network I/O; it converts raw fetched bytes
    or parsed structures into a list of NormalizedCandidate.

    `build_urls` is optional. The default returns an empty list; concrete
    adapters override to declare which URLs to fetch given an allowlist entry.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable source identifier; must equal a key in source_allowlist."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Adapter identifier; must equal the entry's adapter_name."""

    @abstractmethod
    def normalize(
        self, raw: bytes | str | dict | list
    ) -> list[NormalizedCandidate]:
        """Convert raw input into a list of NormalizedCandidate.

        MUST NOT perform any network I/O. MUST NOT touch private keys, env
        secrets, or any allowlist file.
        """

    def build_urls(self, entry: SourceAllowlistEntry) -> list[str]:
        """Optional URL builder. Default is empty list."""
        return []


# ============================================================================
# ReadOnlyHttpClient
# ============================================================================


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTPRedirectHandler that revalidates each redirect target.

    Calls the supplied validator on the redirect URL before letting urllib
    follow it. A blocked redirect raises RedirectBlockedError from inside
    the handler chain; urllib does not wrap custom exceptions, so the error
    propagates to ReadOnlyHttpClient.request.
    """

    def __init__(self, validate_url_fn) -> None:
        super().__init__()
        self._validate = validate_url_fn

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        try:
            self._validate(newurl)
        except SourceAdapterError as e:
            raise RedirectBlockedError(
                f"redirect to off-allowlist URL: {newurl}"
            ) from e
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ReadOnlyHttpClient:
    """A bounded read-only HTTP client for public-source adapters.

    The single network entry point is `_open`, which is intentionally easy
    to monkeypatch from tests. Production callers must go through `request`,
    which performs method validation, URL validation, post-fetch redirect
    validation, and size capping.
    """

    DEFAULT_MAX_BYTES: int = 1_000_000

    def __init__(
        self,
        entry: SourceAllowlistEntry,
        max_bytes: int | None = None,
        timeout_seconds: int | None = None,
        allow_non_https: bool = False,
    ) -> None:
        if not isinstance(entry, SourceAllowlistEntry):
            raise TypeError(
                "ReadOnlyHttpClient requires a SourceAllowlistEntry, got "
                f"{type(entry).__name__}"
            )
        # Default max_bytes from entry.max_response_bytes if not explicitly given
        if max_bytes is None:
            max_bytes = entry.max_response_bytes
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
            raise TypeError("max_bytes must be an int")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        if timeout_seconds is not None and (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be a non-negative int or None")

        self._entry = entry
        self._max_bytes = max_bytes
        self._allow_non_https = bool(allow_non_https)
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else entry.fetch_timeout_seconds
        )
        self._allowed_domains: tuple[str, ...] = tuple(
            d.lower().rstrip(".") for d in entry.domains
        )
        self._allowed_methods: frozenset[str] = frozenset(
            m.upper() for m in entry.methods
        )

    # -- validators ---------------------------------------------------------

    def validate_method(self, method: str) -> None:
        """Raise MethodBlockedError unless method is in entry.methods (case-insensitive)."""
        if not isinstance(method, str) or not method:
            raise MethodBlockedError(
                f"HTTP method must be a non-empty string, got {method!r}"
            )
        if method.upper() not in self._allowed_methods:
            raise MethodBlockedError(
                f"HTTP method {method!r} not in allowlist {sorted(self._allowed_methods)}"
            )

    def validate_url(self, url: str) -> None:
        """Raise DomainBlockedError if scheme or host is not permitted.

        Allowed schemes: https. http only when allow_non_https=True.
        Allowed hosts: exact match or proper subdomain of an allowlisted domain.
        Hostnames are normalized to lowercase and stripped of trailing dots.
        """
        if not isinstance(url, str) or not url:
            raise DomainBlockedError(f"URL must be a non-empty string, got {url!r}")
        try:
            parsed = urllib.parse.urlsplit(url)
        except (ValueError, UnicodeError) as e:
            raise DomainBlockedError(f"unparseable URL {url!r}: {e}") from e

        scheme = (parsed.scheme or "").lower()
        if scheme == "https":
            pass
        elif scheme == "http":
            if not self._allow_non_https:
                raise DomainBlockedError(
                    f"non-HTTPS URL blocked (allow_non_https=False): {url}"
                )
        else:
            raise DomainBlockedError(
                f"unsupported URL scheme {scheme!r} in {url}"
            )

        host_raw = parsed.hostname
        if not host_raw:
            raise DomainBlockedError(f"missing host in URL: {url}")
        host = host_raw.lower().rstrip(".")
        if not host:
            raise DomainBlockedError(f"empty host in URL: {url}")

        for allowed in self._allowed_domains:
            if host == allowed:
                return
            if host.endswith("." + allowed):
                return
        raise DomainBlockedError(
            f"host {host!r} not in allowlist {self._allowed_domains}"
        )

    # -- network entry point (overridable from tests) ----------------------

    def _open(self, request_obj: urllib.request.Request, timeout: int):
        """Perform the actual network call.

        Builds an opener with HTTPSHandler and the allowlist redirect handler.
        No cookie processor, no auth handler, no env-derived proxy handler.
        Tests monkeypatch this method to avoid real network I/O.
        """
        handlers: list = [urllib.request.HTTPSHandler()]
        if self._allow_non_https:
            handlers.append(urllib.request.HTTPHandler())
        handlers.append(_AllowlistRedirectHandler(self.validate_url))
        opener = urllib.request.build_opener(*handlers)
        return opener.open(request_obj, timeout=timeout)

    # -- public request method --------------------------------------------

    def request(self, method: str, url: str) -> bytes:
        """Perform a bounded, read-only request and return the body bytes.

        Order of checks:
          1. Method must be in entry.methods (loader guarantees only GET/HEAD).
          2. URL must be HTTPS (unless allow_non_https) and host must be in
             the allowlist.
          3. Open via `_open` with the allowlist redirect handler.
          4. Final URL after redirects must still be in the allowlist
             (belt-and-suspenders with the redirect handler).
          5. Body must not exceed max_bytes.

        Cookies, Authorization headers, and environment-derived secrets are
        NEVER added by this method.
        """
        self.validate_method(method)
        self.validate_url(url)

        m = method.upper()
        req = urllib.request.Request(url=url, method=m)
        # Defensive: do not attach any auth or cookie headers. Do not derive
        # any header value from environment variables.

        try:
            resp = self._open(req, self._timeout)
        except RedirectBlockedError:
            raise
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise SourceTimeoutError(f"timeout fetching {url}") from e
            raise SourceAdapterError(f"network error fetching {url}: {e}") from e
        except (socket.timeout, TimeoutError) as e:
            raise SourceTimeoutError(f"timeout fetching {url}") from e

        try:
            final_url = resp.geturl() if callable(getattr(resp, "geturl", None)) else url
        except Exception:
            final_url = url
        if final_url != url:
            try:
                self.validate_url(final_url)
            except DomainBlockedError as e:
                raise RedirectBlockedError(
                    f"final URL after redirects is off-allowlist: {final_url}"
                ) from e

        # Read with a hard cap. We ask for max_bytes + 1; if we receive that
        # many or more, the response was too large.
        try:
            body = resp.read(self._max_bytes + 1)
        except (socket.timeout, TimeoutError) as e:
            raise SourceTimeoutError(f"timeout reading body from {url}") from e

        if not isinstance(body, (bytes, bytearray, memoryview)):
            raise SourceAdapterError(
                f"unexpected body type {type(body).__name__} from {url}"
            )
        body_bytes = bytes(body)
        if len(body_bytes) > self._max_bytes:
            raise ResponseTooLargeError(
                f"response from {url} exceeded max_bytes={self._max_bytes}"
            )
        return body_bytes


__all__ = [
    "DomainBlockedError",
    "MalformedSourceAllowlistError",
    "MethodBlockedError",
    "ReadOnlyHttpClient",
    "RedirectBlockedError",
    "ResponseTooLargeError",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceAllowlistEntry",
    "SourceTimeoutError",
    "load_source_allowlist",
]