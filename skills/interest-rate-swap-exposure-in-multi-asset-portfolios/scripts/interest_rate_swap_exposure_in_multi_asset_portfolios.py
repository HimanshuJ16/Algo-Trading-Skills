"""
interest-rate-swap-exposure-in-multi-asset-portfolios: first-order (DV01/PV01)
interest-rate risk for vanilla fixed-vs-float Interest Rate Swaps held alongside
bonds and equities, plus the swap notional required to flatten net portfolio DV01.

Sign convention (read this before supplying any input)
-----------------------------------------------------
Every DV01 in this module is a **signed P&L in USD per +1 basis point parallel
rise in the swap curve**. It is therefore the *negative* of the textbook
``DV01 = -dV/dy`` convention, under which a long bond has a positive DV01.

    Long bond book        -> NEGATIVE dv01 here (loses value when rates rise)
    Pay-fixed swap        -> POSITIVE dv01 here (gains value when rates rise)
    Receive-fixed swap    -> NEGATIVE dv01 here

``MultiAssetPortfolioRisk.bonds_dv01_usd`` must be supplied in this same
convention. Passing a conventionally-signed (positive) bond DV01 makes the
engine size a hedge that *doubles* the portfolio's rate exposure instead of
neutralising it. This is the single most likely misuse of this module.

Valuation and DV01
------------------
For a vanilla fixed-vs-float swap, the fixed-leg present value per unit notional
is ``s_fix * A`` and the floating leg is ``1 - P(t, T)``, where ``A`` is the
fixed-leg annuity ``A = sum_i (delta_i * DF_i)``. Because the par swap rate
``S`` satisfies ``S * A = 1 - P(t, T)``, the value to the payer of fixed is:

    V_payer = N * A * (S - s_fix)

Differentiating with respect to the par rate gives the swap's PV01/DV01:

    DV01_payer    = +N * A * 0.0001        (payer gains as rates rise)
    DV01_receiver = -N * A * 0.0001

so the annuity ``A`` -- not "tenor / 2" -- is the correct duration-like
multiplier. Under a flat curve at rate ``y`` with ``f`` payments per year over
``n`` years the annuity has the closed form used here:

    A(y, n, f) = (1 / y) * (1 - (1 + y / f) ** (-n * f)),   A(0, n, f) = n

For an at-market (par) swap the flat zero rate equals the fixed rate, so
discounting at ``fixed_rate_pct`` is internally consistent.

Conventions
-----------
USD SOFR fixed-vs-float swaps trade with **annual** payments on both legs on an
ACT/360 basis, so ``payment_frequency_per_year`` defaults to 1. Legacy USD LIBOR
swaps used a semi-annual 30/360 fixed leg (``f = 2``).

Limitations (deliberate, and material to how far you can trust the output)
--------------------------------------------------------------------------
- **Flat-curve approximation.** A single flat rate replaces a bootstrapped
  discount curve. For a par swap on a gently sloped curve the annuity error is
  small; for an off-market swap, a steep/inverted curve, or a forward-starting
  swap it is not. Book-level risk, margin and CVA must come from a curve-based
  engine, not from this module.
- **Idealised accrual periods.** Each period accrues ``1 / f`` years. Real
  ACT/360 annual periods accrue ~365/360, which understates the annuity here by
  roughly 1.4% for USD SOFR. A fractional final period is treated as a pro-rata
  stub.
- **Floating leg treated as par at reset.** Its residual DV01 (time to the next
  reset, typically under a day for a compounded SOFR leg) is ignored, which
  slightly overstates the swap's DV01 magnitude.
- **First order only.** DV01 is linear. Convexity is not modelled, so the
  ``+10 bps`` P&L figure degrades for large shocks (materially beyond ~100 bps).
- **Parallel shifts only.** No key-rate/bucketed DV01, so a DV01-neutral
  portfolio here is *not* neutral to curve steepening or twists.
- **Single currency.** Aggregation is USD-only and is refused for any other
  currency; DV01s across curves are not FX-additive.
- **Equities carry no DV01 in this model.** ``equities_notional_usd`` is
  contextual only; equity rate beta is real but out of scope.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: One basis point expressed as a decimal rate.
BASIS_POINT = 0.0001

#: The two sides of a vanilla fixed-vs-float swap.
PAY_FIXED = "PAY_FIXED"
RECEIVE_FIXED = "RECEIVE_FIXED"
SUPPORTED_PAY_RECEIVE_TYPES = frozenset({PAY_FIXED, RECEIVE_FIXED})

#: Payment frequencies (payments per year) this module supports.
SUPPORTED_PAYMENT_FREQUENCIES = frozenset({1, 2, 4, 12})

#: Settlement currency implied by each floating index. Used to reject silent
#: cross-currency aggregation, which is the classic way a "hedged" book turns
#: out to be unhedged.
INDEX_CURRENCY_MAP = {
    "SOFR": "USD",
    "USD-LIBOR": "USD",
    "ESTR": "EUR",
    "EURIBOR": "EUR",
    "SONIA": "GBP",
    "TONA": "JPY",
    "SARON": "CHF",
}

#: Reporting/aggregation currency. DV01 is only additive within one curve.
BASE_CURRENCY = "USD"

#: Default hedge instrument when the caller does not supply one.
DEFAULT_HEDGE_TENOR_YEARS = 5.0
DEFAULT_HEDGE_PAR_RATE_PCT = 4.0

#: Below this absolute rate the annuity closed form is replaced by its limit.
ZERO_RATE_TOLERANCE = 1e-12

#: Net DV01 magnitudes at or below this are treated as already neutral.
DV01_NEUTRAL_TOLERANCE_USD = 1e-6


def _require_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf, which otherwise propagate silently into every DV01."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number, got {value!r}.")
    return numeric


def swap_annuity_factor(
    fixed_rate_pct: float,
    tenor_years: float,
    payment_frequency_per_year: int = 1,
) -> float:
    """
    Fixed-leg annuity ``A = sum_i (delta_i * DF_i)`` under a flat curve.

    ``A(y, n, f) = (1 / y) * (1 - (1 + y / f) ** (-n * f))``, with the removable
    singularity at ``y = 0`` replaced by its limit ``A = n``. Negative rates are
    supported (EUR/JPY/CHF) provided ``1 + y / f > 0``.

    Returns the annuity in years; multiply by notional and ``BASIS_POINT`` to
    obtain the swap's unsigned DV01.
    """
    rate_pct = _require_finite(fixed_rate_pct, "fixed_rate_pct")
    tenor = _require_finite(tenor_years, "tenor_years")

    if tenor <= 0.0:
        raise ValueError(f"tenor_years must be strictly positive, got {tenor_years!r}.")

    frequency = int(payment_frequency_per_year)
    if frequency not in SUPPORTED_PAYMENT_FREQUENCIES:
        raise ValueError(
            f"payment_frequency_per_year must be one of "
            f"{sorted(SUPPORTED_PAYMENT_FREQUENCIES)}, got {payment_frequency_per_year!r}."
        )

    rate = rate_pct / 100.0
    period_growth = 1.0 + rate / frequency
    if period_growth <= 0.0:
        raise ValueError(
            f"fixed_rate_pct={fixed_rate_pct!r} at frequency {frequency} implies a "
            f"non-positive discount factor (1 + y/f = {period_growth}); rate is out of range."
        )

    if abs(rate) < ZERO_RATE_TOLERANCE:
        return tenor

    periods = tenor * frequency
    # log1p/expm1 keep precision for the small-rate and long-tenor cases.
    return -math.expm1(-periods * math.log(period_growth)) / rate


@dataclass
class IrsPositionSpec:
    swap_id: str                            # e.g. 'IRS_SOFR_5Y_001'
    notional_usd: float                     # >= 0; direction is carried by pay_receive_type
    pay_receive_type: str                   # 'PAY_FIXED' (Pay Fixed, Rec Float) or 'RECEIVE_FIXED'
    fixed_rate_pct: float                   # e.g. 4.25 (percent, not decimal)
    tenor_years: float                      # Remaining tenor in years, not original tenor
    floating_rate_index: str = "SOFR"       # SOFR, ESTR, EURIBOR, SONIA, TONA, SARON
    payment_frequency_per_year: int = 1     # SOFR fixed-vs-float is annual; legacy LIBOR was 2
    currency: str = BASE_CURRENCY           # Must be consistent with floating_rate_index


@dataclass
class IrsHedgeSpec:
    """The par swap used to flatten residual DV01. Its annuity sets the notional."""
    tenor_years: float = DEFAULT_HEDGE_TENOR_YEARS
    fixed_rate_pct: float = DEFAULT_HEDGE_PAR_RATE_PCT
    payment_frequency_per_year: int = 1


@dataclass
class MultiAssetPortfolioRisk:
    bonds_dv01_usd: float                   # SIGNED P&L per +1 bps; NEGATIVE for a long bond book
    equities_notional_usd: float            # Contextual only - contributes no DV01 in this model
    irs_positions: List[IrsPositionSpec]
    hedge_instrument: Optional[IrsHedgeSpec] = None


@dataclass
class InterestRateSwapExposureReport:
    total_irs_notional_usd: float
    total_irs_dv01_usd: float               # USD gain/loss per +1 bps parallel rise
    bonds_dv01_usd: float
    net_portfolio_dv01_usd: float
    pnl_impact_plus_10bps_usd: float        # Linear (no convexity) P&L for +10 bps
    required_hedge_irs_notional_usd: float  # SIGNED: > 0 pay-fixed, < 0 receive-fixed
    status: str                             # 'IRS_RISK_AUDIT_SUCCESS'
    audit_notes: str
    required_hedge_side: str = "NONE"              # 'PAY_FIXED' | 'RECEIVE_FIXED' | 'NONE'
    required_hedge_notional_abs_usd: float = 0.0   # Magnitude to trade on required_hedge_side
    hedge_tenor_years: float = DEFAULT_HEDGE_TENOR_YEARS
    hedge_par_rate_pct: float = DEFAULT_HEDGE_PAR_RATE_PCT
    hedge_annuity_factor: float = 0.0
    hedge_rate_is_default: bool = False            # True when no hedge instrument was supplied


class InterestRateSwapExposureEngine:
    """
    First-order interest-rate risk engine for vanilla IRS held in a multi-asset
    portfolio: signed swap DV01, aggregation with bond DV01, and the par-swap
    notional that flattens net DV01.

    All DV01s are signed P&L per +1 bps parallel rise (see the module docstring).
    """

    def _validate_position(self, spec: IrsPositionSpec) -> Tuple[str, float]:
        """Validates a swap spec and returns (normalised side, coerced notional)."""
        type_clean = str(spec.pay_receive_type).strip().upper()
        if type_clean not in SUPPORTED_PAY_RECEIVE_TYPES:
            raise ValueError(
                f"Swap '{spec.swap_id}': invalid swap type '{spec.pay_receive_type}'. "
                f"Must be '{PAY_FIXED}' or '{RECEIVE_FIXED}'."
            )

        notional = _require_finite(spec.notional_usd, f"Swap '{spec.swap_id}' notional_usd")
        if notional < 0.0:
            raise ValueError(
                f"Swap '{spec.swap_id}': notional_usd must be >= 0 (direction is carried by "
                f"pay_receive_type, not by the sign of notional), got {spec.notional_usd!r}."
            )

        index_clean = str(spec.floating_rate_index).strip().upper()
        currency_clean = str(spec.currency).strip().upper()
        expected_currency = INDEX_CURRENCY_MAP.get(index_clean)
        if expected_currency is None:
            raise ValueError(
                f"Swap '{spec.swap_id}': unknown floating_rate_index "
                f"'{spec.floating_rate_index}'. Supported: {sorted(INDEX_CURRENCY_MAP)}."
            )
        if expected_currency != currency_clean:
            raise ValueError(
                f"Swap '{spec.swap_id}': floating_rate_index '{index_clean}' settles in "
                f"{expected_currency} but currency is '{currency_clean}'."
            )
        if currency_clean != BASE_CURRENCY:
            raise ValueError(
                f"Swap '{spec.swap_id}': DV01 is only additive within a single curve. "
                f"This engine aggregates in {BASE_CURRENCY}; convert the {currency_clean} "
                f"DV01 at the spot FX rate and aggregate it outside this engine."
            )
        return type_clean, notional

    def calculate_swap_dv01(self, spec: IrsPositionSpec) -> float:
        """
        Signed swap DV01: USD P&L for a +1 bps parallel rise in the swap curve.

        ``DV01 = +/- notional * annuity * 0.0001``, positive for ``PAY_FIXED``
        (short duration, gains as rates rise) and negative for ``RECEIVE_FIXED``.

        Raises ValueError on an unknown side, an unknown floating index, a
        non-USD currency, a negative or non-finite notional, or a non-positive
        tenor.
        """
        type_clean, notional = self._validate_position(spec)

        annuity = swap_annuity_factor(
            spec.fixed_rate_pct, spec.tenor_years, spec.payment_frequency_per_year
        )
        base_dv01 = notional * annuity * BASIS_POINT

        return base_dv01 if type_clean == PAY_FIXED else -base_dv01

    def analyze_portfolio_irs_exposure(
        self,
        portfolio: MultiAssetPortfolioRisk,
    ) -> InterestRateSwapExposureReport:
        """
        Aggregates signed DV01 across bonds and IRS positions and sizes the par
        swap required for net DV01 neutrality.

        ``bonds_dv01_usd`` must already be signed as P&L per +1 bps rise
        (negative for a long bond book).
        """
        bonds_dv01 = _require_finite(portfolio.bonds_dv01_usd, "bonds_dv01_usd")
        _require_finite(portfolio.equities_notional_usd, "equities_notional_usd")

        total_irs_notional = sum(float(p.notional_usd) for p in portfolio.irs_positions)
        total_irs_dv01 = sum(self.calculate_swap_dv01(p) for p in portfolio.irs_positions)

        net_portfolio_dv01 = bonds_dv01 + total_irs_dv01

        # Linear (first-order) P&L. No convexity - see module limitations.
        pnl_plus_10bps = net_portfolio_dv01 * 10.0

        hedge = portfolio.hedge_instrument
        hedge_rate_is_default = hedge is None
        if hedge is None:
            hedge = IrsHedgeSpec()
            logger.warning(
                "No hedge_instrument supplied; sizing the DV01-neutral hedge off a default "
                "%.1fY par swap at %.2f%%. The hedge annuity - and therefore the notional - "
                "depends on the par rate, so supply the live market rate before trading.",
                hedge.tenor_years,
                hedge.fixed_rate_pct,
            )

        hedge_annuity = swap_annuity_factor(
            hedge.fixed_rate_pct, hedge.tenor_years, hedge.payment_frequency_per_year
        )
        hedge_dv01_per_dollar = hedge_annuity * BASIS_POINT

        # Trade the DV01 that offsets the residual: positive notional = pay-fixed.
        # Side is derived from the *rounded* notional so that "NONE" and a zero
        # notional always agree - never emit a directional instruction to trade $0.
        required_hedge_notional = round(-net_portfolio_dv01 / hedge_dv01_per_dollar, 2)
        if abs(net_portfolio_dv01) <= DV01_NEUTRAL_TOLERANCE_USD or required_hedge_notional == 0.0:
            hedge_side = "NONE"
            required_hedge_notional = 0.0
        elif required_hedge_notional > 0.0:
            hedge_side = PAY_FIXED
        else:
            hedge_side = RECEIVE_FIXED

        notes = (
            f"IRS RISK REPORT: Total IRS Notional = ${total_irs_notional:,.0f}, "
            f"IRS DV01 = ${total_irs_dv01:,.2f}/bps, Bonds DV01 = ${bonds_dv01:,.2f}/bps "
            f"(signed P&L per +1 bps rise). "
            f"Net Portfolio DV01 = ${net_portfolio_dv01:,.2f}/bps. "
            f"Linear PnL for +10 bps Rate Rise = ${pnl_plus_10bps:,.2f} USD. "
            f"DV01-Neutral Hedge = {hedge_side} ${abs(required_hedge_notional):,.2f} on a "
            f"{hedge.tenor_years:g}Y par swap at {hedge.fixed_rate_pct:.2f}% "
            f"(annuity {hedge_annuity:.4f})."
        )
        logger.info(notes)

        return InterestRateSwapExposureReport(
            total_irs_notional_usd=total_irs_notional,
            total_irs_dv01_usd=round(total_irs_dv01, 2),
            bonds_dv01_usd=bonds_dv01,
            net_portfolio_dv01_usd=round(net_portfolio_dv01, 2),
            pnl_impact_plus_10bps_usd=round(pnl_plus_10bps, 2),
            required_hedge_irs_notional_usd=required_hedge_notional,
            status="IRS_RISK_AUDIT_SUCCESS",
            audit_notes=notes,
            required_hedge_side=hedge_side,
            required_hedge_notional_abs_usd=abs(required_hedge_notional),
            hedge_tenor_years=hedge.tenor_years,
            hedge_par_rate_pct=hedge.fixed_rate_pct,
            hedge_annuity_factor=hedge_annuity,
            hedge_rate_is_default=hedge_rate_is_default,
        )
