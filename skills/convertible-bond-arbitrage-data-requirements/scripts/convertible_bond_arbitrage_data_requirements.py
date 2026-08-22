"""Convertible bond (CB) arbitrage data-completeness auditing and metric computation.

Scope: this module is a *data requirements and screening* helper, not a convertible
bond pricing model. Equity delta and implied volatility are **inputs** here; they are
expected to come from a dedicated CB model (binomial / Tsiveriotis-Fernandes style)
or from a vendor analytics feed.

All money amounts are per single bond, in the bond's currency of denomination, unless
a name explicitly says otherwise. All rates are annualized decimals (0.045 == 4.5%).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

PremiumBasis = Literal["clean", "full"]


@dataclass(frozen=True)
class ConvertibleBondData:
    """Static terms plus the CB's own market data.

    Attributes:
        clean_price: Quoted price in points of par (e.g. 98.50 means 98.50% of par).
        par_value: Nominal redemption amount per bond (e.g. 1000.0).
        conversion_ratio: Shares delivered per bond on conversion. Must be quoted
            against the same nominal as ``par_value`` -- ratios quoted "per 100
            nominal" must be rescaled before being passed in.
        coupon_rate_annual: Annual coupon as a decimal of par (0.035 == 3.5%).
        years_to_maturity: Remaining time to maturity/redemption in years.
        accrued_interest: Accrued interest per bond in currency (not points of par).
            Required only when the engine is configured with ``premium_basis="full"``.
        credit_spread_bps: Issuer credit spread over the risk-free curve, in basis
            points, used to discount the straight-bond cash flows (bond floor).
        coupon_frequency: Coupon payments per year (1 = annual, 2 = semi-annual).
    """

    bond_id: str
    symbol: str
    clean_price: float
    par_value: float
    conversion_ratio: float
    coupon_rate_annual: float
    years_to_maturity: float
    accrued_interest: float = 0.0
    credit_spread_bps: Optional[float] = None
    coupon_frequency: int = 2


@dataclass(frozen=True)
class EquityMarketData:
    """Market data for the underlying equity (the short leg).

    Attributes:
        stock_price: Spot price per share.
        annual_borrow_fee_pct: Securities-lending loan fee on the borrowed shares,
            as an annualized decimal of the *short position market value*.
        historical_volatility: Annualized realized/historical volatility (0.30 == 30%).
        annual_dividend_yield: Expected annualized dividend yield. A short seller owes
            the lender substitute payments ("payments in lieu of dividends") equal to
            the dividends paid, so this is a carry *cost* of the hedge.
    """

    symbol: str
    stock_price: float
    annual_borrow_fee_pct: float
    historical_volatility: float
    annual_dividend_yield: float = 0.0


@dataclass(frozen=True)
class ScreenThresholds:
    """Screening thresholds for :meth:`ConvertibleBondArbitrageEngine.evaluate_arbitrage`.

    These defaults are *illustrative desk heuristics, not standards*. There is no
    regulatory or academic basis for any specific value; calibrate them against your
    own book, universe liquidity and funding costs before using them to trade.
    """

    min_vol_discount: float = 0.03             # HV - IV required to call the option cheap
    max_conversion_premium_pct: float = 25.0   # above this, equity upside is thin
    min_net_carry_rate: float = -0.02          # tolerated negative carry to own the gamma
    busted_parity_to_floor_ratio: float = 0.5  # parity below this * bond floor => busted


@dataclass(frozen=True)
class CarryBreakdown:
    """Annualized carry of the hedged package, per bond, in currency.

    ``net_carry_currency`` is the algebraic sum of the components; ``net_carry_rate``
    expresses it as a rate on the capital committed to the long CB leg (the CB full
    price), which is the convention used by ``net_carry_rate_pct`` in
    :class:`ConvertibleArbitrageMetrics`.
    """

    coupon_income: float
    short_proceeds_interest: float
    bond_financing_cost: float
    stock_borrow_cost: float
    dividends_in_lieu_cost: float
    short_leg_market_value: float
    net_carry_currency: float
    net_carry_rate: float


@dataclass(frozen=True)
class DataCompletenessReport:
    """Result of the pre-trade data audit (workflow step 1)."""

    is_complete: bool
    missing_fields: Tuple[str, ...]
    invalid_fields: Tuple[str, ...]

    def raise_if_incomplete(self) -> None:
        if not self.is_complete:
            raise ValueError(
                "Incomplete CB arbitrage input data. "
                f"missing={list(self.missing_fields)} invalid={list(self.invalid_fields)}"
            )


@dataclass(frozen=True)
class ConvertibleArbitrageMetrics:
    parity_conversion_value: float
    conversion_premium_pct: float
    option_delta: float
    optimal_short_stock_shares: int
    net_carry_rate_pct: float
    is_arbitrage_attractive: bool
    rationale: str
    carry: CarryBreakdown
    bond_floor: Optional[float] = None
    premium_over_bond_floor_pct: Optional[float] = None
    is_busted_convert: Optional[bool] = None
    residual_delta_shares: float = 0.0
    warnings: Tuple[str, ...] = field(default_factory=tuple)


def _is_finite_positive(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value) and value > 0.0


def _is_finite(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value)


class ConvertibleBondArbitrageEngine:
    """Audits CB arbitrage input data and computes parity, premium, hedge size and carry.

    Delta convention (read this before sizing a hedge)
    --------------------------------------------------
    ``delta`` throughout this module is the **per-share equity delta in [0, 1]** --
    the sensitivity of the CB price to the underlying, expressed as a fraction of the
    conversion ratio (sometimes written ``delta% = delta_shares / conversion_ratio``).
    Convertible desks and some vendor feeds also quote delta as *shares per bond*, a
    number in ``[0, conversion_ratio]``. Feeding a shares-per-bond delta into
    :meth:`calculate_delta_hedge_quantity` over-hedges by a factor of the conversion
    ratio; the ``[0, 1]`` bound is enforced specifically to catch that mistake.
    """

    def __init__(
        self,
        risk_free_rate: float = 0.04,
        repo_financing_rate: float = 0.045,
        short_proceeds_rate: Optional[float] = None,
        short_proceeds_credit_ratio: float = 1.0,
        premium_basis: PremiumBasis = "clean",
        thresholds: Optional[ScreenThresholds] = None,
    ) -> None:
        """
        Args:
            risk_free_rate: Benchmark curve level used to discount straight-bond cash
                flows (with the issuer credit spread added on top).
            repo_financing_rate: Rate paid to finance the long CB position.
            short_proceeds_rate: Gross rate earned on short-sale proceeds held as
                collateral. Defaults to ``risk_free_rate``. The loan fee is charged
                separately via ``EquityMarketData.annual_borrow_fee_pct``, so the net
                rebate is ``short_proceeds_rate - annual_borrow_fee_pct`` -- negative
                for hard-to-borrow names. Prime-broker specific: confirm before use.
            short_proceeds_credit_ratio: Fraction of short proceeds on which the prime
                broker actually pays interest (haircut). 1.0 = full credit.
            premium_basis: ``"clean"`` computes conversion premium from the quoted
                clean price (the common market convention); ``"full"`` uses clean +
                accrued interest. The two differ by up to a full coupon period of
                accrual, so the basis must be stated wherever a premium is reported.
            thresholds: Screening thresholds. See :class:`ScreenThresholds` -- the
                defaults are heuristics, not standards.

        Raises:
            ValueError: if any rate is not finite, or the credit ratio is outside [0, 1].
        """
        for name, value in (
            ("risk_free_rate", risk_free_rate),
            ("repo_financing_rate", repo_financing_rate),
        ):
            if not _is_finite(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}.")
        if short_proceeds_rate is not None and not _is_finite(short_proceeds_rate):
            raise ValueError("short_proceeds_rate must be a finite number.")
        if not (
            _is_finite(short_proceeds_credit_ratio)
            and 0.0 <= short_proceeds_credit_ratio <= 1.0
        ):
            raise ValueError("short_proceeds_credit_ratio must be within [0.0, 1.0].")
        if premium_basis not in ("clean", "full"):
            raise ValueError("premium_basis must be either 'clean' or 'full'.")

        self.risk_free_rate = risk_free_rate
        self.repo_financing_rate = repo_financing_rate
        self.short_proceeds_rate = (
            risk_free_rate if short_proceeds_rate is None else short_proceeds_rate
        )
        self.short_proceeds_credit_ratio = short_proceeds_credit_ratio
        self.premium_basis: PremiumBasis = premium_basis
        self.thresholds = thresholds or ScreenThresholds()

    # ------------------------------------------------------------------ audit

    def audit_data_completeness(
        self,
        bond: ConvertibleBondData,
        equity: EquityMarketData,
        implied_volatility: Optional[float],
        estimated_delta: Optional[float],
    ) -> DataCompletenessReport:
        """Workflow step 1: verify every input the downstream metrics depend on.

        ``missing_fields`` lists inputs that are absent (``None``); ``invalid_fields``
        lists inputs that are present but non-finite or outside their admissible range.
        Fields that are optional for the current configuration (accrued interest under
        ``premium_basis="clean"``, credit spread when no bond floor is wanted) are not
        reported as missing.
        """
        missing: List[str] = []
        invalid: List[str] = []

        for name, value in (
            ("bond.clean_price", bond.clean_price),
            ("bond.par_value", bond.par_value),
            ("bond.conversion_ratio", bond.conversion_ratio),
            ("equity.stock_price", equity.stock_price),
        ):
            if value is None:
                missing.append(name)
            elif not _is_finite_positive(value):
                invalid.append(name)

        for name, value in (
            ("bond.coupon_rate_annual", bond.coupon_rate_annual),
            ("bond.years_to_maturity", bond.years_to_maturity),
            ("equity.annual_borrow_fee_pct", equity.annual_borrow_fee_pct),
            ("equity.annual_dividend_yield", equity.annual_dividend_yield),
            ("bond.accrued_interest", bond.accrued_interest),
        ):
            if value is None:
                missing.append(name)
            elif not (_is_finite(value) and value >= 0.0):
                invalid.append(name)

        if bond.coupon_frequency is None:
            missing.append("bond.coupon_frequency")
        elif bond.coupon_frequency < 1:
            invalid.append("bond.coupon_frequency")

        for name, value in (
            ("equity.historical_volatility", equity.historical_volatility),
            ("implied_volatility", implied_volatility),
        ):
            if value is None:
                missing.append(name)
            elif not (_is_finite(value) and value >= 0.0):
                invalid.append(name)

        if estimated_delta is None:
            missing.append("estimated_delta")
        elif not (_is_finite(estimated_delta) and 0.0 <= estimated_delta <= 1.0):
            invalid.append("estimated_delta")

        report = DataCompletenessReport(
            is_complete=not missing and not invalid,
            missing_fields=tuple(missing),
            invalid_fields=tuple(invalid),
        )
        if not report.is_complete:
            logger.warning(
                "CB arbitrage data audit failed for %s: missing=%s invalid=%s",
                bond.bond_id,
                report.missing_fields,
                report.invalid_fields,
            )
        return report

    # ------------------------------------------------------------------ pricing

    def cb_market_price(
        self, bond: ConvertibleBondData, basis: Optional[PremiumBasis] = None
    ) -> float:
        """CB price per bond in currency: ``clean_price / 100 * par`` (+ accrued if full)."""
        if not _is_finite_positive(bond.clean_price) or not _is_finite_positive(bond.par_value):
            raise ValueError("clean_price and par_value must be finite and positive.")
        price = (bond.clean_price / 100.0) * bond.par_value
        if (basis or self.premium_basis) == "full":
            if not (_is_finite(bond.accrued_interest) and bond.accrued_interest >= 0.0):
                raise ValueError(
                    "accrued_interest must be finite and non-negative for the 'full' basis."
                )
            price += bond.accrued_interest
        return price

    def calculate_parity(self, conversion_ratio: float, stock_price: float) -> float:
        """Parity (conversion value) = conversion ratio x stock price, per bond."""
        if not _is_finite_positive(conversion_ratio) or not _is_finite_positive(stock_price):
            raise ValueError("Conversion ratio and stock price must be finite and positive.")
        return conversion_ratio * stock_price

    def calculate_conversion_premium_pct(self, cb_market_price: float, parity: float) -> float:
        """Conversion premium in percent: ``(CB price - parity) / parity * 100``.

        ``cb_market_price`` must be on the basis being reported (see ``premium_basis``);
        this method does not know which one the caller used.
        """
        if not _is_finite(cb_market_price):
            raise ValueError("cb_market_price must be a finite number.")
        if not _is_finite_positive(parity):
            raise ValueError("Parity must be finite and positive.")
        return ((cb_market_price - parity) / parity) * 100.0

    def calculate_bond_floor(
        self,
        bond: ConvertibleBondData,
        credit_spread_bps: Optional[float] = None,
    ) -> float:
        """Bond floor (investment value): PV of the straight-bond cash flows, per bond.

        Coupons and principal are discounted at ``risk_free_rate + credit spread``,
        compounded at the coupon frequency. Cash flows are laid out backwards from
        maturity in ``1 / coupon_frequency`` year steps, so a fractional first period
        is handled correctly. The conversion option is ignored by construction -- the
        floor is what the CB is worth if the option expires worthless and the issuer
        performs.

        Raises:
            ValueError: if no credit spread is available, terms are non-finite, or the
                discount rate is <= -100% per period.
        """
        spread_bps = credit_spread_bps if credit_spread_bps is not None else bond.credit_spread_bps
        if spread_bps is None or not _is_finite(spread_bps):
            raise ValueError(
                "credit_spread_bps is required to compute the bond floor; it is a core "
                "CB arbitrage data input and must not be defaulted to zero."
            )
        if not _is_finite_positive(bond.par_value):
            raise ValueError("par_value must be finite and positive.")
        if not (_is_finite(bond.years_to_maturity) and bond.years_to_maturity >= 0.0):
            raise ValueError("years_to_maturity must be finite and non-negative.")
        if bond.coupon_frequency < 1:
            raise ValueError("coupon_frequency must be at least 1.")
        if not (_is_finite(bond.coupon_rate_annual) and bond.coupon_rate_annual >= 0.0):
            raise ValueError("coupon_rate_annual must be finite and non-negative.")

        freq = int(bond.coupon_frequency)
        discount_rate = self.risk_free_rate + spread_bps / 10_000.0
        period_rate = discount_rate / freq
        if period_rate <= -1.0:
            raise ValueError("Discount rate implies a non-positive discount factor.")

        coupon_payment = bond.coupon_rate_annual * bond.par_value / freq
        step = 1.0 / freq
        pv = 0.0
        remaining = bond.years_to_maturity
        # Walk backwards from maturity so a stub first period is priced at its actual
        # remaining time rather than being rounded up to a whole coupon period.
        while remaining > 1e-9:
            pv += coupon_payment / ((1.0 + period_rate) ** (remaining * freq))
            remaining -= step
        pv += bond.par_value / ((1.0 + period_rate) ** (bond.years_to_maturity * freq))
        return pv

    # ------------------------------------------------------------------ hedging

    def calculate_delta_hedge_quantity(
        self,
        cb_quantity: int,
        conversion_ratio: float,
        delta: float,
        lot_size: int = 1,
    ) -> int:
        """Shares to short = ``cb_quantity x conversion_ratio x delta``, rounded to a lot.

        ``delta`` is the per-share delta in ``[0, 1]`` -- see the class docstring; a
        shares-per-bond delta passed here would over-hedge by the conversion ratio.
        Rounding is half-up and deterministic; the un-hedged remainder is available
        from :meth:`residual_delta_shares`.

        Raises:
            ValueError: on a negative CB quantity, a delta outside [0, 1], a
                non-positive lot size, or non-finite inputs.
        """
        if cb_quantity < 0:
            raise ValueError(
                "cb_quantity must be non-negative; short-CB packages are out of scope."
            )
        if not _is_finite_positive(conversion_ratio):
            raise ValueError("conversion_ratio must be finite and positive.")
        if not _is_finite(delta) or delta < 0.0 or delta > 1.0:
            raise ValueError(
                f"Delta must be the per-share delta in [0.0, 1.0], got {delta!r}. "
                "A shares-per-bond delta (range [0, conversion_ratio]) must be divided "
                "by the conversion ratio first."
            )
        if lot_size < 1:
            raise ValueError("lot_size must be a positive integer.")

        raw = Decimal(str(cb_quantity)) * Decimal(str(conversion_ratio)) * Decimal(str(delta))
        lots = (raw / Decimal(lot_size)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(lots) * lot_size

    def residual_delta_shares(
        self,
        cb_quantity: int,
        conversion_ratio: float,
        delta: float,
        hedged_shares: int,
    ) -> float:
        """Share-equivalent exposure left un-hedged by lot rounding (positive = long)."""
        return cb_quantity * conversion_ratio * delta - hedged_shares

    # ------------------------------------------------------------------ carry

    def calculate_carry(
        self,
        bond: ConvertibleBondData,
        equity: EquityMarketData,
        delta: float,
    ) -> CarryBreakdown:
        """Annualized carry of one delta-hedged CB package (1 bond + its short hedge).

        Components, all per bond and in currency:

        * ``+ coupon_income``           = coupon rate x par
        * ``+ short_proceeds_interest`` = short proceeds rate x credit ratio x short MV
        * ``- bond_financing_cost``     = repo financing rate x CB full price
        * ``- stock_borrow_cost``       = borrow fee x short MV
        * ``- dividends_in_lieu_cost``  = dividend yield x short MV

        The three hedge-leg terms scale with the **short position market value**
        (``delta x parity``), not with the bond's notional -- applying a stock borrow
        rate directly to bond notional misstates the drag whenever delta or the
        parity/price ratio is far from 1.

        ``net_carry_rate`` divides the net by the CB full price (clean + accrued), i.e.
        the capital committed to the long leg.
        """
        if not _is_finite(delta) or delta < 0.0 or delta > 1.0:
            raise ValueError("Delta must be the per-share delta in [0.0, 1.0].")

        cb_full_price = self.cb_market_price(bond, basis="full")
        parity = self.calculate_parity(bond.conversion_ratio, equity.stock_price)
        short_leg_mv = delta * parity

        coupon_income = bond.coupon_rate_annual * bond.par_value
        short_proceeds_interest = (
            self.short_proceeds_rate * self.short_proceeds_credit_ratio * short_leg_mv
        )
        bond_financing_cost = self.repo_financing_rate * cb_full_price
        stock_borrow_cost = equity.annual_borrow_fee_pct * short_leg_mv
        dividends_in_lieu_cost = equity.annual_dividend_yield * short_leg_mv

        net = (
            coupon_income
            + short_proceeds_interest
            - bond_financing_cost
            - stock_borrow_cost
            - dividends_in_lieu_cost
        )
        return CarryBreakdown(
            coupon_income=coupon_income,
            short_proceeds_interest=short_proceeds_interest,
            bond_financing_cost=bond_financing_cost,
            stock_borrow_cost=stock_borrow_cost,
            dividends_in_lieu_cost=dividends_in_lieu_cost,
            short_leg_market_value=short_leg_mv,
            net_carry_currency=net,
            net_carry_rate=net / cb_full_price,
        )

    # ------------------------------------------------------------------ screening

    def evaluate_arbitrage(
        self,
        bond: ConvertibleBondData,
        equity: EquityMarketData,
        cb_quantity: int,
        implied_volatility: float,
        estimated_delta: float,
        lot_size: int = 1,
    ) -> ConvertibleArbitrageMetrics:
        """Audit the inputs, compute the metric set, and apply the screening thresholds.

        The returned ``is_arbitrage_attractive`` is a *screen*, not a trade decision:
        it says the package passes the configured cheap-vol / premium / carry filters.
        It deliberately does not account for gamma-trading P&L, borrow recall risk,
        issuer call features, or takeover protection.

        Raises:
            ValueError: if :meth:`audit_data_completeness` fails. Screening on partial
                data would silently propagate NaNs into every downstream metric.
        """
        self.audit_data_completeness(
            bond, equity, implied_volatility, estimated_delta
        ).raise_if_incomplete()

        warnings: List[str] = []

        premium_price = self.cb_market_price(bond)
        parity = self.calculate_parity(bond.conversion_ratio, equity.stock_price)
        premium_pct = self.calculate_conversion_premium_pct(premium_price, parity)

        short_shares = self.calculate_delta_hedge_quantity(
            cb_quantity, bond.conversion_ratio, estimated_delta, lot_size=lot_size
        )
        residual = self.residual_delta_shares(
            cb_quantity, bond.conversion_ratio, estimated_delta, short_shares
        )

        carry = self.calculate_carry(bond, equity, estimated_delta)

        bond_floor: Optional[float] = None
        premium_over_floor: Optional[float] = None
        is_busted: Optional[bool] = None
        if bond.credit_spread_bps is None:
            warnings.append(
                "credit_spread_bps not supplied: bond floor, downside protection and "
                "busted-convert detection were not evaluated."
            )
        else:
            bond_floor = self.calculate_bond_floor(bond)
            if bond_floor > 0.0:
                premium_over_floor = (premium_price / bond_floor - 1.0) * 100.0
                is_busted = parity < self.thresholds.busted_parity_to_floor_ratio * bond_floor
                if is_busted:
                    warnings.append(
                        "Parity is far below the bond floor: this is a busted/credit-like "
                        "convert. Equity delta is small and the position is a credit trade; "
                        "vol-arbitrage screening does not apply."
                    )

        vol_discount = equity.historical_volatility - implied_volatility
        t = self.thresholds
        is_attractive = (
            vol_discount > t.min_vol_discount
            and premium_pct < t.max_conversion_premium_pct
            and carry.net_carry_rate > t.min_net_carry_rate
            and not bool(is_busted)
        )

        if self.premium_basis == "full" and bond.accrued_interest == 0.0:
            warnings.append(
                "premium_basis='full' but accrued_interest is 0.0: legitimate only "
                "immediately after a coupon date. Confirm accrued interest was supplied."
            )

        if equity.annual_borrow_fee_pct >= 0.05:
            warnings.append(
                f"Hard-to-borrow underlying ({equity.annual_borrow_fee_pct * 100:.2f}% fee): "
                "borrow cost and recall risk dominate this package's economics."
            )

        rationale = (
            f"Parity={parity:.2f}, Premium({self.premium_basis})={premium_pct:.2f}%, "
            f"NetCarry={carry.net_carry_rate * 100:.2f}%, "
            f"IV={implied_volatility * 100:.1f}% vs HV={equity.historical_volatility * 100:.1f}%"
            + (f", BondFloor={bond_floor:.2f}" if bond_floor is not None else ", BondFloor=n/a")
        )
        logger.info(
            "CB arbitrage screen %s: attractive=%s (%s)", bond.bond_id, is_attractive, rationale
        )

        return ConvertibleArbitrageMetrics(
            parity_conversion_value=round(parity, 2),
            conversion_premium_pct=round(premium_pct, 2),
            option_delta=round(estimated_delta, 4),
            optimal_short_stock_shares=short_shares,
            net_carry_rate_pct=round(carry.net_carry_rate * 100, 2),
            is_arbitrage_attractive=is_attractive,
            rationale=rationale,
            carry=carry,
            bond_floor=None if bond_floor is None else round(bond_floor, 2),
            premium_over_bond_floor_pct=(
                None if premium_over_floor is None else round(premium_over_floor, 2)
            ),
            is_busted_convert=is_busted,
            residual_delta_shares=round(residual, 4),
            warnings=tuple(warnings),
        )
