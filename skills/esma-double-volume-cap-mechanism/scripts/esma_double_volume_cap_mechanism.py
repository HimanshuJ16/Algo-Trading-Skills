"""
esma-double-volume-cap-mechanism: MiFIR Article 5 volume cap gate for EU dark
routing under the reference price waiver.

The skill slug is historical. The Double Volume Cap (DVC) it names **no longer
exists**: Regulation (EU) 2024/791 (the MiFIR review, in force 28 March 2024)
replaced it with a *single* Union-wide cap, and ESMA's September 2025 file was
the last DVC publication, with all DVC suspensions expiring on 28 September 2025.

Current law -- MiFIR Article 5(1) as amended:

    "Trading venues shall suspend their use of the waiver referred to in
     Article 4(1), point (a), where the percentage of trading in a financial
     instrument in the Union carried out under that waiver exceeds 7 % of the
     total volume of trading in that financial instrument in the Union."

Three consequences that this module encodes and that the pre-2024 design got
wrong:

1. **One cap, not two.** There is no 4 % per-venue cap and no 8 % Union cap. The
   cap is 7 %, Union-wide only. Article 5(8) sets 29 September 2025 as the start
   of the monitored period.
2. **Reference price waiver only.** Article 5(1) points at Article 4(1)(a) alone.
   The negotiated trade waiver (Art. 4(1)(b)), the large-in-scale waiver
   (Art. 4(1)(c)) and the order management facility waiver (Art. 4(1)(d)) are
   outside the cap entirely. Blocking a negotiated trade because of the volume
   cap is over-compliance that costs execution quality for no regulatory reason.
3. **The suspension is ESMA's decision, not the firm's.** Article 5(1) requires
   venues to base the suspension "on data published by ESMA", within two working
   days of that publication, for three months. ESMA publishes quarterly (within
   seven working days of the end of March, June, September and December --
   Art. 5(4)); the first Volume Cap results file was published 9 October 2025
   with suspensions effective 14 October 2025 to 13 January 2026. A ratio the
   firm computes from its own volume estimates is an **early warning**, never
   the regulatory status. This module reports the two separately and never lets
   the estimate stand in for the register.

Legacy DVC semantics (4 % venue / 8 % Union, six-month suspension, covering both
Art. 4(1)(a) and the liquid-instrument negotiated trade waiver Art. 4(1)(b)(i))
are retained *only* for backtests over data before 29 September 2025, and are
selected automatically from the ``as_of`` date. They must never be used to route
a live order.

Fail-closed design (deliberate): when the ESMA register is absent or too old to
cover ``as_of``, RPW dark routing is blocked and the order goes lit. Routing lit
is always lawful; routing dark against an unknown suspension status is not. The
cost of the default is execution quality, never compliance.

Limitations (documented, deliberate):

- **Not a substitute for the ESMA register.** Supply the published suspension
  file. The internal ratio uses whatever volume numbers the caller has and will
  not reproduce ESMA's methodology, which is derived from transaction reports
  collected by national competent authorities (the venue-reported DVC feed was
  decommissioned in January 2026).
- **LIS thresholds are per-instrument and must be supplied.** There is no single
  large-in-scale value. RTS 1 (Delegated Regulation (EU) 2017/587) Annex II
  Table 1 bands them from EUR 15 000 to EUR 650 000 by average daily turnover.
  ``rts1_lis_threshold_eur`` implements those bands for shares, depositary
  receipts and certificates; for ETFs and other equity-like instruments the
  caller must read the per-ISIN value from ESMA's transparency calculations.
- **Single instrument, single order.** No aggregation, no venue selection. The
  answer is "may this order use this waiver", not "where should it go".
- **EU/EEA only.** No non-EU regime is modelled.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Single volume cap, MiFIR Art. 5(1) as amended by Regulation (EU) 2024/791.
SVC_CAP_PCT = 7.0

#: MiFIR Art. 5(8): start of the period for which trading under the waiver is
#: monitored and for which ESMA publishes trading data.
SVC_MONITORING_START_DATE = date(2025, 9, 29)

#: MiFIR Art. 5(1): a suspension runs for three months.
SVC_SUSPENSION_MONTHS = 3

#: Repealed DVC thresholds. Backtest-only -- see the module docstring.
LEGACY_DVC_SINGLE_VENUE_CAP_PCT = 4.0
LEGACY_DVC_UNION_CAP_PCT = 8.0
LEGACY_DVC_SUSPENSION_MONTHS = 6

#: Last day any DVC suspension could still be in force (ESMA transition notice).
LEGACY_DVC_FINAL_SUSPENSION_EXPIRY = date(2025, 9, 28)

REGIME_SVC = "SVC"
REGIME_LEGACY_DVC = "LEGACY_DVC"

WAIVER_RPW = "RPW"   # Art. 4(1)(a) reference price waiver     -- capped
WAIVER_NTW = "NTW"   # Art. 4(1)(b) negotiated transaction     -- capped under DVC only
WAIVER_LIS = "LIS"   # Art. 4(1)(c) large in scale             -- never capped
WAIVER_OMF = "OMF"   # Art. 4(1)(d) order management facility  -- never capped
VALID_WAIVERS = frozenset({WAIVER_RPW, WAIVER_NTW, WAIVER_LIS, WAIVER_OMF})

REGISTER_SUSPENDED = "SUSPENDED"
REGISTER_NOT_SUSPENDED = "NOT_SUSPENDED"
REGISTER_NOT_SUPPLIED = "REGISTER_NOT_SUPPLIED"
REGISTER_STALE = "REGISTER_STALE"
REGISTER_NOT_APPLICABLE = "NOT_APPLICABLE"
#: Statuses that mean "the official position is unknown" -> fail closed.
REGISTER_UNKNOWN_STATUSES = frozenset({REGISTER_NOT_SUPPLIED, REGISTER_STALE})

#: ESMA publishes quarterly and suspensions run three months, so a register file
#: older than this cannot describe the current quarter. An engineering guard
#: sized from the publication cadence, not a figure taken from the Regulation.
DEFAULT_MAX_REGISTER_AGE_DAYS = 100

#: RTS 1 (Delegated Regulation (EU) 2017/587) Annex II Table 1 -- minimum order
#: size qualifying as large in scale for shares and depositary receipts, by
#: average daily turnover band. Pairs are (exclusive ADT upper bound, LIS EUR);
#: the final band is open-ended.
RTS1_SHARE_DR_LIS_BANDS: Tuple[Tuple[Optional[float], float], ...] = (
    (50_000.0, 15_000.0),
    (100_000.0, 30_000.0),
    (500_000.0, 60_000.0),
    (1_000_000.0, 100_000.0),
    (5_000_000.0, 200_000.0),
    (25_000_000.0, 300_000.0),
    (50_000_000.0, 400_000.0),
    (100_000_000.0, 500_000.0),
    (None, 650_000.0),
)

#: RTS 1 Annex II Table 2 -- certificates and other similar instruments.
RTS1_CERTIFICATE_LIS_BANDS: Tuple[Tuple[Optional[float], float], ...] = (
    (50_000.0, 15_000.0),
    (None, 30_000.0),
)

#: RTS 1 Annex III instrument classes whose LIS band table is implemented here.
_LIS_TABLE_BY_CLASS: Dict[str, Tuple[Tuple[Optional[float], float], ...]] = {
    "SHRS": RTS1_SHARE_DR_LIS_BANDS,
    "DPRS": RTS1_SHARE_DR_LIS_BANDS,
    "CRFT": RTS1_CERTIFICATE_LIS_BANDS,
}


def _require_finite_non_negative(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a real number, got {type(value).__name__}.")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{label} must be finite, got {numeric!r}.")
    if numeric < 0.0:
        raise ValueError(f"{label} must be >= 0, got {numeric!r}.")
    return numeric


def _require_plain_date(value: object, label: str) -> date:
    """
    Require a ``datetime.date``, and reject a ``datetime.datetime``.

    ``datetime`` subclasses ``date``, so an ``isinstance`` check alone lets a
    timestamp through and it then fails on the first date comparison. Rejecting
    it here is also the honest answer: a suspension runs from 08:00 CET on its
    start date to the close of the trading day on its end date, so mapping an
    instant to a trading date is a timezone decision the caller must make.
    """
    if isinstance(value, datetime):
        raise TypeError(
            f"{label} must be a datetime.date, not a datetime.datetime. Convert the timestamp "
            f"to a trading date in the venue's timezone first -- ESMA suspensions run from "
            f"08:00 CET on the start date to the close of the trading day on the end date."
        )
    if not isinstance(value, date):
        raise TypeError(f"{label} must be a datetime.date, got {type(value).__name__}.")
    return value


def _share_pct(numerator_eur: float, total_eur: float) -> float:
    """
    ``numerator / total`` as a percentage, multiplying before dividing.

    ``(u / t) * 100`` rounds twice: 70,000,000 / 1,000,000,000 * 100 evaluates to
    7.000000000000001, so an instrument sitting exactly on the cap reads as a
    breach. ``u * 100 / t`` rounds once and returns 7.0.
    """
    return (numerator_eur * 100.0) / total_eur


def _exceeds_cap(numerator_eur: float, total_eur: float, cap_pct: float) -> bool:
    """
    MiFIR Art. 5(1) suspends where the share *exceeds* the cap -- strictly greater.

    Compared by cross multiplication (``u * 100 > cap * t``) rather than on the
    percentage, and never on a rounded percentage: rounding to two decimals
    before the comparison turns 6.996 % into 7.00 % and fabricates a suspension
    for an instrument that is inside the cap. ``total_eur`` is positive by the
    metrics invariant, so the direction of the inequality is preserved.
    """
    return numerator_eur * 100.0 > cap_pct * total_eur


def rts1_lis_threshold_eur(
    average_daily_turnover_eur: float,
    instrument_class: str = "SHRS",
) -> float:
    """
    Large-in-scale threshold from RTS 1 Annex II, in EUR.

    Implements Table 1 (shares ``SHRS`` / depositary receipts ``DPRS``) and
    Table 2 (certificates ``CRFT``) of Commission Delegated Regulation (EU)
    2017/587. ETFs (``ETFS``) and other equity-like instruments (``OTHR``) are
    deliberately unsupported: their thresholds are not set by these tables, and
    guessing one would be exactly the defect this function exists to remove.
    Read those from ESMA's published transparency calculations per ISIN.

    The tables are the *statutory* bands. Where ESMA publishes an instrument's
    applicable threshold, that published value governs; use this function when
    you have the ADT but not the published figure.
    """
    adt = _require_finite_non_negative(average_daily_turnover_eur, "average_daily_turnover_eur")
    key = (instrument_class or "").strip().upper()
    table = _LIS_TABLE_BY_CLASS.get(key)
    if table is None:
        raise ValueError(
            f"No RTS 1 Annex II LIS band table is implemented for instrument_class "
            f"{instrument_class!r}. Supported: {sorted(_LIS_TABLE_BY_CLASS)}. For ETFs and other "
            f"equity-like instruments read the per-ISIN threshold from ESMA's transparency "
            f"calculations instead of deriving one here."
        )
    for upper_bound, threshold in table:
        if upper_bound is None or adt < upper_bound:
            return threshold
    raise AssertionError("LIS band table must end in an open-ended band.")  # pragma: no cover


@dataclass(frozen=True)
class VolumeCapSuspension:
    """One row of an ESMA Volume Cap suspension file."""

    isin: str
    suspension_start_date: date
    suspension_end_date: date

    def __post_init__(self) -> None:
        if not self.isin or not self.isin.strip():
            raise ValueError("VolumeCapSuspension.isin must be a non-empty string.")
        _require_plain_date(self.suspension_start_date, "suspension_start_date")
        _require_plain_date(self.suspension_end_date, "suspension_end_date")
        if self.suspension_end_date < self.suspension_start_date:
            raise ValueError(
                f"Suspension for {self.isin} ends ({self.suspension_end_date}) before it starts "
                f"({self.suspension_start_date})."
            )

    def is_active_on(self, as_of: date) -> bool:
        """
        Inclusive of both endpoints: ESMA suspensions start at 08:00 CET on the
        start date and end at the close of the trading day on the end date, so
        both calendar days are suspended days.
        """
        return self.suspension_start_date <= as_of <= self.suspension_end_date


@dataclass(frozen=True)
class EsmaSuspensionRegister:
    """
    The ESMA-published suspension file as of ``published_on``.

    An empty ``suspensions`` tuple is a meaningful, valid state: it says "ESMA
    published on this date and no instrument is suspended". That is different
    from passing no register at all, which the engine treats as unknown.
    """

    published_on: date
    suspensions: Tuple[VolumeCapSuspension, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_plain_date(self.published_on, "EsmaSuspensionRegister.published_on")
        object.__setattr__(self, "suspensions", tuple(self.suspensions))
        for entry in self.suspensions:
            if not isinstance(entry, VolumeCapSuspension):
                raise TypeError(
                    "EsmaSuspensionRegister.suspensions must contain VolumeCapSuspension entries.")

    def age_days(self, as_of: date) -> int:
        return (as_of - self.published_on).days

    def active_suspension(self, isin: str, as_of: date) -> Optional[VolumeCapSuspension]:
        """
        The active suspension for ``isin`` on ``as_of``, or ``None``.

        ISINs are matched case-insensitively after whitespace stripping; a file
        parsed from the published Excel routinely carries stray spacing, and a
        silent non-match here would read as "not suspended".
        """
        key = (isin or "").strip().upper()
        for entry in self.suspensions:
            if entry.isin.strip().upper() == key and entry.is_active_on(as_of):
                return entry
        return None


@dataclass(frozen=True)
class ReferencePriceWaiverVolumeMetrics:
    """
    Rolling 12-month volumes for one instrument.

    The numerator is volume executed **under the reference price waiver**, not
    all dark volume. Dark trading also happens under the LIS and negotiated
    trade waivers, neither of which counts toward the Article 5 cap; feeding
    total dark volume in overstates the ratio and manufactures suspensions ESMA
    has not declared.
    """

    isin: str
    symbol: str
    rolling_12m_total_eu_volume_eur: float
    rolling_12m_union_rpw_volume_eur: float
    rolling_12m_venue_rpw_volume_eur: Optional[float] = None  # legacy DVC only
    venue_id: str = ""

    def __post_init__(self) -> None:
        if not self.isin or not self.isin.strip():
            raise ValueError("ReferencePriceWaiverVolumeMetrics.isin must be a non-empty string.")
        total = _require_finite_non_negative(
            self.rolling_12m_total_eu_volume_eur, "rolling_12m_total_eu_volume_eur")
        union = _require_finite_non_negative(
            self.rolling_12m_union_rpw_volume_eur, "rolling_12m_union_rpw_volume_eur")
        if total <= 0.0:
            raise ValueError("rolling_12m_total_eu_volume_eur must be > 0 to form a percentage.")
        if union > total:
            raise ValueError(
                f"Union RPW volume ({union:,.2f}) exceeds total EU volume ({total:,.2f}) for "
                f"{self.isin}; RPW volume is a subset of total volume, so one of the two inputs "
                f"is wrong."
            )
        if self.rolling_12m_venue_rpw_volume_eur is not None:
            venue = _require_finite_non_negative(
                self.rolling_12m_venue_rpw_volume_eur, "rolling_12m_venue_rpw_volume_eur")
            if venue > union:
                raise ValueError(
                    f"Venue RPW volume ({venue:,.2f}) exceeds Union RPW volume ({union:,.2f}) for "
                    f"{self.isin}; a venue's RPW volume is a subset of the Union total."
                )


@dataclass(frozen=True)
class SorOrderRouteRequest:
    """A single order the router wants to place under a named MiFIR waiver."""

    order_id: str
    isin: str
    symbol: str
    order_val_eur: float
    intended_waiver_type: str
    lis_threshold_eur: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.order_id or not self.order_id.strip():
            raise ValueError("SorOrderRouteRequest.order_id must be a non-empty string.")
        if not self.isin or not self.isin.strip():
            raise ValueError("SorOrderRouteRequest.isin must be a non-empty string.")
        value = _require_finite_non_negative(self.order_val_eur, "order_val_eur")
        if value <= 0.0:
            raise ValueError("order_val_eur must be > 0.")
        waiver = (self.intended_waiver_type or "").strip().upper()
        if waiver not in VALID_WAIVERS:
            raise ValueError(
                f"intended_waiver_type {self.intended_waiver_type!r} is not a MiFIR Article 4(1) "
                f"waiver. Expected one of {sorted(VALID_WAIVERS)}."
            )
        object.__setattr__(self, "intended_waiver_type", waiver)
        if self.lis_threshold_eur is not None:
            threshold = _require_finite_non_negative(self.lis_threshold_eur, "lis_threshold_eur")
            if threshold <= 0.0:
                raise ValueError("lis_threshold_eur must be > 0 when supplied.")
        elif waiver == WAIVER_LIS:
            raise ValueError(
                "A LIS order must carry lis_threshold_eur: the large-in-scale threshold is "
                "instrument-specific (RTS 1 Annex II Table 1 bands it from EUR 15 000 to "
                "EUR 650 000 by average daily turnover). Use rts1_lis_threshold_eur() or the "
                "ESMA-published per-ISIN value; there is no safe default."
            )


@dataclass(frozen=True)
class VolumeCapAuditReport:
    order_id: str
    isin: str
    symbol: str
    regime: str                                # REGIME_SVC | REGIME_LEGACY_DVC
    cap_pct: float
    union_rpw_share_pct: float                 # unrounded; round only for display
    venue_rpw_share_pct: Optional[float]       # legacy DVC only
    rpw_headroom_eur: float                    # EUR of further RPW volume before the cap
    internal_estimate_status: str
    official_register_status: str
    suspension_end_date: Optional[date]
    effective_waiver_type: str                 # after LIS eligibility is resolved
    is_cap_applicable: bool
    is_dark_routing_allowed: bool
    # DARK_RPW | DARK_NTW | DARK_LIS_EXEMPT | DARK_OMF | LIT_VENUE
    final_routed_venue_type: str
    audit_notes: str


class EsmaVolumeCapEngine:
    """
    MiFIR Article 5 volume cap gate for a single order.

    Answers one question: may this order use the waiver it asks for, given the
    ESMA-published suspension register? The routing outcome is binary -- the
    named dark waiver, or lit.
    """

    def __init__(
        self,
        *,
        cap_pct: float = SVC_CAP_PCT,
        legacy_venue_cap_pct: float = LEGACY_DVC_SINGLE_VENUE_CAP_PCT,
        legacy_union_cap_pct: float = LEGACY_DVC_UNION_CAP_PCT,
        block_rpw_on_estimated_breach: bool = True,
        max_register_age_days: int = DEFAULT_MAX_REGISTER_AGE_DAYS,
    ) -> None:
        for label, raw in (
            ("cap_pct", cap_pct),
            ("legacy_venue_cap_pct", legacy_venue_cap_pct),
            ("legacy_union_cap_pct", legacy_union_cap_pct),
        ):
            numeric = _require_finite_non_negative(raw, label)
            if not 0.0 < numeric <= 100.0:
                raise ValueError(f"{label} must be in (0, 100], got {numeric!r}.")
        if isinstance(max_register_age_days, bool) or not isinstance(max_register_age_days, int):
            raise TypeError("max_register_age_days must be an int.")
        if max_register_age_days <= 0:
            raise ValueError("max_register_age_days must be > 0.")
        self.cap_pct = float(cap_pct)
        self.legacy_venue_cap_pct = float(legacy_venue_cap_pct)
        self.legacy_union_cap_pct = float(legacy_union_cap_pct)
        self.block_rpw_on_estimated_breach = bool(block_rpw_on_estimated_breach)
        self.max_register_age_days = int(max_register_age_days)

    @staticmethod
    def regime_for(as_of: date) -> str:
        """
        MiFIR Art. 5(8): the monitored period starts 29 September 2025. Dates
        before that fall under the repealed DVC and are backtest-only.
        """
        _require_plain_date(as_of, "as_of")
        return REGIME_SVC if as_of >= SVC_MONITORING_START_DATE else REGIME_LEGACY_DVC

    def audit_volume_cap_and_route_order(
        self,
        req: SorOrderRouteRequest,
        metrics: ReferencePriceWaiverVolumeMetrics,
        *,
        as_of: date,
        register: Optional[EsmaSuspensionRegister] = None,
    ) -> VolumeCapAuditReport:
        """
        Evaluate one order against the Article 5 cap and return a routing decision.

        ``register`` is the ESMA-published suspension file. Omitting it is not a
        convenience: the cap-bound waiver is blocked and the order goes lit,
        because an unknown suspension status must never be traded through.
        """
        _require_plain_date(as_of, "as_of")
        if req.isin.strip().upper() != metrics.isin.strip().upper():
            raise ValueError(
                f"Order ISIN {req.isin!r} does not match metrics ISIN {metrics.isin!r}; evaluating "
                f"an order against another instrument's volumes would produce a correct-looking "
                f"but meaningless decision."
            )
        regime = self.regime_for(as_of)
        notes: List[str] = []

        total = metrics.rolling_12m_total_eu_volume_eur
        union_share = _share_pct(metrics.rolling_12m_union_rpw_volume_eur, total)
        venue_share: Optional[float] = None

        if regime == REGIME_SVC:
            cap_pct = self.cap_pct
            # Art. 5(1): "exceeds" -- strictly greater, evaluated by cross
            # multiplication so an instrument sitting exactly on the cap is not
            # tipped over it by floating-point error.
            estimated_breach = _exceeds_cap(
                metrics.rolling_12m_union_rpw_volume_eur, total, cap_pct)
            estimate_status = (
                "ESTIMATED_BREACH_UNION_CAP" if estimated_breach else "ESTIMATED_WITHIN_CAP")
        else:
            if metrics.rolling_12m_venue_rpw_volume_eur is None:
                raise ValueError(
                    f"as_of {as_of} precedes the single volume cap monitoring start "
                    f"({SVC_MONITORING_START_DATE}), so the repealed DVC applies and its 4 % "
                    f"per-venue limb cannot be evaluated without rolling_12m_venue_rpw_volume_eur."
                )
            cap_pct = self.legacy_union_cap_pct
            venue_share = _share_pct(metrics.rolling_12m_venue_rpw_volume_eur, total)
            union_breach = _exceeds_cap(
                metrics.rolling_12m_union_rpw_volume_eur, total, self.legacy_union_cap_pct)
            venue_breach = _exceeds_cap(
                metrics.rolling_12m_venue_rpw_volume_eur, total, self.legacy_venue_cap_pct)
            estimated_breach = union_breach or venue_breach
            if union_breach and venue_breach:
                estimate_status = "ESTIMATED_BREACH_UNION_AND_VENUE_CAP"
            elif union_breach:
                estimate_status = "ESTIMATED_BREACH_UNION_CAP"
            elif venue_breach:
                estimate_status = "ESTIMATED_BREACH_VENUE_CAP"
            else:
                estimate_status = "ESTIMATED_WITHIN_CAP"
            notes.append(
                f"LEGACY DVC MODE [{req.symbol}]: as_of {as_of} precedes "
                f"{SVC_MONITORING_START_DATE}; the repealed 4 %/8 % Double Volume Cap is applied "
                f"for historical analysis only. Never route a live order on this path."
            )

        headroom = (cap_pct * total) / 100.0 - metrics.rolling_12m_union_rpw_volume_eur

        effective_waiver, lis_note = self._resolve_lis_eligibility(req)
        if lis_note:
            notes.append(lis_note)

        cap_applies = effective_waiver == WAIVER_RPW or (
            regime == REGIME_LEGACY_DVC and effective_waiver == WAIVER_NTW)

        register_status, suspension_end, register_note = self._official_status(
            register=register, isin=req.isin, as_of=as_of, evaluate=cap_applies)
        if register_note:
            notes.append(register_note)

        if not cap_applies:
            allowed = True
            routed = {
                WAIVER_LIS: "DARK_LIS_EXEMPT",
                WAIVER_OMF: "DARK_OMF",
                WAIVER_NTW: "DARK_NTW",
            }[effective_waiver]
            notes.append(
                f"OUTSIDE ARTICLE 5 [{req.symbol}]: waiver {effective_waiver} is not the "
                f"Article 4(1)(a) reference price waiver, so the volume cap does not apply. "
                f"Routed to {routed}."
            )
            logger.info(notes[-1])
        else:
            blocked_by_register = (register_status in REGISTER_UNKNOWN_STATUSES
                                   or register_status == REGISTER_SUSPENDED)
            blocked_by_estimate = self.block_rpw_on_estimated_breach and estimated_breach
            if blocked_by_register or blocked_by_estimate:
                allowed = False
                routed = "LIT_VENUE"
                if register_status == REGISTER_SUSPENDED:
                    reason = "ESMA register"
                elif blocked_by_register:
                    reason = "unknown official status"
                else:
                    reason = "internal breach estimate"
                notes.append(
                    f"VOLUME CAP BLOCK [{req.symbol}]: {effective_waiver} dark routing blocked "
                    f"({reason}); register={register_status}, estimate={estimate_status}, "
                    f"Union RPW share {union_share:.4f}% vs cap {cap_pct:.2f}%. "
                    f"Rerouted to LIT_VENUE."
                )
                logger.warning(notes[-1])
            else:
                allowed = True
                routed = "DARK_RPW" if effective_waiver == WAIVER_RPW else "DARK_NTW"
                published = register.published_on if register is not None else "n/a"
                notes.append(
                    f"DARK ROUTE APPROVED [{req.symbol}]: not suspended in the ESMA register "
                    f"published {published}; Union RPW share {union_share:.4f}% vs cap "
                    f"{cap_pct:.2f}% (headroom EUR {headroom:,.2f}). Routed to {routed}."
                )
                logger.info(notes[-1])

        return VolumeCapAuditReport(
            order_id=req.order_id,
            isin=req.isin,
            symbol=req.symbol,
            regime=regime,
            cap_pct=cap_pct,
            union_rpw_share_pct=union_share,
            venue_rpw_share_pct=venue_share,
            rpw_headroom_eur=headroom,
            internal_estimate_status=estimate_status,
            official_register_status=register_status,
            suspension_end_date=suspension_end,
            effective_waiver_type=effective_waiver,
            is_cap_applicable=cap_applies,
            is_dark_routing_allowed=allowed,
            final_routed_venue_type=routed,
            audit_notes=" | ".join(notes),
        )

    @staticmethod
    def _resolve_lis_eligibility(req: SorOrderRouteRequest) -> Tuple[str, str]:
        """
        Confirm a claimed LIS waiver is actually available for this order size.

        An order is outside Article 5 because it is executed under the
        Article 4(1)(c) waiver -- not merely because it happens to be large. A
        LIS claim below the instrument's threshold is downgraded to the
        reference price waiver and re-evaluated against the cap, rather than
        being waved through as exempt.
        """
        if req.intended_waiver_type != WAIVER_LIS:
            return req.intended_waiver_type, ""
        threshold = float(req.lis_threshold_eur)  # presence guaranteed by __post_init__
        if req.order_val_eur >= threshold:
            return WAIVER_LIS, (
                f"LIS ELIGIBLE [{req.symbol}]: order EUR {req.order_val_eur:,.2f} >= threshold "
                f"EUR {threshold:,.2f} (Art. 4(1)(c)); outside the Article 5 cap."
            )
        return WAIVER_RPW, (
            f"LIS CLAIM REJECTED [{req.symbol}]: order EUR {req.order_val_eur:,.2f} < threshold "
            f"EUR {threshold:,.2f}. The order is not large in scale; re-evaluated under the "
            f"reference price waiver and subject to the volume cap."
        )

    def _official_status(
        self,
        *,
        register: Optional[EsmaSuspensionRegister],
        isin: str,
        as_of: date,
        evaluate: bool,
    ) -> Tuple[str, Optional[date], str]:
        """
        Resolve the ESMA-published suspension status for ``isin`` on ``as_of``.

        Returns ``NOT_APPLICABLE`` without consulting the register when the
        order's waiver is outside Article 5 -- the register says nothing about
        LIS, OMF or (post-2024) negotiated trades.
        """
        if not evaluate:
            return REGISTER_NOT_APPLICABLE, None, ""
        if register is None:
            return REGISTER_NOT_SUPPLIED, None, (
                "REGISTER MISSING: no ESMA suspension file supplied; the official status is "
                "unknown and the capped waiver is blocked (fail closed). Load the quarterly "
                "Volume Cap suspension file from the ESMA register."
            )
        age = register.age_days(as_of)
        if age < 0:
            raise ValueError(
                f"ESMA register is published {register.published_on}, after as_of {as_of}; a "
                f"future-dated register cannot be applied to this order."
            )
        if age > self.max_register_age_days:
            return REGISTER_STALE, None, (
                f"REGISTER STALE: file published {register.published_on} is {age} days old "
                f"(> {self.max_register_age_days}); ESMA publishes quarterly, so it cannot cover "
                f"{as_of}. Capped waiver blocked (fail closed)."
            )
        active = register.active_suspension(isin, as_of)
        if active is None:
            return REGISTER_NOT_SUSPENDED, None, ""
        return REGISTER_SUSPENDED, active.suspension_end_date, (
            f"ESMA SUSPENSION ACTIVE [{isin}]: reference price waiver suspended "
            f"{active.suspension_start_date} to {active.suspension_end_date} inclusive "
            f"(MiFIR Art. 5(1))."
        )
