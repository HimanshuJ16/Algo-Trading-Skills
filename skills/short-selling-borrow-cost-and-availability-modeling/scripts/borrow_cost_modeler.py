"""
short-selling-borrow-cost-and-availability-modeling: securities-lending borrow cost
and locate-availability model for equity short strategies.

The module answers two separate questions and deliberately keeps them separate:

1. *Can this short be borrowed at all?* -- ``check_availability`` / ``can_short``.
2. *What does holding it cost?* -- ``calculate_borrow_cost`` and friends.

Fee mechanics implemented here follow the US securities-lending conventions that are
actually documented by primary sources:

- **Daily accrual on the daily mark, not on the entry price.** SIFMA 2017 Master
  Securities Loan Agreement Sec. 5.1: the Loan Fee is "computed daily on each Loan ...
  based on the aggregate Market Value of the Loaned Securities on the day for which
  such Loan Fee is being computed". Loans are marked to market daily (ibid. Sec. 9.1).
- **Accrual period is inclusive of the open date and exclusive of the return date**
  (ibid. Sec. 5.1), and runs on calendar days -- weekends accrue.
- **Collateral markup.** MSLA Sec. 9 requires collateral at the agreed Margin
  Percentage of the Loaned Securities' Market Value (at least 100%). US market
  practice is 102%; Interactive Brokers documents "a deposit equal to 102% of the
  prior day's settlement price, rounded up to the nearest whole dollar and then
  multiplied times the number of shares borrowed" and charges "(Value x Fee Rate)/360".
- **Day-count basis is 360, not 365,** for USD- and EUR-denominated loans (money-market
  ACT/360; IBKR's published divisor is 360). GBP-denominated loans use ACT/365 fixed.
  ``day_count_basis`` is therefore a constructor parameter, defaulting to 360.
- **Fees can be negative** (MSLA Sec. 5.1 contemplates a Loan Fee "less than zero"),
  and where collateral is cash the borrower's economics run through a rebate rather
  than a fee -- see ``short_proceeds_credit_rate``.

Limitations (documented, deliberate):

- **The utilization -> rate curve is a heuristic, not a market standard.** Utilization
  (on-loan quantity / lendable inventory) is a published securities-finance metric, but
  no public source defines a functional mapping from utilization to fee. The piecewise
  linear ramp here is a *research fallback for when only utilization is observable*.
  When a real quoted rate exists, put it on ``BorrowStatus.observed_borrow_rate`` and
  the heuristic is bypassed entirely. Production systems should always do this.
- **Rates are not constant over a holding period.** US equity loans are open/callable
  and reprice daily. ``calculate_borrow_cost`` prices a whole holding period at one
  rate and one price; it is an approximation. ``calculate_borrow_cost_schedule`` takes
  the realized per-day marks and rates and is the correct path for a backtest.
- **Availability is not the same as a locate.** A locate is a regulatory artifact under
  SEC Regulation SHO Rule 203(b)(1); this module models inventory sufficiency only. See
  ``us-reg-sho-short-sale-locate-requirements`` for the compliance gate.
- **Borrow status has no staleness model.** Availability and rates move intraday. The
  caller owns refresh cadence; a stale ``BorrowStatus`` will be used as if current.
- **No corporate-action, dividend-substitute or tax handling.** A short pays
  manufactured dividends; that is a separate P&L line and is out of scope.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Utilization above which the heuristic treats a name as Hard-To-Borrow. This is an
#: operational triage threshold chosen for this model, NOT a market or regulatory
#: standard -- no public source defines one. Override per desk.
DEFAULT_HTB_UTILIZATION_THRESHOLD = 0.80

#: Utilization at or above which recall/squeeze risk is escalated. Same caveat: an
#: operational trigger for review, not an empirical probability estimate.
DEFAULT_RECALL_WATCH_UTILIZATION = 0.90

#: Money-market day-count basis. USD and EUR securities loans accrue ACT/360; IBKR
#: publishes "(Value x Fee Rate)/360". GBP-denominated loans accrue ACT/365 fixed.
DAY_COUNT_ACT_360 = 360
DAY_COUNT_ACT_365 = 365

#: Customary US collateral Margin Percentage (MSLA Sec. 9; IBKR documents 102%).
US_COLLATERAL_MARGIN_PCT = 1.02

RATE_SOURCE_OBSERVED = "observed"
RATE_SOURCE_HEURISTIC_GC = "heuristic_gc"
RATE_SOURCE_HEURISTIC_HTB = "heuristic_htb"


class UnknownBorrowStatusError(LookupError):
    """
    Raised when borrow economics or availability are requested for a ticker with no
    registered ``BorrowStatus``.

    Absence of borrow data is not evidence of a cheap, freely available borrow. An
    earlier version of this module returned the General Collateral rate and
    ``can_short() -> True`` for unknown tickers, which silently priced an unknown
    special at a few basis points and waved through a short with no inventory
    evidence -- the exact backtest inflation and locate failure this skill exists to
    prevent. Unknown now fails loudly.
    """


def _require_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return value


def _require_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
    return value


@dataclass
class BorrowStatus:
    """
    Point-in-time borrow supply picture for one ticker.

    ``utilization_rate`` is the securities-finance definition: on-loan quantity divided
    by lendable inventory quantity, in [0, 1]. It is a *market-wide supply pressure*
    signal derived from custodial lending pools; it is not a statement about what your
    prime broker will lend you, which is what ``available_shares`` carries.

    ``available_shares`` has no default on purpose. A default (the previous version
    assumed 1,000,000) is a fabricated inventory number that makes the availability
    gate pass for securities nobody checked.

    ``observed_borrow_rate`` is the annualized rate actually quoted by the lending desk
    or broker, as a decimal (0.0725 == 7.25%). When present it overrides the
    utilization heuristic. Negative values are permitted: MSLA Sec. 5.1 explicitly
    contemplates a Loan Fee "less than zero".
    """
    ticker: str
    utilization_rate: float
    available_shares: int
    observed_borrow_rate: Optional[float] = None
    is_hard_to_borrow: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if not isinstance(self.ticker, str) or not self.ticker:
            raise ValueError(f"ticker must be a non-empty string, got {self.ticker!r}.")
        self.utilization_rate = _require_finite(self.utilization_rate, "utilization_rate")
        if not 0.0 <= self.utilization_rate <= 1.0:
            raise ValueError(
                f"utilization_rate for {self.ticker} must be in [0, 1] "
                f"(on-loan / lendable), got {self.utilization_rate}.")
        _require_int(self.available_shares, f"available_shares for {self.ticker}")
        if self.available_shares < 0:
            raise ValueError(
                f"available_shares for {self.ticker} must be >= 0, "
                f"got {self.available_shares}.")
        if self.observed_borrow_rate is not None:
            self.observed_borrow_rate = _require_finite(
                self.observed_borrow_rate, "observed_borrow_rate")
        # Convenience flag against the module default threshold. A modeler configured
        # with a different htb_utilization_threshold classifies independently and does
        # not read this field.
        self.is_hard_to_borrow = self.utilization_rate > DEFAULT_HTB_UTILIZATION_THRESHOLD


@dataclass
class ShortTrade:
    """
    A short position priced at a single entry price over a single holding period.

    ``days_held`` is the count of calendar accrual days, inclusive of the open date and
    exclusive of the cover date (MSLA Sec. 5.1). Weekends accrue.
    """
    ticker: str
    shares: int
    entry_price: float
    days_held: int

    def __post_init__(self) -> None:
        if not isinstance(self.ticker, str) or not self.ticker:
            raise ValueError(f"ticker must be a non-empty string, got {self.ticker!r}.")
        _require_int(self.shares, "shares")
        if self.shares <= 0:
            raise ValueError(
                f"shares must be > 0; pass the absolute size of the short, "
                f"got {self.shares}.")
        self.entry_price = _require_finite(self.entry_price, "entry_price")
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}.")
        _require_int(self.days_held, "days_held")
        if self.days_held < 0:
            raise ValueError(f"days_held must be >= 0, got {self.days_held}.")


@dataclass
class LocateAvailability:
    """Outcome of an inventory sufficiency check, with the reason recorded for audit."""
    ticker: str
    requested_shares: int
    is_available: bool
    reason: str
    available_shares: Optional[int] = None
    utilization_rate: Optional[float] = None


@dataclass
class BorrowCostResult:
    """
    Financing outcome for one short position.

    ``gross_borrow_cost_usd`` is the borrow fee alone. ``short_proceeds_credit_usd`` is
    the interest credited on the short sale proceeds, which is zero unless the caller
    supplied ``short_proceeds_credit_rate`` -- many arrangements credit nothing, so zero
    is the conservative default rather than a claim that no credit exists.
    ``net_financing_cost_usd`` is the difference and may be negative (positive carry).
    """
    ticker: str
    shares: int
    annualized_borrow_rate: float
    rate_source: str
    accrual_days: int
    day_count_basis: int
    average_collateral_value_usd: float
    gross_borrow_cost_usd: float
    short_proceeds_credit_usd: float
    net_financing_cost_usd: float
    is_hard_to_borrow: bool


@dataclass
class RecallRiskAssessment:
    """
    Triage of lender-recall exposure.

    US equity loans are open term: MSLA Sec. 6.1(a) lets *either* party terminate a Loan
    on notice, with the termination date no earlier than standard settlement. A borrow
    that exists today is therefore not a borrow you own for the holding period, and a
    recall that cannot be re-borrowed forces a buy-in at whatever the market is doing.

    The tiers below are operational review triggers driven by utilization. They are not
    calibrated recall probabilities; no public source publishes such a calibration.
    """
    ticker: str
    utilization_rate: float
    available_shares: int
    tier: str
    rationale: str


class BorrowCostModeler:
    """
    Models borrow availability and financing cost for equity short positions.

    All rates are annualized decimals (0.05 == 5%).
    """

    def __init__(
        self,
        gc_rate: float = 0.003,
        htb_base_rate: float = 0.05,
        max_htb_rate: float = 0.50,
        htb_utilization_threshold: float = DEFAULT_HTB_UTILIZATION_THRESHOLD,
        day_count_basis: int = DAY_COUNT_ACT_360,
        collateral_margin_pct: float = US_COLLATERAL_MARGIN_PCT,
        round_collateral_price_up: bool = False,
        short_proceeds_credit_rate: Optional[float] = None,
        recall_watch_utilization: float = DEFAULT_RECALL_WATCH_UTILIZATION,
    ) -> None:
        """
        gc_rate: General Collateral annualized rate used when utilization is at or
            below ``htb_utilization_threshold`` and no observed rate is available.
        htb_base_rate / max_htb_rate: endpoints of the utilization ramp applied above
            the threshold. These are placeholders, not market constants -- calibrate
            them against your own borrow history or supply ``observed_borrow_rate``.
        day_count_basis: 360 for USD/EUR loans (default, matches IBKR's published
            divisor), 365 for GBP-denominated loans.
        collateral_margin_pct: Margin Percentage applied to market value (MSLA Sec. 9);
            1.02 is customary US practice. Set 1.0 to accrue on bare market value.
        round_collateral_price_up: reproduce IBKR's convention of rounding the margined
            per-share price up to the next whole dollar before multiplying by share
            count. Off by default because it is broker-specific.
        short_proceeds_credit_rate: annualized rate credited on short sale proceeds.
            ``None`` models no credit, the conservative assumption for any account whose
            proceeds are not rebated.
        recall_watch_utilization: utilization at or above which recall risk escalates.
        """
        self.gc_rate = _require_finite(gc_rate, "gc_rate")
        self.htb_base_rate = _require_finite(htb_base_rate, "htb_base_rate")
        self.max_htb_rate = _require_finite(max_htb_rate, "max_htb_rate")
        if self.max_htb_rate < self.htb_base_rate:
            raise ValueError(
                f"max_htb_rate ({self.max_htb_rate}) must be >= htb_base_rate "
                f"({self.htb_base_rate}).")

        self.htb_utilization_threshold = _require_finite(
            htb_utilization_threshold, "htb_utilization_threshold")
        if not 0.0 <= self.htb_utilization_threshold < 1.0:
            raise ValueError(
                "htb_utilization_threshold must be in [0, 1); a threshold of 1.0 leaves "
                f"no ramp to interpolate over, got {self.htb_utilization_threshold}.")

        _require_int(day_count_basis, "day_count_basis")
        if day_count_basis <= 0:
            raise ValueError(f"day_count_basis must be > 0, got {day_count_basis}.")
        self.day_count_basis = day_count_basis

        self.collateral_margin_pct = _require_finite(
            collateral_margin_pct, "collateral_margin_pct")
        if self.collateral_margin_pct < 1.0:
            raise ValueError(
                "collateral_margin_pct must be >= 1.0; MSLA Sec. 9 requires collateral "
                f"of at least 100% of market value, got {self.collateral_margin_pct}.")
        self.round_collateral_price_up = bool(round_collateral_price_up)

        if short_proceeds_credit_rate is not None:
            short_proceeds_credit_rate = _require_finite(
                short_proceeds_credit_rate, "short_proceeds_credit_rate")
        self.short_proceeds_credit_rate = short_proceeds_credit_rate

        self.recall_watch_utilization = _require_finite(
            recall_watch_utilization, "recall_watch_utilization")
        if not 0.0 <= self.recall_watch_utilization <= 1.0:
            raise ValueError(
                f"recall_watch_utilization must be in [0, 1], "
                f"got {self.recall_watch_utilization}.")

        self.borrow_statuses: Dict[str, BorrowStatus] = {}

    # ------------------------------------------------------------------ inventory

    def update_status(self, status: BorrowStatus) -> None:
        """Register or replace the borrow picture for a ticker."""
        if not isinstance(status, BorrowStatus):
            raise TypeError(f"status must be a BorrowStatus, got {type(status).__name__}.")
        self.borrow_statuses[status.ticker] = status

    def get_status(self, ticker: str) -> BorrowStatus:
        """Return the registered status, raising ``UnknownBorrowStatusError`` if absent."""
        try:
            return self.borrow_statuses[ticker]
        except KeyError:
            raise UnknownBorrowStatusError(
                f"No borrow status registered for {ticker!r}. Absent borrow data is not "
                f"evidence of a cheap or available borrow -- register a BorrowStatus "
                f"before shorting or pricing it.") from None

    # --------------------------------------------------------------- availability

    def check_availability(self, ticker: str, requested_shares: int) -> LocateAvailability:
        """
        Inventory sufficiency check, fail-closed, with the rejection reason recorded.

        This is *not* a Regulation SHO locate. It says inventory looks sufficient; it
        does not create the borrow, and it does not survive a recall.
        """
        _require_int(requested_shares, "requested_shares")
        if requested_shares <= 0:
            raise ValueError(
                f"requested_shares must be > 0; pass the absolute short size, "
                f"got {requested_shares}.")

        status = self.borrow_statuses.get(ticker)
        if status is None:
            logger.warning("Short rejected for %s: no borrow status registered.", ticker)
            return LocateAvailability(
                ticker=ticker,
                requested_shares=requested_shares,
                is_available=False,
                reason="NO_BORROW_STATUS",
            )

        if status.available_shares <= 0:
            reason = "NO_INVENTORY"
        elif requested_shares > status.available_shares:
            reason = "INSUFFICIENT_INVENTORY"
        elif status.utilization_rate >= 1.0:
            # Inventory is reported but the lendable pool is fully lent. Treat the
            # contradiction as a data-quality failure and refuse, rather than picking
            # whichever field happens to be more permissive.
            reason = "FULLY_UTILIZED"
            logger.warning(
                "%s reports %d available shares at 100%% utilization; refusing short on "
                "contradictory borrow data.", ticker, status.available_shares)
        else:
            return LocateAvailability(
                ticker=ticker,
                requested_shares=requested_shares,
                is_available=True,
                reason="AVAILABLE",
                available_shares=status.available_shares,
                utilization_rate=status.utilization_rate,
            )

        logger.info(
            "Short rejected for %s (%s): requested %d, available %d, utilization %.4f.",
            ticker, reason, requested_shares, status.available_shares,
            status.utilization_rate)
        return LocateAvailability(
            ticker=ticker,
            requested_shares=requested_shares,
            is_available=False,
            reason=reason,
            available_shares=status.available_shares,
            utilization_rate=status.utilization_rate,
        )

    def can_short(self, ticker: str, requested_shares: int) -> bool:
        """Boolean form of :meth:`check_availability`. Unknown tickers return ``False``."""
        return self.check_availability(ticker, requested_shares).is_available

    # ---------------------------------------------------------------------- rates

    def resolve_rate(self, ticker: str) -> Tuple[float, str]:
        """
        Return ``(annualized_rate, source)`` for a ticker.

        An ``observed_borrow_rate`` on the status always wins. Otherwise the utilization
        heuristic applies: flat ``gc_rate`` at or below the threshold, then a linear ramp
        from ``htb_base_rate`` to ``max_htb_rate`` between the threshold and 100%
        utilization. The ramp is discontinuous at the threshold by construction -- that
        step is a modelling choice, not a market phenomenon.
        """
        status = self.get_status(ticker)

        if status.observed_borrow_rate is not None:
            return status.observed_borrow_rate, RATE_SOURCE_OBSERVED

        if status.utilization_rate <= self.htb_utilization_threshold:
            return self.gc_rate, RATE_SOURCE_HEURISTIC_GC

        span = 1.0 - self.htb_utilization_threshold
        scale = (status.utilization_rate - self.htb_utilization_threshold) / span
        scale = min(max(scale, 0.0), 1.0)
        rate = self.htb_base_rate + scale * (self.max_htb_rate - self.htb_base_rate)
        return rate, RATE_SOURCE_HEURISTIC_HTB

    def calculate_annualized_rate(self, ticker: str) -> float:
        """Annualized borrow rate for a ticker. Raises on an unregistered ticker."""
        return self.resolve_rate(ticker)[0]

    # ----------------------------------------------------------------- collateral

    def collateral_value(self, shares: int, price: float) -> float:
        """
        Collateral the fee accrues on: market value grossed up by the Margin Percentage
        (MSLA Sec. 9), optionally with IBKR's round-up-to-the-dollar per-share
        convention applied before multiplying by share count.
        """
        _require_int(shares, "shares")
        if shares <= 0:
            raise ValueError(f"shares must be > 0, got {shares}.")
        price = _require_finite(price, "price")
        if price <= 0:
            raise ValueError(f"price must be > 0, got {price}.")
        margined_price = price * self.collateral_margin_pct
        if self.round_collateral_price_up:
            margined_price = float(math.ceil(margined_price))
        return shares * margined_price

    # ------------------------------------------------------------------ cost math

    def calculate_borrow_cost(self, trade: ShortTrade) -> float:
        """
        Gross borrow fee in currency units for the whole holding period, priced at a
        single rate and a single price.

        This is an approximation and understates cost on a short that moves against you,
        because the real fee accrues on each day's mark (MSLA Sec. 5.1). Use
        :meth:`calculate_borrow_cost_schedule` when the marks are known.
        """
        return self.calculate_borrow_cost_detail(trade).gross_borrow_cost_usd

    def calculate_borrow_cost_detail(self, trade: ShortTrade) -> BorrowCostResult:
        """Flat-price borrow cost with the rate, basis and financing legs itemized."""
        if not isinstance(trade, ShortTrade):
            raise TypeError(f"trade must be a ShortTrade, got {type(trade).__name__}.")
        rate, source = self.resolve_rate(trade.ticker)
        collateral = self.collateral_value(trade.shares, trade.entry_price)
        gross = collateral * rate / self.day_count_basis * trade.days_held
        credit = self._proceeds_credit(
            trade.shares * trade.entry_price * trade.days_held)
        return self._build_result(
            ticker=trade.ticker,
            shares=trade.shares,
            rate=rate,
            source=source,
            accrual_days=trade.days_held,
            average_collateral=collateral,
            gross=gross,
            credit=credit,
        )

    def calculate_borrow_cost_schedule(
        self,
        ticker: str,
        shares: int,
        daily_marks: Sequence[float],
        daily_rates: Optional[Sequence[float]] = None,
    ) -> BorrowCostResult:
        """
        Accrue the borrow fee day by day on the realized marks -- the convention the fee
        is actually computed under (MSLA Sec. 5.1).

        ``daily_marks[i]`` is the settlement price that day *i*'s fee accrues on. Both
        IBKR and the MSLA accrue on a previously established settlement price, so feed
        prior-day settlement prices: accruing day *i* on day *i*'s own close charges the
        position against a price it did not yet know and leaks it into the backtest's
        cost line.

        ``daily_rates`` lets the rate reprice per day, as open loans do. When omitted a
        single resolved rate is applied to every day.
        """
        _require_int(shares, "shares")
        if shares <= 0:
            raise ValueError(f"shares must be > 0, got {shares}.")
        marks: List[float] = [_require_finite(m, "daily_marks entry") for m in daily_marks]
        if not marks:
            raise ValueError("daily_marks must contain at least one accrual day.")
        if any(m <= 0 for m in marks):
            raise ValueError("every entry in daily_marks must be > 0.")

        flat_rate, source = self.resolve_rate(ticker)
        if daily_rates is None:
            rates = [flat_rate] * len(marks)
        else:
            rates = [_require_finite(r, "daily_rates entry") for r in daily_rates]
            if len(rates) != len(marks):
                raise ValueError(
                    f"daily_rates has {len(rates)} entries but daily_marks has "
                    f"{len(marks)}; one rate per accrual day is required.")
            source = RATE_SOURCE_OBSERVED

        gross = 0.0
        collateral_total = 0.0
        proceeds_day_product = 0.0
        for mark, rate in zip(marks, rates):
            collateral = self.collateral_value(shares, mark)
            collateral_total += collateral
            gross += collateral * rate / self.day_count_basis
            proceeds_day_product += shares * mark

        accrual_days = len(marks)
        return self._build_result(
            ticker=ticker,
            shares=shares,
            rate=sum(rates) / accrual_days,
            source=source,
            accrual_days=accrual_days,
            average_collateral=collateral_total / accrual_days,
            gross=gross,
            credit=self._proceeds_credit(proceeds_day_product),
        )

    def _proceeds_credit(self, proceeds_day_product: float) -> float:
        """
        Interest credited on short sale proceeds over the accrual period.

        ``proceeds_day_product`` is the sum over accrual days of (shares x mark). The
        credit accrues on bare proceeds, not on the margined collateral. Broker-specific
        tiers and minimum-balance thresholds are out of scope.
        """
        if self.short_proceeds_credit_rate is None:
            return 0.0
        return proceeds_day_product * self.short_proceeds_credit_rate / self.day_count_basis

    def _build_result(
        self,
        ticker: str,
        shares: int,
        rate: float,
        source: str,
        accrual_days: int,
        average_collateral: float,
        gross: float,
        credit: float,
    ) -> BorrowCostResult:
        status = self.get_status(ticker)
        return BorrowCostResult(
            ticker=ticker,
            shares=shares,
            annualized_borrow_rate=rate,
            rate_source=source,
            accrual_days=accrual_days,
            day_count_basis=self.day_count_basis,
            average_collateral_value_usd=average_collateral,
            gross_borrow_cost_usd=gross,
            short_proceeds_credit_usd=credit,
            net_financing_cost_usd=gross - credit,
            is_hard_to_borrow=status.utilization_rate > self.htb_utilization_threshold,
        )

    # ------------------------------------------------------------ recall / squeeze

    def assess_recall_risk(self, ticker: str) -> RecallRiskAssessment:
        """
        Utilization-driven triage of recall and squeeze exposure.

        Tiers are review triggers, not probabilities. ``HIGH`` means the lendable pool is
        fully lent or nothing is offered, so a recall under MSLA Sec. 6.1(a) is unlikely
        to be replaceable and a buy-in becomes the realistic outcome.
        """
        status = self.get_status(ticker)
        if status.utilization_rate >= 1.0 or status.available_shares <= 0:
            tier = "HIGH"
            rationale = (
                "Lendable pool fully lent or no inventory offered; a recall is unlikely "
                "to be replaceable and would force a buy-in.")
        elif status.utilization_rate >= self.recall_watch_utilization:
            tier = "ELEVATED"
            rationale = (
                f"Utilization {status.utilization_rate:.1%} is at or above the "
                f"{self.recall_watch_utilization:.0%} watch level; borrow supply is thin "
                "and rates can reprice sharply.")
        else:
            tier = "LOW"
            rationale = (
                f"Utilization {status.utilization_rate:.1%} leaves headroom in the "
                "lendable pool.")
        if tier != "LOW":
            logger.warning("Recall risk %s for %s: %s", tier, ticker, rationale)
        return RecallRiskAssessment(
            ticker=ticker,
            utilization_rate=status.utilization_rate,
            available_shares=status.available_shares,
            tier=tier,
            rationale=rationale,
        )
