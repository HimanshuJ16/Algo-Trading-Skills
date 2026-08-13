"""algorithmic-trading-firm-licensing-thresholds.

Evaluates a proprietary firm's trading activity against widely-reported
regulatory thresholds (SEC Rule 15b9-1, MiFID II HFT designation, and SEBI
retail algo rules) to determine whether mandatory registration or licensing
is required for the firm's operating jurisdiction.

This module is an educational reference for compliance auditing. It encodes
commonly-cited trigger thresholds in plain text and does not constitute
legal advice. Always pair the output with legal review by qualified
regulatory counsel and a live check against current rule text.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import math
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Supported operating jurisdictions. Coverage is intentionally narrow and
# fail-closed: unknown jurisdictions require manual legal review.
_SUPPORTED_JURISDICTIONS: Tuple[str, ...] = ("US", "EU", "IN")

# Internal schema version. Bump when the report/contract changes so log
# aggregators and downstream callers can distinguish report shapes.
_REPORT_SCHEMA_VERSION = "1.1.0"


@dataclasses.dataclass(frozen=True)
class FirmTradingActivity:
    """Snapshot of a firm's trading activity used for licensing evaluation.

    All metrics must be finite, non-negative, and measured over the firm's
    most recent rolling evaluation window. :class:`LicensingThresholdEvaluator`
    does not compute these for you; it only consumes them.
    """

    jurisdiction: str                       # 'US', 'EU', or 'IN'
    is_exchange_member: bool                # National exchange membership flag
    has_customers: bool                     # Trades on behalf of clients
    off_exchange_volume_usd: float          # Dark-pool / ATS volume over window
    peak_orders_per_second: int             # Peak OPS over rolling 1-second window

    def __post_init__(self) -> None:
        normalized_jurisdiction = (self.jurisdiction or "").strip().upper()
        if not normalized_jurisdiction:
            raise ValueError("jurisdiction is required")
        if normalized_jurisdiction not in _SUPPORTED_JURISDICTIONS:
            raise ValueError(
                "jurisdiction %r is not supported; expected one of %s"
                % (self.jurisdiction, _SUPPORTED_JURISDICTIONS)
            )
        # Frozen dataclass: write-through assignment via object.__setattr__.
        object.__setattr__(self, "jurisdiction", normalized_jurisdiction)

        if not isinstance(self.is_exchange_member, bool):
            raise ValueError("is_exchange_member must be a boolean")
        if not isinstance(self.has_customers, bool):
            raise ValueError("has_customers must be a boolean")

        if not isinstance(self.off_exchange_volume_usd, (int, float)):
            raise ValueError("off_exchange_volume_usd must be numeric")
        off_ex = float(self.off_exchange_volume_usd)
        if math.isnan(off_ex) or math.isinf(off_ex):
            raise ValueError("off_exchange_volume_usd must be finite")
        if off_ex < 0.0:
            raise ValueError("off_exchange_volume_usd must be non-negative")
        object.__setattr__(self, "off_exchange_volume_usd", off_ex)

        if isinstance(self.peak_orders_per_second, bool) or not isinstance(
            self.peak_orders_per_second, int
        ):
            raise ValueError("peak_orders_per_second must be a non-negative integer")
        if self.peak_orders_per_second < 0:
            raise ValueError("peak_orders_per_second must be non-negative")


@dataclasses.dataclass(frozen=True)
class LicensingComplianceReport:
    """Result of an evaluation.

    ``rule_id`` is a stable string identifying the underlying rule chain.
    ``evaluated_at`` is the UTC evaluation timestamp the evaluator stamped.
    ``violations`` preserves every trigger that fired, in deterministic
    evaluation order. ``reason`` is the highest-severity human-readable
    explanation; ``violations`` is the auditable list.
    """

    requires_registration: bool
    jurisdiction: str
    reason: Optional[str] = None
    rule_id: Optional[str] = None
    evaluated_at: str = ""
    schema_version: str = _REPORT_SCHEMA_VERSION
    violations: Tuple[str, ...] = ()


class LicensingThresholdEvaluator:
    """Evaluates trading activity against jurisdictional regulatory thresholds.

    Thresholds default to commonly-cited policy benchmarks and can be
    overridden via the dataclass-style constructor for tests or for firms
    operating under stricter internal policy. They are **not** a substitute
    for current rule text.
    """

    #: SEBI (India) retail automated trading OPS ceiling. Exceeding this
    #: historically triggered SEBI algo registration. This is a conservative
    #: benchmark, not a verbatim citation of current rule text.
    SEBI_RETAIL_OPS_LIMIT: int = 10

    #: MiFID II (EU) HFT designation threshold. The actual ESMA guidance is
    #: two orders/second average over a day, but reasonable-operation tier
    #: interpretation commonly cites 50 msg/s as a practical HFT signal.
    MIFID_II_HFT_OPS_LIMIT: int = 50

    #: US Rule 15b9-1 (2023 amendments) practical dark-pool floor. SEC removed
    #: the de minimis carve-out for proprietary off-exchange activity, so any
    #: material off-exchange volume invalidates the exemption. We use a small
    #: floor so that purely-test zero values still classify as compliant.
    SEC_OFF_EXCHANGE_FLOOR_USD: float = 1.0

    def __init__(
        self,
        *,
        sebi_retail_ops_limit: Optional[int] = None,
        mifid_ii_hft_ops_limit: Optional[int] = None,
        sec_off_exchange_floor_usd: Optional[float] = None,
    ) -> None:
        if sebi_retail_ops_limit is not None and sebi_retail_ops_limit < 0:
            raise ValueError("sebi_retail_ops_limit must be non-negative")
        if mifid_ii_hft_ops_limit is not None and mifid_ii_hft_ops_limit < 0:
            raise ValueError("mifid_ii_hft_ops_limit must be non-negative")
        if (
            sec_off_exchange_floor_usd is not None
            and (
                math.isnan(sec_off_exchange_floor_usd)
                or math.isinf(sec_off_exchange_floor_usd)
                or sec_off_exchange_floor_usd < 0
            )
        ):
            raise ValueError("sec_off_exchange_floor_usd must be a finite, non-negative number")

        self._sebi_limit = sebi_retail_ops_limit or self.SEBI_RETAIL_OPS_LIMIT
        self._mifid_limit = mifid_ii_hft_ops_limit or self.MIFID_II_HFT_OPS_LIMIT
        self._sec_off_ex_floor = (
            sec_off_exchange_floor_usd
            if sec_off_exchange_floor_usd is not None
            else self.SEC_OFF_EXCHANGE_FLOOR_USD
        )

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _utc_now_iso() -> str:
        return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _dedupe(violations: Iterable[str]) -> Tuple[str, ...]:
        seen: List[str] = []
        for v in violations:
            if v and v not in seen:
                seen.append(v)
        return tuple(seen)

    # --------------------------------------------------------------- entrypoint

    def evaluate(self, activity: FirmTradingActivity) -> LicensingComplianceReport:
        """Evaluate a single activity snapshot.

        All triggered violations are returned in the report, in deterministic
        evaluation order. ``reason`` is the highest-severity explanation.
        """
        jurisdiction = activity.jurisdiction
        violations: List[str] = []
        rule_id: Optional[str] = None

        # Customer-funds check is global: any firm handling customer funds
        # universally triggers broker/advisor licensing independent of OPS.
        customer_reasons = self._customer_fund_violations(activity)
        if customer_reasons:
            violations.extend(customer_reasons)
            rule_id = "GLOBAL/CUSTOMER-FUNDS"

        jurisdiction_violations, jurisdiction_rule = self._dispatch(activity)
        violations.extend(jurisdiction_violations)
        if rule_id is None:
            rule_id = jurisdiction_rule

        violations = list(self._dedupe(violations))
        requires = bool(violations)

        if violations:
            reason = "; ".join(violations)
        else:
            reason = (
                "Activity within configured thresholds for %s. No mandatory "
                "registration triggered by automation; legal confirmation "
                "still required before relying on this result." % jurisdiction
            )

        if jurisdiction not in _SUPPORTED_JURISDICTIONS:
            # Should be impossible after FirmTradingActivity validation.
            logger.warning(
                "LicensingThresholdEvaluator.evaluate received unsupported jurisdiction %r",
                jurisdiction,
            )

        return LicensingComplianceReport(
            requires_registration=requires,
            jurisdiction=jurisdiction,
            reason=reason,
            rule_id=rule_id,
            evaluated_at=self._utc_now_iso(),
            schema_version=_REPORT_SCHEMA_VERSION,
            violations=tuple(violations),
        )

    # --------------------------------------------------------------- dispatch

    def _dispatch(
        self, activity: FirmTradingActivity
    ) -> Tuple[List[str], Optional[str]]:
        if activity.jurisdiction == "US":
            return self._evaluate_us(activity), "US/15b9-1+HFT"
        if activity.jurisdiction == "EU":
            return self._evaluate_eu(activity), "EU/MiFID-II-HFT"
        if activity.jurisdiction == "IN":
            return self._evaluate_in(activity), "IN/SEBI-ALGO"
        # Fail-closed: unsupported jurisdiction must go to manual review.
        return (
            [
                "Unrecognized jurisdiction %r requires manual legal review."
                % activity.jurisdiction
            ],
            None,
        )

    # ----------------------------------------------------------------- checks

    @staticmethod
    def _customer_fund_violations(activity: FirmTradingActivity) -> List[str]:
        if activity.has_customers:
            return [
                "Firm trades on behalf of customers. Full broker-dealer or "
                "investment-firm license required."
            ]
        return []

    def _evaluate_us(self, activity: FirmTradingActivity) -> List[str]:
        violations: List[str] = []
        if activity.off_exchange_volume_usd >= self._sec_off_ex_floor:
            violations.append(
                "Off-exchange volume %.2f USD exceeds Rule 15b9-1 exemption "
                "practical floor %.2f USD. FINRA broker-dealer registration "
                "required." % (activity.off_exchange_volume_usd, self._sec_off_ex_floor)
            )
        if not activity.is_exchange_member and activity.off_exchange_volume_usd == 0.0:
            violations.append(
                "Firm is not an exchange member and routes no off-exchange "
                "flow; broker-dealer registration is required before any "
                "trading activity under SEC oversight."
            )
        if activity.peak_orders_per_second >= self._mifid_limit:
            violations.append(
                "Peak OPS %d meets the practical HFT signal "
                "(%d). US HFT-style activity requires registration review."
                % (activity.peak_orders_per_second, self._mifid_limit)
            )
        return violations

    def _evaluate_eu(self, activity: FirmTradingActivity) -> List[str]:
        if activity.peak_orders_per_second >= self._mifid_limit:
            return [
                "Peak OPS %d meets the MiFID II HFT designation threshold "
                "(%d). Investment Firm license required."
                % (activity.peak_orders_per_second, self._mifid_limit)
            ]
        return []

    def _evaluate_in(self, activity: FirmTradingActivity) -> List[str]:
        if activity.peak_orders_per_second >= self._sebi_limit:
            return [
                "Peak OPS %d exceeds SEBI retail automated-trading limit "
                "(%d). Algo Registration required."
                % (activity.peak_orders_per_second, self._sebi_limit)
            ]
        return []
