"""
total-return-swap-synthetic-exposure: equity Total Return Swap (TRS) reset engine --
total return leg (capital return + manufactured dividends), funding leg
(overnight benchmark + spread), periodic net settlement, synthetic delta, and
ISDA CSA initial / variation margin *requirements*.

Conventions this engine implements, stated explicitly because they are the terms
that most often differ between a model and the executed Confirmation:

  * **Share-locked TRS.** `quantity_shares` is fixed for the life of the swap and the
    notional RESETS each period to `quantity_shares * reset.start_price`. Every
    notional-scaled figure -- funding interest, initial margin, maintenance margin --
    uses that period notional, not `config.notional_amount_usd` (which is retained as
    the booked trade-date notional and is only cross-checked for consistency).
    A fixed-notional TRS, where the share count instead changes at each reset, is a
    different product and is NOT modelled here.

  * **Dividend eligibility is date-filtered.** Under the 2002 ISDA Equity Derivatives
    Definitions Section 10.1 the Dividend Amount is the Record Amount, the Ex Amount or
    the Paid Amount "as specified in the related Confirmation" -- three different
    eligibility dates. Only dividends whose relevant date falls inside the Dividend
    Period accrue. The default Dividend Period (Section 10.3, "Second Period") runs
    from, but EXCLUDING, one Valuation Date to, and INCLUDING, the next, so the period
    is treated as the half-open interval `(start_date, end_date]`. Dividends outside it
    are reported in `TRSSettlement.excluded_dividend_ids` rather than silently dropped.

  * **Gross vs. net dividends.** ISDA defines each of the Record / Ex / Paid Amounts as
    100% of the *gross* cash dividend, explicitly "before the withholding or deduction
    of taxes at the source". Any lower pass-through -- net of withholding, or an agreed
    percentage -- is a Confirmation-level term. `withholding_tax_pct` is that haircut;
    set it to 0.0 to model a 100% gross pass-through.

  * **Margin is reported, not netted.** Initial margin under the BCBS-IOSCO uncleared
    margin framework is exchanged gross, segregated and cannot be re-hypothecated, so
    it does NOT offset a variation margin call. Variation margin fully collateralises
    the mark-to-market at a zero threshold, subject only to the Minimum Transfer Amount.
    IM, maintenance margin and VM are therefore returned as separate fields.

This module computes contractual cash flows and margin *requirements*. It does not
book trades, call collateral, or make a legal determination of tax withholding --
Section 871(m) applicability depends on the counterparty, the treaty and the
transaction's delta, none of which this engine knows.
"""
import datetime
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tolerance for the trade-date notional consistency cross-check (0.5% of notional).
NOTIONAL_CONSISTENCY_TOLERANCE = 0.005


class TRSSide(str, Enum):
    """Which leg the modelled party is on. Inherits `str`, so `== "..."` still works."""
    RECEIVER_TOTAL_RETURN = "RECEIVER_TOTAL_RETURN"  # Long synthetic: receives total return, pays funding
    PAYER_TOTAL_RETURN = "PAYER_TOTAL_RETURN"        # Short synthetic: pays total return, receives funding


class BenchmarkRate(str, Enum):
    SOFR = "SOFR"            # Secured Overnight Financing Rate (USD)
    ESTR = "ESTR"            # Euro Short-Term Rate (EUR)
    SONIA = "SONIA"          # Sterling Overnight Index Average (GBP)
    EURIBOR = "EURIBOR"      # Euro Interbank Offered Rate (EUR)
    FED_FUNDS = "FED_FUNDS"  # Effective Federal Funds Rate (USD)
    TONA = "TONA"            # Tokyo Overnight Average Rate (JPY)


class DayCountConvention(str, Enum):
    ACT_360 = "ACT_360"
    ACT_365 = "ACT_365"        # Act/365 (Fixed)
    THIRTY_360 = "THIRTY_360"  # 30/360 Bond Basis (2006 ISDA Definitions Section 4.16(f))


class DividendBasis(str, Enum):
    """Which date decides whether a dividend accrues to the period (2002 ISDA EDD 10.1)."""
    RECORD = "RECORD"  # Record Amount -- record date in the Dividend Period
    EX = "EX"          # Ex Amount     -- ex-dividend date in the Dividend Period
    PAID = "PAID"      # Paid Amount   -- payment date in the Dividend Period


# Market-standard day count per benchmark. A bespoke Confirmation may legitimately
# depart from these, so a mismatch is warned about, never silently corrected.
BENCHMARK_DAY_COUNT = {
    BenchmarkRate.SOFR: DayCountConvention.ACT_360,
    BenchmarkRate.FED_FUNDS: DayCountConvention.ACT_360,
    BenchmarkRate.ESTR: DayCountConvention.ACT_360,
    BenchmarkRate.EURIBOR: DayCountConvention.ACT_360,
    BenchmarkRate.SONIA: DayCountConvention.ACT_365,
    BenchmarkRate.TONA: DayCountConvention.ACT_365,
}


class TRSModelError(ValueError):
    """Raised for a TRS contract, reset period or dividend input that cannot be priced."""


# Backwards-compatible alias for the pre-2.0 exception name.
DERIVATIVES_ERROR = TRSModelError


def _require_finite(value: float, name: str) -> float:
    """Rejects NaN/Inf early so they cannot propagate silently into a cash flow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise TRSModelError(f"{name} must be a finite number, got {value!r}")
    return float(value)


@dataclass
class DividendEvent:
    ex_date: datetime.date
    payment_date: datetime.date
    gross_amount_per_share: float           # GROSS declared amount per share, before withholding
    withholding_tax_pct: float = 0.0        # e.g. 15.0 for a 15% haircut; must be in [0, 100]
    # --- default-valued: positional construction unchanged ---
    record_date: Optional[datetime.date] = None  # required only for DividendBasis.RECORD
    dividend_id: str = ""                        # label used in exclusion/warning reporting

    @property
    def net_amount_per_share(self) -> float:
        """Net manufactured dividend per share after the contractual withholding haircut."""
        return self.gross_amount_per_share * (1.0 - (self.withholding_tax_pct / 100.0))

    def relevant_date(self, basis: DividendBasis) -> datetime.date:
        """
        Eligibility date for the requested Dividend Amount basis.

        RECORD falls back to the ex-date when no record date is supplied -- the two are
        typically one settlement cycle apart, so the fallback is reported as a warning
        by the engine rather than treated as equivalent.
        """
        if basis == DividendBasis.PAID:
            return self.payment_date
        if basis == DividendBasis.RECORD:
            return self.record_date if self.record_date is not None else self.ex_date
        return self.ex_date

    def label(self) -> str:
        return self.dividend_id or f"div@{self.ex_date.isoformat()}"

    def validate(self) -> None:
        _require_finite(self.gross_amount_per_share, "gross_amount_per_share")
        _require_finite(self.withholding_tax_pct, "withholding_tax_pct")
        if not 0.0 <= self.withholding_tax_pct <= 100.0:
            raise TRSModelError(
                f"withholding_tax_pct must be a percentage in [0, 100], got {self.withholding_tax_pct}"
            )
        if self.gross_amount_per_share < 0.0:
            raise TRSModelError(
                f"gross_amount_per_share must be non-negative, got {self.gross_amount_per_share}. "
                "A negative dividend is not a cash dividend -- model it as a price adjustment."
            )
        if self.payment_date < self.ex_date:
            raise TRSModelError(
                f"payment_date {self.payment_date} precedes ex_date {self.ex_date} for {self.label()}"
            )


@dataclass
class TRSContractConfig:
    swap_id: str
    symbol: str
    notional_amount_usd: float          # booked trade-date notional; cross-checked, not used for accrual
    initial_reference_price: float
    quantity_shares: float              # fixed share count (share-locked TRS)
    funding_benchmark: BenchmarkRate = BenchmarkRate.SOFR
    funding_spread_bps: float = 50.0    # e.g. SOFR + 50 bps
    day_count: DayCountConvention = DayCountConvention.ACT_360
    initial_margin_pct: float = 15.0    # BCBS-IOSCO standardised schedule: equity = 15% of notional
    maintenance_margin_pct: float = 10.0
    # --- default-valued: positional construction unchanged ---
    dividend_basis: DividendBasis = DividendBasis.EX
    vm_threshold_usd: float = 0.0             # UMR requires a zero VM threshold
    minimum_transfer_amount_usd: float = 0.0  # MTA; transfers below it are not made

    def validate(self) -> None:
        for name in ("notional_amount_usd", "initial_reference_price", "quantity_shares",
                     "funding_spread_bps", "initial_margin_pct", "maintenance_margin_pct",
                     "vm_threshold_usd", "minimum_transfer_amount_usd"):
            _require_finite(getattr(self, name), name)

        if self.quantity_shares <= 0.0:
            raise TRSModelError(
                f"quantity_shares must be positive, got {self.quantity_shares}. "
                "Direction is expressed by TRSSide, not by a negative share count."
            )
        if self.initial_reference_price <= 0.0:
            raise TRSModelError(
                f"initial_reference_price must be positive, got {self.initial_reference_price}"
            )
        if self.notional_amount_usd <= 0.0:
            raise TRSModelError(f"notional_amount_usd must be positive, got {self.notional_amount_usd}")
        for name in ("initial_margin_pct", "maintenance_margin_pct"):
            value = getattr(self, name)
            if not 0.0 <= value <= 100.0:
                raise TRSModelError(f"{name} must be a percentage in [0, 100], got {value}")
        for name in ("vm_threshold_usd", "minimum_transfer_amount_usd"):
            value = getattr(self, name)
            if value < 0.0:
                raise TRSModelError(f"{name} must be non-negative, got {value}")

    def consistency_warnings(self) -> List[str]:
        """Non-fatal booking checks: contradictions that price fine but signal bad trade capture."""
        warnings: List[str] = []

        implied_notional = self.quantity_shares * self.initial_reference_price
        tolerance = NOTIONAL_CONSISTENCY_TOLERANCE * self.notional_amount_usd
        if abs(implied_notional - self.notional_amount_usd) > tolerance:
            warnings.append(
                f"booked notional_amount_usd={self.notional_amount_usd:,.2f} disagrees with "
                f"quantity_shares * initial_reference_price={implied_notional:,.2f}"
            )

        expected_dcc = BENCHMARK_DAY_COUNT.get(self.funding_benchmark)
        if expected_dcc is not None and expected_dcc != self.day_count:
            warnings.append(
                f"day_count {self.day_count.value} is not the market convention "
                f"{expected_dcc.value} for {self.funding_benchmark.value}"
            )

        if self.maintenance_margin_pct > self.initial_margin_pct:
            warnings.append(
                f"maintenance_margin_pct ({self.maintenance_margin_pct}) exceeds "
                f"initial_margin_pct ({self.initial_margin_pct})"
            )

        return warnings


@dataclass
class TRSResetPeriod:
    period_id: str
    start_date: datetime.date
    end_date: datetime.date
    start_price: float
    end_price: float
    benchmark_rate_pct: float  # e.g. 5.25 for 5.25%; may be negative (EUR/JPY have printed negative fixings)
    dividends: List[DividendEvent] = field(default_factory=list)

    @property
    def days_in_period(self) -> int:
        return (self.end_date - self.start_date).days

    def validate(self) -> None:
        for name in ("start_price", "end_price", "benchmark_rate_pct"):
            _require_finite(getattr(self, name), name)
        if self.start_price <= 0.0 or self.end_price <= 0.0:
            raise TRSModelError(
                f"reset {self.period_id}: prices must be positive, got "
                f"start_price={self.start_price}, end_price={self.end_price}"
            )
        if self.end_date <= self.start_date:
            raise TRSModelError(
                f"reset {self.period_id}: end_date {self.end_date} must be after "
                f"start_date {self.start_date}"
            )
        for div in self.dividends:
            div.validate()


@dataclass
class TRSSettlement:
    period_id: str
    total_return_amount_usd: float          # capital return only (dividends reported separately)
    funding_interest_amount_usd: float      # gross funding accrual, sign-independent of side
    net_manufactured_dividends_usd: float
    net_cashflow_usd: float                 # signed for the modelled party: + = inflow, - = outflow
    current_mtm_usd: float                  # signed for the modelled party; == net_cashflow at a cash reset
    synthetic_delta_shares: float
    variation_margin_due_usd: float         # + = modelled party posts, - = collateral returnable
    # --- default-valued: positional construction unchanged ---
    period_notional_usd: float = 0.0
    initial_margin_requirement_usd: float = 0.0
    maintenance_margin_requirement_usd: float = 0.0
    variation_margin_requirement_usd: float = 0.0  # gross requirement before MTA / posted collateral
    excluded_dividend_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class TRSEngine:
    """
    Total Return Swap (TRS) reset and synthetic-exposure engine.

    Models a share-locked equity TRS: total return leg (capital return + manufactured
    dividends), funding leg (benchmark + spread on the period-reset notional), periodic
    net settlement, synthetic share delta, and separately reported ISDA CSA initial,
    maintenance and variation margin requirements.

    See the module docstring for the conventions applied.
    """

    def __init__(self) -> None:
        logger.info("Initialized Total Return Swap (TRS) engine")

    @staticmethod
    def calculate_day_count_fraction(days: int, convention: DayCountConvention) -> float:
        """
        Day count fraction from an actual day count.

        Only the Actual/N conventions can be derived from a day count alone. 30/360 is a
        function of the two calendar dates, not of the number of days between them, so it
        is rejected here -- use `day_count_fraction_for_dates` instead.
        """
        if days < 0:
            raise TRSModelError(f"days must be non-negative, got {days}")
        if convention == DayCountConvention.ACT_360:
            return days / 360.0
        if convention == DayCountConvention.ACT_365:
            return days / 365.0
        if convention == DayCountConvention.THIRTY_360:
            raise TRSModelError(
                "THIRTY_360 cannot be computed from a day count: the 30/360 Bond Basis "
                "fraction depends on the start and end calendar dates. Call "
                "TRSEngine.day_count_fraction_for_dates(start, end, convention) instead."
            )
        raise TRSModelError(f"Unsupported day count convention: {convention}")

    @staticmethod
    def day_count_fraction_for_dates(
        start: datetime.date, end: datetime.date, convention: DayCountConvention
    ) -> float:
        """
        Day count fraction between two dates, accrued from `start` inclusive to `end` exclusive.

        30/360 follows the Bond Basis formula of the 2006 ISDA Definitions Section 4.16(f):

            [360*(Y2-Y1) + 30*(M2-M1) + (D2-D1)] / 360

        with D1 changed to 30 if it is 31, and D2 changed to 30 if it is 31 and D1 is
        30 or 31. Feeding actual days into `days / 360` instead is Act/360, not 30/360,
        and mis-accrues every period that contains a 31-day month or February.
        """
        if end < start:
            raise TRSModelError(f"end date {end} precedes start date {start}")

        if convention == DayCountConvention.THIRTY_360:
            d1 = min(start.day, 30)
            d2 = end.day
            if d2 == 31 and d1 == 30:
                d2 = 30
            numerator = 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)
            return numerator / 360.0

        return TRSEngine.calculate_day_count_fraction((end - start).days, convention)

    def _accrue_dividends(
        self, config: TRSContractConfig, reset: TRSResetPeriod
    ) -> Tuple[float, List[str], List[str]]:
        """
        Accrues manufactured dividends whose relevant date falls in the Dividend Period.

        The Dividend Period is the half-open interval `(start_date, end_date]`, matching
        the 2002 ISDA EDD default "Second Period" (from but excluding one Valuation Date
        to and including the next). Returns (net dividend USD, excluded ids, warnings).
        """
        net_div_usd = 0.0
        excluded: List[str] = []
        warnings: List[str] = []

        for div in reset.dividends:
            if config.dividend_basis == DividendBasis.RECORD and div.record_date is None:
                warnings.append(
                    f"dividend {div.label()}: RECORD basis requested but no record_date supplied; "
                    "falling back to ex_date for eligibility"
                )
            relevant = div.relevant_date(config.dividend_basis)
            if reset.start_date < relevant <= reset.end_date:
                net_div_usd += config.quantity_shares * div.net_amount_per_share
            else:
                excluded.append(div.label())

        if excluded:
            warnings.append(
                f"{len(excluded)} dividend(s) excluded: {config.dividend_basis.value} date outside "
                f"({reset.start_date}, {reset.end_date}]"
            )
        return net_div_usd, excluded, warnings

    def calculate_total_return_leg(
        self, config: TRSContractConfig, reset: TRSResetPeriod
    ) -> Tuple[float, float]:
        """
        Capital return and net manufactured dividend cash flows for the period.

            Capital Return = quantity_shares * (end_price - start_price)

        Only dividends whose relevant date (per `config.dividend_basis`) falls inside the
        Dividend Period are accrued; see `_accrue_dividends`.
        """
        config.validate()
        reset.validate()

        capital_return_usd = config.quantity_shares * (reset.end_price - reset.start_price)
        net_div_usd, _, _ = self._accrue_dividends(config, reset)
        return capital_return_usd, net_div_usd

    def calculate_funding_leg(self, config: TRSContractConfig, reset: TRSResetPeriod) -> float:
        """
        Funding interest accrued over the period on the period-reset notional:

            period_notional * (benchmark_rate_pct + spread_bps/100) / 100 * day_count_fraction

        `benchmark_rate_pct` and `funding_spread_bps` are in percent and basis points
        respectively; 50 bps contributes 0.50 percentage points. The result is the gross
        accrual signed from the funding payer's perspective -- a negative all-in rate
        therefore yields a negative accrual, which the funding payer receives.
        """
        config.validate()
        reset.validate()

        if reset.days_in_period <= 0:
            raise TRSModelError(f"Invalid reset period days: {reset.days_in_period}")

        total_rate_pct = reset.benchmark_rate_pct + (config.funding_spread_bps / 100.0)
        day_frac = self.day_count_fraction_for_dates(reset.start_date, reset.end_date, config.day_count)

        period_start_notional = config.quantity_shares * reset.start_price
        return period_start_notional * (total_rate_pct / 100.0) * day_frac

    def process_reset_period(
        self,
        config: TRSContractConfig,
        reset: TRSResetPeriod,
        side: TRSSide,
        collateral_already_posted_usd: float = 0.0,
    ) -> TRSSettlement:
        """
        Prices one periodic reset and evaluates the resulting margin requirements.

        `collateral_already_posted_usd` is variation margin the modelled party has already
        posted against this exposure; the returned `variation_margin_due_usd` is the
        incremental transfer (positive to post, negative to receive back) after applying
        the VM threshold and the Minimum Transfer Amount.

        Initial margin is a separate, segregated requirement under the BCBS-IOSCO uncleared
        margin framework and is NOT netted against the variation margin call.
        """
        config.validate()
        reset.validate()
        _require_finite(collateral_already_posted_usd, "collateral_already_posted_usd")

        capital_return_usd = config.quantity_shares * (reset.end_price - reset.start_price)
        div_usd, excluded_ids, div_warnings = self._accrue_dividends(config, reset)
        funding_interest_usd = self.calculate_funding_leg(config, reset)

        warnings = config.consistency_warnings() + div_warnings

        total_rate_pct = reset.benchmark_rate_pct + (config.funding_spread_bps / 100.0)
        if total_rate_pct < 0.0:
            warnings.append(
                f"all-in funding rate is negative ({total_rate_pct:.4f}%); the total return "
                "receiver is credited funding rather than charged"
            )

        # Receiver receives the total return and pays funding; the payer is the mirror image.
        receiver_pnl_usd = capital_return_usd + div_usd - funding_interest_usd
        if side == TRSSide.RECEIVER_TOTAL_RETURN:
            net_cashflow = receiver_pnl_usd
            synthetic_delta = config.quantity_shares
        elif side == TRSSide.PAYER_TOTAL_RETURN:
            net_cashflow = -receiver_pnl_usd
            synthetic_delta = -config.quantity_shares
        else:
            raise TRSModelError(f"Unsupported TRS side: {side}")

        # At a fully cash-settled reset the period mark-to-market equals the settlement
        # amount; between resets the two diverge (MtM accrues, cash does not move).
        current_mtm = net_cashflow

        period_notional = config.quantity_shares * reset.start_price
        required_im = period_notional * (config.initial_margin_pct / 100.0)
        required_maint = period_notional * (config.maintenance_margin_pct / 100.0)

        # VM fully collateralises a negative mark-to-market at the contractual threshold
        # (zero under UMR). IM is segregated and never offsets it.
        exposure = max(0.0, -current_mtm)
        vm_requirement = max(0.0, exposure - config.vm_threshold_usd)
        vm_transfer = vm_requirement - collateral_already_posted_usd
        if abs(vm_transfer) < config.minimum_transfer_amount_usd:
            vm_transfer = 0.0

        logger.info(
            f"TRS Reset {reset.period_id} [{config.swap_id}] {side.value}: "
            f"Cap Return=${capital_return_usd:,.2f}, Dividends=${div_usd:,.2f}, "
            f"Funding=${funding_interest_usd:,.2f}, Net Cashflow=${net_cashflow:,.2f}, "
            f"VM Transfer=${vm_transfer:,.2f}"
        )
        for warning in warnings:
            logger.warning(f"TRS Reset {reset.period_id} [{config.swap_id}]: {warning}")

        return TRSSettlement(
            period_id=reset.period_id,
            total_return_amount_usd=capital_return_usd,
            funding_interest_amount_usd=funding_interest_usd,
            net_manufactured_dividends_usd=div_usd,
            net_cashflow_usd=net_cashflow,
            current_mtm_usd=current_mtm,
            synthetic_delta_shares=synthetic_delta,
            variation_margin_due_usd=vm_transfer,
            period_notional_usd=period_notional,
            initial_margin_requirement_usd=required_im,
            maintenance_margin_requirement_usd=required_maint,
            variation_margin_requirement_usd=vm_requirement,
            excluded_dividend_ids=excluded_ids,
            warnings=warnings,
        )
