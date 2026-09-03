"""algorithmic-trading-firm-licensing-thresholds.

Screens a firm's trading activity against the *quantitative* limbs of three
registration regimes and reports, for each, whether the firm has crossed them:

* **US** - 17 CFR 240.15b9-1, the exemption from the Exchange Act section
  15(b)(8) requirement that a registered broker-dealer join a registered
  national securities association (in practice FINRA). Rule text as amended,
  88 FR 61893 (Sept. 7, 2023).
* **EU** - the high-frequency algorithmic trading technique definition,
  MiFID II Article 4(1)(40) with the quantitative test in Article 19 of
  Commission Delegated Regulation (EU) 2017/565. Meeting it removes the
  own-account dealing exemption in MiFID II Article 2(1)(d)(iii).
* **IN** - the Threshold Orders Per Second (TOPS) above which a retail
  investor's API-routed algorithm must be registered with each exchange
  through the broker. SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013
  (Feb. 4, 2025) defers the number to the exchanges; NSE/INVG/67858 sets it.

Two properties matter more than breadth here, because a compliance screen that
quietly answers "compliant" is worse than one that answers nothing:

1. **Defaults are the regulators' own published numbers**, not invented
   benchmarks. No threshold in this module is a round number chosen because it
   looked like high-frequency trading.
2. **"Cannot determine" is a distinct outcome from "compliant."** Where the
   caller has not supplied the input a rule is actually measured on, the report
   says so through ``manual_review_items`` instead of returning a clean bill of
   health. See :class:`LicensingComplianceReport`.

This module is a screening aid, not legal advice, and it models only the
numeric limbs above. Registration turns on many qualitative facts this module
never sees. Pair every report with qualified regulatory counsel.
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

# Internal schema version. Bump when the report contract changes so log
# aggregators and downstream callers can distinguish report shapes.
_REPORT_SCHEMA_VERSION = "2.0.0"

# Half a US cent. Netting exempt volume out of total volume is a floating-point
# subtraction, so an exact cancellation can leave a residue on the order of
# 1e-17 USD; at the default screening floor of 0.00 that residue would be
# reported as a condition (c) breach of "0.00 USD". A USD notional below half a
# cent cannot be a real execution, so it is collapsed to zero. This is a limit
# of the representation, not a de minimis allowance - amended Rule 15b9-1
# retains none.
_USD_ROUNDING_TOLERANCE = 0.005


def _require_finite_non_negative(value: object, field: str) -> float:
    """Coerce ``value`` to a finite, non-negative float or raise ``ValueError``.

    ``bool`` is rejected explicitly: it satisfies ``isinstance(x, int)`` and
    would otherwise be read silently as a 0.0/1.0 monetary or rate quantity.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a non-negative real number" % field)
    coerced = float(value)
    if math.isnan(coerced) or math.isinf(coerced):
        raise ValueError("%s must be finite" % field)
    if coerced < 0.0:
        raise ValueError("%s must be non-negative" % field)
    return coerced


@dataclasses.dataclass(frozen=True)
class FirmTradingActivity:
    """Snapshot of a firm's trading activity used for licensing evaluation.

    All metrics must be finite, non-negative, and measured over the firm's
    most recent documented evaluation window.
    :class:`LicensingThresholdEvaluator` does not compute these for you; it
    only consumes them.

    Attributes:
        jurisdiction: ``'US'``, ``'EU'`` or ``'IN'``. Case-insensitive.
        is_exchange_member: Member of a national securities exchange. This is
            condition (a) of 17 CFR 240.15b9-1; without it the exemption from
            FINRA membership is unavailable whatever the firm's volumes.
        has_customers: Carries customer accounts or trades for clients.
            Condition (b) of Rule 15b9-1, and in the EU the fact that removes
            the own-account dealing exemption in MiFID II Article 2(1)(d).
        off_exchange_volume_usd: Total volume executed otherwise than on a
            national securities exchange of which the firm is a member, over
            the evaluation window.
        exempt_off_exchange_volume_usd: The portion of
            ``off_exchange_volume_usd`` falling solely within the two
            exceptions Rule 15b9-1(c) retains - orders routed by an exchange
            of which the firm is a member to comply with Rule 611 or the
            Options Order Protection and Locked/Crossed Market Plan
            (paragraph (c)(1)), and the stock leg of a stock-option order
            (paragraph (c)(2)). Must not exceed ``off_exchange_volume_usd``.
            Defaults to 0.0, the conservative reading.
        peak_orders_per_second: Highest order count in any single calendar
            clock second, per exchange - the basis NSE/INVG/67858 specifies
            for TOPS. It is *not* an input to the EU test, which counts
            messages rather than orders and averages rather than peaks.
        avg_messages_per_second_per_instrument: Average messages per second
            for the busiest single liquid instrument on a trading venue,
            computed per Article 19(1)(a) and 19(2) of Delegated Regulation
            (EU) 2017/565. ``None`` means "not measured" - which is not the
            same as zero, and the EU branch will say so.
        avg_messages_per_second_all_instruments: Average messages per second
            summed across all liquid instruments on a trading venue, per
            Article 19(1)(b). ``None`` means "not measured".
        is_retail_api_algo_flow: True when the flow is a retail investor's
            algorithm routed through a broker's API - the only flow the SEBI
            TOPS registration threshold governs. False for a trading member's
            or proprietary firm's own flow, which answers to the exchange
            algo-approval regime this module does not model.
    """

    jurisdiction: str
    is_exchange_member: bool
    has_customers: bool
    off_exchange_volume_usd: float
    peak_orders_per_second: int
    exempt_off_exchange_volume_usd: float = 0.0
    avg_messages_per_second_per_instrument: Optional[float] = None
    avg_messages_per_second_all_instruments: Optional[float] = None
    is_retail_api_algo_flow: bool = False

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

        for flag in ("is_exchange_member", "has_customers", "is_retail_api_algo_flow"):
            if not isinstance(getattr(self, flag), bool):
                raise ValueError("%s must be a boolean" % flag)

        off_ex = _require_finite_non_negative(
            self.off_exchange_volume_usd, "off_exchange_volume_usd"
        )
        object.__setattr__(self, "off_exchange_volume_usd", off_ex)

        exempt = _require_finite_non_negative(
            self.exempt_off_exchange_volume_usd, "exempt_off_exchange_volume_usd"
        )
        if exempt > off_ex:
            raise ValueError(
                "exempt_off_exchange_volume_usd (%.2f) must not exceed "
                "off_exchange_volume_usd (%.2f)" % (exempt, off_ex)
            )
        object.__setattr__(self, "exempt_off_exchange_volume_usd", exempt)

        if isinstance(self.peak_orders_per_second, bool) or not isinstance(
            self.peak_orders_per_second, int
        ):
            raise ValueError("peak_orders_per_second must be a non-negative integer")
        if self.peak_orders_per_second < 0:
            raise ValueError("peak_orders_per_second must be non-negative")

        for rate_field in (
            "avg_messages_per_second_per_instrument",
            "avg_messages_per_second_all_instruments",
        ):
            raw = getattr(self, rate_field)
            if raw is None:
                continue
            object.__setattr__(
                self, rate_field, _require_finite_non_negative(raw, rate_field)
            )

    @property
    def non_exempt_off_exchange_volume_usd(self) -> float:
        """Off-exchange volume not covered by a Rule 15b9-1(c) exception.

        A remainder below half a cent is returned as exactly 0.0; see
        ``_USD_ROUNDING_TOLERANCE``.
        """
        remainder = self.off_exchange_volume_usd - self.exempt_off_exchange_volume_usd
        return remainder if remainder >= _USD_ROUNDING_TOLERANCE else 0.0


@dataclasses.dataclass(frozen=True)
class LicensingComplianceReport:
    """Result of an evaluation.

    Three outcomes, not two. ``requires_registration`` means a modelled
    threshold was crossed. ``manual_review_required`` means the evaluator could
    not reach a conclusion - a rule applies whose measurement input the caller
    did not supply, or a regime outside this module's scope governs. Only a
    report where *both* are ``False`` represents "no modelled threshold
    crossed", and even that is not a finding of compliance overall, because
    registration also turns on qualitative facts this module never sees.

    Attributes:
        requires_registration: True when ``violations`` is non-empty.
        manual_review_required: True when ``manual_review_items`` is non-empty.
        jurisdiction: The normalised jurisdiction evaluated.
        reason: Human-readable summary of every violation and review item.
        rule_id: Stable identifier of the dominant rule chain, or ``None`` when
            no modelled rule chain applies.
        evaluated_at: UTC evaluation timestamp, second resolution.
        schema_version: Report contract version.
        violations: Every crossed threshold, in deterministic evaluation order
            (customer accounts first, then the jurisdiction's own rules).
        manual_review_items: Every question the evaluator could not answer, in
            the same deterministic order.
    """

    requires_registration: bool
    jurisdiction: str
    reason: Optional[str] = None
    rule_id: Optional[str] = None
    evaluated_at: str = ""
    schema_version: str = _REPORT_SCHEMA_VERSION
    violations: Tuple[str, ...] = ()
    manual_review_required: bool = False
    manual_review_items: Tuple[str, ...] = ()

    @property
    def is_clear(self) -> bool:
        """True only when nothing fired and nothing was left undetermined."""
        return not self.requires_registration and not self.manual_review_required


class LicensingThresholdEvaluator:
    """Evaluates trading activity against jurisdictional registration thresholds.

    Every default below is a number the regulator or the exchange actually
    publishes, cited on the constant. Overrides exist so a firm can screen at a
    *stricter* internal threshold; loosening one past the published figure puts
    the firm outside the rule it is screening for.
    """

    #: Exchange Threshold Orders Per Second, per exchange, above which a retail
    #: investor's API-routed algorithm must be registered with each exchange
    #: through the broker. NSE/INVG/67858 sets it at "not exceeding 10 orders
    #: per second per exchange", measured on the calendar clock second, so the
    #: breach condition is strictly greater than this value. The SEBI circular
    #: itself specifies no number - footnote 2 defers it to the Brokers'
    #: Industry Standards Forum under the aegis of the exchanges.
    SEBI_TOPS_ORDERS_PER_SECOND: int = 10

    #: MiFID II high message intraday rate, limb (a): at least 2 messages per
    #: second for any single liquid financial instrument traded on a trading
    #: venue. Article 19(1)(a), Commission Delegated Regulation (EU) 2017/565.
    MIFID_II_MSGS_PER_SEC_SINGLE_INSTRUMENT: float = 2.0

    #: MiFID II high message intraday rate, limb (b): at least 4 messages per
    #: second across all liquid financial instruments traded on a trading
    #: venue. Article 19(1)(b), Delegated Regulation (EU) 2017/565.
    MIFID_II_MSGS_PER_SEC_ALL_INSTRUMENTS: float = 4.0

    #: Firm-set materiality screen on non-exempt off-exchange volume, in USD.
    #: The default of 0.0 means "any non-exempt off-exchange volume at all",
    #: which is what amended Rule 15b9-1(c) requires: the amendments removed
    #: the de minimis allowance, retaining only the (c)(1) exchange-routed and
    #: (c)(2) stock-leg exceptions. A higher value is a firm's own triage
    #: threshold and reflects no regulatory de minimis.
    SEC_OFF_EXCHANGE_FLOOR_USD: float = 0.0

    def __init__(
        self,
        *,
        sebi_tops_orders_per_second: Optional[int] = None,
        mifid_ii_msgs_per_sec_single_instrument: Optional[float] = None,
        mifid_ii_msgs_per_sec_all_instruments: Optional[float] = None,
        sec_off_exchange_floor_usd: Optional[float] = None,
    ) -> None:
        if sebi_tops_orders_per_second is not None and (
            isinstance(sebi_tops_orders_per_second, bool)
            or not isinstance(sebi_tops_orders_per_second, int)
            or sebi_tops_orders_per_second < 0
        ):
            raise ValueError(
                "sebi_tops_orders_per_second must be a non-negative integer"
            )
        for name, value in (
            (
                "mifid_ii_msgs_per_sec_single_instrument",
                mifid_ii_msgs_per_sec_single_instrument,
            ),
            (
                "mifid_ii_msgs_per_sec_all_instruments",
                mifid_ii_msgs_per_sec_all_instruments,
            ),
            ("sec_off_exchange_floor_usd", sec_off_exchange_floor_usd),
        ):
            if value is not None:
                _require_finite_non_negative(value, name)

        # ``is not None`` throughout: 0 is a meaningful, stricter override and
        # must not be swallowed by an ``or`` fallback to the class default.
        self._sebi_tops = (
            sebi_tops_orders_per_second
            if sebi_tops_orders_per_second is not None
            else self.SEBI_TOPS_ORDERS_PER_SECOND
        )
        self._mifid_single = (
            float(mifid_ii_msgs_per_sec_single_instrument)
            if mifid_ii_msgs_per_sec_single_instrument is not None
            else self.MIFID_II_MSGS_PER_SEC_SINGLE_INSTRUMENT
        )
        self._mifid_all = (
            float(mifid_ii_msgs_per_sec_all_instruments)
            if mifid_ii_msgs_per_sec_all_instruments is not None
            else self.MIFID_II_MSGS_PER_SEC_ALL_INSTRUMENTS
        )
        self._sec_off_ex_floor = (
            float(sec_off_exchange_floor_usd)
            if sec_off_exchange_floor_usd is not None
            else self.SEC_OFF_EXCHANGE_FLOOR_USD
        )

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _utc_now_iso() -> str:
        return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _dedupe(items: Iterable[str]) -> Tuple[str, ...]:
        seen: List[str] = []
        for item in items:
            if item and item not in seen:
                seen.append(item)
        return tuple(seen)

    # --------------------------------------------------------------- entrypoint

    def evaluate(self, activity: FirmTradingActivity) -> LicensingComplianceReport:
        """Evaluate a single activity snapshot.

        Every check runs; nothing short-circuits. Violations and review items
        are returned in deterministic evaluation order - the global customer
        account check first, then the jurisdiction's own rules.
        """
        jurisdiction = activity.jurisdiction
        violations: List[str] = []
        review: List[str] = []
        rule_id: Optional[str] = None

        # Carrying customer accounts defeats Rule 15b9-1(b) in the US and the
        # own-account dealing exemption in MiFID II Article 2(1)(d), so it is
        # checked ahead of, and independently of, the jurisdiction branch.
        customer_violations = self._customer_account_violations(activity)
        if customer_violations:
            violations.extend(customer_violations)
            rule_id = "GLOBAL/CUSTOMER-ACCOUNTS"

        branch_violations, branch_review, branch_rule = self._dispatch(activity)
        violations.extend(branch_violations)
        review.extend(branch_review)
        if rule_id is None:
            rule_id = branch_rule

        violation_tuple = self._dedupe(violations)
        review_tuple = self._dedupe(review)

        if violation_tuple or review_tuple:
            reason = "; ".join(violation_tuple + review_tuple)
        else:
            reason = (
                "No modelled threshold crossed for %s. This is not a finding of "
                "compliance: registration also turns on qualitative facts this "
                "evaluator does not model. Legal confirmation still required."
                % jurisdiction
            )

        return LicensingComplianceReport(
            requires_registration=bool(violation_tuple),
            jurisdiction=jurisdiction,
            reason=reason,
            rule_id=rule_id,
            evaluated_at=self._utc_now_iso(),
            schema_version=_REPORT_SCHEMA_VERSION,
            violations=violation_tuple,
            manual_review_required=bool(review_tuple),
            manual_review_items=review_tuple,
        )

    # --------------------------------------------------------------- dispatch

    def _dispatch(
        self, activity: FirmTradingActivity
    ) -> Tuple[List[str], List[str], Optional[str]]:
        if activity.jurisdiction == "US":
            return self._evaluate_us(activity) + ("US/15b9-1",)
        if activity.jurisdiction == "EU":
            return self._evaluate_eu(activity) + ("EU/MiFID-II-HFT",)
        if activity.jurisdiction == "IN":
            return self._evaluate_in(activity) + ("IN/SEBI-TOPS",)
        # Unreachable through the public constructor, which rejects unknown
        # jurisdictions. Retained so an activity built by other means cannot
        # come back clean.
        logger.warning(
            "LicensingThresholdEvaluator.evaluate received unsupported jurisdiction %r",
            activity.jurisdiction,
        )
        return (
            [],
            [
                "Unrecognized jurisdiction %r requires manual legal review."
                % activity.jurisdiction
            ],
            None,
        )

    # ----------------------------------------------------------------- checks

    @staticmethod
    def _customer_account_violations(activity: FirmTradingActivity) -> List[str]:
        if activity.has_customers:
            return [
                "Firm carries customer accounts or trades on behalf of clients. "
                "The own-account exemptions (17 CFR 240.15b9-1(b); MiFID II "
                "Article 2(1)(d)) are unavailable; full broker-dealer or "
                "investment-firm authorisation applies."
            ]
        return []

    def _evaluate_us(
        self, activity: FirmTradingActivity
    ) -> Tuple[List[str], List[str]]:
        """17 CFR 240.15b9-1 as amended (88 FR 61893, Sept. 7, 2023).

        The rule exempts a broker-dealer from the Exchange Act section 15(b)(8)
        requirement to join a registered national securities association
        (FINRA). It is not an exemption from broker-dealer registration under
        section 15(a), and nothing in it turns on message rates - this branch
        therefore applies no order-rate test.
        """
        violations: List[str] = []
        review: List[str] = []

        if not activity.is_exchange_member:
            violations.append(
                "Firm is not a member of a national securities exchange, so "
                "condition (a) of 17 CFR 240.15b9-1 is not met and the "
                "exemption is unavailable regardless of volumes. A registered "
                "broker-dealer in this position must join FINRA under Exchange "
                "Act section 15(b)(8)."
            )

        non_exempt = activity.non_exempt_off_exchange_volume_usd
        if non_exempt > self._sec_off_ex_floor:
            violations.append(
                "Non-exempt off-exchange volume %.2f USD is above the "
                "configured screening floor %.2f USD, so condition (c) of "
                "17 CFR 240.15b9-1 is not met. The amended rule retains no de "
                "minimis allowance - only exchange-routed orders under Rule 611 "
                "or the Options Order Protection and Locked/Crossed Market Plan "
                "(paragraph (c)(1)) and the stock leg of a stock-option order "
                "(paragraph (c)(2)). FINRA membership required under Exchange "
                "Act section 15(b)(8)."
                % (non_exempt, self._sec_off_ex_floor)
            )

        if activity.exempt_off_exchange_volume_usd > 0.0:
            review.append(
                "%.2f USD of off-exchange volume is claimed under a Rule "
                "15b9-1(c) exception and was netted out before screening. "
                "Confirm which limb is relied on and that it is evidenced: "
                "(c)(1) requires the transactions to result *solely* from "
                "orders an exchange of which the firm is a member routed to "
                "comply with Rule 611 or the Options Order Protection and "
                "Locked/Crossed Market Plan; (c)(2) additionally requires "
                "written policies and procedures reasonably designed to ensure "
                "and demonstrate the transactions were solely for the stock leg "
                "of a stock-option order, preserved three years consistent with "
                "Rule 17a-4." % activity.exempt_off_exchange_volume_usd
            )

        return violations, review

    def _evaluate_eu(
        self, activity: FirmTradingActivity
    ) -> Tuple[List[str], List[str]]:
        """MiFID II Article 4(1)(40) with Article 19 of Del. Reg. (EU) 2017/565.

        Article 19 is measured on *average* message rates over the assessment
        period, and on messages rather than orders. ``peak_orders_per_second``
        bounds neither: it is a peak rather than an average, and it counts
        orders while Article 19 counts messages, which include modifications
        and cancellations. There is therefore no order-rate shortcut to a clean
        EU report - without both averages the branch returns an undetermined
        result rather than compliance.
        """
        violations: List[str] = []
        review: List[str] = []

        per_instrument = activity.avg_messages_per_second_per_instrument
        all_instruments = activity.avg_messages_per_second_all_instruments

        if per_instrument is not None and per_instrument >= self._mifid_single:
            violations.append(
                "Average %.2f messages/second on a single liquid instrument "
                "meets the Article 19(1)(a) high message intraday rate (%.2f). "
                "The firm applies a high-frequency algorithmic trading "
                "technique under MiFID II Article 4(1)(40); the own-account "
                "dealing exemption in Article 2(1)(d)(iii) is unavailable and "
                "investment firm authorisation is required."
                % (per_instrument, self._mifid_single)
            )
        if all_instruments is not None and all_instruments >= self._mifid_all:
            violations.append(
                "Average %.2f messages/second across all liquid instruments on "
                "a trading venue meets the Article 19(1)(b) high message "
                "intraday rate (%.2f). The firm applies a high-frequency "
                "algorithmic trading technique under MiFID II Article 4(1)(40); "
                "the own-account dealing exemption in Article 2(1)(d)(iii) is "
                "unavailable and investment firm authorisation is required."
                % (all_instruments, self._mifid_all)
            )

        if violations:
            return violations, review

        if per_instrument is None or all_instruments is None:
            review.append(
                "Article 19 average message rates were not supplied, so the "
                "high-frequency designation cannot be decided. An order rate "
                "cannot stand in for them: Article 19 counts *messages*, which "
                "include order modifications and cancellations, so a firm "
                "submitting one order per second and cancel-replacing it "
                "several times is already above the 2 messages/second limb. "
                "Compute the Article 19(1)(a) and 19(1)(b) averages over "
                "liquid instruments per Article 19(2) and re-run; ESMA expects "
                "firms to self-assess at least monthly."
            )

        return violations, review

    def _evaluate_in(
        self, activity: FirmTradingActivity
    ) -> Tuple[List[str], List[str]]:
        """SEBI/NSE Threshold Orders Per Second for retail API algo flow.

        TOPS governs a retail investor's algorithm routed through a broker's
        API. It is an obligation to register the *algorithm* with each exchange
        through the broker - not entity licensing, and not applicable to a
        trading member's own flow.
        """
        violations: List[str] = []
        review: List[str] = []

        if not activity.is_retail_api_algo_flow:
            review.append(
                "SEBI's Threshold Orders Per Second applies to a retail "
                "investor's API-routed algorithm, not to a trading member's or "
                "proprietary firm's own flow, whose exchange algo-approval "
                "obligations this evaluator does not model. Route to counsel; "
                "do not record as compliant."
            )
            return violations, review

        if activity.peak_orders_per_second > self._sebi_tops:
            violations.append(
                "Peak %d orders in a calendar clock second exceeds the exchange "
                "Threshold Orders Per Second (%d OPS per exchange, "
                "NSE/INVG/67858). The algorithm must be registered with each "
                "exchange on which it is used, through the broker, and every "
                "algo order must carry the exchange-provided unique identifier."
                % (activity.peak_orders_per_second, self._sebi_tops)
            )

        return violations, review
