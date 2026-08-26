"""
multi-leg-strategy-margin-optimization: strategy-based (Reg T) margin estimator
for listed equity/index option combinations, quantifying how much capital an
exchange-recognised spread frees versus margining every leg independently.

What this module is
-------------------
An implementation of the *strategy-based* margin methodology that FINRA Rule
4210(f)(2) and Cboe Rule 10.3 prescribe for listed options, and to which
Regulation T (12 CFR 220.12(f)) defers for exchange-listed contracts:

  * Naked short option (FINRA 4210(f)(2)(E) / Cboe strategy-based margin):
        100% of option market value
        + underlying_pct x underlying value
        - out-of-the-money amount
    with a floor of option market value + 10% of the underlying value (calls)
    or + 10% of the put's aggregate exercise price (puts).

  * Long option: paid for in full.

  * Spread (FINRA 4210(f)(2)(H), as amended by Regulatory Notice 12-44):
    long options are paid for in full and the margin carried on the short
    options is the *lesser of* the naked (E) requirement and the "maximum
    potential loss", computed by netting the intrinsic values of the legs at
    price points corresponding to every exercise price present in the spread
    and taking the greatest loss.

Because the requirement is derived from that payoff computation rather than
from a hard-coded per-pattern formula, the same code path prices a vertical, an
iron condor, an iron butterfly, a box and any other combination correctly, and
the strategy label is descriptive only -- it never drives the number. That is
deliberate: pattern-matched formulas silently mis-price anything that merely
*looks* like the pattern (four naked shorts have the leg shape of an iron
condor but none of its defined risk).

What this module is NOT
-----------------------
* NOT a portfolio-margin calculation. Portfolio margin accounts are margined
  under FINRA Rule 4210(g) using the OCC's Theoretical Intermarket Margining
  System (TIMS), which revalues the whole portfolio under a scenario set rather
  than applying strategy templates. TIMS numbers are usually materially lower
  and are not derivable from anything here.
* NOT the broker's number. Brokers routinely impose house requirements above
  the SRO minimum. Reconcile against the broker before sizing against freed
  capital.
* NOT a calendar/diagonal engine. Every leg must share one expiration; see the
  fail-closed note below.

Fail-closed design
------------------
Anything the module cannot price as a defined-risk combination degrades to the
un-offset naked sum rather than to an optimistic number:

* legs with differing expirations get no offset at all (a calendar/diagonal's
  loss is not the strike-width payoff this module computes), and a short leg
  expiring after a long leg is additionally flagged -- 4210(f)(2)(H) requires
  the short to expire on or before the long, so such a structure is not a
  spread in the first place;
* a combination with unbounded upside loss (net short calls at the top of the
  strike ladder -- ratio spreads, uncovered strangles) is reported with no
  offset;
* malformed input raises `MarginInputError` instead of being coerced. An
  unrecognised `option_type` on a short leg previously returned 0.0 margin.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# FINRA 4210(f)(2)(E) / Cboe strategy-based percentages. Equity options carry
# 20% of the underlying; broad-based index options carry 15%. Both floor at 10%.
EQUITY_UNDERLYING_PCT = 0.20
BROAD_BASED_INDEX_UNDERLYING_PCT = 0.15
MINIMUM_PCT = 0.10

STATUS_OFFSET_APPLIED = "DEFINED_RISK_OFFSET_APPLIED"
STATUS_NO_OFFSET_UNDEFINED_RISK = "NO_OFFSET_UNDEFINED_RISK"
STATUS_NO_OFFSET_MULTI_EXPIRY = "NO_OFFSET_MULTI_EXPIRY"
# Bounded loss, but the naked (E) requirement is already the lesser of the two,
# so recognising the combination frees nothing (a very wide credit spread, or a
# single short leg). Distinguished from OFFSET_APPLIED so an "optimised"
# status never appears on a position that got no offset.
STATUS_NO_OFFSET_NAKED_BINDS = "NO_OFFSET_NAKED_REQUIREMENT_BINDS"

BINDING_MAX_LOSS = "MAX_POTENTIAL_LOSS"
BINDING_NAKED_REQUIREMENT = "NAKED_SHORT_REQUIREMENT"
BINDING_NONE = "NO_SHORT_LEGS"


class MarginInputError(ValueError):
    """Raised when a payload or leg is malformed: unrecognised option type or
    action, non-positive quantity, non-finite or non-positive strike or
    underlying price, negative premium, or an unparseable expiration.

    Bad input is rejected rather than coerced, because every coercion available
    here (treating an unknown option type as zero-margin, abs()-ing a signed
    quantity, letting a NaN premium propagate) understates the requirement, and
    an understated margin number is the failure mode that empties an account.
    """


@dataclass(frozen=True)
class OptionLeg:
    """One listed option leg.

    `action` carries the direction; `quantity` is therefore always a positive
    contract count. `premium` is the per-share option price -- use the current
    mark for a maintenance figure and the trade price for an initial figure,
    since 4210(f)(2)(E) is stated on option *market* value.
    """

    option_type: str                       # 'CALL' or 'PUT'
    action: str                            # 'BUY' or 'SELL'
    strike: float
    expiration: Union[str, date]           # ISO 'YYYY-MM-DD' or datetime.date
    quantity: int = 1
    premium: float = 0.0


@dataclass
class MultiLegStrategyPayload:
    symbol: str
    underlying_price: float
    legs: List[OptionLeg]
    # Shares per contract. 100 for standard US equity/index options, but
    # adjusted contracts (post-split, merger, special dividend) and some
    # index/mini products deliver a different number -- read it from the
    # contract specification rather than assuming.
    contract_multiplier: float = 100.0


@dataclass
class MarginOptimizationReport:
    symbol: str
    strategy_type: str
    # Both requirement figures are gross SRO requirements on the same basis
    # (short-sale proceeds not yet netted), so the saving is apples-to-apples.
    uncombined_requirement_usd: float
    combined_requirement_usd: float
    # FINRA 4210(f)(2)(H) maximum potential loss; None when unbounded.
    max_potential_loss_usd: Optional[float]
    # Positive = net credit received, negative = net debit paid.
    net_premium_usd: float
    # Cash actually to be deposited: the combined requirement less the credit
    # received. Equals "maximum risk less net credit" for a credit spread and
    # the net debit for a debit spread, i.e. the buying-power effect.
    net_capital_required_usd: float
    margin_savings_usd: float
    margin_reduction_pct: float
    binding_constraint: str
    status: str
    audit_notes: str
    warnings: List[str] = field(default_factory=list)


class MultiLegStrategyMarginOptimizerEngine:
    """Strategy-based margin estimator for listed multi-leg option positions.

    Parameters
    ----------
    underlying_pct:
        The 4210(f)(2)(E) percentage of underlying value carried on a short
        leg. 0.20 for equity options (the default), 0.15 for broad-based index
        options. Set it from the option class actually being margined.
    minimum_pct:
        The floor percentage -- 10% of the underlying for calls, 10% of the
        aggregate exercise price for puts.
    """

    _VALID_TYPES = ("CALL", "PUT")
    _VALID_ACTIONS = ("BUY", "SELL")

    def __init__(
        self,
        underlying_pct: float = EQUITY_UNDERLYING_PCT,
        minimum_pct: float = MINIMUM_PCT,
    ) -> None:
        for label, value in (("underlying_pct", underlying_pct), ("minimum_pct", minimum_pct)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not (0.0 < value <= 1.0)
            ):
                raise MarginInputError(
                    f"{label} must be a finite fraction in (0, 1], got {value!r}"
                )
        if minimum_pct > underlying_pct:
            raise MarginInputError(
                f"minimum_pct ({minimum_pct}) cannot exceed underlying_pct ({underlying_pct}) -- "
                "the floor would then bind on every leg."
            )
        self.underlying_pct = float(underlying_pct)
        self.minimum_pct = float(minimum_pct)

    # ---------------------------------------------------------------- input

    @staticmethod
    def _finite_positive(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MarginInputError(f"{label} must be a number, got {value!r}")
        if not math.isfinite(value) or value <= 0.0:
            raise MarginInputError(f"{label} must be finite and positive, got {value!r}")
        return float(value)

    @staticmethod
    def _parse_expiration(value: Union[str, date], leg_index: int) -> date:
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise MarginInputError(
                f"leg[{leg_index}].expiration must be an ISO 'YYYY-MM-DD' string or a "
                f"datetime.date, got {value!r}"
            )
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise MarginInputError(
                f"leg[{leg_index}].expiration {value!r} is not an ISO 'YYYY-MM-DD' date"
            ) from exc

    def _validate(self, payload: MultiLegStrategyPayload) -> List[date]:
        if not isinstance(payload.legs, Sequence) or not payload.legs:
            raise MarginInputError("payload.legs must be a non-empty sequence of OptionLeg.")
        self._finite_positive(payload.underlying_price, "underlying_price")
        self._finite_positive(payload.contract_multiplier, "contract_multiplier")

        expirations: List[date] = []
        for i, leg in enumerate(payload.legs):
            if not isinstance(leg, OptionLeg):
                raise MarginInputError(f"leg[{i}] must be an OptionLeg, got {type(leg).__name__}")
            if str(leg.option_type).upper() not in self._VALID_TYPES:
                raise MarginInputError(
                    f"leg[{i}].option_type must be one of {self._VALID_TYPES}, got "
                    f"{leg.option_type!r}"
                )
            if str(leg.action).upper() not in self._VALID_ACTIONS:
                raise MarginInputError(
                    f"leg[{i}].action must be one of {self._VALID_ACTIONS}, got {leg.action!r}"
                )
            if isinstance(leg.quantity, bool) or not isinstance(leg.quantity, int):
                raise MarginInputError(
                    f"leg[{i}].quantity must be a positive int contract count "
                    f"(direction is carried by `action`), got {leg.quantity!r}"
                )
            if leg.quantity <= 0:
                raise MarginInputError(
                    f"leg[{i}].quantity must be positive; use action='SELL' for a short leg, "
                    f"got {leg.quantity!r}"
                )
            self._finite_positive(leg.strike, f"leg[{i}].strike")
            if (
                isinstance(leg.premium, bool)
                or not isinstance(leg.premium, (int, float))
                or not math.isfinite(leg.premium)
                or leg.premium < 0.0
            ):
                raise MarginInputError(
                    f"leg[{i}].premium must be a finite non-negative per-share price, "
                    f"got {leg.premium!r}"
                )
            expirations.append(self._parse_expiration(leg.expiration, i))
        return expirations

    # ------------------------------------------------------------ mechanics

    def _naked_leg_requirement(
        self, leg: OptionLeg, underlying_price: float, multiplier: float
    ) -> float:
        """FINRA 4210(f)(2)(E) / Cboe strategy-based requirement for one leg."""
        q = leg.quantity
        premium = float(leg.premium)
        s = underlying_price
        k = float(leg.strike)

        if leg.action.upper() == "BUY":
            # Long options are paid for in full. (Cboe permits 75% of cost for
            # listed equity options with more than 9 months to expiration; this
            # module always charges 100%, which is the conservative direction.)
            return premium * multiplier * q

        if leg.option_type.upper() == "CALL":
            out_of_the_money = max(0.0, k - s)
            base = (self.underlying_pct * s) - out_of_the_money + premium
            floor = (self.minimum_pct * s) + premium
        else:
            out_of_the_money = max(0.0, s - k)
            base = (self.underlying_pct * s) - out_of_the_money + premium
            # Puts floor on the aggregate exercise price, not the underlying.
            floor = (self.minimum_pct * k) + premium
        return max(base, floor) * multiplier * q

    @staticmethod
    def _net_intrinsic_per_share(legs: Sequence[OptionLeg], spot: float) -> float:
        """Net intrinsic value of the combination at `spot`, longs positive."""
        total = 0.0
        for leg in legs:
            if leg.option_type.upper() == "CALL":
                intrinsic = max(0.0, spot - leg.strike)
            else:
                intrinsic = max(0.0, leg.strike - spot)
            sign = 1.0 if leg.action.upper() == "BUY" else -1.0
            total += sign * intrinsic * leg.quantity
        return total

    @staticmethod
    def _upper_tail_slope(legs: Sequence[OptionLeg]) -> float:
        """Payoff slope above the highest strike. Negative => unbounded loss."""
        slope = 0.0
        for leg in legs:
            if leg.option_type.upper() == "CALL":
                slope += (1.0 if leg.action.upper() == "BUY" else -1.0) * leg.quantity
        return slope

    def _max_potential_loss(
        self, legs: Sequence[OptionLeg], multiplier: float
    ) -> Optional[float]:
        """FINRA 4210(f)(2)(H) maximum potential loss, or None if unbounded.

        Rule text evaluates intrinsic values "at price points ... that
        correspond to every exercise price present in the spread". Spot 0.0 is
        evaluated in addition, to bound the downside tail below the lowest
        strike; the payoff is piecewise linear with breakpoints only at
        strikes, so extrema can only occur at those points or in the tails.
        """
        if self._upper_tail_slope(legs) < 0.0:
            return None  # net short calls above the ladder -- unbounded.
        price_points = {0.0}
        price_points.update(float(leg.strike) for leg in legs)
        worst = min(self._net_intrinsic_per_share(legs, p) for p in sorted(price_points))
        return max(0.0, -worst) * multiplier

    @staticmethod
    def _classify(legs: Sequence[OptionLeg], bounded: bool) -> str:
        """Descriptive label only. It never feeds the margin computation."""
        if not bounded:
            return "UNDEFINED_RISK_COMBINATION"
        if len(legs) == 1:
            return "SINGLE_LEG"
        if len(legs) == 2:
            first, second = legs
            if (
                first.option_type.upper() == second.option_type.upper()
                and first.action.upper() != second.action.upper()
                and first.strike != second.strike
                and first.quantity == second.quantity
            ):
                return "VERTICAL_SPREAD"
            return "DEFINED_RISK_COMBINATION"
        if len(legs) == 4:
            calls = [leg for leg in legs if leg.option_type.upper() == "CALL"]
            puts = [leg for leg in legs if leg.option_type.upper() == "PUT"]
            balanced = (
                len(calls) == 2
                and len(puts) == 2
                and len({leg.action.upper() for leg in calls}) == 2
                and len({leg.action.upper() for leg in puts}) == 2
                and len({leg.quantity for leg in legs}) == 1
            )
            if balanced:
                short_call = next(leg for leg in calls if leg.action.upper() == "SELL")
                long_call = next(leg for leg in calls if leg.action.upper() == "BUY")
                short_put = next(leg for leg in puts if leg.action.upper() == "SELL")
                long_put = next(leg for leg in puts if leg.action.upper() == "BUY")
                if (
                    short_call.strike < long_call.strike
                    and short_put.strike > long_put.strike
                    and short_put.strike <= short_call.strike
                ):
                    return (
                        "IRON_BUTTERFLY"
                        if short_call.strike == short_put.strike
                        else "IRON_CONDOR"
                    )
                if (
                    long_call.strike < short_call.strike
                    and long_put.strike > short_put.strike
                    and long_put.strike <= long_call.strike
                ):
                    return "REVERSE_IRON_CONDOR"
        return "DEFINED_RISK_COMBINATION"

    # -------------------------------------------------------------- public

    def optimize_strategy_margin(
        self, payload: MultiLegStrategyPayload
    ) -> MarginOptimizationReport:
        """Compare the un-offset per-leg requirement against the recognised
        combination requirement, and report the capital the combination frees.

        Raises
        ------
        MarginInputError
            If the payload or any leg is malformed. Nothing is silently coerced.
        """
        expirations = self._validate(payload)
        legs = list(payload.legs)
        spot = float(payload.underlying_price)
        multiplier = float(payload.contract_multiplier)
        warnings: List[str] = []

        # 1. Un-offset requirement: every leg margined independently.
        long_cost = sum(
            self._naked_leg_requirement(leg, spot, multiplier)
            for leg in legs
            if leg.action.upper() == "BUY"
        )
        naked_short_requirement = sum(
            self._naked_leg_requirement(leg, spot, multiplier)
            for leg in legs
            if leg.action.upper() == "SELL"
        )
        uncombined_requirement = long_cost + naked_short_requirement

        short_proceeds = sum(
            leg.premium * multiplier * leg.quantity
            for leg in legs
            if leg.action.upper() == "SELL"
        )
        net_premium = short_proceeds - long_cost

        # 2. Expiration gate. 4210(f)(2)(H) requires the short leg to expire on
        #    or before the long leg, and the intrinsic-netting payoff below is
        #    only meaningful when all legs expire together.
        single_expiry = len(set(expirations)) == 1
        if not single_expiry:
            latest_long = max(
                (e for e, leg in zip(expirations, legs) if leg.action.upper() == "BUY"),
                default=None,
            )
            latest_short = max(
                (e for e, leg in zip(expirations, legs) if leg.action.upper() == "SELL"),
                default=None,
            )
            if latest_short is not None and (latest_long is None or latest_short > latest_long):
                warnings.append(
                    "A short leg expires after the longest long leg. FINRA Rule 4210(f)(2)(H) "
                    "requires the short option(s) to expire on or before the long option(s), so "
                    "this is not a spread and the short leg is margined naked."
                )
            warnings.append(
                "Legs span more than one expiration (calendar/diagonal). No spread offset was "
                "applied: the strike-width payoff this module computes is not the loss profile "
                "of a multi-expiry structure. Price it against the broker's own requirement."
            )

        # 3. Maximum potential loss and the recognised combination requirement.
        max_potential_loss = self._max_potential_loss(legs, multiplier) if single_expiry else None
        bounded = max_potential_loss is not None

        if not single_expiry:
            status = STATUS_NO_OFFSET_MULTI_EXPIRY
            short_margin = naked_short_requirement
        elif not bounded:
            status = STATUS_NO_OFFSET_UNDEFINED_RISK
            short_margin = naked_short_requirement
            warnings.append(
                "Loss is unbounded above the highest strike (net short calls -- e.g. a ratio "
                "spread or an uncovered short call). No defined-risk offset was applied; the "
                "short legs are margined naked."
            )
        else:
            status = STATUS_OFFSET_APPLIED
            # 4210(f)(2)(H): margin on the shorts is the LESSER of the naked
            # (E) requirement and the maximum potential loss; longs paid in full.
            short_margin = min(naked_short_requirement, max_potential_loss)

        if naked_short_requirement <= 0.0:
            binding = BINDING_NONE
        elif short_margin >= naked_short_requirement:
            binding = BINDING_NAKED_REQUIREMENT
            if status == STATUS_OFFSET_APPLIED:
                status = STATUS_NO_OFFSET_NAKED_BINDS
        else:
            binding = BINDING_MAX_LOSS

        combined_requirement = long_cost + short_margin
        net_capital_required = combined_requirement - short_proceeds

        strategy_type = (
            self._classify(legs, bounded) if single_expiry else "MULTI_EXPIRY_COMBINATION"
        )

        # 4. Savings audit -- both sides on the gross-requirement basis.
        margin_savings = max(0.0, uncombined_requirement - combined_requirement)
        margin_reduction_pct = (
            margin_savings / uncombined_requirement * 100.0
            if uncombined_requirement > 0.0
            else 0.0
        )

        notes = (
            f"[{payload.symbol} - {strategy_type}] status={status}, binding={binding}: "
            f"un-offset requirement ${uncombined_requirement:,.2f} vs combination requirement "
            f"${combined_requirement:,.2f} (saving ${margin_savings:,.2f}, "
            f"{margin_reduction_pct:.1f}%). Net capital to deposit "
            f"${net_capital_required:,.2f} on a net "
            f"{'credit' if net_premium >= 0 else 'debit'} of ${abs(net_premium):,.2f}. "
            "Strategy-based (FINRA 4210(f)(2)) estimate -- reconcile against the broker."
        )
        logger.info(notes)
        for warning in warnings:
            logger.warning("[%s] %s", payload.symbol, warning)

        return MarginOptimizationReport(
            symbol=payload.symbol,
            strategy_type=strategy_type,
            uncombined_requirement_usd=round(uncombined_requirement, 2),
            combined_requirement_usd=round(combined_requirement, 2),
            max_potential_loss_usd=(
                round(max_potential_loss, 2) if max_potential_loss is not None else None
            ),
            net_premium_usd=round(net_premium, 2),
            net_capital_required_usd=round(net_capital_required, 2),
            margin_savings_usd=round(margin_savings, 2),
            margin_reduction_pct=round(margin_reduction_pct, 1),
            binding_constraint=binding,
            status=status,
            audit_notes=notes,
            warnings=warnings,
        )
