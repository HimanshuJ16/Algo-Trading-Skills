import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TRSSide(Enum):
    RECEIVER_TOTAL_RETURN = "RECEIVER_TOTAL_RETURN" # Long Synthetic Exposure (Receives Total Return, Pays Funding)
    PAYER_TOTAL_RETURN = "PAYER_TOTAL_RETURN"       # Short Synthetic Exposure (Pays Total Return, Receives Funding)


class BenchmarkRate(Enum):
    SOFR = "SOFR"           # Secured Overnight Financing Rate (USD)
    ESTR = "ESTR"           # Euro Short-Term Rate (EUR)
    SONIA = "SONIA"         # Sterling Overnight Index Average (GBP)
    EURIBOR = "EURIBOR"     # Euro Interbank Offered Rate
    FED_FUNDS = "FED_FUNDS" # Effective Federal Funds Rate


class DayCountConvention(Enum):
    ACT_360 = "ACT_360"
    ACT_365 = "ACT_365"
    THIRTY_360 = "THIRTY_360"


class DERIVATIVES_ERROR(Exception):
    """Base exception for Total Return Swap pricing errors."""
    pass


@dataclass
class DividendEvent:
    ex_date: datetime.date
    payment_date: datetime.date
    gross_amount_per_share: float
    withholding_tax_pct: float = 0.0  # e.g., 15.0 for 15% dividend tax withholding

    @property
    def net_amount_per_share(self) -> float:
        """Net manufactured dividend per share after tax withholding."""
        return self.gross_amount_per_share * (1.0 - (self.withholding_tax_pct / 100.0))


@dataclass
class TRSContractConfig:
    swap_id: str
    symbol: str
    notional_amount_usd: float
    initial_reference_price: float
    quantity_shares: float
    funding_benchmark: BenchmarkRate = BenchmarkRate.SOFR
    funding_spread_bps: float = 50.0  # Funding spread e.g., SOFR + 50 bps
    day_count: DayCountConvention = DayCountConvention.ACT_360
    initial_margin_pct: float = 15.0  # ISDA initial margin requirement (%)
    maintenance_margin_pct: float = 10.0


@dataclass
class TRSResetPeriod:
    period_id: str
    start_date: datetime.date
    end_date: datetime.date
    start_price: float
    end_price: float
    benchmark_rate_pct: float  # e.g., 5.25 for 5.25% SOFR
    dividends: List[DividendEvent] = field(default_factory=list)

    @property
    def days_in_period(self) -> int:
        return (self.end_date - self.start_date).days


@dataclass
class TRSSettlement:
    period_id: str
    total_return_amount_usd: float
    funding_interest_amount_usd: float
    net_manufactured_dividends_usd: float
    net_cashflow_usd: float  # Positive = Cash Inflow to Receiver, Negative = Outflow
    current_mtm_usd: float
    synthetic_delta_shares: float
    variation_margin_due_usd: float


class TRSEngine:
    """
    Institutional Total Return Swap (TRS) Pricing & Synthetic Exposure Engine.
    
    Models synthetic asset exposure, total return leg cash flows (capital appreciation +
    manufactured dividends), funding leg interest payments (SOFR/ESTR + spread bps),
    periodic net resets, synthetic delta/gamma risk, and ISDA variation margin calls.
    """

    def __init__(self):
        logger.info("Initialized Institutional Total Return Swap (TRS) Engine")

    @staticmethod
    def calculate_day_count_fraction(days: int, convention: DayCountConvention) -> float:
        """Computes day count fraction for funding interest calculations."""
        if convention in (DayCountConvention.ACT_360, DayCountConvention.THIRTY_360):
            return days / 360.0
        elif convention == DayCountConvention.ACT_365:
            return days / 365.0
        else:
            raise DERIVATIVES_ERROR(f"Unsupported day count convention: {convention}")

    def calculate_total_return_leg(self, config: TRSContractConfig, reset: TRSResetPeriod) -> Tuple[float, float]:
        """
        Calculates capital return amount and net manufactured dividend cash flows.
        Capital Return = Quantity * (End Price - Start Price)
        """
        price_diff = reset.end_price - reset.start_price
        capital_return_usd = config.quantity_shares * price_diff

        net_div_usd = 0.0
        for div in reset.dividends:
            net_div_usd += config.quantity_shares * div.net_amount_per_share

        return capital_return_usd, net_div_usd

    def calculate_funding_leg(self, config: TRSContractConfig, reset: TRSResetPeriod) -> float:
        """
        Calculates funding interest leg: Notional * (Benchmark % + Spread bps / 10000) * DayFraction.
        """
        if reset.days_in_period <= 0:
            raise DERIVATIVES_ERROR(f"Invalid reset period days: {reset.days_in_period}")

        total_rate_pct = reset.benchmark_rate_pct + (config.funding_spread_bps / 100.0)
        day_frac = self.calculate_day_count_fraction(reset.days_in_period, config.day_count)

        # Funding interest based on starting notional for the reset period
        period_start_notional = config.quantity_shares * reset.start_price
        funding_interest_usd = period_start_notional * (total_rate_pct / 100.0) * day_frac

        return funding_interest_usd

    def process_reset_period(
        self, config: TRSContractConfig, reset: TRSResetPeriod, side: TRSSide
    ) -> TRSSettlement:
        """
        Processes a periodic TRS reset cash flow settlement and risk evaluation.
        """
        cap_return_usd, div_usd = self.calculate_total_return_leg(config, reset)
        funding_interest_usd = self.calculate_funding_leg(config, reset)

        total_return_gross_usd = cap_return_usd + div_usd

        if side == TRSSide.RECEIVER_TOTAL_RETURN:
            # Receiver receives Total Return, pays Funding
            net_cashflow = total_return_gross_usd - funding_interest_usd
            synthetic_delta = config.quantity_shares
        else:
            # Payer pays Total Return, receives Funding
            net_cashflow = funding_interest_usd - total_return_gross_usd
            synthetic_delta = -config.quantity_shares

        current_mtm = cap_return_usd + div_usd - funding_interest_usd

        # Calculate Variation Margin requirement under ISDA CSA rules
        required_im = config.notional_amount_usd * (config.initial_margin_pct / 100.0)
        vm_due = max(0.0, -net_cashflow - required_im)

        logger.info(
            f"TRS Reset {reset.period_id} [{config.swap_id}]: Cap Return=${cap_return_usd:,.2f}, "
            f"Funding Interest=${funding_interest_usd:,.2f}, Net Cashflow=${net_cashflow:,.2f}"
        )

        return TRSSettlement(
            period_id=reset.period_id,
            total_return_amount_usd=cap_return_usd,
            funding_interest_amount_usd=funding_interest_usd,
            net_manufactured_dividends_usd=div_usd,
            net_cashflow_usd=net_cashflow,
            current_mtm_usd=current_mtm,
            synthetic_delta_shares=synthetic_delta,
            variation_margin_due_usd=vm_due,
        )

