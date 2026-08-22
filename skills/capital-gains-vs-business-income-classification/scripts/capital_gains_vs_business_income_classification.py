"""Jurisdiction-aware classification of closed trades into tax buckets.

The capital-gains-versus-business-income question does not have a single
cross-border answer, and the *categories themselves* differ by jurisdiction:

* **India** splits business income into *speculative* and *non-speculative*
  (Income-tax Act, 1961, s.43(5)), and splits capital gains by holding period
  (s.2(42A)).
* **United States** has no speculative-business bucket at all. A trader's
  securities gains stay *capital* (short- or long-term) unless a s.475(f)
  mark-to-market election converts them to *ordinary* income.
* **Canada** has no holding-period split whatsoever. Every capital gain is
  taxed the same way regardless of how long the property was held; the only
  question is capital account versus income account.

Applying one jurisdiction's taxonomy to another produces categories that do
not exist in the destination tax code, so this module refuses to guess: the
jurisdiction and the relevant elections are explicit inputs.

This module classifies. It does not compute tax payable, apply rates, net
losses across buckets, or handle wash-sale / superficial-loss adjustments --
see the related skills listed in ``SKILL.md``.
"""

from __future__ import annotations

import calendar
import datetime
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, List, Mapping, Optional, Tuple, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MoneyInput = Union[Decimal, int, str, float]


class Jurisdiction(Enum):
    """Tax jurisdiction whose taxonomy is applied. There is no generic mode."""

    INDIA = "IN"
    UNITED_STATES = "US"
    CANADA = "CA"


class AssetClass(Enum):
    EQUITY = 1
    DERIVATIVE = 2  # Futures & options


class TaxCategory(Enum):
    # India (Income-tax Act, 1961)
    SPECULATIVE_BUSINESS = 1
    NON_SPECULATIVE_BUSINESS = 2
    # India / United States -- holding-period split
    SHORT_TERM_CAPITAL_GAINS = 3
    LONG_TERM_CAPITAL_GAINS = 4
    # Canada -- capital account; no holding-period split exists
    CAPITAL_GAINS = 5
    # Canada income account, and US ordinary income under a s.475(f) election
    BUSINESS_INCOME = 6
    # US IRC s.1256 contracts -- statutory 60/40 split, handled by a separate skill
    SECTION_1256_60_40 = 7


#: Categories each jurisdiction is capable of emitting. Used to assert that a
#: classification never leaks a foreign concept into a domestic ledger.
CATEGORIES_BY_JURISDICTION: Mapping[Jurisdiction, frozenset] = {
    Jurisdiction.INDIA: frozenset({
        TaxCategory.SPECULATIVE_BUSINESS,
        TaxCategory.NON_SPECULATIVE_BUSINESS,
        TaxCategory.SHORT_TERM_CAPITAL_GAINS,
        TaxCategory.LONG_TERM_CAPITAL_GAINS,
    }),
    Jurisdiction.UNITED_STATES: frozenset({
        TaxCategory.SHORT_TERM_CAPITAL_GAINS,
        TaxCategory.LONG_TERM_CAPITAL_GAINS,
        TaxCategory.BUSINESS_INCOME,
        TaxCategory.SECTION_1256_60_40,
    }),
    Jurisdiction.CANADA: frozenset({
        TaxCategory.CAPITAL_GAINS,
        TaxCategory.BUSINESS_INCOME,
    }),
}

#: Exchange-local timezone used to decide the *session date* of a fill. Trade
#: timestamps are frequently stored in UTC, where a US session that ends at
#: 16:00 ET falls on the following UTC date -- an intraday round trip would
#: then look like an overnight hold.
DEFAULT_SESSION_TIMEZONE: Mapping[Jurisdiction, str] = {
    Jurisdiction.INDIA: "Asia/Kolkata",
    Jurisdiction.UNITED_STATES: "America/New_York",
    Jurisdiction.CANADA: "America/Toronto",
}

#: India, s.2(42A) as amended by the Finance (No. 2) Act, 2024 (w.e.f.
#: 23 July 2024): 12 months for listed securities, 24 months for everything
#: else. Long-term requires holding for *more than* this many months.
INDIA_LONG_TERM_MONTHS_LISTED = 12
INDIA_LONG_TERM_MONTHS_UNLISTED = 24

#: United States, IRC s.1222: long-term requires holding for *more than* one year.
US_LONG_TERM_MONTHS = 12


class TaxClassificationError(ValueError):
    """Raised when a trade cannot be classified from the data supplied."""


@dataclass(frozen=True)
class TaxElections:
    """Elections and filing positions that change how a trade is classified.

    Every field defaults to ``False``, which is the *no election made* state.
    None of these can be inferred from trade data -- they are filing positions
    the taxpayer takes and must then apply consistently year on year.
    """

    #: India: equity held as stock-in-trade rather than as an investment. Under
    #: CBDT Circular No. 6/2016 the taxpayer may take this position irrespective
    #: of holding period, and must then hold it in later assessment years too.
    india_equity_as_stock_in_trade: bool = False

    #: United States: a valid and timely IRC s.475(f) mark-to-market election,
    #: which converts a trader's securities gains and losses to *ordinary*
    #: income reported on Form 4797 instead of capital gains on Schedule D.
    us_section_475f_elected: bool = False

    #: Canada: an ITA s.39(4) election (Form T123) deeming every Canadian
    #: security the taxpayer owns to be capital property. Unavailable to traders
    #: and dealers in securities under s.39(5), and it cannot be rescinded.
    canada_section_39_4_elected: bool = False

    #: Canada: a speculator reporting commodity-futures gains and losses on
    #: capital account per IT-346R, permitted only if followed consistently
    #: from year to year.
    canada_derivatives_on_capital_account: bool = False


@dataclass
class ClosedTrade:
    """A round-trip position that has been fully closed.

    Args:
        trade_id: Stable identifier used in log lines and error messages.
        symbol: Instrument identifier.
        asset_class: Equity or derivative.
        open_time: Acquisition timestamp. Naive or timezone-aware, but must
            match ``close_time`` in awareness.
        close_time: Disposal timestamp.
        net_pnl: Realised profit or loss. Floats are accepted and converted via
            ``Decimal(str(value))``; supply ``Decimal`` or ``str`` directly to
            keep ledger totals exact.
        settled_without_delivery: Whether the position was squared off without
            actual delivery of the scrip. This -- not the calendar -- is the
            statutory test for a speculative transaction in India. Leave
            ``None`` to fall back to a same-session-date proxy.
        is_listed: Whether the security is listed on a recognised stock
            exchange. Drives India's 12- versus 24-month holding threshold, and
            whether a derivative falls inside the s.43(5) proviso (d) carve-out.
        is_section_1256_contract: US only. Whether the contract is an IRC
            s.1256 contract (regulated futures, broad-based index options,
            certain foreign currency contracts, dealer equity options).
    """

    trade_id: str
    symbol: str
    asset_class: AssetClass
    open_time: datetime.datetime
    close_time: datetime.datetime
    net_pnl: MoneyInput
    settled_without_delivery: Optional[bool] = None
    is_listed: bool = True
    is_section_1256_contract: bool = False


@dataclass(frozen=True)
class TradeClassification:
    """The classification of a single trade, with the reasoning retained."""

    trade_id: str
    symbol: str
    category: TaxCategory
    net_pnl: Decimal
    holding_days: int
    rationale: str


def _to_decimal(value: MoneyInput, trade_id: str) -> Decimal:
    """Normalise a PnL amount to ``Decimal``, rejecting NaN and infinities.

    Tax ledgers are summed and filed, so binary float drift is not acceptable
    in the accumulator. Floats are routed through ``str`` so that ``0.1``
    becomes ``Decimal("0.1")`` rather than its binary expansion.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TaxClassificationError(
                f"Trade {trade_id}: net_pnl is not a finite number ({value!r}).")
        value = str(value)
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TaxClassificationError(
            f"Trade {trade_id}: net_pnl {value!r} is not a valid decimal amount."
        ) from exc
    if not result.is_finite():
        raise TaxClassificationError(
            f"Trade {trade_id}: net_pnl is not a finite number ({value!r}).")
    return result


def _add_months(start: datetime.date, months: int) -> datetime.date:
    """Return the calendar date ``months`` after ``start``, clamping the day.

    Holding periods in both India and the US are expressed in calendar months
    and years, not in a fixed number of days, so 29 February + 12 months is
    28 February of the following year.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def _held_longer_than_months(
    open_date: datetime.date, close_date: datetime.date, months: int
) -> bool:
    """Whether the position was held for *more than* ``months`` calendar months.

    Both the Indian and US tests are strict: India's s.2(42A) makes an asset
    short-term when held "for not more than twelve months", and IRS Topic 409
    makes a gain long-term only when the asset is held "more than one year",
    counting from the day *after* acquisition through the disposal date. So an
    asset bought on 1 January and sold on 1 January the next year is still
    short-term; sold on 2 January, it is long-term.
    """
    return close_date > _add_months(open_date, months)


class TaxClassificationEngine:
    """Classifies closed trades into the tax categories of one jurisdiction.

    The engine is deterministic and holds no mutable state between calls, so a
    single instance may be reused across a whole ledger.

    Args:
        jurisdiction: Whose tax taxonomy to apply. Required -- there is no
            jurisdiction-neutral classification of trading income.
        elections: Filing positions the taxpayer has taken. See
            :class:`TaxElections`.
        session_timezone: IANA timezone used to derive the session date of a
            fill from an aware timestamp. Defaults to the jurisdiction's
            primary exchange timezone.
    """

    def __init__(
        self,
        jurisdiction: Jurisdiction,
        elections: Optional[TaxElections] = None,
        session_timezone: Optional[str] = None,
    ) -> None:
        if not isinstance(jurisdiction, Jurisdiction):
            raise TaxClassificationError(
                f"jurisdiction must be a Jurisdiction member, got {jurisdiction!r}.")
        self.jurisdiction = jurisdiction
        self.elections = elections if elections is not None else TaxElections()
        tz_name = session_timezone or DEFAULT_SESSION_TIMEZONE[jurisdiction]
        self.session_timezone = ZoneInfo(tz_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_trade(self, trade: ClosedTrade) -> TaxCategory:
        """Classify one closed trade. See :meth:`explain_trade` for reasoning."""
        return self.explain_trade(trade).category

    def explain_trade(self, trade: ClosedTrade) -> TradeClassification:
        """Classify one closed trade and return the reasoning alongside it."""
        net_pnl = _to_decimal(trade.net_pnl, trade.trade_id)
        open_date, close_date = self._session_dates(trade)
        holding_days = (close_date - open_date).days

        if self.jurisdiction is Jurisdiction.INDIA:
            category, rationale = self._classify_india(trade, open_date, close_date)
        elif self.jurisdiction is Jurisdiction.UNITED_STATES:
            category, rationale = self._classify_united_states(
                trade, open_date, close_date)
        elif self.jurisdiction is Jurisdiction.CANADA:
            category, rationale = self._classify_canada(trade)
        else:  # pragma: no cover - guarded by the constructor
            raise TaxClassificationError(
                f"Unsupported jurisdiction: {self.jurisdiction!r}")

        if category not in CATEGORIES_BY_JURISDICTION[self.jurisdiction]:  # pragma: no cover
            raise TaxClassificationError(
                f"Internal error: {category.name} is not a valid category in "
                f"{self.jurisdiction.name}.")

        return TradeClassification(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            category=category,
            net_pnl=net_pnl,
            holding_days=holding_days,
            rationale=rationale,
        )

    def classify_portfolio(
        self, trades: List[ClosedTrade]
    ) -> List[TradeClassification]:
        """Classify every trade in a ledger, preserving input order."""
        return [self.explain_trade(trade) for trade in trades]

    def aggregate_pnl(self, trades: List[ClosedTrade]) -> Dict[TaxCategory, Decimal]:
        """Sum net PnL per tax category.

        Only the categories valid in this engine's jurisdiction appear in the
        result, so a caller cannot silently read a zero out of a bucket that
        does not exist in the applicable tax code.
        """
        summary: Dict[TaxCategory, Decimal] = {
            category: Decimal("0")
            for category in TaxCategory
            if category in CATEGORIES_BY_JURISDICTION[self.jurisdiction]
        }
        for classification in self.classify_portfolio(trades):
            summary[classification.category] += classification.net_pnl
        return summary

    # ------------------------------------------------------------------
    # Input normalisation
    # ------------------------------------------------------------------

    def _session_dates(
        self, trade: ClosedTrade
    ) -> Tuple[datetime.date, datetime.date]:
        """Return the exchange-local open and close dates, after validation."""
        if not trade.trade_id:
            raise TaxClassificationError(
                "ClosedTrade.trade_id must be a non-empty string.")
        for label, value in (
            ("open_time", trade.open_time),
            ("close_time", trade.close_time),
        ):
            if not isinstance(value, datetime.datetime):
                raise TaxClassificationError(
                    f"Trade {trade.trade_id}: {label} must be a datetime, "
                    f"got {value!r}.")
        if not isinstance(trade.asset_class, AssetClass):
            raise TaxClassificationError(
                f"Trade {trade.trade_id}: unknown asset class {trade.asset_class!r}.")

        open_aware = trade.open_time.tzinfo is not None
        close_aware = trade.close_time.tzinfo is not None
        if open_aware != close_aware:
            raise TaxClassificationError(
                f"Trade {trade.trade_id}: open_time and close_time must both be "
                "timezone-aware or both be naive; comparing them otherwise is "
                "undefined.")

        if trade.close_time < trade.open_time:
            raise TaxClassificationError(
                f"Trade {trade.trade_id} has a negative holding period "
                f"({trade.open_time} -> {trade.close_time}).")

        if open_aware:
            open_local = trade.open_time.astimezone(self.session_timezone)
            close_local = trade.close_time.astimezone(self.session_timezone)
        else:
            logger.debug(
                "Trade %s has naive timestamps; assuming they are already in %s.",
                trade.trade_id, self.session_timezone,
            )
            open_local, close_local = trade.open_time, trade.close_time
        return open_local.date(), close_local.date()

    def _is_speculative_settlement(
        self,
        trade: ClosedTrade,
        open_date: datetime.date,
        close_date: datetime.date,
    ) -> bool:
        """Whether an equity trade was settled otherwise than by actual delivery.

        The statutory test in s.43(5) is settlement without delivery. When the
        caller has not supplied that fact, fall back to the same-session-date
        proxy that is correct for ordinary intraday square-offs, and say so.
        """
        if trade.settled_without_delivery is not None:
            return trade.settled_without_delivery
        logger.warning(
            "Trade %s: settled_without_delivery not supplied; falling back to a "
            "same-session-date proxy for the s.43(5) delivery test. This proxy "
            "misclassifies delivery-based same-day trades and BTST positions.",
            trade.trade_id,
        )
        return open_date == close_date

    # ------------------------------------------------------------------
    # Per-jurisdiction rules
    # ------------------------------------------------------------------

    def _classify_india(
        self,
        trade: ClosedTrade,
        open_date: datetime.date,
        close_date: datetime.date,
    ) -> Tuple[TaxCategory, str]:
        if trade.asset_class is AssetClass.DERIVATIVE:
            if not trade.is_listed:
                logger.warning(
                    "Trade %s: derivative not traded on a recognised stock "
                    "exchange, so the s.43(5) proviso (d) carve-out does not apply.",
                    trade.trade_id,
                )
                return (
                    TaxCategory.SPECULATIVE_BUSINESS,
                    "Derivative settled otherwise than by delivery and outside the "
                    "s.43(5) proviso (d) recognised-exchange carve-out.",
                )
            return (
                TaxCategory.NON_SPECULATIVE_BUSINESS,
                "Eligible exchange-traded derivative transaction excluded from "
                "'speculative transaction' by s.43(5) proviso (d).",
            )

        if self._is_speculative_settlement(trade, open_date, close_date):
            return (
                TaxCategory.SPECULATIVE_BUSINESS,
                "Equity position settled otherwise than by actual delivery -- a "
                "speculative transaction under s.43(5).",
            )

        if self.elections.india_equity_as_stock_in_trade:
            return (
                TaxCategory.NON_SPECULATIVE_BUSINESS,
                "Delivery-based equity held as stock-in-trade, so business income "
                "irrespective of holding period (CBDT Circular No. 6/2016).",
            )

        threshold = (
            INDIA_LONG_TERM_MONTHS_LISTED if trade.is_listed
            else INDIA_LONG_TERM_MONTHS_UNLISTED
        )
        if _held_longer_than_months(open_date, close_date, threshold):
            return (
                TaxCategory.LONG_TERM_CAPITAL_GAINS,
                f"Delivery-based equity held for more than {threshold} months "
                "(s.2(42A) as amended by the Finance (No. 2) Act, 2024).",
            )
        return (
            TaxCategory.SHORT_TERM_CAPITAL_GAINS,
            f"Delivery-based equity held for not more than {threshold} months "
            "(s.2(42A)).",
        )

    def _classify_united_states(
        self,
        trade: ClosedTrade,
        open_date: datetime.date,
        close_date: datetime.date,
    ) -> Tuple[TaxCategory, str]:
        if trade.asset_class is AssetClass.DERIVATIVE and trade.is_section_1256_contract:
            return (
                TaxCategory.SECTION_1256_60_40,
                "IRC s.1256 contract -- statutory 60/40 split, reported on "
                "Form 6781.",
            )

        if self.elections.us_section_475f_elected:
            return (
                TaxCategory.BUSINESS_INCOME,
                "IRC s.475(f) mark-to-market election in force -- ordinary gain or "
                "loss on Form 4797 rather than capital gain.",
            )

        if _held_longer_than_months(open_date, close_date, US_LONG_TERM_MONTHS):
            return (
                TaxCategory.LONG_TERM_CAPITAL_GAINS,
                "Held more than one year (IRC s.1222; IRS Topic 409).",
            )
        return (
            TaxCategory.SHORT_TERM_CAPITAL_GAINS,
            "Held one year or less. US tax law has no speculative-business "
            "category: an intraday round trip is still a short-term capital gain "
            "absent a s.475(f) election.",
        )

    def _classify_canada(self, trade: ClosedTrade) -> Tuple[TaxCategory, str]:
        if trade.asset_class is AssetClass.DERIVATIVE:
            if self.elections.canada_derivatives_on_capital_account:
                return (
                    TaxCategory.CAPITAL_GAINS,
                    "Speculator reporting commodity futures on capital account, "
                    "applied consistently year to year (IT-346R).",
                )
            return (
                TaxCategory.BUSINESS_INCOME,
                "Commodity futures on income account (IT-346R default for a "
                "taxpayer not consistently reporting on capital account).",
            )

        if self.elections.canada_section_39_4_elected:
            return (
                TaxCategory.CAPITAL_GAINS,
                "ITA s.39(4) election deems every Canadian security to be capital "
                "property. Canada applies no holding-period split.",
            )
        return (
            TaxCategory.BUSINESS_INCOME,
            "Securities on income account (IT-479R factors) with no s.39(4) "
            "election in force; fully taxable rather than capital.",
        )
