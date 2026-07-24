"""
sandbox-vs-production-endpoint-drift: Production-grade API schema drift detector,
response payload comparator, header audit tool, and environment parity reporter.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class DriftSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class DriftFinding:
    endpoint: str
    field_name: str
    severity: DriftSeverity
    description: str
    sandbox_val: Any
    prod_val: Any


@dataclass
class EndpointDriftReport:
    endpoint: str
    passed: bool
    critical_count: int
    warning_count: int
    findings: List[DriftFinding] = field(default_factory=list)


class EndpointDriftDetector:
    """
    Audits JSON response schemas, headers, and status code behaviors between
    sandbox and production broker endpoints to detect integration risk.
    """

    def compare_schemas(
        self, endpoint: str, sandbox_json: Dict[str, Any], prod_json: Dict[str, Any]
    ) -> EndpointDriftReport:
        """Compares JSON field presence and data types between sandbox and prod."""
        findings: List[DriftFinding] = []

        sandbox_keys = set(sandbox_json.keys())
        prod_keys = set(prod_json.keys())

        # 1. Missing Fields in Sandbox (Present in Prod)
        missing_in_sandbox = prod_keys - sandbox_keys
        for k in missing_in_sandbox:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=k,
                    severity=DriftSeverity.CRITICAL,
                    description=f"Field '{k}' present in production payload but missing in sandbox response",
                    sandbox_val=None,
                    prod_val=type(prod_json[k]).__name__,
                )
            )

        # 2. Extra Fields in Sandbox (Absent in Prod)
        extra_in_sandbox = sandbox_keys - prod_keys
        for k in extra_in_sandbox:
            findings.append(
                DriftFinding(
                    endpoint=endpoint,
                    field_name=k,
                    severity=DriftSeverity.WARNING,
                    description=f"Field '{k}' present in sandbox payload but absent in production response",
                    sandbox_val=type(sandbox_json[k]).__name__,
                    prod_val=None,
                )
            )

        # 3. Data Type Mismatches
        common_keys = sandbox_keys & prod_keys
        for k in common_keys:
            s_val = sandbox_json[k]
            p_val = prod_json[k]

            if s_val is not None and p_val is not None:
                s_type = type(s_val)
                p_type = type(p_val)

                # Int/Float compatibility check vs String/Numeric mismatch
                if s_type != p_type:
                    if s_type in (int, float) and p_type in (int, float):
                        # Numeric float vs int (Warning)
                        findings.append(
                            DriftFinding(
                                endpoint=endpoint,
                                field_name=k,
                                severity=DriftSeverity.WARNING,
                                description=f"Numeric type mismatch for '{k}': sandbox is {s_type.__name__}, prod is {p_type.__name__}",
                                sandbox_val=s_val,
                                prod_val=p_val,
                            )
                        )
                    else:
                        # String vs Numeric / Dict vs List (Critical)
                        findings.append(
                            DriftFinding(
                                endpoint=endpoint,
                                field_name=k,
                                severity=DriftSeverity.CRITICAL,
                                description=f"Type mismatch for '{k}': sandbox is {s_type.__name__}, prod is {p_type.__name__}",
                                sandbox_val=s_val,
                                prod_val=p_val,
                            )
                        )

        criticals = sum(1 for f in findings if f.severity == DriftSeverity.CRITICAL)
        warnings = sum(1 for f in findings if f.severity == DriftSeverity.WARNING)
        passed = criticals == 0

        logger.info(f"Schema comparison complete for '{endpoint}': passed={passed}, {criticals} critical, {warnings} warning.")
        return EndpointDriftReport(
            endpoint=endpoint,
            passed=passed,
            critical_count=criticals,
            warning_count=warnings,
            findings=findings,
        )

    def compare_headers(
        self, endpoint: str, sandbox_headers: Dict[str, str], prod_headers: Dict[str, str]
    ) -> List[DriftFinding]:
        """Audits rate-limit and header presence between sandbox and prod."""
        findings: List[DriftFinding] = []
        sb_h = {k.lower(): v for k, v in sandbox_headers.items()}
        pr_h = {k.lower(): v for k, v in prod_headers.items()}

        rate_limit_keys = ["x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"]
        for rk in rate_limit_keys:
            if rk in pr_h and rk not in sb_h:
                findings.append(
                    DriftFinding(
                        endpoint=endpoint,
                        field_name=rk,
                        severity=DriftSeverity.WARNING,
                        description=f"Rate limit header '{rk}' present in production but missing in sandbox response",
                        sandbox_val=None,
                        prod_val=pr_h[rk],
                    )
                )
        return findings

    def compare_status_codes(
        self, endpoint: str, sandbox_status: int, prod_status: int
    ) -> Optional[DriftFinding]:
        """Audits HTTP status code discrepancies for identical error scenarios."""
        if sandbox_status != prod_status:
            sev = DriftSeverity.CRITICAL if abs(sandbox_status - prod_status) >= 100 else DriftSeverity.WARNING
            return DriftFinding(
                endpoint=endpoint,
                field_name="http_status_code",
                severity=sev,
                description=f"Status code mismatch for '{endpoint}': sandbox returned {sandbox_status}, prod returned {prod_status}",
                sandbox_val=sandbox_status,
                prod_val=prod_status,
            )
        return None
