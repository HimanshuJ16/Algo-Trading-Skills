"""EU Short Selling Regulation (SSR) net short position disclosure engine.

Decides, for a single share issuer, (a) whether a net short position triggers a
private notification to the relevant competent authority (RCA) or a public
disclosure under Regulation (EU) No 236/2012 Arts. 5 and 6, and (b) whether a
short sale order may be entered at all under the Art. 12 uncovered-short-sale
prohibition. The two questions are answered *independently* -- see "Design
notes" below.

Regulatory anchors (full citations in ``references/standards.md``):

* **Art. 5(2)** -- the relevant notification threshold is 0.1% of the issued
  share capital of the company concerned and each 0.1% above that. The original
  0.2% figure was permanently lowered to 0.1% by Commission Delegated Regulation
  (EU) 2022/27, applicable from 31 January 2022.
* **Art. 6(2)** -- the relevant publication threshold is 0.5% of the issued
  share capital and each 0.1% above that.
* **Art. 9(2)** -- the relevant time for the calculation is midnight at the end
  of the trading day on which the person has the position; the notification or
  disclosure is made not later than 15.30 on the following trading day.
* **Art. 9(2)/(3) as read by ESMA Q&A A5.2 and A9.3** -- that 15.30 is *local
  time in the Member State of the relevant competent authority*, on that Member
  State's trading-day calendar. It is **not** 15:30 CET everywhere: 15:30 in
  Helsinki (EET/EEST) is 12:30 UTC while 15:30 in Berlin (CET/CEST) is 13:30
  UTC, so a firm filing "15:30 CET" to a Finnish NCA files an hour late.
* **ESMA Q&A A5.6** -- the position to report is rounded to two decimal places
  *by truncating* the remaining decimals, and the thresholds are tested on that
  basis (0.1987% truncates to 0.19%).
* **ESMA Q&A A5.7** -- a notification is required where the position reaches,
  exceeds or *falls below* a relevant threshold; a change that stays inside an
  already-notified band requires nothing further.
* **Art. 12(1)** -- a short sale of a share may only be entered into where the
  seller has borrowed the share, has an agreement to borrow it, or has a
  third-party arrangement confirming the share has been located together with
  measures giving a reasonable expectation of settlement when due. Commission
  Implementing Regulation (EU) No 827/2012 Arts. 5-7 specify the arrangement
  types and require evidence in a durable medium.
* **Art. 16** (and ESMA Q&A A4.4/A4.5) -- shares whose principal trading venue
  is in a third country are outside Arts. 5, 6 and 12; ESMA publishes the
  exempted-shares list. **Art. 17** -- market making and primary market
  operations are exempt, on 30 calendar days' prior written notice to the home
  competent authority.

Design notes:

* **Art. 12 never suppresses Arts. 5/6.** A locate gap is an execution problem;
  it does not cancel a disclosure obligation. An engine that returns
  "NAKED_SHORT_BAN_BREACH" and stops would silently drop the public disclosure
  owed on a 0.8% position.
* **Position vs. sale.** The Arts. 5/6 net short position is a delta-adjusted
  portfolio measure that includes derivatives, ETF look-through and ADRs/GDRs
  (Delegated Regulation (EU) No 918/2012 Annex II Part 1; ESMA Q&A A4.6/A4.7).
  Art. 12 bites on the act of selling a *share* short. A position built purely
  from options is not an uncovered short sale, and an uncovered sale by an
  account that is net long is still an Art. 12 breach. They are separate calls:
  ``evaluate_short_position_disclosure`` and ``evaluate_short_sale_order``.
* **Delta adjustment is the caller's job.** ``long_shares_qty`` and
  ``short_shares_qty`` are delta-adjusted share equivalents, not raw share
  counts. This module does not price options.
* **Fail closed on the deadline.** Without an RCA timezone and a trading-day
  calendar for that Member State the deadline is reported as unconfigured
  rather than defaulted to CET and weekdays. A missing deadline is a prompt; a
  wrong deadline is a late filing.
* Threshold arithmetic runs on integers in units of 0.01 percentage points, so
  no boundary decision depends on binary floating point.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Arts. 5/6 reporting status -------------------------------------------------
STATUS_BELOW_THRESHOLDS = "BELOW_REPORTING_THRESHOLDS"
STATUS_PRIVATE_NCA = "PRIVATE_NCA_NOTIFICATION_REQUIRED"
STATUS_PUBLIC_DISCLOSURE = "PUBLIC_DISCLOSURE_REQUIRED"
STATUS_OUT_OF_SCOPE = "OUT_OF_SSR_SCOPE"

# --- What the position holder must actually do today ----------------------------
ACTION_NONE = "NO_ACTION"
ACTION_NOTIFY_NCA = "NOTIFY_NCA"
ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY = "NOTIFY_NCA_AND_DISCLOSE_PUBLICLY"

# --- Art. 12 uncovered short sale -----------------------------------------------
ART12_COVERED = "COVERED"
ART12_BREACH = "NAKED_SHORT_BAN_BREACH"
ART12_NOT_APPLICABLE = "ART12_NOT_APPLICABLE"

# Art. 12(1)(a)-(c) limbs, plus the absence of any of them.
COVER_BORROWED = "BORROWED"
COVER_AGREEMENT_TO_BORROW = "AGREEMENT_TO_BORROW"
COVER_LOCATE_ARRANGEMENT = "LOCATE_ARRANGEMENT"
COVER_NONE = "NONE"
_VALID_COVER = frozenset(
    {COVER_BORROWED, COVER_AGREEMENT_TO_BORROW, COVER_LOCATE_ARRANGEMENT, COVER_NONE}
)

# Art. 12 restricts short sales of *shares*. ESMA Q&A A4.6/A4.7: ADRs/GDRs and
# ETFs are not shares for Art. 12, though they do count towards Arts. 5/6.
INSTRUMENT_SHARE = "SHARE"
INSTRUMENT_ETF = "ETF"
INSTRUMENT_DEPOSITARY_RECEIPT = "DEPOSITARY_RECEIPT"
INSTRUMENT_DERIVATIVE = "DERIVATIVE"

# Why a deadline could not be pinned to an instant.
DEADLINE_OK = "COMPUTED"
DEADLINE_NO_ACTION = "NO_ACTION_DUE"
DEADLINE_NO_TIMEZONE = "RCA_TIMEZONE_NOT_CONFIGURED"
DEADLINE_NO_CALENDAR = "TRADING_CALENDAR_NOT_CONFIGURED"
DEADLINE_NO_POSITION_DATE = "POSITION_DATE_NOT_SUPPLIED"

#: Art. 9(2) filing cut-off, in the local time of the RCA's Member State.
FILING_CUTOFF_LOCAL_TIME = time(15, 30)

#: Working unit: one hundredth of a percentage point (0.01%).
_CPCT = Decimal("0.01")

DEFAULT_PRIVATE_NCA_THRESHOLD_PCT = 0.10
DEFAULT_PUBLIC_DISCLOSURE_THRESHOLD_PCT = 0.50
DEFAULT_THRESHOLD_STEP_PCT = 0.10


def next_weekday_excluding_holidays(position_day: date) -> date:
    """Return the next Mon-Fri day after ``position_day``.

    A deliberately named stopgap for tests and demos. It knows nothing about
    Member State public holidays, exchange closures or half-days, so it will
    return the wrong filing date around, for example, Easter Monday. Supply the
    RCA Member State's real trading calendar to
    ``EuShortSellingRegulationEngine(next_trading_day=...)`` before using this
    engine to drive an actual filing.
    """
    nxt = position_day + timedelta(days=1)
    while nxt.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        nxt += timedelta(days=1)
    return nxt


@dataclass(frozen=True)
class EquityShortPositionState:
    """A single issuer's end-of-day net short position, as of ``position_date``.

    ``long_shares_qty`` and ``short_shares_qty`` are **delta-adjusted share
    equivalents** for that issuer (Delegated Regulation (EU) No 918/2012
    Annex II Part 1), not raw share counts: cash positions have delta 1, every
    derivative contributes its delta-adjusted equivalent, and ETF/index exposure
    enters through look-through. Fractional values are expected and accepted.

    ``issued_share_capital_qty`` is the total of ordinary *and* preference shares
    issued, all classes, irrespective of voting rights (Art. 2(1)(l); ESMA
    Q&A A6.6).

    ``previously_notified_percentage`` is the percentage figure last notified or
    disclosed for this issuer by this reporting entity, or ``None`` if there is
    no prior notification on record. ``None`` is treated conservatively: any
    position at or above the notification threshold is reported as due.
    """

    isin: str
    symbol: str
    issued_share_capital_qty: int
    long_shares_qty: float
    short_shares_qty: float
    has_valid_locate_agreement: bool
    position_date: Optional[date] = None
    relevant_competent_authority: str = "UNSPECIFIED"
    nca_timezone: Optional[str] = None
    previously_notified_percentage: Optional[float] = None
    is_exempt_principal_venue_outside_union: bool = False
    is_market_making_exempt: bool = False


@dataclass(frozen=True)
class ShortSaleOrderIntent:
    """A proposed short sale, evaluated against Art. 12 before it is dispatched."""

    isin: str
    symbol: str
    order_qty: float
    instrument_type: str = INSTRUMENT_SHARE
    covering_arrangement: str = COVER_NONE
    #: Reference to the durable-medium evidence required by ITS 827/2012 Art. 7.
    locate_evidence_reference: Optional[str] = None
    is_exempt_principal_venue_outside_union: bool = False
    is_market_making_exempt: bool = False


@dataclass(frozen=True)
class UncoveredShortSaleDecision:
    """Art. 12 verdict for one order. ``is_execution_allowed`` is the gate."""

    isin: str
    symbol: str
    order_qty: float
    art12_status: str
    is_execution_allowed: bool
    rejection_reason: Optional[str]
    audit_notes: str


@dataclass(frozen=True)
class EuSsrDisclosureReport:
    """Arts. 5/6 verdict for one issuer's end-of-day net short position.

    ``net_short_percentage`` is the ESMA-truncated two-decimal figure that goes
    on the filing; ``net_short_percentage_exact`` keeps the unrounded value for
    reconciliation only. ``art12_status`` is an advisory read of the position's
    coverage flag and never changes the reporting status.
    """

    isin: str
    symbol: str
    net_short_shares_qty: float
    net_short_percentage: float
    net_short_percentage_exact: float
    reporting_status: str
    disclosure_action: str
    current_threshold_pct: Optional[float]
    previous_threshold_pct: Optional[float]
    is_short_execution_allowed: bool
    art12_status: str
    notification_deadline_local: Optional[datetime]
    notification_deadline_basis: str
    reporting_deadline_description: str
    audit_notes: str


class EuShortSellingRegulationEngine:
    """Evaluates EU SSR disclosure obligations and the Art. 12 execution gate.

    Thresholds are configurable because emergency measures under Arts. 18-23 can
    change them and because national regimes outside the Union use different
    figures; the defaults are the Arts. 5(2)/6(2) share thresholds in force since
    31 January 2022. Sovereign debt (Art. 7) and sovereign CDS (Art. 14) are
    **out of scope** for this engine -- their thresholds are set per issuer by
    ESMA and are not percentages of issued share capital.

    ``next_trading_day`` must map a position date to the following trading day in
    the Member State of the relevant competent authority. Left unset, no deadline
    instant is produced.
    """

    def __init__(
        self,
        private_nca_threshold_pct: float = DEFAULT_PRIVATE_NCA_THRESHOLD_PCT,
        public_disclosure_threshold_pct: float = DEFAULT_PUBLIC_DISCLOSURE_THRESHOLD_PCT,
        threshold_step_pct: float = DEFAULT_THRESHOLD_STEP_PCT,
        next_trading_day: Optional[Callable[[date], date]] = None,
    ) -> None:
        self.private_nca_threshold_pct = _validate_threshold(
            private_nca_threshold_pct, "private_nca_threshold_pct"
        )
        self.public_disclosure_threshold_pct = _validate_threshold(
            public_disclosure_threshold_pct, "public_disclosure_threshold_pct"
        )
        self.threshold_step_pct = _validate_threshold(
            threshold_step_pct, "threshold_step_pct"
        )
        if self.public_disclosure_threshold_pct < self.private_nca_threshold_pct:
            raise ValueError(
                "public_disclosure_threshold_pct must be >= private_nca_threshold_pct"
            )
        self.next_trading_day = next_trading_day

        self._private_c = _to_centipct(self.private_nca_threshold_pct)
        self._public_c = _to_centipct(self.public_disclosure_threshold_pct)
        self._step_c = _to_centipct(self.threshold_step_pct)

    # -- Arts. 5 and 6 -----------------------------------------------------------

    def evaluate_short_position_disclosure(
        self, pos: EquityShortPositionState
    ) -> EuSsrDisclosureReport:
        """Evaluate one issuer's end-of-day net short position.

        Returns the truncated percentage to file, the reporting status, whether a
        notification is actually due today (a position that has not left its
        already-notified band triggers nothing -- ESMA Q&A A5.7), and the filing
        deadline in the RCA's local time where one can be computed.
        """
        _validate_position(pos)

        net_short_qty = float(pos.short_shares_qty) - float(pos.long_shares_qty)
        exact_pct = _percentage(net_short_qty, pos.issued_share_capital_qty)
        current_c = _truncate_to_centipct(exact_pct)
        reportable_pct = float(Decimal(current_c) * _CPCT)

        if net_short_qty > pos.issued_share_capital_qty:
            logger.warning(
                "Net short position for %s (%s) exceeds issued share capital (%.4f%%) "
                "-- verify the delta-adjusted inputs and the ESMA issued-share-capital "
                "figure.",
                pos.symbol,
                pos.isin,
                float(exact_pct),
            )

        art12_status, execution_allowed = self._assess_position_coverage(
            pos, net_short_qty
        )

        if pos.is_exempt_principal_venue_outside_union or pos.is_market_making_exempt:
            return self._exempt_report(
                pos, net_short_qty, exact_pct, reportable_pct, art12_status, execution_allowed
            )

        current_band_c = self._band(current_c)
        previous_band_c: Optional[int] = None
        if pos.previously_notified_percentage is not None:
            previous_band_c = self._band(
                _truncate_to_centipct(
                    Decimal(str(float(pos.previously_notified_percentage)))
                )
            )

        if current_band_c is None:
            status = STATUS_BELOW_THRESHOLDS
        elif current_band_c >= self._public_c:
            status = STATUS_PUBLIC_DISCLOSURE
        else:
            status = STATUS_PRIVATE_NCA

        # ESMA Q&A A5.7: notify on reaching, exceeding *or falling below* a
        # threshold; nothing is owed for a move inside an already-notified band.
        # No prior notification on record is treated as "not yet notified".
        if pos.previously_notified_percentage is None:
            action_due = current_band_c is not None
        else:
            action_due = current_band_c != previous_band_c

        if not action_due:
            action = ACTION_NONE
        elif (current_band_c is not None and current_band_c >= self._public_c) or (
            previous_band_c is not None and previous_band_c >= self._public_c
        ):
            # Entering, moving within, or leaving the publication regime all
            # require the public register to be updated (Art. 6(2)).
            action = ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY
        else:
            action = ACTION_NOTIFY_NCA

        deadline, basis = self._filing_deadline(pos, action)
        deadline_text = _describe_deadline(pos, deadline, basis)
        notes = _compose_notes(
            pos,
            status,
            action,
            reportable_pct,
            current_band_c,
            previous_band_c,
            deadline_text,
        )

        if action == ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY:
            logger.warning(notes)
        elif action == ACTION_NOTIFY_NCA:
            logger.info(notes)
        else:
            logger.debug(notes)

        return EuSsrDisclosureReport(
            isin=pos.isin,
            symbol=pos.symbol,
            net_short_shares_qty=net_short_qty,
            net_short_percentage=reportable_pct,
            net_short_percentage_exact=float(exact_pct),
            reporting_status=status,
            disclosure_action=action,
            current_threshold_pct=_centipct_to_float(current_band_c),
            previous_threshold_pct=_centipct_to_float(previous_band_c),
            is_short_execution_allowed=execution_allowed,
            art12_status=art12_status,
            notification_deadline_local=deadline,
            notification_deadline_basis=basis,
            reporting_deadline_description=deadline_text,
            audit_notes=notes,
        )

    # -- Art. 12 -----------------------------------------------------------------

    def evaluate_short_sale_order(
        self, order: ShortSaleOrderIntent
    ) -> UncoveredShortSaleDecision:
        """Gate one short sale order against the Art. 12 uncovered-sale prohibition.

        Art. 12 applies to shares admitted to trading on a Union venue whose
        principal venue is not in a third country. ETFs and depositary receipts
        are not shares for this purpose (ESMA Q&A A4.6/A4.7), and derivatives are
        not share sales at all -- those instruments still feed the Arts. 5/6
        position, which is evaluated separately.
        """
        _validate_order(order)

        if order.instrument_type != INSTRUMENT_SHARE:
            notes = (
                f"ART. 12 NOT APPLICABLE [{order.symbol}]: instrument type "
                f"{order.instrument_type} is not a share for Art. 12 purposes; the "
                f"exposure still counts towards the Arts. 5/6 net short position."
            )
            return _allow(order, notes)

        if order.is_exempt_principal_venue_outside_union or order.is_market_making_exempt:
            reason = (
                "Art. 16 (principal trading venue outside the Union)"
                if order.is_exempt_principal_venue_outside_union
                else "Art. 17 (market making / primary market operations)"
            )
            return _allow(
                order, f"ART. 12 NOT APPLICABLE [{order.symbol}]: exempt under {reason}."
            )

        rejection: Optional[str] = None
        if order.covering_arrangement == COVER_NONE:
            rejection = "NO_ART12_COVERING_ARRANGEMENT"
        elif not order.locate_evidence_reference:
            # ITS 827/2012 Art. 7: the arrangements, confirmations and
            # instructions must be evidenced in a durable medium. An unevidenced
            # borrow or locate is not a demonstrable Art. 12 arrangement.
            rejection = "NO_DURABLE_MEDIUM_EVIDENCE"

        if rejection is not None:
            notes = (
                f"NAKED SHORT BAN BREACH [{order.symbol}]: short sale of "
                f"{order.order_qty:,.0f} shares blocked -- {rejection}. Art. 12(1) "
                f"requires the share to be borrowed, subject to an agreement to borrow, "
                f"or covered by a located-and-confirmed third-party arrangement "
                f"evidenced in a durable medium (ITS 827/2012 Arts. 5-7)."
            )
            logger.critical(notes)
            return UncoveredShortSaleDecision(
                isin=order.isin,
                symbol=order.symbol,
                order_qty=order.order_qty,
                art12_status=ART12_BREACH,
                is_execution_allowed=False,
                rejection_reason=rejection,
                audit_notes=notes,
            )

        notes = (
            f"ART. 12 COVERED [{order.symbol}]: {order.covering_arrangement} evidenced "
            f"by {order.locate_evidence_reference}."
        )
        return UncoveredShortSaleDecision(
            isin=order.isin,
            symbol=order.symbol,
            order_qty=order.order_qty,
            art12_status=ART12_COVERED,
            is_execution_allowed=True,
            rejection_reason=None,
            audit_notes=notes,
        )

    # -- internals ---------------------------------------------------------------

    def _band(self, centipct: int) -> Optional[int]:
        """Highest threshold reached, in 0.01% units, or None if below the first."""
        if centipct < self._private_c:
            return None
        steps = (centipct - self._private_c) // self._step_c
        return self._private_c + steps * self._step_c

    def _assess_position_coverage(
        self, pos: EquityShortPositionState, net_short_qty: float
    ) -> Tuple[str, bool]:
        """Advisory read of the position-level coverage flag.

        This is *not* the Art. 12 test -- Art. 12 bites on individual share sales,
        which ``evaluate_short_sale_order`` handles. A net short position built
        entirely from derivatives breaches nothing. The flag is surfaced so a desk
        carrying an uncovered short leg is told, without that ever suppressing the
        Arts. 5/6 obligation.
        """
        if net_short_qty <= 0:
            return ART12_NOT_APPLICABLE, True
        if pos.is_exempt_principal_venue_outside_union or pos.is_market_making_exempt:
            return ART12_NOT_APPLICABLE, True
        if pos.has_valid_locate_agreement:
            return ART12_COVERED, True
        logger.critical(
            "UNCOVERED SHORT EXPOSURE [%s]: net short %.2f share equivalents carry no "
            "recorded Art. 12 arrangement. Disclosure obligations are unaffected and "
            "still apply.",
            pos.symbol,
            net_short_qty,
        )
        return ART12_BREACH, False

    def _exempt_report(
        self,
        pos: EquityShortPositionState,
        net_short_qty: float,
        exact_pct: Decimal,
        reportable_pct: float,
        art12_status: str,
        execution_allowed: bool,
    ) -> EuSsrDisclosureReport:
        reason = (
            "Art. 16 (principal trading venue outside the Union)"
            if pos.is_exempt_principal_venue_outside_union
            else "Art. 17 (market making / primary market operations)"
        )
        notes = (
            f"OUT OF SSR SCOPE [{pos.symbol}]: exempt under {reason}; no Arts. 5/6 "
            f"notification or disclosure is owed. Net short {reportable_pct:.2f}% "
            f"recorded for internal monitoring only."
        )
        logger.info(notes)
        return EuSsrDisclosureReport(
            isin=pos.isin,
            symbol=pos.symbol,
            net_short_shares_qty=net_short_qty,
            net_short_percentage=reportable_pct,
            net_short_percentage_exact=float(exact_pct),
            reporting_status=STATUS_OUT_OF_SCOPE,
            disclosure_action=ACTION_NONE,
            current_threshold_pct=None,
            previous_threshold_pct=None,
            is_short_execution_allowed=execution_allowed,
            art12_status=art12_status,
            notification_deadline_local=None,
            notification_deadline_basis=DEADLINE_NO_ACTION,
            reporting_deadline_description="None -- exempt from Arts. 5/6.",
            audit_notes=notes,
        )

    def _filing_deadline(
        self, pos: EquityShortPositionState, action: str
    ) -> Tuple[Optional[datetime], str]:
        if action == ACTION_NONE:
            return None, DEADLINE_NO_ACTION
        if pos.position_date is None:
            return None, DEADLINE_NO_POSITION_DATE
        if not pos.nca_timezone:
            logger.warning(
                "No RCA timezone configured for %s (%s): the Art. 9(2) 15:30 cut-off is "
                "local time in the Member State of the relevant competent authority, so "
                "no deadline instant can be derived. Do not assume CET.",
                pos.symbol,
                pos.relevant_competent_authority,
            )
            return None, DEADLINE_NO_TIMEZONE
        if self.next_trading_day is None:
            logger.warning(
                "No trading-day calendar configured: cannot resolve the trading day "
                "following %s in the Member State of %s.",
                pos.position_date,
                pos.relevant_competent_authority,
            )
            return None, DEADLINE_NO_CALENDAR

        tzinfo = _resolve_timezone(pos.nca_timezone)
        filing_day = self.next_trading_day(pos.position_date)
        if isinstance(filing_day, datetime) or not isinstance(filing_day, date):
            raise TypeError("next_trading_day must return a datetime.date")
        if filing_day <= pos.position_date:
            raise ValueError(
                "next_trading_day returned a day on or before the position date"
            )
        return (
            datetime.combine(filing_day, FILING_CUTOFF_LOCAL_TIME, tzinfo=tzinfo),
            DEADLINE_OK,
        )


# --- module-level helpers ---------------------------------------------------------


def _allow(order: ShortSaleOrderIntent, notes: str) -> UncoveredShortSaleDecision:
    return UncoveredShortSaleDecision(
        isin=order.isin,
        symbol=order.symbol,
        order_qty=order.order_qty,
        art12_status=ART12_NOT_APPLICABLE,
        is_execution_allowed=True,
        rejection_reason=None,
        audit_notes=notes,
    )


def _resolve_timezone(name: str):
    """Resolve an IANA timezone, with an actionable error if the db is missing."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Unknown or unavailable IANA timezone {name!r}. On platforms without a "
            f"system tz database, install the 'tzdata' package."
        ) from exc


def _to_centipct(pct: float) -> int:
    """Convert a percentage to whole units of 0.01%, rejecting finer precision."""
    scaled = Decimal(str(pct)) / _CPCT
    if scaled != scaled.to_integral_value():
        raise ValueError(
            f"threshold {pct} is finer than 0.01% -- SSR thresholds and reported "
            f"positions are expressed to two decimal places (ESMA Q&A A5.6)"
        )
    return int(scaled)


def _truncate_to_centipct(pct: Decimal) -> int:
    """ESMA Q&A A5.6: truncate to two decimal places, discarding the remainder."""
    return int(pct.quantize(_CPCT, rounding=ROUND_DOWN) / _CPCT)


def _centipct_to_float(centipct: Optional[int]) -> Optional[float]:
    return None if centipct is None else float(Decimal(centipct) * _CPCT)


def _percentage(net_short_qty: float, issued: int) -> Decimal:
    return Decimal(str(net_short_qty)) / Decimal(issued) * Decimal(100)


def _validate_threshold(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive percentage, got {value!r}")
    return float(value)


def _validate_quantity(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if float(value) < 0:
        raise ValueError(
            f"{name} must be >= 0; net short direction is derived from "
            f"short_shares_qty - long_shares_qty, not from a signed quantity"
        )


def _validate_identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_position(pos: EquityShortPositionState) -> None:
    _validate_identifier(pos.isin, "isin")
    _validate_identifier(pos.symbol, "symbol")
    if isinstance(pos.issued_share_capital_qty, bool) or not isinstance(
        pos.issued_share_capital_qty, int
    ):
        raise TypeError("issued_share_capital_qty must be an int (share count)")
    if pos.issued_share_capital_qty <= 0:
        raise ValueError("issued_share_capital_qty must be > 0")
    _validate_quantity(pos.long_shares_qty, "long_shares_qty")
    _validate_quantity(pos.short_shares_qty, "short_shares_qty")
    if not isinstance(pos.has_valid_locate_agreement, bool):
        raise TypeError("has_valid_locate_agreement must be a bool")
    if pos.position_date is not None and (
        isinstance(pos.position_date, datetime) or not isinstance(pos.position_date, date)
    ):
        raise TypeError("position_date must be a datetime.date (the Art. 9(2) midnight)")
    if pos.previously_notified_percentage is not None:
        _validate_quantity(
            pos.previously_notified_percentage, "previously_notified_percentage"
        )


def _validate_order(order: ShortSaleOrderIntent) -> None:
    _validate_identifier(order.isin, "isin")
    _validate_identifier(order.symbol, "symbol")
    _validate_quantity(order.order_qty, "order_qty")
    if float(order.order_qty) <= 0:
        raise ValueError("order_qty must be > 0")
    if order.covering_arrangement not in _VALID_COVER:
        raise ValueError(
            f"covering_arrangement must be one of {sorted(_VALID_COVER)}, "
            f"got {order.covering_arrangement!r}"
        )


def _describe_deadline(
    pos: EquityShortPositionState, deadline: Optional[datetime], basis: str
) -> str:
    if basis == DEADLINE_NO_ACTION:
        return "None -- no notification or disclosure is due for this position."
    rule = (
        "Art. 9(2): not later than 15:30 local time in the Member State of the relevant "
        "competent authority, on the following trading day"
    )
    if basis == DEADLINE_OK and deadline is not None:
        return f"{deadline.isoformat()} ({pos.nca_timezone}) -- {rule}."
    hint = {
        DEADLINE_NO_TIMEZONE: "RCA timezone not configured -- do not assume CET",
        DEADLINE_NO_CALENDAR: "trading-day calendar for the RCA Member State not configured",
        DEADLINE_NO_POSITION_DATE: "position_date not supplied",
    }[basis]
    return f"Deadline not computed ({hint}). {rule}."


def _compose_notes(
    pos: EquityShortPositionState,
    status: str,
    action: str,
    reportable_pct: float,
    current_band_c: Optional[int],
    previous_band_c: Optional[int],
    deadline_text: str,
) -> str:
    band = _centipct_to_float(current_band_c)
    prev = _centipct_to_float(previous_band_c)
    if action == ACTION_NONE:
        if current_band_c is None:
            return (
                f"SSR no action [{pos.symbol}]: net short {reportable_pct:.2f}% is below "
                f"the notification threshold and no prior notification is open."
            )
        return (
            f"SSR no action [{pos.symbol}]: net short {reportable_pct:.2f}% remains "
            f"within the already-notified {band:.2f}% band (ESMA Q&A A5.7)."
        )
    verb = (
        "NOTIFY NCA AND DISCLOSE PUBLICLY"
        if action == ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY
        else "NOTIFY NCA"
    )
    if band is None:
        movement = "fell below the notification threshold"
    elif prev is None:
        movement = f"reached the {band:.2f}% threshold"
    elif band > prev:
        movement = f"crossed up into the {band:.2f}% band"
    else:
        movement = f"fell back into the {band:.2f}% band"
    prev_text = "no prior notification" if prev is None else f"previously {prev:.2f}%"
    return (
        f"SSR {verb} [{pos.symbol}]: net short {reportable_pct:.2f}% ({prev_text}) -- "
        f"{movement}; status {status}; RCA {pos.relevant_competent_authority}. "
        f"Deadline: {deadline_text}"
    )
