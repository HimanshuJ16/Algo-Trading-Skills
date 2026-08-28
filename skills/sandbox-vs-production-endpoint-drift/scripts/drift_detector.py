"""
sandbox-vs-production-endpoint-drift: response schema, header and status-code drift
detector for broker sandbox/paper environments versus live production endpoints.

The tool is a **promotion gate**, so its failure modes are asymmetric: a false positive
costs a developer a few minutes, a false negative promotes an integration that was
rehearsed against a contract production does not honour. Three properties follow from
that:

  1. **Comparison is recursive.** Broker responses nest — an order object inside an
     envelope, a list of legs or fills inside the order. A comparator that walks only the
     top-level keys reports "no drift" for a payload in which every nested price changed
     from a JSON number to a string. Nested containers are compared element-wise and every
     finding carries the dotted path (``order.legs[0].price``) at which it was found.

  2. **Absence and null are drift.** A field the sandbox never returns, a field production
     no longer returns, an array production populates but the sandbox leaves empty, and a
     null where the other environment returns a value are all differences between the
     contract the integration was written against and the one it will meet in production.
     None of them are visible to a comparator that only diffs the types of keys present
     and non-null on both sides.

  3. **Every stage feeds one report.** Schema, header and status-code drift are audited by
     separate methods, but a caller that gates a promotion on the schema report alone gets
     a green light while a CRITICAL status-code mismatch sits in a list it never merged.
     ``audit_endpoint`` runs every stage and folds the findings into a single report.

Severity contract (see ``references/standards.md``):

  * ``CRITICAL`` — promotion blocker. Production differs from the sandbox contract in a
    way that can raise or mis-parse in live trading, or exposes a production surface the
    sandbox never exercised at all.
  * ``WARNING``  — review required. A difference most parsers tolerate, or a region that
    could not be compared.
  * ``INFO``     — observation with no direct integration impact.

Scope limits, deliberately not papered over:

  * This compares **captured samples**, not environments. It is only as representative as
    the responses handed to it, and one sample per environment cannot prove a field is
    always present or always non-null.
  * It compares **structure, not behaviour**. Simulated fills, unenforced liquidity checks
    and different matching semantics are invisible here and are the subject of
    ``demo-account-realism-gap-assessment``.
  * Arrays are compared using their **first element** as representative; a heterogeneous
    array is therefore only partially compared.
  * Values are not compared, only shapes. Two ``str`` fields differing in content produce
    no finding.
"""
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Recursion ceiling for payload comparison. Broker responses nest a handful of levels;
#: anything beyond this is either pathological or hostile input, and truncating with a
#: visible finding is preferable to a RecursionError inside a CI gate.
DEFAULT_MAX_DEPTH = 20

#: Rate-limit fields audited for presence. ``x-ratelimit-*`` / ``x-rate-limit-*`` are
#: de-facto vendor conventions with no standard behind them; ``ratelimit`` and
#: ``ratelimit-policy`` come from draft-ietf-httpapi-ratelimit-headers (Standards Track,
#: still an Internet-Draft as of August 2026, not an RFC); ``retry-after`` is RFC 9110
#: Section 10.2.3. Field names are matched case-insensitively per RFC 9110 Section 5.1.
RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-rate-limit-limit",
    "x-rate-limit-remaining",
    "x-rate-limit-reset",
    "ratelimit",
    "ratelimit-policy",
    "retry-after",
)

#: Subset of the above whose value is a quota the client calibrates its request pacing
#: against, and which is therefore compared by value and not only by presence.
QUOTA_HEADERS = ("x-ratelimit-limit", "x-rate-limit-limit")


class DriftAuditError(ValueError):
    """Raised when the supplied samples cannot be audited at all.

    Distinct from a drift finding: a finding says the environments differ, this says the
    comparison never happened. A gate that reports "no drift" for an empty payload or a
    failed capture is worse than one that refuses to run.
    """


class DriftSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class DriftCategory(str, Enum):
    """Machine-readable classification, so callers filter on category, not description."""

    MISSING_IN_SANDBOX = "MISSING_IN_SANDBOX"
    MISSING_IN_PRODUCTION = "MISSING_IN_PRODUCTION"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    NUMERIC_TYPE_MISMATCH = "NUMERIC_TYPE_MISMATCH"
    NULLABILITY_MISMATCH = "NULLABILITY_MISMATCH"
    ARRAY_NOT_EXERCISED = "ARRAY_NOT_EXERCISED"
    ARRAY_NOT_COMPARED = "ARRAY_NOT_COMPARED"
    BODY_PRESENCE_MISMATCH = "BODY_PRESENCE_MISMATCH"
    DEPTH_LIMIT_REACHED = "DEPTH_LIMIT_REACHED"
    HEADER_MISSING_IN_SANDBOX = "HEADER_MISSING_IN_SANDBOX"
    RATE_LIMIT_VALUE_MISMATCH = "RATE_LIMIT_VALUE_MISMATCH"
    CONTENT_TYPE_MISMATCH = "CONTENT_TYPE_MISMATCH"
    STATUS_CLASS_MISMATCH = "STATUS_CLASS_MISMATCH"
    STATUS_CODE_MISMATCH = "STATUS_CODE_MISMATCH"
    ENDPOINT_MISSING_IN_SANDBOX = "ENDPOINT_MISSING_IN_SANDBOX"
    ENDPOINT_MISSING_IN_PRODUCTION = "ENDPOINT_MISSING_IN_PRODUCTION"
    OTHER = "OTHER"


@dataclass
class DriftFinding:
    endpoint: str
    field_name: str
    severity: DriftSeverity
    description: str
    sandbox_val: Any
    prod_val: Any
    #: Appended with a default so existing positional construction keeps working.
    category: DriftCategory = DriftCategory.OTHER


@dataclass
class EndpointDriftReport:
    endpoint: str
    passed: bool
    critical_count: int
    warning_count: int
    findings: List[DriftFinding] = field(default_factory=list)

    @classmethod
    def from_findings(
        cls, endpoint: str, findings: Sequence[DriftFinding]
    ) -> "EndpointDriftReport":
        """Builds a report whose counters cannot disagree with its findings."""
        findings = list(findings)
        criticals = sum(1 for f in findings if f.severity is DriftSeverity.CRITICAL)
        warnings = sum(1 for f in findings if f.severity is DriftSeverity.WARNING)
        return cls(
            endpoint=endpoint,
            passed=criticals == 0,
            critical_count=criticals,
            warning_count=warnings,
            findings=findings,
        )

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is DriftSeverity.INFO)

    @property
    def exit_code(self) -> int:
        """0 when no CRITICAL drift was found, 1 otherwise — for use as a CI gate."""
        return 0 if self.passed else 1

    def format_report(self) -> str:
        """Human-readable summary, most severe findings first."""
        order = {
            DriftSeverity.CRITICAL: 0,
            DriftSeverity.WARNING: 1,
            DriftSeverity.INFO: 2,
        }
        lines = [
            f"Endpoint drift audit: {self.endpoint}",
            f"  result: {'PASS' if self.passed else 'BLOCK'} "
            f"({self.critical_count} critical, {self.warning_count} warning, "
            f"{self.info_count} info)",
        ]
        for f in sorted(self.findings, key=lambda x: (order[x.severity], x.field_name)):
            lines.append(f"  [{f.severity.value}] {f.field_name}: {f.description}")
        if not self.findings:
            lines.append("  no drift detected in the supplied samples")
        return "\n".join(lines)


class EndpointDriftDetector:
    """
    Audits JSON response schemas, headers, and status-code behaviour between sandbox and
    production broker endpoints to detect integration risk before a live promotion.

    Stateless and deterministic: the same samples always yield the same findings in the
    same order (keys are walked in sorted order, not in set-iteration order).
    """

    def __init__(self, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        if max_depth < 1:
            raise DriftAuditError(f"max_depth must be >= 1, got {max_depth}")
        self.max_depth = max_depth

    # ------------------------------------------------------------------ schemas

    def compare_schemas(
        self, endpoint: str, sandbox_json: Mapping[str, Any], prod_json: Mapping[str, Any]
    ) -> EndpointDriftReport:
        """Recursively compares field presence, nullability and data types.

        Raises:
            DriftAuditError: if either sample is not a mapping, or both are empty — an
                empty capture is indistinguishable from a failed one, and reporting it as
                parity is exactly the false green this gate exists to prevent.
        """
        for label, payload in (("sandbox", sandbox_json), ("production", prod_json)):
            if not isinstance(payload, Mapping):
                raise DriftAuditError(
                    f"{label} payload for '{endpoint}' must be a mapping of the decoded "
                    f"JSON object, got {type(payload).__name__}"
                )
        if not sandbox_json and not prod_json:
            raise DriftAuditError(
                f"both payloads for '{endpoint}' are empty; nothing was compared. For an "
                f"endpoint that genuinely returns no body, audit headers and status codes "
                f"instead of passing empty payloads."
            )

        findings: List[DriftFinding] = []
        self._compare_values(endpoint, "", sandbox_json, prod_json, findings, depth=0)
        report = EndpointDriftReport.from_findings(endpoint, findings)
        logger.info(
            "Schema comparison complete for '%s': passed=%s, %d critical, %d warning.",
            endpoint, report.passed, report.critical_count, report.warning_count,
        )
        return report

    def _compare_values(
        self,
        endpoint: str,
        path: str,
        sandbox_val: Any,
        prod_val: Any,
        findings: List[DriftFinding],
        depth: int,
    ) -> None:
        """Recursive shape comparison. ``path`` is the dotted location in the payload."""
        label = path or "<root>"

        if depth > self.max_depth:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=label,
                    severity=DriftSeverity.WARNING,
                    description=(
                        f"Nesting at '{label}' exceeds max_depth={self.max_depth}; this "
                        f"subtree was NOT compared"
                    ),
                    sandbox_val=type(sandbox_val).__name__,
                    prod_val=type(prod_val).__name__,
                    category=DriftCategory.DEPTH_LIMIT_REACHED,
                )
            )
            return

        # Null drift, checked before type comparison: None has no useful type to diff, and
        # a field that is null in one environment was never exercised in the other.
        if sandbox_val is None or prod_val is None:
            if sandbox_val is None and prod_val is None:
                return
            if prod_val is None:
                description = (
                    f"Production returns null for '{label}' where sandbox returns "
                    f"{type(sandbox_val).__name__}; parsers built against the sandbox will "
                    f"fail on the null"
                )
            else:
                description = (
                    f"Sandbox returns null for '{label}' where production returns "
                    f"{type(prod_val).__name__}; the parse path for this field was never "
                    f"exercised in sandbox"
                )
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=label,
                    severity=DriftSeverity.CRITICAL,
                    description=description,
                    sandbox_val=sandbox_val,
                    prod_val=prod_val,
                    category=DriftCategory.NULLABILITY_MISMATCH,
                )
            )
            return

        s_is_map = isinstance(sandbox_val, Mapping)
        p_is_map = isinstance(prod_val, Mapping)
        s_is_arr = self._is_array(sandbox_val)
        p_is_arr = self._is_array(prod_val)

        if s_is_map and p_is_map:
            self._compare_objects(endpoint, path, sandbox_val, prod_val, findings, depth)
            return
        if s_is_arr and p_is_arr:
            self._compare_arrays(endpoint, label, sandbox_val, prod_val, findings, depth)
            return
        if s_is_map or p_is_map or s_is_arr or p_is_arr:
            self._add_type_mismatch(endpoint, label, sandbox_val, prod_val, findings)
            return

        self._compare_scalars(endpoint, label, sandbox_val, prod_val, findings)

    def _compare_objects(
        self,
        endpoint: str,
        path: str,
        sandbox_obj: Mapping[str, Any],
        prod_obj: Mapping[str, Any],
        findings: List[DriftFinding],
        depth: int,
    ) -> None:
        sandbox_keys = set(sandbox_obj)
        prod_keys = set(prod_obj)

        # Present in production, absent in sandbox: the integration would be promoted
        # without ever having seen this field. Not necessarily a crash, but an unrehearsed
        # production surface, which a promotion gate blocks on.
        for k in sorted(prod_keys - sandbox_keys, key=str):
            child = self._join(path, k)
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=child,
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Field '{child}' present in production payload but missing in "
                        f"sandbox response"
                    ),
                    sandbox_val=None,
                    prod_val=type(prod_obj[k]).__name__,
                    category=DriftCategory.MISSING_IN_SANDBOX,
                )
            )

        # Present in sandbox, absent in production: code written and tested against the
        # sandbox contract raises KeyError the first time it runs live. This is the
        # direction that breaks production outright.
        for k in sorted(sandbox_keys - prod_keys, key=str):
            child = self._join(path, k)
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=child,
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Field '{child}' present in sandbox payload but absent in "
                        f"production response; code reading it will fail live"
                    ),
                    sandbox_val=type(sandbox_obj[k]).__name__,
                    prod_val=None,
                    category=DriftCategory.MISSING_IN_PRODUCTION,
                )
            )

        for k in sorted(sandbox_keys & prod_keys, key=str):
            self._compare_values(
                endpoint,
                self._join(path, k),
                sandbox_obj[k],
                prod_obj[k],
                findings,
                depth + 1,
            )

    def _compare_arrays(
        self,
        endpoint: str,
        label: str,
        sandbox_arr: Sequence[Any],
        prod_arr: Sequence[Any],
        findings: List[DriftFinding],
        depth: int,
    ) -> None:
        if not sandbox_arr and prod_arr:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=label,
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Array '{label}' is empty in sandbox but populated in production; "
                        f"the element schema was never exercised in sandbox"
                    ),
                    sandbox_val=0,
                    prod_val=len(prod_arr),
                    category=DriftCategory.ARRAY_NOT_EXERCISED,
                )
            )
            return
        if not prod_arr:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=label,
                    severity=(
                        DriftSeverity.INFO if not sandbox_arr else DriftSeverity.WARNING
                    ),
                    description=(
                        f"Array '{label}' is empty in the production sample; its element "
                        f"schema was NOT compared"
                    ),
                    sandbox_val=len(sandbox_arr),
                    prod_val=0,
                    category=DriftCategory.ARRAY_NOT_COMPARED,
                )
            )
            return
        # First element as representative: enough to catch a changed element schema
        # without emitting one finding per row of a long fill list.
        self._compare_values(
            endpoint, f"{label}[0]", sandbox_arr[0], prod_arr[0], findings, depth + 1
        )

    def _compare_scalars(
        self,
        endpoint: str,
        label: str,
        sandbox_val: Any,
        prod_val: Any,
        findings: List[DriftFinding],
    ) -> None:
        s_type = type(sandbox_val)
        p_type = type(prod_val)
        if s_type is p_type:
            return

        # bool is a subclass of int in Python, but JSON true is not JSON 1: treat any
        # bool/number pair as an incompatible change rather than a numeric widening.
        both_numeric = (
            s_type in (int, float)
            and p_type in (int, float)
            and not isinstance(sandbox_val, bool)
            and not isinstance(prod_val, bool)
        )
        if both_numeric:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=label,
                    severity=DriftSeverity.WARNING,
                    description=(
                        f"Numeric type mismatch for '{label}': sandbox is "
                        f"{s_type.__name__}, prod is {p_type.__name__}"
                    ),
                    sandbox_val=sandbox_val,
                    prod_val=prod_val,
                    category=DriftCategory.NUMERIC_TYPE_MISMATCH,
                )
            )
            return
        self._add_type_mismatch(endpoint, label, sandbox_val, prod_val, findings)

    def _add_type_mismatch(
        self,
        endpoint: str,
        label: str,
        sandbox_val: Any,
        prod_val: Any,
        findings: List[DriftFinding],
    ) -> None:
        findings.append(
            DriftFinding(
                endpoint=endpoint,
                field_name=label,
                severity=DriftSeverity.CRITICAL,
                description=(
                    f"Type mismatch for '{label}': sandbox is "
                    f"{type(sandbox_val).__name__}, prod is {type(prod_val).__name__}"
                ),
                sandbox_val=sandbox_val,
                prod_val=prod_val,
                category=DriftCategory.TYPE_MISMATCH,
            )
        )

    @staticmethod
    def _is_array(value: Any) -> bool:
        """True for JSON arrays only. ``str`` and ``bytes`` are sequences and are not."""
        return isinstance(value, (list, tuple))

    @staticmethod
    def _join(path: str, key: str) -> str:
        return f"{path}.{key}" if path else str(key)

    # ------------------------------------------------------------------ headers

    def compare_headers(
        self,
        endpoint: str,
        sandbox_headers: Mapping[str, str],
        prod_headers: Mapping[str, str],
    ) -> List[DriftFinding]:
        """Audits rate-limit, quota and content-type drift between the two environments.

        Header field names are compared case-insensitively (RFC 9110 Section 5.1).
        """
        for label, headers in (("sandbox", sandbox_headers), ("production", prod_headers)):
            if not isinstance(headers, Mapping):
                raise DriftAuditError(
                    f"{label} headers for '{endpoint}' must be a mapping, got "
                    f"{type(headers).__name__}"
                )
        sb_h = {str(k).lower(): self._header_value(v) for k, v in sandbox_headers.items()}
        pr_h = {str(k).lower(): self._header_value(v) for k, v in prod_headers.items()}
        findings: List[DriftFinding] = []

        for rk in RATE_LIMIT_HEADERS:
            if rk in pr_h and rk not in sb_h:
                findings.append(
                    DriftFinding(
                        endpoint=endpoint,
                        field_name=rk,
                        severity=DriftSeverity.WARNING,
                        description=(
                            f"Rate limit header '{rk}' present in production but missing "
                            f"in sandbox response; throttling handling is unrehearsed"
                        ),
                        sandbox_val=None,
                        prod_val=pr_h[rk],
                        category=DriftCategory.HEADER_MISSING_IN_SANDBOX,
                    )
                )

        for qk in QUOTA_HEADERS:
            sb_quota = self._parse_quota(sb_h.get(qk))
            pr_quota = self._parse_quota(pr_h.get(qk))
            if sb_quota is None or pr_quota is None or sb_quota == pr_quota:
                continue
            if sb_quota > pr_quota:
                findings.append(
                    DriftFinding(
                        endpoint=endpoint,
                        field_name=qk,
                        severity=DriftSeverity.CRITICAL,
                        description=(
                            f"Sandbox advertises a more permissive quota via '{qk}' "
                            f"({sb_quota} vs {pr_quota} in production); request pacing "
                            f"tuned in sandbox will breach the production limit"
                        ),
                        sandbox_val=sb_h[qk],
                        prod_val=pr_h[qk],
                        category=DriftCategory.RATE_LIMIT_VALUE_MISMATCH,
                    )
                )
            else:
                findings.append(
                    DriftFinding(
                        endpoint=endpoint,
                        field_name=qk,
                        severity=DriftSeverity.WARNING,
                        description=(
                            f"Production quota via '{qk}' is higher than sandbox "
                            f"({pr_quota} vs {sb_quota}); sandbox pacing is conservative "
                            f"but production throttling paths remain unrehearsed"
                        ),
                        sandbox_val=sb_h[qk],
                        prod_val=pr_h[qk],
                        category=DriftCategory.RATE_LIMIT_VALUE_MISMATCH,
                    )
                )

        sb_ct = self._media_type(sb_h.get("content-type"))
        pr_ct = self._media_type(pr_h.get("content-type"))
        if sb_ct and pr_ct and sb_ct != pr_ct:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name="content-type",
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Content type mismatch: sandbox returns '{sb_ct}', production "
                        f"returns '{pr_ct}'; the response decoder will not match live"
                    ),
                    sandbox_val=sb_h.get("content-type"),
                    prod_val=pr_h.get("content-type"),
                    category=DriftCategory.CONTENT_TYPE_MISMATCH,
                )
            )
        return findings

    @staticmethod
    def _header_value(raw: Any) -> Any:
        """First value of a header field.

        Some HTTP clients hand back a list per field name rather than the combined string;
        stringifying the list would report ``['application/json']`` as a media type and
        raise a spurious content-type finding.
        """
        if isinstance(raw, (list, tuple)):
            return raw[0] if raw else None
        return raw

    @staticmethod
    def _parse_quota(raw: Any) -> Optional[int]:
        """Leading integer of a quota header, or None when it carries no plain number.

        Vendors emit bare integers (``100``) and policy-annotated forms
        (``100, 100;w=60``); anything that does not start with an integer is skipped
        rather than guessed at.
        """
        if raw is None:
            return None
        token = str(raw).strip().split(",")[0].split(";")[0].strip()
        try:
            return int(token)
        except ValueError:
            logger.debug(
                "Non-numeric quota header value %r; skipping value comparison", raw
            )
            return None

    @staticmethod
    def _media_type(raw: Any) -> Optional[str]:
        """Media type of a Content-Type field, without parameters such as ``charset``."""
        if raw is None:
            return None
        return str(raw).split(";")[0].strip().lower() or None

    # ------------------------------------------------------------ status codes

    def compare_status_codes(
        self, endpoint: str, sandbox_status: int, prod_status: int
    ) -> Optional[DriftFinding]:
        """Audits HTTP status-code drift for the same request in both environments.

        Severity follows the status class (RFC 9110 Section 15), not numeric distance: a
        sandbox 200 against a production 4xx/5xx, or a 404 against a 500, is a different
        outcome class and blocks promotion, whereas 400 vs 404 is a same-class difference
        worth reviewing. Numeric distance conflates the two — 404 and 500 differ by 96,
        200 and 299 by 99.
        """
        sandbox_status = self._validate_status(endpoint, "sandbox", sandbox_status)
        prod_status = self._validate_status(endpoint, "production", prod_status)
        if sandbox_status == prod_status:
            return None

        same_class = sandbox_status // 100 == prod_status // 100
        return DriftFinding(
            endpoint=endpoint,
            field_name="http_status_code",
            severity=DriftSeverity.WARNING if same_class else DriftSeverity.CRITICAL,
            description=(
                f"Status code mismatch for '{endpoint}': sandbox returned "
                f"{sandbox_status}, prod returned {prod_status}"
                + ("" if same_class else " (different status class)")
            ),
            sandbox_val=sandbox_status,
            prod_val=prod_status,
            category=(
                DriftCategory.STATUS_CODE_MISMATCH
                if same_class
                else DriftCategory.STATUS_CLASS_MISMATCH
            ),
        )

    @staticmethod
    def _validate_status(endpoint: str, label: str, status: int) -> int:
        if isinstance(status, bool) or not isinstance(status, int):
            raise DriftAuditError(
                f"{label} status for '{endpoint}' must be an int, got "
                f"{type(status).__name__}"
            )
        if not 100 <= status <= 599:
            raise DriftAuditError(
                f"{label} status for '{endpoint}' is {status}, outside the valid HTTP "
                f"status range 100-599"
            )
        return status

    # ------------------------------------------------------- endpoint inventory

    def compare_endpoint_inventory(
        self, sandbox_paths: Iterable[str], prod_paths: Iterable[str]
    ) -> List[DriftFinding]:
        """Compares which endpoints exist at all in each environment.

        Whole endpoint families can be absent from a sandbox — Binance's Spot Test Network
        exposes only ``/api`` endpoints, not ``/sapi`` — so an integration can pass every
        payload-level check and still call a path that does not exist in the environment it
        was rehearsed against, or that no longer exists in production.
        """
        sb = {str(p).strip() for p in sandbox_paths}
        pr = {str(p).strip() for p in prod_paths}
        findings: List[DriftFinding] = []
        for path in sorted(pr - sb):
            findings.append(
                DriftFinding(
                    endpoint=path,
                    field_name=path,
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Endpoint '{path}' exists in production but not in sandbox; it "
                        f"cannot be rehearsed before promotion"
                    ),
                    sandbox_val=None,
                    prod_val=path,
                    category=DriftCategory.ENDPOINT_MISSING_IN_SANDBOX,
                )
            )
        for path in sorted(sb - pr):
            findings.append(
                DriftFinding(
                    endpoint=path,
                    field_name=path,
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Endpoint '{path}' exists in sandbox but not in production; code "
                        f"calling it will fail live"
                    ),
                    sandbox_val=path,
                    prod_val=None,
                    category=DriftCategory.ENDPOINT_MISSING_IN_PRODUCTION,
                )
            )
        return findings

    # --------------------------------------------------------------- full audit

    def audit_endpoint(
        self,
        endpoint: str,
        sandbox_json: Optional[Mapping[str, Any]] = None,
        prod_json: Optional[Mapping[str, Any]] = None,
        sandbox_headers: Optional[Mapping[str, str]] = None,
        prod_headers: Optional[Mapping[str, str]] = None,
        sandbox_status: Optional[int] = None,
        prod_status: Optional[int] = None,
    ) -> EndpointDriftReport:
        """Runs every audit stage and folds the findings into a single report.

        Use this to gate a promotion. ``compare_schemas`` alone returns a report whose
        ``passed`` flag knows nothing about header or status-code drift, which is the
        easiest way to get a green light from this tool for an endpoint that has drifted.

        Stages with no samples supplied are skipped; a body supplied for only one
        environment is itself reported as drift.
        """
        findings: List[DriftFinding] = []

        if sandbox_json is not None and prod_json is not None:
            findings.extend(
                self.compare_schemas(endpoint, sandbox_json, prod_json).findings
            )
        elif sandbox_json is not None or prod_json is not None:
            present = "sandbox" if sandbox_json is not None else "production"
            absent = "production" if sandbox_json is not None else "sandbox"
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name="<body>",
                    severity=DriftSeverity.CRITICAL,
                    description=(
                        f"Response body captured in {present} but not in {absent}; the "
                        f"environments do not agree on whether this endpoint returns one"
                    ),
                    sandbox_val=None if sandbox_json is None else "body",
                    prod_val=None if prod_json is None else "body",
                    category=DriftCategory.BODY_PRESENCE_MISMATCH,
                )
            )

        if sandbox_headers is not None and prod_headers is not None:
            findings.extend(self.compare_headers(endpoint, sandbox_headers, prod_headers))

        if sandbox_status is not None and prod_status is not None:
            status_finding = self.compare_status_codes(
                endpoint, sandbox_status, prod_status
            )
            if status_finding is not None:
                findings.append(status_finding)

        report = EndpointDriftReport.from_findings(endpoint, findings)
        logger.info(
            "Endpoint audit complete for '%s': passed=%s, %d critical, %d warning.",
            endpoint, report.passed, report.critical_count, report.warning_count,
        )
        return report
