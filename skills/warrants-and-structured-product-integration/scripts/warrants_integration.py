"""
warrants-and-structured-product-integration: pricing, gearing and delta-hedge
sizing for listed *covered warrants* and *Turbo warrants / CBBCs*.

Two product families with two different models
----------------------------------------------
This engine deliberately does **not** price everything with Black-Scholes.

* **Covered call / put warrants** are vanilla European options issued by a bank.
  They are priced with Black-Scholes-Merton (continuous dividend yield ``q``) and
  scaled by the entitlement ratio.

* **Turbo warrants / CBBCs** are *not* vanilla options. They are down-and-out
  (bull) / up-and-out (bear) barrier products that are always deep in the money
  relative to their strike, and the market prices them by the issuer convention::

      price = entitlement_ratio * [ intrinsic + strike * funding_rate * days/365 ]

  HKEX and the HK issuers describe them as **delta-one** instruments: "All CBBCs
  are in-the-price products with a Delta of approximately 1", and "CBBCs' prices
  are generally higher than the calculated intrinsic values, mainly because
  certain funding costs are included". Pricing a CBBC with a vanilla
  Black-Scholes N(d1) delta systematically *under-hedges* an issued book,
  because N(d1) < 1 always. Version 1.1.0 of this module did exactly that.

Entitlement ratio: this module uses the RECIPROCAL of the exchange quote
-----------------------------------------------------------------------
HKEX quotes the entitlement ratio as **warrants per share** -- "the number of
products required to be converted into a unit of the underlying asset at the
strike price on the expiry date". A term sheet reading *"Entitlement ratio: 10"*
means ten warrants convert into one share.

``WarrantContract.entitlement_ratio`` is the other way round: **shares per
warrant**, so that term sheet is ``entitlement_ratio = 0.1``. Pasting the
exchange's ``10`` straight in is a **100x** error in price, delta and hedge size,
and nothing downstream will flag it -- the numbers stay positive and plausible.
Use :func:`entitlement_ratio_from_conversion_ratio` rather than inverting by hand.

Hedge sign convention
---------------------
``position_warrants`` is **signed**: positive = warrants held long, negative =
warrants sold / issued (the market-maker book). The delta-neutral target is
therefore ``-position_warrants * delta``: a long call-warrant position is hedged
by *selling* the underlying, an issued one by *buying* it. Version 1.1.0 returned
``+position_warrants * delta``, which inverts the hedge for a long holder and
doubles the exposure it was meant to neutralise.

Greek units
-----------
``delta``   -- underlying shares per warrant (already scaled by R).
``gamma``   -- change in ``delta`` per 1.00 move in the underlying.
``theta``   -- change in warrant price per **one calendar day** (365-day year).
``vega``    -- change in warrant price per **one volatility point** (1% = 0.01).
Prices are in the warrant's own quote currency (HKD, EUR, SGD, ...); the engine
never converts. Nothing is rounded -- quantize at the presentation layer.

Scope / what this engine is not
-------------------------------
* European exercise, flat volatility, no smile and no term structure.
* The CBBC model returns ``gamma = vega = 0`` because a delta-one convention has
  no convexity. The *real* product is a barrier option whose gamma spikes
  violently as spot approaches the call price. Use the MCE monitor for
  barrier-proximity risk, not these Greeks.
* Autocallable notes and other path-dependent structured products are **not**
  priced here; they raise :class:`WarrantEngineError`.
* Issuer credit risk is not modelled. Covered warrants and CBBCs are
  non-collateralised obligations of the issuer.

Sources: HKEX "Pricing Parameters and Ratios" FAQ (gearing, effective gearing,
delta, entitlement ratio); HKEX "Introduction to Callable Bull / Bear Contracts";
HK IFEC "A tricky derivative for the market's ups and downs" (funding cost,
residual value based on the strike price). See references/standards.md.
"""

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DAYS_PER_YEAR = 365.0


class WarrantType(Enum):
    COVERED_CALL = "COVERED_CALL"               # Vanilla European covered call warrant
    COVERED_PUT = "COVERED_PUT"                 # Vanilla European covered put warrant
    TURBO_BULL_CBBC = "TURBO_BULL_CBBC"         # Callable Bull Contract (down-and-out)
    TURBO_BEAR_CBBC = "TURBO_BEAR_CBBC"         # Callable Bear Contract (up-and-out)
    AUTOCALLABLE_NOTE = "AUTOCALLABLE_NOTE"     # NOT PRICED -- raises WarrantEngineError


class CBBCCategory(Enum):
    """HKEX CBBC category, which decides what a holder receives on a call.

    ``CATEGORY_N`` -- call price equals the strike price; the holder receives
    nothing on a Mandatory Call Event and loses the entire investment.
    ``CATEGORY_R`` -- call price sits above (bull) / below (bear) the strike,
    leaving a buffer that may pay a residual value.
    """

    NOT_APPLICABLE = "NOT_APPLICABLE"
    CATEGORY_N = "CATEGORY_N"
    CATEGORY_R = "CATEGORY_R"


class SettlementType(Enum):
    CASH_SETTLED = "CASH_SETTLED"
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"


class KnockOutStatus(Enum):
    ACTIVE = "ACTIVE"
    KNOCKED_OUT = "KNOCKED_OUT"
    EXPIRED = "EXPIRED"


class PricingModel(Enum):
    """Which model produced a valuation -- recorded so a mark is auditable."""

    BLACK_SCHOLES_MERTON = "BLACK_SCHOLES_MERTON"
    CBBC_INTRINSIC_PLUS_FUNDING = "CBBC_INTRINSIC_PLUS_FUNDING"
    TERMINATED = "TERMINATED"                   # knocked out or expired


class WarrantEngineError(Exception):
    """Base exception for Warrants Integration Engine errors."""


def standard_normal_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def standard_normal_pdf(x: float) -> float:
    """Probability density function for the standard normal distribution."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def _require_finite(value: float, name: str) -> float:
    """Reject NaN, infinity and non-numeric input at the boundary."""
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise WarrantEngineError(f"{name} must be numeric, got {value!r}") from exc
    if math.isnan(numeric) or math.isinf(numeric):
        raise WarrantEngineError(f"{name} must be finite, got {value!r}")
    return numeric


def _require_positive(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if numeric <= 0.0:
        raise WarrantEngineError(f"{name} must be strictly positive, got {numeric}")
    return numeric


def _require_non_negative(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if numeric < 0.0:
        raise WarrantEngineError(f"{name} must be non-negative, got {numeric}")
    return numeric


def entitlement_ratio_from_conversion_ratio(conversion_ratio: float) -> float:
    """Convert an exchange-quoted conversion ratio into this module's ratio.

    Exchanges quote *warrants per share* (HKEX: "the number of products required
    to be converted into a unit of the underlying asset"). This module wants
    *shares per warrant*. A term sheet reading "Entitlement ratio: 10" becomes
    ``0.1``.

    Raises:
        WarrantEngineError: if ``conversion_ratio`` is not finite and positive.
    """
    ratio = _require_positive(conversion_ratio, "conversion_ratio")
    return 1.0 / ratio


@dataclass
class WarrantContract:
    """Static term-sheet data for one listed warrant or CBBC.

    Attributes:
        entitlement_ratio: **Shares per warrant** -- the reciprocal of the
            exchange-quoted conversion ratio. See the module docstring.
        barrier_price: Call price for Turbo / CBBC contracts. A bull CBBC
            requires ``barrier_price >= strike_price``; a bear CBBC requires
            ``barrier_price <= strike_price``. Ignored for covered warrants.
        funding_rate_annual: Issuer funding rate used to price a CBBC's funding
            cost, as an annualized decimal taken from the launch announcement.
            Ignored for covered warrants. Left at ``0.0`` a CBBC prices at pure
            intrinsic value, which is below every real market quote.
        cbbc_category: Decides whether a Mandatory Call Event pays a residual
            value (``CATEGORY_R``) or nothing (``CATEGORY_N``).
        implied_volatility, risk_free_rate, dividend_yield: Black-Scholes inputs
            for covered warrants only, as annualized decimals.
    """

    warrant_id: str
    symbol: str
    underlying_symbol: str
    warrant_type: WarrantType
    strike_price: float
    barrier_price: float = 0.0
    entitlement_ratio: float = 0.1              # 0.1 == exchange conversion ratio of 10
    days_to_expiry: int = 90
    risk_free_rate: float = 0.03
    implied_volatility: float = 0.30
    dividend_yield: float = 0.0
    funding_rate_annual: float = 0.0
    cbbc_category: CBBCCategory = CBBCCategory.NOT_APPLICABLE
    settlement_type: SettlementType = SettlementType.CASH_SETTLED


@dataclass
class WarrantValuation:
    """Model output for one warrant. See the module docstring for Greek units."""

    warrant_id: str
    underlying_symbol: str
    spot_price: float
    fair_price: float
    intrinsic_value: float
    time_value: float                           # may be negative for a deep-ITM European put
    delta: float                                # underlying shares per warrant
    gamma: float
    theta: float                                # per calendar day
    vega: float                                 # per 1 volatility point
    simple_gearing: float
    effective_gearing: float
    gearing_basis_price: float                  # price the gearing was computed against
    status: KnockOutStatus
    pricing_model: PricingModel
    residual_value: float = 0.0                 # MCE / expiry settlement, per warrant


@dataclass
class DeltaHedgeSignal:
    """Delta-neutral hedge instruction. All share counts are signed.

    ``warrant_book_delta_shares`` is the warrant book's own equity-equivalent
    exposure; ``required_underlying_delta_shares`` is its negation, i.e. the
    target underlying position that neutralises it.
    """

    warrant_id: str
    underlying_symbol: str
    position_warrants: int                      # signed: + long warrants, - issued/short
    warrant_book_delta_shares: float
    required_underlying_delta_shares: float
    current_underlying_hedged_shares: float
    net_rebalance_shares: float
    action: str                                 # "BUY", "SELL", "HOLD"


class WarrantsIntegrationEngine:
    """Prices covered warrants and Turbo warrants / CBBCs and sizes delta hedges.

    Covered warrants use Black-Scholes-Merton with a continuous dividend yield;
    Turbo warrants / CBBCs use the issuer's intrinsic-plus-funding-cost
    convention with a delta of one underlying unit per warrant. Both are scaled
    by the entitlement ratio. Mandatory Call Events are evaluated on every
    valuation, and Category R residual value is reported.
    """

    def __init__(self) -> None:
        logger.info("Initialized Warrants & Structured Product Integration Engine")

    # ------------------------------------------------------------------ pricing

    def price_warrant(
        self,
        spot_price: float,
        contract: WarrantContract,
        market_price: Optional[float] = None,
    ) -> WarrantValuation:
        """Value one warrant and compute its gearing.

        Args:
            spot_price: Current price of the underlying, in the underlying's
                quote currency.
            contract: Term-sheet data. Validated on every call.
            market_price: Traded price of the *warrant*. When supplied, gearing
                is computed against it rather than against the theoretical
                price -- which is what an exchange publishes, and the only
                meaningful basis when the theoretical price sits below the
                minimum tick.

        Returns:
            A :class:`WarrantValuation`. For a called CBBC, ``residual_value``
            is **provisional**: it is computed from ``spot_price``, whereas the
            exchange settles a bull CBBC on the *lowest* underlying price over
            the MCE valuation period. The realized residual is therefore at most
            the provisional one. Recompute with
            :meth:`mandatory_call_residual_value` once that period closes.

        Raises:
            WarrantEngineError: on any invalid input, an inconsistent CBBC
                barrier, or an unsupported product type.
        """
        spot = _require_positive(spot_price, "spot_price")
        self._validate_contract(contract)
        if market_price is not None:
            # Validated here, not only inside _gearing: a terminated contract
            # skips the gearing path, and validation must not depend on which
            # branch the contract happens to take.
            _require_positive(market_price, "market_price")

        if contract.warrant_type is WarrantType.AUTOCALLABLE_NOTE:
            raise WarrantEngineError(
                "AUTOCALLABLE_NOTE is not priced by this engine: an autocallable is a "
                "path-dependent structured note requiring a coupon/observation schedule "
                "and a barrier model, not a single-strike Black-Scholes valuation."
            )

        is_cbbc = contract.warrant_type in (
            WarrantType.TURBO_BULL_CBBC,
            WarrantType.TURBO_BEAR_CBBC,
        )

        # A Mandatory Call Event is checked before expiry: it can fire on the
        # last trading day, and it terminates the contract either way.
        if is_cbbc and self.is_mandatory_call_triggered(spot, contract):
            return self._terminated_valuation(
                contract,
                spot,
                KnockOutStatus.KNOCKED_OUT,
                residual_value=self.mandatory_call_residual_value(contract, spot),
                market_price=market_price,
                log_reason=(
                    f"MANDATORY CALL EVENT [{contract.symbol}]: spot {spot:.4f} breached "
                    f"call price {contract.barrier_price:.4f}"
                ),
            )

        if contract.days_to_expiry <= 0:
            return self._terminated_valuation(
                contract,
                spot,
                KnockOutStatus.EXPIRED,
                residual_value=self._intrinsic(spot, contract),
                market_price=market_price,
                log_reason=(
                    f"EXPIRED [{contract.symbol}]: days_to_expiry="
                    f"{contract.days_to_expiry}; settle, do not price"
                ),
            )

        if is_cbbc:
            return self._price_cbbc(spot, contract, market_price)
        return self._price_black_scholes(spot, contract, market_price)

    def is_mandatory_call_triggered(self, spot_price: float, contract: WarrantContract) -> bool:
        """True when spot has breached a Turbo / CBBC call price.

        Bull contracts are called at ``spot <= call price``, bear contracts at
        ``spot >= call price``. Both comparisons are inclusive: touching the
        call price triggers the call. Always ``False`` for a covered warrant.

        The call price is validated here as well as in :meth:`price_warrant`,
        because this method is the tick-by-tick monitor and is called directly.
        A NaN barrier would otherwise compare ``False`` forever and silently
        disable the monitor on the one contract that needs it.
        """
        spot = _require_positive(spot_price, "spot_price")
        if contract.warrant_type is WarrantType.TURBO_BULL_CBBC:
            return spot <= _require_positive(contract.barrier_price, "barrier_price")
        if contract.warrant_type is WarrantType.TURBO_BEAR_CBBC:
            return spot >= _require_positive(contract.barrier_price, "barrier_price")
        return False

    def mandatory_call_residual_value(
        self, contract: WarrantContract, mce_settlement_price: float
    ) -> float:
        """Residual value per warrant payable after a Mandatory Call Event.

        Category N contracts pay nothing -- their call price equals their
        strike, so there is no buffer. Category R contracts pay
        ``max(0, settlement - strike) * entitlement_ratio`` (bull) or
        ``max(0, strike - settlement) * entitlement_ratio`` (bear), floored at
        zero. The residual is calculated against the **strike price**, not the
        call price.

        Args:
            mce_settlement_price: Underlying price fixed over the MCE valuation
                period -- for a bull contract the *lowest* underlying price in
                the session in which the call occurred and the session after.
                Passing the triggering tick instead gives a provisional upper
                bound, never a lower one.
        """
        settlement = _require_positive(mce_settlement_price, "mce_settlement_price")
        _require_positive(contract.strike_price, "strike_price")
        _require_positive(contract.entitlement_ratio, "entitlement_ratio")
        if contract.cbbc_category is not CBBCCategory.CATEGORY_R:
            return 0.0
        if contract.warrant_type is WarrantType.TURBO_BULL_CBBC:
            gross = settlement - contract.strike_price
        elif contract.warrant_type is WarrantType.TURBO_BEAR_CBBC:
            gross = contract.strike_price - settlement
        else:
            return 0.0
        return max(0.0, gross) * contract.entitlement_ratio

    # ------------------------------------------------------------------ hedging

    def calculate_delta_hedge_signal(
        self,
        valuation: WarrantValuation,
        position_warrants: int,
        current_underlying_hedged_shares: float,
        rebalance_threshold_shares: float = 1.0,
    ) -> DeltaHedgeSignal:
        """Size the underlying trade that restores a delta-neutral hedge.

        Args:
            valuation: Output of :meth:`price_warrant` for this warrant line.
            position_warrants: **Signed** warrant count. Positive = warrants
                held long; negative = warrants sold or issued (the market-maker
                book). Getting this sign wrong does not raise -- it doubles the
                exposure the hedge was meant to remove.
            current_underlying_hedged_shares: Signed underlying position already
                held against this warrant line.
            rebalance_threshold_shares: Minimum absolute rebalance worth
                trading. Set it to the instrument's board lot to avoid
                generating sub-lot orders an exchange will reject.

        Returns:
            A :class:`DeltaHedgeSignal` whose ``net_rebalance_shares`` is signed
            (positive = buy). A terminated warrant always targets zero shares,
            so the signal liquidates the whole hedge in one instruction.
        """
        if not isinstance(valuation, WarrantValuation):
            raise WarrantEngineError(
                f"valuation must be a WarrantValuation, got {type(valuation)!r}"
            )
        if not isinstance(position_warrants, int) or isinstance(position_warrants, bool):
            raise WarrantEngineError(
                f"position_warrants must be a signed int, got {position_warrants!r}"
            )
        _require_finite(current_underlying_hedged_shares, "current_underlying_hedged_shares")
        _require_non_negative(rebalance_threshold_shares, "rebalance_threshold_shares")

        if valuation.status is KnockOutStatus.ACTIVE:
            book_delta_shares = position_warrants * valuation.delta
        else:
            # Called or expired: the warrant has no delta, so whatever underlying
            # is still held against it is now naked directional exposure.
            book_delta_shares = 0.0

        # `+ 0.0` normalises -0.0, which is numerically equal to 0.0 but renders
        # as "-0" in an audit log.
        required_shares = -book_delta_shares + 0.0
        net_rebalance = required_shares - current_underlying_hedged_shares

        # The `== 0.0` arm matters when the threshold is set to 0.0: an exactly
        # flat book must still HOLD rather than emit a zero-share SELL.
        if net_rebalance == 0.0 or abs(net_rebalance) < rebalance_threshold_shares:
            action = "HOLD"
        elif net_rebalance > 0:
            action = "BUY"
        else:
            action = "SELL"

        if valuation.status is not KnockOutStatus.ACTIVE and action != "HOLD":
            logger.warning(
                "HEDGE UNWIND [%s]: status=%s, liquidating %.4f underlying shares",
                valuation.warrant_id,
                valuation.status.value,
                current_underlying_hedged_shares,
            )

        logger.info(
            "Delta hedge [%s/%s]: book_delta=%.4f, target=%.4f, held=%.4f, "
            "rebalance=%+.4f -> %s",
            valuation.warrant_id,
            valuation.underlying_symbol,
            book_delta_shares,
            required_shares,
            current_underlying_hedged_shares,
            net_rebalance,
            action,
        )

        return DeltaHedgeSignal(
            warrant_id=valuation.warrant_id,
            underlying_symbol=valuation.underlying_symbol,
            position_warrants=position_warrants,
            warrant_book_delta_shares=book_delta_shares,
            required_underlying_delta_shares=required_shares,
            current_underlying_hedged_shares=float(current_underlying_hedged_shares),
            net_rebalance_shares=net_rebalance,
            action=action,
        )

    # ------------------------------------------------------------------ internals

    def _validate_contract(self, contract: WarrantContract) -> None:
        if not isinstance(contract, WarrantContract):
            raise WarrantEngineError(
                f"contract must be a WarrantContract, got {type(contract)!r}"
            )
        if not isinstance(contract.warrant_type, WarrantType):
            raise WarrantEngineError(
                f"warrant_type must be a WarrantType, got {contract.warrant_type!r}"
            )
        _require_positive(contract.strike_price, "strike_price")
        _require_positive(contract.entitlement_ratio, "entitlement_ratio")
        _require_finite(contract.risk_free_rate, "risk_free_rate")
        _require_non_negative(contract.dividend_yield, "dividend_yield")
        _require_non_negative(contract.funding_rate_annual, "funding_rate_annual")
        if not isinstance(contract.days_to_expiry, int) or isinstance(
            contract.days_to_expiry, bool
        ):
            raise WarrantEngineError(
                f"days_to_expiry must be an int, got {contract.days_to_expiry!r}"
            )

        if contract.warrant_type in (WarrantType.COVERED_CALL, WarrantType.COVERED_PUT):
            _require_positive(contract.implied_volatility, "implied_volatility")

        if contract.warrant_type in (
            WarrantType.TURBO_BULL_CBBC,
            WarrantType.TURBO_BEAR_CBBC,
        ):
            _require_positive(contract.barrier_price, "barrier_price")
            # A bull's call price sits at (Category N) or above (Category R) the
            # strike; a bear's sits at or below it. The reverse is not a tradable
            # term sheet, and it would let a live bull CBBC price below intrinsic.
            if (
                contract.warrant_type is WarrantType.TURBO_BULL_CBBC
                and contract.barrier_price < contract.strike_price
            ):
                raise WarrantEngineError(
                    f"bull CBBC call price ({contract.barrier_price}) must be >= strike "
                    f"({contract.strike_price})"
                )
            if (
                contract.warrant_type is WarrantType.TURBO_BEAR_CBBC
                and contract.barrier_price > contract.strike_price
            ):
                raise WarrantEngineError(
                    f"bear CBBC call price ({contract.barrier_price}) must be <= strike "
                    f"({contract.strike_price})"
                )
            if contract.cbbc_category is CBBCCategory.NOT_APPLICABLE:
                raise WarrantEngineError(
                    "Turbo / CBBC contracts must declare cbbc_category (CATEGORY_N or "
                    "CATEGORY_R): it decides whether a Mandatory Call Event pays a "
                    "residual value or nothing at all."
                )

    def _intrinsic(self, spot: float, contract: WarrantContract) -> float:
        """Intrinsic value per warrant, floored at zero and scaled by R."""
        if contract.warrant_type in (WarrantType.COVERED_CALL, WarrantType.TURBO_BULL_CBBC):
            gross = spot - contract.strike_price
        else:
            gross = contract.strike_price - spot
        return max(0.0, gross) * contract.entitlement_ratio

    def _gearing(
        self,
        spot: float,
        contract: WarrantContract,
        fair_price: float,
        raw_delta: float,
        market_price: Optional[float],
    ) -> Tuple[float, float, float]:
        """Simple and effective gearing, plus the price they were computed on.

        Effective gearing is the price elasticity of the warrant with respect to
        the underlying: ``(S / P) * dP/dS``. Because ``dP/dS = R * raw_delta``
        this equals ``simple_gearing * |raw_delta|`` -- exactly HKEX's
        "gearing x delta". The entitlement ratio appears once, not twice.
        """
        if market_price is not None:
            basis = _require_positive(market_price, "market_price")
        else:
            basis = fair_price

        if basis <= 0.0:
            # A theoretical price of zero has no defined elasticity. Report zero
            # and say so, rather than dividing: version 1.1.0 floored the price at
            # 0.0001 instead and reported gearings of 100,000x.
            logger.warning(
                "GEARING UNDEFINED [%s]: basis price is %.8f; supply market_price to "
                "compute gearing against the traded price",
                contract.symbol,
                basis,
            )
            return 0.0, 0.0, basis

        simple = (spot * contract.entitlement_ratio) / basis
        return simple, simple * abs(raw_delta), basis

    def _terminated_valuation(
        self,
        contract: WarrantContract,
        spot: float,
        status: KnockOutStatus,
        residual_value: float,
        market_price: Optional[float],
        log_reason: str,
    ) -> WarrantValuation:
        """Valuation for a called or expired contract: no Greeks, no gearing."""
        logger.warning("%s -- residual value %.6f per warrant", log_reason, residual_value)
        return WarrantValuation(
            warrant_id=contract.warrant_id,
            underlying_symbol=contract.underlying_symbol,
            spot_price=spot,
            fair_price=residual_value,
            intrinsic_value=residual_value,
            time_value=0.0,
            delta=0.0,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            simple_gearing=0.0,
            effective_gearing=0.0,
            gearing_basis_price=(
                market_price if market_price is not None else residual_value
            ),
            status=status,
            pricing_model=PricingModel.TERMINATED,
            residual_value=residual_value,
        )

    def _price_cbbc(
        self, spot: float, contract: WarrantContract, market_price: Optional[float]
    ) -> WarrantValuation:
        """Price a live Turbo / CBBC as intrinsic value plus issuer funding cost.

        Delta is one underlying unit per warrant before scaling -- the delta-one
        convention HKEX and the HK issuers document. Gamma and vega are
        identically zero under this convention; see the module docstring.
        """
        r_ent = contract.entitlement_ratio
        intrinsic = self._intrinsic(spot, contract)
        days = float(contract.days_to_expiry)

        funding_cost = (
            contract.strike_price
            * contract.funding_rate_annual
            * (days / DAYS_PER_YEAR)
            * r_ent
        )
        fair_price = intrinsic + funding_cost

        raw_delta = 1.0 if contract.warrant_type is WarrantType.TURBO_BULL_CBBC else -1.0
        delta = raw_delta * r_ent
        # The funding cost accrues away linearly, one day at a time.
        theta = -(contract.strike_price * contract.funding_rate_annual * r_ent) / DAYS_PER_YEAR

        simple_gearing, effective_gearing, basis = self._gearing(
            spot, contract, fair_price, raw_delta, market_price
        )

        distance_to_call_pct = abs(spot - contract.barrier_price) / spot * 100.0
        logger.info(
            "CBBC valuation [%s]: price=%.6f (intrinsic=%.6f + funding=%.6f), delta=%.6f, "
            "effective_gearing=%.4fx, %.2f%% from call price",
            contract.symbol,
            fair_price,
            intrinsic,
            funding_cost,
            delta,
            effective_gearing,
            distance_to_call_pct,
        )

        return WarrantValuation(
            warrant_id=contract.warrant_id,
            underlying_symbol=contract.underlying_symbol,
            spot_price=spot,
            fair_price=fair_price,
            intrinsic_value=intrinsic,
            time_value=funding_cost,
            delta=delta,
            gamma=0.0,
            theta=theta,
            vega=0.0,
            simple_gearing=simple_gearing,
            effective_gearing=effective_gearing,
            gearing_basis_price=basis,
            status=KnockOutStatus.ACTIVE,
            pricing_model=PricingModel.CBBC_INTRINSIC_PLUS_FUNDING,
        )

    def _price_black_scholes(
        self, spot: float, contract: WarrantContract, market_price: Optional[float]
    ) -> WarrantValuation:
        """Price a live covered warrant with Black-Scholes-Merton, scaled by R."""
        r = contract.risk_free_rate
        q = contract.dividend_yield
        sigma = contract.implied_volatility
        k = contract.strike_price
        r_ent = contract.entitlement_ratio
        t = contract.days_to_expiry / DAYS_PER_YEAR

        sqrt_t = math.sqrt(t)
        d1 = (math.log(spot / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t

        disc_q = math.exp(-q * t)
        disc_r = math.exp(-r * t)
        pdf_d1 = standard_normal_pdf(d1)
        is_call = contract.warrant_type is WarrantType.COVERED_CALL

        # Shared diffusion term of theta; the rate and dividend terms differ by side.
        theta_diffusion = -(spot * disc_q * pdf_d1 * sigma) / (2.0 * sqrt_t)

        if is_call:
            bs_price = (
                spot * disc_q * standard_normal_cdf(d1) - k * disc_r * standard_normal_cdf(d2)
            )
            raw_delta = disc_q * standard_normal_cdf(d1)
            theta_annual = (
                theta_diffusion
                + q * spot * disc_q * standard_normal_cdf(d1)
                - r * k * disc_r * standard_normal_cdf(d2)
            )
        else:
            bs_price = (
                k * disc_r * standard_normal_cdf(-d2) - spot * disc_q * standard_normal_cdf(-d1)
            )
            raw_delta = -disc_q * standard_normal_cdf(-d1)
            theta_annual = (
                theta_diffusion
                - q * spot * disc_q * standard_normal_cdf(-d1)
                + r * k * disc_r * standard_normal_cdf(-d2)
            )

        # Black-Scholes cannot return a negative value; this clamps only the
        # sub-tick floating-point residue of a deep out-of-the-money warrant.
        fair_price = max(0.0, bs_price) * r_ent
        intrinsic = self._intrinsic(spot, contract)
        # Not clamped at zero: a deep in-the-money European put genuinely trades
        # below intrinsic, and hiding that behind max(0, ...) also hides the
        # early-exercise signal on an American-style line.
        time_value = fair_price - intrinsic

        delta = raw_delta * r_ent
        gamma = (disc_q * pdf_d1 / (spot * sigma * sqrt_t)) * r_ent
        vega = (spot * disc_q * sqrt_t * pdf_d1) * r_ent / 100.0     # per 1 vol point
        theta = theta_annual * r_ent / DAYS_PER_YEAR                 # per calendar day

        simple_gearing, effective_gearing, basis = self._gearing(
            spot, contract, fair_price, raw_delta, market_price
        )

        logger.info(
            "Covered warrant valuation [%s]: price=%.6f, delta=%.6f, gamma=%.6f, "
            "theta/day=%.6f, vega/pt=%.6f, effective_gearing=%.4fx",
            contract.symbol,
            fair_price,
            delta,
            gamma,
            theta,
            vega,
            effective_gearing,
        )

        return WarrantValuation(
            warrant_id=contract.warrant_id,
            underlying_symbol=contract.underlying_symbol,
            spot_price=spot,
            fair_price=fair_price,
            intrinsic_value=intrinsic,
            time_value=time_value,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            simple_gearing=simple_gearing,
            effective_gearing=effective_gearing,
            gearing_basis_price=basis,
            status=KnockOutStatus.ACTIVE,
            pricing_model=PricingModel.BLACK_SCHOLES_MERTON,
        )
