"""Swiss algorithmic-trading control audit under FMIO Art. 31, the SIX rulebook and
FINMA Circular 2013/8.

**Read this first: where the Swiss obligations actually live.**

The Financial Market Infrastructure Act (FMIA / FinfraG, SR 958.1) does not mention
algorithmic trading anywhere. The operative provision is Art. 31 of the *ordinance*
(FMIO / FinfraV, SR 958.11), headed "Algorithmic trading and high-frequency trading"
and issued under Art. 30 FMIA. Its addressee is the **trading venue**, not the firm::

    Art. 31 para. 1  "The trading venue must be able to identify the following: ..."
    Art. 31 para. 2  "It shall require participants that pursue algorithmic trading
                      to flag the orders generated in this manner, record all entered
                      orders, including order cancellations, and in particular to
                      possess effective precautions and risk controls ..."

So a participant's binding, enforceable duties are the ones its **venue rulebook**
imposes, layered on top of FINMA's supervisory expectations for the institution. This
module encodes the SIX Swiss Exchange set, because that is the one whose text can be
cited precisely:

* SIX Trading Rules cl. 11.1.4 -- report the operation of algorithmic trading to the
  Exchange, flag algo-generated orders, use a separate identification for each
  algorithm, indicate the initiating traders, and keep sent orders including
  cancellations on file.
* SIX Directive 3: Trading, cl. 10 -- the participant-facing restatement of FMIO
  Art. 31 para. 2 lit. a-e, near-verbatim (addressed to "the Exchange" rather than
  "the trading venue").
* SIX Directive 3: Trading, cl. 5.1.3 lit. h -- the three order attributes that carry
  the flagging.
* SIX Directive 7: Sponsored Access, cl. 8 -- mandatory pre-/at-trade controls and the
  Exchange-provided "kill switch" for a Sponsored User's flow.
* SIX Trading Rules cl. 4.3.4 para. 2 -- DEA order filtering, and the obligation to be
  able to delete DEA client orders at any time on the Exchange's instruction.
* FINMA Circular 2013/8 "Market conduct rules" mn 62-63 -- effective systems and risk
  controls against false or misleading signals, and documentation of the key features
  of algorithmic trading strategies understandable to a third party.

**What Swiss law does not prescribe.** No message-rate cap. No price-collar
percentage. No notional ceiling. No order-purge latency. No timestamp granularity, and
in particular no microsecond requirement -- that is MiFID II RTS 25, an EU instrument.
FinfraV-FINMA (SR 958.111) Art. 1 para. 2 lit. b asks only for the time of order
receipt, with no stated precision. Any numeric threshold this module accepts is
therefore a **firm-calibrated value the auditor records**, never a figure the engine
can assert on a regulator's behalf.

**What the score is not.** ``finma_score_pct`` is an internal readiness indicator over
the controls found *applicable* to the audited system. Neither FINMA nor SIX publishes
a compliance percentage, and a partial score is not partial compliance: a participant
that cannot identify its algorithms is in breach of cl. 11.1.4 whatever the other
controls look like. Use ``is_compliant`` and ``failed_controls``; treat the percentage
as a progress bar for remediation tracking only.

Two failure classes are kept apart, following the repository convention:

* **Mis-specification** of the audit itself -- a blank ``algo_id``, a non-boolean
  attestation, a spec of the wrong type -- raises ``ValueError``/``TypeError``. An
  audit that cannot identify its subject must not issue a verdict.
* **Missing evidence** -- an empty algorithm identifier, no threshold calibration
  reference, no strategy documentation -- is a **control failure**, not an exception.
  That is precisely the finding the audit exists to surface.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Trading venues whose rulebook this module encodes. SIX Swiss Exchange only: BX Swiss
#: and SDX publish their own participant rules, and BX's published Participant Rules
#: carry no algorithmic-trading clause at all. Auditing a BX or SDX connection against
#: the SIX control set would assert obligations that venue has not imposed.
SUPPORTED_VENUES: Tuple[str, ...] = ("SIX_SWISS_EXCHANGE",)


class SwissAlgoControl(str, Enum):
    """One identifier per control, annotated with the provision that requires it."""

    ORDER_FLAGGING = "CH_ALGO_01_ORDER_FLAGGING"                  # SIX TR 11.1.4(1)
    ALGORITHM_IDENTIFICATION = "CH_ALGO_02_ALGORITHM_ID"          # SIX TR 11.1.4(1)
    INITIATING_TRADER = "CH_ALGO_03_INITIATING_TRADER"            # SIX D3 5.1.3(h)(3)
    EXCHANGE_NOTIFICATION = "CH_ALGO_04_EXCHANGE_NOTIFICATION"    # SIX TR 11.1.4(1)
    ORDER_RECORD_KEEPING = "CH_ALGO_05_ORDER_RECORDS"             # SIX TR 11.1.4(2)
    PEAK_CAPACITY = "CH_ALGO_06_PEAK_CAPACITY"                    # FMIO 31(2)(a)
    TRADING_THRESHOLDS = "CH_ALGO_07_TRADING_THRESHOLDS"          # FMIO 31(2)(b)
    MARKET_ABUSE_PREVENTION = "CH_ALGO_08_MARKET_ABUSE"           # FMIO 31(2)(d)
    ALGORITHM_TESTING = "CH_ALGO_09_ALGORITHM_TESTING"            # FMIO 31(2)(e)
    ORDER_TO_TRADE_RATIO = "CH_ALGO_10_ORDER_TO_TRADE_RATIO"      # FMIO 31(2)(e)(1)
    ORDER_FLOW_THROTTLING = "CH_ALGO_11_FLOW_THROTTLING"          # FMIO 31(2)(e)(2)
    MINIMUM_TICK_SIZE = "CH_ALGO_12_MINIMUM_TICK_SIZE"            # FMIO 31(2)(e)(3)
    STRATEGY_DOCUMENTATION = "CH_ALGO_13_STRATEGY_DOCS"           # FINMA Circ 13/8 mn 63
    DEA_ORDER_DELETION = "CH_ALGO_14_DEA_ORDER_DELETION"          # SIX TR 4.3.4(2), D7 8


#: Human-readable citation for each control. Kept beside the code so a finding can be
#: traced to a provision without leaving the audit record.
CONTROL_CITATIONS: Dict[SwissAlgoControl, str] = {
    SwissAlgoControl.ORDER_FLAGGING:
        "SIX Trading Rules cl. 11.1.4 para. 1; SIX Directive 3 cl. 5.1.3 lit. h no. 1; "
        "FMIO Art. 31 para. 2",
    SwissAlgoControl.ALGORITHM_IDENTIFICATION:
        "SIX Trading Rules cl. 11.1.4 para. 1 (a separate identification for each "
        "algorithm); SIX Directive 3 cl. 5.1.3 lit. h no. 2; FMIO Art. 31 para. 1 lit. b",
    SwissAlgoControl.INITIATING_TRADER:
        "SIX Trading Rules cl. 11.1.4 para. 1; SIX Directive 3 cl. 5.1.3 lit. h no. 3; "
        "FMIO Art. 31 para. 1 lit. c",
    SwissAlgoControl.EXCHANGE_NOTIFICATION:
        "SIX Trading Rules cl. 11.1.4 para. 1 (must report the operation of algorithmic "
        "trading to the Exchange)",
    SwissAlgoControl.ORDER_RECORD_KEEPING:
        "SIX Trading Rules cl. 11.1.4 para. 2; FMIO Art. 31 para. 2 (record all entered "
        "orders, including order cancellations); FMIA Art. 38; FMIO Art. 36; "
        "FinfraV-FINMA Art. 1",
    SwissAlgoControl.PEAK_CAPACITY:
        "FMIO Art. 31 para. 2 lit. a; SIX Directive 3 cl. 10 para. 1 lit. a",
    SwissAlgoControl.TRADING_THRESHOLDS:
        "FMIO Art. 31 para. 2 lit. b; SIX Directive 3 cl. 10 para. 1 lit. b "
        "(appropriate trading thresholds and upper trading limits -- no figure given)",
    SwissAlgoControl.MARKET_ABUSE_PREVENTION:
        "FMIO Art. 31 para. 2 lit. d; SIX Directive 3 cl. 10 para. 1 lit. d "
        "(Arts. 142/143 FMIA); FINMA Circular 2013/8 mn 62",
    SwissAlgoControl.ALGORITHM_TESTING:
        "FMIO Art. 31 para. 2 lit. e; SIX Directive 3 cl. 10 para. 1 lit. e",
    SwissAlgoControl.ORDER_TO_TRADE_RATIO:
        "FMIO Art. 31 para. 2 lit. e no. 1; SIX Directive 3 cl. 10 para. 1 lit. e no. 1",
    SwissAlgoControl.ORDER_FLOW_THROTTLING:
        "FMIO Art. 31 para. 2 lit. e no. 2; SIX Directive 3 cl. 10 para. 1 lit. e no. 2 "
        "(slow down the flow of orders -- no rate given)",
    SwissAlgoControl.MINIMUM_TICK_SIZE:
        "FMIO Art. 31 para. 2 lit. e no. 3; SIX Directive 3 cl. 10 para. 1 lit. e no. 3",
    SwissAlgoControl.STRATEGY_DOCUMENTATION:
        "FINMA Circular 2013/8 Market conduct rules mn 63 (document the key features of "
        "their algorithmic trading strategies in a way that third parties can "
        "understand)",
    SwissAlgoControl.DEA_ORDER_DELETION:
        "SIX Trading Rules cl. 4.3.4 para. 2; SIX Directive 7: Sponsored Access cl. 8 "
        "paras. 2-4 (Exchange-provided kill switch for Sponsored User flow)",
}


def _is_real_number(value: Any) -> bool:
    """True only for a finite int/float. Rejects bool, NaN, +/-inf and non-numerics.

    ``True > 100`` is ``False`` and every comparison against NaN is ``False``, so an
    unvalidated boolean or NaN rate silently satisfies any ceiling it is compared to.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _has_evidence(value: Any) -> bool:
    """True when ``value`` is a non-blank string.

    Whitespace is not evidence: ``"   "`` is truthy in Python, so a blank reference
    field passes a naive ``if value:`` check and the control reports itself satisfied.
    """
    return isinstance(value, str) and bool(value.strip())


@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "swiss-finma-algorithmic-trading-expectations"


class Engine:
    """Legacy Engine class for backward compatibility."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run(self) -> bool:
        return True


@dataclass
class ComplianceRecord:
    """Outcome of one algorithmic-trading control audit.

    ``trade_id`` carries the audited ``algo_id``. The field name predates this engine
    and is retained for backward compatibility; nothing here audits an individual
    trade.

    ``finma_score_pct`` is an internal readiness indicator over the *applicable*
    controls, not a regulatory metric -- see the module docstring. ``is_compliant`` and
    ``failed_controls`` are the fields to act on.
    """
    trade_id: str
    is_compliant: bool
    notes: str
    finma_score_pct: float = 100.0
    failed_controls: List[str] = field(default_factory=list)
    applicable_controls: List[str] = field(default_factory=list)
    citations: Dict[str, str] = field(default_factory=dict)


@dataclass
class AlgoTradingSystemAuditSpec:
    """Attested facts about one algorithmic trading system on a Swiss trading venue.

    Every ``bool`` is a firm attestation that a control exists; every ``str`` is the
    *evidence pointer* that makes the attestation auditable -- an algorithm identifier,
    a calibration document, a strategy description. A blank evidence pointer fails its
    control rather than raising, because that is the finding the audit is for.

    ``max_message_rate_per_sec`` and ``max_order_to_trade_ratio`` are the firm's own
    calibrated settings. Swiss law fixes neither value; they are recorded so the audit
    trail shows *what* was configured, and they are never compared against a regulatory
    ceiling this engine invents. Leave them ``None`` when not configured -- which itself
    fails the corresponding control.

    ``provides_direct_electronic_access`` gates the DEA control. A firm trading only its
    own flow is not subject to SIX Trading Rules cl. 4.3.4 or Directive 7, and scoring it
    against them would manufacture a breach.
    """
    algo_id: str
    strategy_version: str
    governance_owner: str
    venue: str = "SIX_SWISS_EXCHANGE"

    # Identification and notification -- SIX TR 11.1.4, D3 5.1.3(h), FMIO 31(1)-(2)
    flags_algo_generated_orders: bool = False
    algorithm_identifier: str = ""
    initiating_trader_id: str = ""
    reported_algo_trading_to_exchange: bool = False
    records_orders_including_cancellations: bool = False

    # Risk controls -- FMIO Art. 31(2)(a)-(e), SIX Directive 3 cl. 10
    capacity_tested_for_peak_volume: bool = False
    has_pre_trade_thresholds: bool = False
    threshold_calibration_reference: str = ""
    prevents_market_abuse_art_142_143: bool = False
    algorithms_and_controls_tested: bool = False
    limits_order_to_trade_ratio: bool = False
    max_order_to_trade_ratio: Optional[float] = None
    can_throttle_order_flow: bool = False
    max_message_rate_per_sec: Optional[float] = None
    enforces_minimum_tick_size: bool = False

    # Supervisory documentation -- FINMA Circular 2013/8 mn 63
    strategy_documentation_reference: str = ""

    # Sponsored Access / DEA -- SIX TR 4.3.4(2), Directive 7 cl. 8. Conditional.
    provides_direct_electronic_access: bool = False
    can_delete_client_orders_on_demand: bool = False


_BOOL_FIELDS: Tuple[str, ...] = (
    "flags_algo_generated_orders",
    "reported_algo_trading_to_exchange",
    "records_orders_including_cancellations",
    "capacity_tested_for_peak_volume",
    "has_pre_trade_thresholds",
    "prevents_market_abuse_art_142_143",
    "algorithms_and_controls_tested",
    "limits_order_to_trade_ratio",
    "can_throttle_order_flow",
    "enforces_minimum_tick_size",
    "provides_direct_electronic_access",
    "can_delete_client_orders_on_demand",
)

_TEXT_FIELDS: Tuple[str, ...] = (
    "algorithm_identifier",
    "initiating_trader_id",
    "threshold_calibration_reference",
    "strategy_documentation_reference",
    "strategy_version",
    "governance_owner",
)

_OPTIONAL_NUMERIC_FIELDS: Tuple[str, ...] = (
    "max_order_to_trade_ratio",
    "max_message_rate_per_sec",
)


class SwissFINMAComplianceEngine:
    """Audits an algorithmic trading system against the Swiss control set.

    The engine asserts no numeric regulatory threshold, because Swiss law states none
    (see the module docstring). It checks that each control required by FMIO Art. 31,
    the venue rulebook and FINMA Circular 2013/8 is attested *and* evidenced, and
    reports every gap with the provision that requires it.
    """

    def __init__(self, venue: str = "SIX_SWISS_EXCHANGE"):
        normalised = venue.strip().upper() if isinstance(venue, str) else ""
        if normalised not in SUPPORTED_VENUES:
            raise ValueError(
                f"Unsupported venue {venue!r}. This engine encodes the SIX Swiss "
                f"Exchange rulebook only; supported: {list(SUPPORTED_VENUES)}. "
                "BX Swiss and SDX publish their own participant rules and must be "
                "audited against those."
            )
        self.venue = normalised

    # -- validation ------------------------------------------------------------

    @staticmethod
    def _validate(spec: AlgoTradingSystemAuditSpec) -> None:
        """Reject a spec the audit cannot meaningfully attribute or interpret.

        Structural problems raise. Missing *evidence* does not -- that is a finding.
        """
        if not isinstance(spec, AlgoTradingSystemAuditSpec):
            raise TypeError(
                f"Expected an AlgoTradingSystemAuditSpec, got {type(spec).__name__}."
            )
        if not _has_evidence(spec.algo_id):
            raise ValueError(
                "algo_id must be a non-blank string: an audit record that cannot "
                "identify its subject is not an audit record."
            )
        for name in _BOOL_FIELDS:
            value = getattr(spec, name)
            if not isinstance(value, bool):
                raise TypeError(
                    f"{name} must be a bool, got {type(value).__name__}. A truthy "
                    "string or a non-zero int would silently attest to a control that "
                    "was never assessed."
                )
        for name in _TEXT_FIELDS:
            value = getattr(spec, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a str, got {type(value).__name__}.")
        for name in _OPTIONAL_NUMERIC_FIELDS:
            value = getattr(spec, name)
            if value is None:
                continue
            if not _is_real_number(value) or value <= 0:
                raise ValueError(
                    f"{name} must be None or a finite positive number, got {value!r}. "
                    "NaN, infinity and booleans defeat every comparison they enter."
                )
        if (not isinstance(spec.venue, str)
                or spec.venue.strip().upper() not in SUPPORTED_VENUES):
            raise ValueError(
                f"spec.venue {spec.venue!r} is not a venue this engine encodes "
                f"({list(SUPPORTED_VENUES)})."
            )

    # -- audit -----------------------------------------------------------------

    def audit_algo_system(
        self, spec: AlgoTradingSystemAuditSpec
    ) -> ComplianceRecord:
        """Audit one algorithmic trading system and return a traceable record.

        Raises ``TypeError``/``ValueError`` for a mis-specified audit. Otherwise every
        outcome is reported in the record, never by exception.
        """
        self._validate(spec)

        checks: List[Tuple[SwissAlgoControl, bool, str]] = [
            (
                SwissAlgoControl.ORDER_FLAGGING,
                spec.flags_algo_generated_orders,
                "Orders generated by algorithmic trading are not flagged to the Exchange.",
            ),
            (
                SwissAlgoControl.ALGORITHM_IDENTIFICATION,
                _has_evidence(spec.algorithm_identifier),
                "No separate identification recorded for this algorithm.",
            ),
            (
                SwissAlgoControl.INITIATING_TRADER,
                _has_evidence(spec.initiating_trader_id),
                "The trader initiating the algorithm's orders is not identified.",
            ),
            (
                SwissAlgoControl.EXCHANGE_NOTIFICATION,
                spec.reported_algo_trading_to_exchange,
                "Operation of algorithmic trading has not been reported to the Exchange.",
            ),
            (
                SwissAlgoControl.ORDER_RECORD_KEEPING,
                spec.records_orders_including_cancellations,
                "Sent orders and their cancellations are not kept on file.",
            ),
            (
                SwissAlgoControl.PEAK_CAPACITY,
                spec.capacity_tested_for_peak_volume,
                "System resilience at peak order and announcement volumes is unevidenced.",
            ),
            (
                SwissAlgoControl.TRADING_THRESHOLDS,
                spec.has_pre_trade_thresholds
                and _has_evidence(spec.threshold_calibration_reference),
                "Pre-trade thresholds and upper limits are absent, or set without a "
                "recorded calibration basis. The provision requires them to be "
                "appropriate; without the calibration record, appropriateness cannot be "
                "demonstrated.",
            ),
            (
                SwissAlgoControl.MARKET_ABUSE_PREVENTION,
                spec.prevents_market_abuse_art_142_143,
                "No controls evidenced against Arts. 142/143 FMIA (insider trading, "
                "price and market manipulation).",
            ),
            (
                SwissAlgoControl.ALGORITHM_TESTING,
                spec.algorithms_and_controls_tested,
                "Algorithms and control mechanisms are not subject to appropriate tests.",
            ),
            (
                SwissAlgoControl.ORDER_TO_TRADE_RATIO,
                spec.limits_order_to_trade_ratio
                and spec.max_order_to_trade_ratio is not None,
                "No configured limit on the proportion of unexecuted orders relative to "
                "transactions.",
            ),
            (
                SwissAlgoControl.ORDER_FLOW_THROTTLING,
                spec.can_throttle_order_flow
                and spec.max_message_rate_per_sec is not None,
                "No configured ability to slow the order flow when system capacity is "
                "at risk.",
            ),
            (
                SwissAlgoControl.MINIMUM_TICK_SIZE,
                spec.enforces_minimum_tick_size,
                "Minimum tick size is not limited and enforced.",
            ),
            (
                SwissAlgoControl.STRATEGY_DOCUMENTATION,
                _has_evidence(spec.strategy_documentation_reference)
                and _has_evidence(spec.governance_owner),
                "Key features of the strategy are not documented understandably for a "
                "third party, or no accountable owner is named.",
            ),
        ]

        if spec.provides_direct_electronic_access:
            checks.append((
                SwissAlgoControl.DEA_ORDER_DELETION,
                spec.can_delete_client_orders_on_demand,
                "Direct electronic access is provided without the ability to delete "
                "client orders at any time on the Exchange's instruction.",
            ))

        failed: List[str] = []
        for control, satisfied, reason in checks:
            if not satisfied:
                failed.append(f"{control.value}: {reason} [{CONTROL_CITATIONS[control]}]")

        applicable = [control.value for control, _, _ in checks]
        total = len(checks)
        score = ((total - len(failed)) / total) * 100.0
        is_compliant = not failed

        notes = (
            f"SWISS ALGO CONTROL AUDIT [{spec.algo_id} v{spec.strategy_version}] "
            f"venue={self.venue}: {total - len(failed)}/{total} applicable controls "
            f"evidenced ({score:.1f}%). Compliant = {is_compliant}. "
            f"Owner: {spec.governance_owner.strip() or 'UNASSIGNED'}. "
            f"Basis: FMIO Art. 31, SIX Trading Rules cl. 11.1.4, SIX Directive 3 cl. 10, "
            f"FINMA Circ. 13/8 mn 62-63. "
            f"The percentage is an internal readiness indicator, not a regulatory metric."
        )

        if is_compliant:
            logger.info(notes)
        else:
            logger.warning("%s Gaps: %s", notes, failed)

        return ComplianceRecord(
            trade_id=spec.algo_id,
            is_compliant=is_compliant,
            notes=notes,
            finma_score_pct=score,
            failed_controls=failed,
            applicable_controls=applicable,
            citations={c.value: CONTROL_CITATIONS[c] for c, _, _ in checks},
        )


class ComplianceChecker:
    """Legacy wrapper retained for backward compatibility -- now fail-closed.

    The previous implementation returned ``is_compliant=True`` with the note
    "Compliant with Swiss FINMA FinfraG regulations." for *any* string, having assessed
    nothing. Callers -- including an AI agent reading only the method name -- could
    obtain an unconditional Swiss compliance attestation. It now returns a
    non-compliant record unless an ``AlgoTradingSystemAuditSpec`` is supplied, in which
    case it delegates to :class:`SwissFINMAComplianceEngine`.
    """

    def __init__(self) -> None:
        self.rules = [
            "FMIO_ART_31_ALGORITHMIC_TRADING",
            "SIX_TRADING_RULES_11_1_4",
            "FINMA_CIRC_13_8_MN_62_63",
        ]
        self.finma_engine = SwissFINMAComplianceEngine()

    def check_compliance(
        self,
        trade_id: str,
        spec: Optional[AlgoTradingSystemAuditSpec] = None,
    ) -> ComplianceRecord:
        """Audit ``spec`` if given; otherwise return a fail-closed record.

        An identifier alone carries no evidence about any control, so no compliance
        conclusion can be drawn from it.
        """
        if spec is not None:
            return self.finma_engine.audit_algo_system(spec)

        identifier = trade_id.strip() if isinstance(trade_id, str) else ""
        notes = (
            "NOT ASSESSED: no AlgoTradingSystemAuditSpec supplied. A trade or algorithm "
            "identifier carries no evidence about FMIO Art. 31, SIX Trading Rules "
            "cl. 11.1.4 or FINMA Circ. 13/8 controls, so no compliance conclusion is "
            "available. Call audit_algo_system() with a populated spec."
        )
        logger.warning("%s (identifier=%r)", notes, trade_id)
        return ComplianceRecord(
            trade_id=identifier,
            is_compliant=False,
            notes=notes,
            finma_score_pct=0.0,
            failed_controls=["CH_ALGO_00_NOT_ASSESSED: no audit spec supplied."],
        )

    def batch_check(self, trade_ids: List[str]) -> List[ComplianceRecord]:
        """Fail-closed record per identifier. See :meth:`check_compliance`."""
        return [self.check_compliance(tid) for tid in trade_ids]
