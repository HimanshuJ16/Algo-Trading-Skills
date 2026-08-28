"""
options-margin-span-calculation-global: a legacy-CME-SPAN-style scenario-scan
margin approximator for multi-leg listed option portfolios.

What this module is
-------------------
A reimplementation of the *scanning risk* core of the legacy SPAN methodology.
The portfolio is revalued under the 16 standard SPAN risk-array scenarios --
14 pairings of a price move of {0, +/-1/3, +/-2/3, +/-1} x the price scan range
against the volatility scan range moved up and down, plus 2 "extreme move"
scenarios at a multiple of the price scan range whose loss is only fractionally
covered -- and the worst covered loss becomes the scan risk. Net option value is
then credited (long options) or charged (short options), a short-option-minimum
floor is applied, and that SPAN component is capped at the position's exact
worst-case loss at expiry.

The scenario set follows CME's published risk-array specification (scenarios
1-14 pair vol up/down against 0, 1/3, 2/3 and the full price scan range;
scenarios 15-16 move price by a multiple of the scan range and cover only a
fraction of the resulting loss):
    https://cmegroupclientsite.atlassian.net/wiki/spaces/pubsub/pages/457411520/Risk+Arrays+-+Standard

The combination rule follows CME's "the SPAN methodology compares the resulting
value with the short option minimum, whichever is larger is the risk
requirement" and "total SPAN margin = SPAN risk requirement less the net option
value":
    https://www.cmegroup.com/clearing/files/span-methodology.pdf

What this module is NOT
-----------------------
* NOT the clearing house's or broker's number. Real SPAN reads a daily exchange
  parameter file (price scan range, volatility scan range, short option
  minimum, extreme-move multiplier and cover fraction, inter/intra-commodity
  spread parameters) that this module does not ship and cannot guess. Every
  default below is an illustrative placeholder -- source the real values from
  the exchange's parameter file and pass them in. Treat the output as a
  pre-screen and the broker's margin calculator as authoritative.
* NOT CME SPAN 2. CME is migrating products off the legacy scenario grid onto
  SPAN 2, a filtered-historical-simulation VaR model whose numbers are not
  reproducible from any scenario grid:
    https://www.cmegroup.com/solutions/risk-management/performance-bonds-margins/span-methodology-overview/launching-span-2.html
* NOT US portfolio margin. FINRA Rule 4210(g) accounts are margined under the
  OCC's TIMS with *asymmetric* stress ranges by product group (-8%/+6% for
  high-capitalisation broad-based indexes, +/-10% non-high-cap broad-based,
  +/-15% sector indexes and individual equities). Set the scan range
  accordingly, and note TIMS is a different model, not a re-parameterised SPAN.
* NOT US Reg T strategy-based margin -- see the sibling skill
  `multi-leg-strategy-margin-optimization`.
* NOT a spread-charge engine. Intra-commodity (calendar) and inter-commodity
  spread charges and credits, delivery/spot charges and cross-margin offsets are
  out of scope; a portfolio spanning several expiries or underlyings will be
  mis-margined.
* NOT an underlying/futures-leg engine. Only option legs are modelled, so a
  delta hedge held in the underlying is invisible to the scan and to the
  bounded-loss test.
* NOT an American-exercise model. Legs are valued as European; early exercise,
  assignment and pin risk are not priced.

Exposure margin
---------------
`exposure_margin_pct` models an India/NSE-style Extreme Loss Margin: a flat
percentage of short-leg notional charged *on top of* SPAN. It has no analogue in
a CME performance bond -- set it to 0.0 outside that context. NSE Clearing's
published base ELM rates are 2% for index futures and 3.5% for stock futures,
with product- and event-specific overlays (for example an additional ELM on
short index options on expiry day); the 0.03 default here is a placeholder, not
a rate that applies to any particular contract.
    https://www.nseclearing.in/risk-management/equity-derivatives/margins
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Volatility floor used when a downward vol shock would drive sigma to zero or
# below. Purely numerical: Black-Scholes is undefined at sigma <= 0.
_MIN_VOL = 1e-6

# Price fractions of the price scan range used by SPAN scenarios 1-14, in the
# order CME publishes them (unchanged, +/-1/3, +/-2/3, +/-full), each paired
# with volatility up and volatility down.
_STANDARD_PRICE_FRACTIONS: Tuple[float, ...] = (
    0.0, 1.0 / 3.0, -1.0 / 3.0, 2.0 / 3.0, -2.0 / 3.0, 1.0, -1.0,
)


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


def _finite(name: str, value: float) -> float:
    """Coerce to float and reject NaN/Inf, which would silently poison the scan."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, got bool")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric, got {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return out


def _positive(name: str, value: float) -> float:
    out = _finite(name, value)
    if out <= 0.0:
        raise ValueError(f"{name} must be > 0, got {out!r}")
    return out


@dataclass
class OptionLeg:
    """One listed option leg.

    Attributes:
        strike: Exercise price, in underlying price units.
        option_type: CALL or PUT.
        quantity: Signed contract count; positive is long, negative is short.
        premium: Traded/entry premium per unit. Informational only -- it is
            deliberately NOT used in the margin calculation. Margin is a
            forward-looking measure of loss from the *current* mark, so mixing
            in the entry price would make the requirement depend on the fill.
        multiplier: Contract multiplier (units of underlying per contract).
        time_to_expiry_years: Year fraction to expiry. Required for the
            volatility scan to have any effect: when it is None or 0 the leg is
            valued at intrinsic, which removes all time value and understates
            the requirement on a short position. Always supply it if known.
        rate: Continuously-compounded risk-free rate for this leg's tenor.
            Dividends and cost of carry are not modelled.
        mark_price: Current market price per unit. When supplied it is used for
            net option value and for the worst-case-loss cap; the scenario scan
            always uses model values on both sides of the difference so that the
            scan measures a pure market move, not a model-vs-market basis.
    """

    strike: float
    option_type: OptionType
    quantity: float
    premium: float = 0.0
    multiplier: float = 100.0
    time_to_expiry_years: Optional[float] = None
    rate: float = 0.0
    mark_price: Optional[float] = None

    def __post_init__(self) -> None:
        self.strike = _finite("strike", self.strike)
        if self.strike < 0.0:
            raise ValueError(f"strike must be >= 0, got {self.strike!r}")
        self.option_type = OptionType(self.option_type)
        self.quantity = _finite("quantity", self.quantity)
        if self.quantity == 0.0:
            raise ValueError("quantity must be non-zero")
        self.premium = _finite("premium", self.premium)
        self.multiplier = _positive("multiplier", self.multiplier)
        self.rate = _finite("rate", self.rate)
        if self.time_to_expiry_years is not None:
            self.time_to_expiry_years = _finite(
                "time_to_expiry_years", self.time_to_expiry_years)
            if self.time_to_expiry_years < 0.0:
                raise ValueError("time_to_expiry_years must be >= 0")
        if self.mark_price is not None:
            self.mark_price = _finite("mark_price", self.mark_price)
            if self.mark_price < 0.0:
                raise ValueError("mark_price must be >= 0")

    @property
    def uses_intrinsic_valuation(self) -> bool:
        """True when no positive time to expiry was supplied for this leg."""
        return not self.time_to_expiry_years


@dataclass
class SPANMarginResult:
    """Outcome of one scan. All money amounts are in account currency.

    Attributes:
        net_option_value: Mark-to-market value of the option position; positive
            when net long premium, negative when net short.
        span_scan_risk: Worst covered loss across the risk array, floored at 0.
        span_margin: SPAN component -- max(scan risk, short option minimum) less
            net option value, floored at 0 and capped at
            `worst_case_terminal_loss` where that is finite.
        exposure_margin: NSE-style extreme-loss add-on on short-leg notional;
            0.0 when `exposure_margin_pct` is 0.
        total_required_margin: `span_margin` + `exposure_margin`. The cap applies
            to the SPAN component only, so a configured exposure overlay can
            push this above `worst_case_terminal_loss` -- which is what a venue
            charging a notional-based overlay actually does.
        is_defined_risk: True when `worst_case_terminal_loss` is finite, i.e.
            the loss at expiry is bounded over all underlying prices. Note a
            naked short put is bounded (the underlying cannot go below zero) and
            so reports True despite being an undefined-risk trade in the
            colloquial sense -- read `has_unbounded_loss` and the loss figure
            itself, not this flag alone.
        worst_scenario_name: Risk-array scenario that produced the scan risk.
        scenario_payoffs: Scenario name -> covered profit/loss versus the
            current model value; negative is a loss. The extreme scenarios
            already carry the cover fraction, matching how SPAN publishes them.
        short_option_minimum: Floor charge applied to the risk requirement.
        has_unbounded_loss: True when net short calls leave the upside open.
        worst_case_terminal_loss: Exact greatest loss at expiry relative to the
            current mark, over all underlying prices; math.inf when unbounded.
        margin_capped_at_max_loss: True when the cap bound the SPAN component.
        valuation_mode: "black_scholes", "intrinsic", or "mixed".
    """

    net_option_value: float
    span_scan_risk: float
    exposure_margin: float
    total_required_margin: float
    is_defined_risk: bool
    worst_scenario_name: str
    scenario_payoffs: Dict[str, float]
    short_option_minimum: float = 0.0
    span_margin: float = 0.0
    has_unbounded_loss: bool = False
    worst_case_terminal_loss: float = 0.0
    margin_capped_at_max_loss: bool = False
    valuation_mode: str = "intrinsic"


class SPANMarginCalculator:
    """Legacy-SPAN-style scenario scan over a single-underlying option portfolio.

    Every parameter below is an exchange-published input in real SPAN. The
    defaults are illustrative placeholders chosen to preserve this module's
    historical behaviour, NOT values that apply to any particular contract.

    Args:
        price_shocks_pct: Optional explicit list of relative price shocks. When
            supplied it replaces the standard SPAN grid entirely -- the two
            extreme-move scenarios are then NOT added, because a custom grid
            carries no scan range for them to be a multiple of.
        vol_shocks_pct: Optional explicit list of relative volatility shocks,
            paired with `price_shocks_pct`.
        exposure_margin_pct: NSE-style extreme-loss add-on as a fraction of
            short-leg notional. Set to 0.0 for CME-style performance bonds.
        price_scan_range_pct: Price scan range as a fraction of spot.
        vol_scan_range_pct: Volatility scan range, applied as a *relative* move
            on the supplied volatility (sigma * (1 +/- vsr)). Some SPAN
            parameter files express it in absolute volatility points -- convert
            before passing.
        extreme_move_multiplier: Multiple of the price scan range used by
            scenarios 15-16.
        extreme_move_cover_fraction: Fraction of the extreme-scenario loss that
            is charged. CME's published risk-array description states 30%; the
            value is an exchange parameter and varies, so override it from the
            parameter file rather than relying on this default.
        short_option_minimum_per_contract: Per-short-contract floor on the risk
            requirement. Defaults to 0.0 (disabled) because it is exchange
            published and cannot be guessed; leaving it at 0.0 lets a deep
            out-of-the-money short option scan to a near-zero requirement, so a
            warning is logged whenever that combination occurs.
    """

    def __init__(
        self,
        price_shocks_pct: Optional[List[float]] = None,
        vol_shocks_pct: Optional[List[float]] = None,
        exposure_margin_pct: float = 0.03,
        *,
        price_scan_range_pct: float = 0.06,
        vol_scan_range_pct: float = 0.15,
        extreme_move_multiplier: float = 3.0,
        extreme_move_cover_fraction: float = 0.30,
        short_option_minimum_per_contract: float = 0.0,
    ) -> None:
        self.exposure_margin_pct = _finite("exposure_margin_pct", exposure_margin_pct)
        if self.exposure_margin_pct < 0.0:
            raise ValueError("exposure_margin_pct must be >= 0")
        self.price_scan_range_pct = _positive(
            "price_scan_range_pct", price_scan_range_pct)
        self.vol_scan_range_pct = _finite("vol_scan_range_pct", vol_scan_range_pct)
        if self.vol_scan_range_pct < 0.0:
            raise ValueError("vol_scan_range_pct must be >= 0")
        self.extreme_move_multiplier = _finite(
            "extreme_move_multiplier", extreme_move_multiplier)
        if self.extreme_move_multiplier < 0.0:
            raise ValueError("extreme_move_multiplier must be >= 0")
        self.extreme_move_cover_fraction = _finite(
            "extreme_move_cover_fraction", extreme_move_cover_fraction)
        if not 0.0 <= self.extreme_move_cover_fraction <= 1.0:
            raise ValueError("extreme_move_cover_fraction must be in [0, 1]")
        self.short_option_minimum_per_contract = _finite(
            "short_option_minimum_per_contract", short_option_minimum_per_contract)
        if self.short_option_minimum_per_contract < 0.0:
            raise ValueError("short_option_minimum_per_contract must be >= 0")

        if (price_shocks_pct is None) != (vol_shocks_pct is None):
            raise ValueError(
                "price_shocks_pct and vol_shocks_pct must be supplied together")
        if price_shocks_pct is None:
            self.price_shocks_pct: Optional[List[float]] = None
            self.vol_shocks_pct: Optional[List[float]] = None
        else:
            if not price_shocks_pct or not vol_shocks_pct:
                raise ValueError("custom shock lists must be non-empty")
            self.price_shocks_pct = [
                _finite("price shock", s) for s in price_shocks_pct]
            self.vol_shocks_pct = [_finite("vol shock", s) for s in vol_shocks_pct]

    # ----------------------------------------------------------------- pricing

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def _intrinsic(leg: "OptionLeg", spot: float) -> float:
        if leg.option_type == OptionType.CALL:
            return max(0.0, spot - leg.strike)
        return max(0.0, leg.strike - spot)

    @classmethod
    def _black_scholes(
        cls, leg: "OptionLeg", spot: float, vol: float, ttm: float
    ) -> float:
        """European Black-Scholes price per unit. No dividends or carry."""
        if ttm <= 0.0 or vol <= 0.0 or spot <= 0.0 or leg.strike <= 0.0:
            return cls._intrinsic(leg, spot)
        sqrt_t = math.sqrt(ttm)
        d1 = (
            math.log(spot / leg.strike) + (leg.rate + 0.5 * vol * vol) * ttm
        ) / (vol * sqrt_t)
        d2 = d1 - vol * sqrt_t
        disc_k = leg.strike * math.exp(-leg.rate * ttm)
        if leg.option_type == OptionType.CALL:
            return spot * cls._norm_cdf(d1) - disc_k * cls._norm_cdf(d2)
        return disc_k * cls._norm_cdf(-d2) - spot * cls._norm_cdf(-d1)

    @classmethod
    def _model_price(cls, leg: "OptionLeg", spot: float, vol: float) -> float:
        """Per-unit model price, used consistently on both sides of a scan diff."""
        if leg.uses_intrinsic_valuation:
            return cls._intrinsic(leg, spot)
        return cls._black_scholes(leg, spot, vol, float(leg.time_to_expiry_years))

    @classmethod
    def _portfolio_model_value(
        cls, legs: List["OptionLeg"], spot: float, vol: float
    ) -> float:
        return sum(
            cls._model_price(leg, spot, vol) * leg.quantity * leg.multiplier
            for leg in legs
        )

    # --------------------------------------------------------------- scenarios

    def _scenarios(
        self, spot: float, vol: float
    ) -> List[Tuple[str, float, float, float]]:
        """Build (name, scenario_spot, scenario_vol, cover_fraction) tuples."""
        scenarios: List[Tuple[str, float, float, float]] = []

        if self.price_shocks_pct is not None and self.vol_shocks_pct is not None:
            for p_shock in self.price_shocks_pct:
                for v_shock in self.vol_shocks_pct:
                    scenarios.append((
                        f"price{p_shock:+.1%}_vol{v_shock:+.1%}",
                        max(0.0, spot * (1.0 + p_shock)),
                        max(_MIN_VOL, vol * (1.0 + v_shock)),
                        1.0,
                    ))
            return scenarios

        # Scenarios 1-14: price fraction of scan range x volatility up/down.
        for frac in _STANDARD_PRICE_FRACTIONS:
            for direction, label in ((1.0, "up"), (-1.0, "dn")):
                scenarios.append((
                    f"p{frac:+.2f}PSR_vol{label}",
                    max(0.0, spot * (1.0 + frac * self.price_scan_range_pct)),
                    max(_MIN_VOL, vol * (1.0 + direction * self.vol_scan_range_pct)),
                    1.0,
                ))
        # Scenarios 15-16: extreme price move, volatility unchanged, loss only
        # fractionally covered.
        for sign in (1.0, -1.0):
            move = sign * self.extreme_move_multiplier * self.price_scan_range_pct
            scenarios.append((
                f"p{sign * self.extreme_move_multiplier:+.2f}PSR_extreme",
                max(0.0, spot * (1.0 + move)),
                vol,
                self.extreme_move_cover_fraction,
            ))
        return scenarios

    def evaluate_scenario_grid(
        self, legs: List[OptionLeg], spot: float, vol: float
    ) -> Dict[str, float]:
        """Scenario name -> covered profit/loss versus the current model value.

        Negative values are losses. The extreme scenarios already carry the
        cover fraction, so taking the minimum over this mapping reproduces the
        SPAN scan risk directly.
        """
        spot = _positive("spot", spot)
        vol = _positive("vol", vol)
        if not legs:
            return {}
        base_value = self._portfolio_model_value(legs, spot, vol)
        payoffs: Dict[str, float] = {}
        for name, scen_spot, scen_vol, cover in self._scenarios(spot, vol):
            scen_value = self._portfolio_model_value(legs, scen_spot, scen_vol)
            payoffs[name] = (scen_value - base_value) * cover
        return payoffs

    # ---------------------------------------------------------- terminal loss

    @staticmethod
    def _terminal_value(legs: List[OptionLeg], spot: float) -> float:
        return sum(
            SPANMarginCalculator._intrinsic(leg, spot) * leg.quantity * leg.multiplier
            for leg in legs
        )

    @classmethod
    def _worst_case_terminal_loss(
        cls, legs: List[OptionLeg], net_option_value: float
    ) -> Tuple[float, bool]:
        """Exact greatest loss at expiry relative to the current mark.

        The expiry payoff is piecewise linear with kinks only at the strikes, so
        its minimum over [0, inf) is attained at 0, at a strike, or in the limit.
        The limit is unbounded exactly when the multiplier-weighted net call
        quantity is negative. Puts cannot run away because the underlying is
        floored at zero.

        Returns:
            (worst_case_loss, has_unbounded_loss). The loss is math.inf when
            unbounded.
        """
        upside_slope = sum(
            leg.quantity * leg.multiplier
            for leg in legs
            if leg.option_type == OptionType.CALL
        )
        if upside_slope < 0.0:
            return math.inf, True
        breakpoints = [0.0] + sorted({leg.strike for leg in legs})
        # One point beyond the top strike pins down the flat upside region.
        breakpoints.append(breakpoints[-1] + max(1.0, breakpoints[-1]))
        worst_value = min(cls._terminal_value(legs, s) for s in breakpoints)
        return max(0.0, net_option_value - worst_value), False

    # ------------------------------------------------------------------ margin

    def calculate_span_margin(
        self, legs: List[OptionLeg], spot: float, vol: float
    ) -> SPANMarginResult:
        """Scan `legs` and return the approximated requirement.

        Args:
            legs: Option legs on a single underlying. An empty list yields a
                zero requirement.
            spot: Current underlying price, > 0.
            vol: Current implied volatility as a decimal (0.20 = 20%), > 0.

        Raises:
            ValueError: on a non-positive or non-finite spot/vol, or on a leg
                that fails validation.
        """
        if not legs:
            return SPANMarginResult(
                net_option_value=0.0,
                span_scan_risk=0.0,
                exposure_margin=0.0,
                total_required_margin=0.0,
                is_defined_risk=True,
                worst_scenario_name="NONE",
                scenario_payoffs={},
                valuation_mode="intrinsic",
            )
        spot = _positive("spot", spot)
        vol = _positive("vol", vol)

        intrinsic_legs = [leg for leg in legs if leg.uses_intrinsic_valuation]
        if intrinsic_legs:
            logger.warning(
                "%d of %d legs have no time_to_expiry_years and are valued at "
                "intrinsic; the volatility scan cannot affect them and the "
                "requirement on short legs is understated",
                len(intrinsic_legs), len(legs),
            )
        if len(intrinsic_legs) == len(legs):
            valuation_mode = "intrinsic"
        elif intrinsic_legs:
            valuation_mode = "mixed"
        else:
            valuation_mode = "black_scholes"

        payoffs = self.evaluate_scenario_grid(legs, spot, vol)
        worst_scen = min(payoffs, key=lambda k: payoffs[k])
        span_scan_risk = max(0.0, -payoffs[worst_scen])

        # Net option value: mark where supplied, model price otherwise.
        nov = sum(
            (leg.mark_price if leg.mark_price is not None
             else self._model_price(leg, spot, vol))
            * leg.quantity * leg.multiplier
            for leg in legs
        )

        short_contracts = sum(-leg.quantity for leg in legs if leg.quantity < 0.0)
        som_total = short_contracts * self.short_option_minimum_per_contract
        if short_contracts > 0.0 and self.short_option_minimum_per_contract == 0.0:
            logger.warning(
                "short_option_minimum_per_contract is 0 with %g short contracts "
                "in the portfolio; a deep out-of-the-money short option will "
                "scan to a near-zero requirement. Source the short option "
                "minimum from the exchange SPAN parameter file.",
                short_contracts,
            )

        # Extreme-loss add-on on short-leg notional only. Long options are paid
        # for in full and carry no exposure charge.
        short_notional = sum(
            abs(leg.quantity) * spot * leg.multiplier
            for leg in legs
            if leg.quantity < 0.0
        )
        exposure_margin = short_notional * self.exposure_margin_pct

        risk_requirement = max(span_scan_risk, som_total)
        span_margin = max(0.0, risk_requirement - nov)

        # The cap applies to the SPAN component only. A notional-based overlay
        # such as NSE's Extreme Loss Margin is charged on top of SPAN and can
        # legitimately push the blocked capital above the position's maximum
        # loss, so capping the total would understate what the venue takes.
        max_loss, unbounded = self._worst_case_terminal_loss(legs, nov)
        capped = not unbounded and span_margin > max_loss
        if capped:
            span_margin = max_loss
        total_margin = span_margin + exposure_margin

        return SPANMarginResult(
            net_option_value=round(nov, 2),
            span_scan_risk=round(span_scan_risk, 2),
            exposure_margin=round(exposure_margin, 2),
            total_required_margin=round(total_margin, 2),
            is_defined_risk=not unbounded,
            worst_scenario_name=worst_scen,
            scenario_payoffs=payoffs,
            short_option_minimum=round(som_total, 2),
            span_margin=round(span_margin, 2),
            has_unbounded_loss=unbounded,
            worst_case_terminal_loss=max_loss,
            margin_capped_at_max_loss=capped,
            valuation_mode=valuation_mode,
        )


# -------------------------------------------------------- backwards-compatible

def span_style_margin_estimate(leg_payoffs_by_scenario):
    """Worst-case loss across a scenario -> profit/loss mapping, floored at 0."""
    if not leg_payoffs_by_scenario:
        return 0.0
    return max(0.0, -min(leg_payoffs_by_scenario.values()))


def build_price_vol_scenarios(spot, vol, price_shocks_pct, vol_shocks_pct):
    """Cartesian product of relative price and volatility shocks."""
    scenarios = {}
    for p_shock in price_shocks_pct:
        for v_shock in vol_shocks_pct:
            scenarios[f"price{p_shock:+.0%}_vol{v_shock:+.0%}"] = {
                "spot": spot * (1 + p_shock),
                "vol": vol * (1 + v_shock),
            }
    return scenarios
