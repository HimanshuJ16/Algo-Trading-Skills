"""
liquidity-adjusted-position-sizing: ADV participation cap, Days-to-Liquidate (DTL)
auditor, optional order-book-depth cap, and position size scaling engine.

The position a strategy *wants* and the position it can *exit* are different
quantities. This module caps the second against the first:

    daily_capacity = (max_participation_pct / 100) * adv_shares
    max_shares_adv = daily_capacity * max_dtl_days
    max_shares_depth = max_book_depth_multiple * book_depth_shares   (optional)
    final_shares = sign(target) * floor(min(|target|, max_shares_adv, max_shares_depth))

Days-to-Liquidate is the position divided by the daily capacity the policy allows
itself to consume — i.e. how many sessions of trading at the participation cap it
takes to get flat. It is a *liquidation-horizon* control, not an impact model.

Why the position size itself must be capped, not just the participation rate
---------------------------------------------------------------------------
Empirically the price impact of a metaorder follows a square-root law in total size
relative to daily volume, I ~ sigma * sqrt(Q/V), and to a first approximation it is
insensitive to the participation rate and to the duration over which the metaorder is
worked (Tóth et al., "Anomalous price impact and the critical nature of liquidity in
financial markets", Phys. Rev. X 1, 021006, 2011; refined by Zarinelli et al. 2015,
which fits weak separate exponents ~0.52-0.54 on participation and duration). So
stretching a large order over more days by lowering the participation rate buys much
less impact reduction than a linear model suggests, while adding timing risk. The
control that actually bounds impact is a cap on Q/ADV — which is what
``max_participation_pct * max_dtl_days`` is. Raising ``max_dtl_days`` relaxes that cap
linearly; it does not make the position cheaper to trade.

Threshold provenance
--------------------
The defaults here are *policy* defaults, not regulatory limits. No general rule
obliges a proprietary trader to trade at 10% of ADV. The nearest regulator-set
participation cap in EU/UK equities is the market-abuse safe harbour for buy-back
programmes: Commission Delegated Regulation (EU) 2016/1052, Article 3(3), caps
purchases at 25% of the average daily volume computed over the 20 trading days
preceding the purchase (raised to 50% in conditions of extremely low liquidity,
subject to notification). That is the source of the 20-day averaging convention used
throughout this module, and it is an upper bound for a programme that is explicitly
*not* trying to move the price — not a target.

For funds subject to it, SEC Rule 22e-4 (17 CFR 270.22e-4) classifies positions by how
long they take to convert to cash "without the conversion to cash significantly
changing the market value of the investment" (highly liquid: three business days or
less; illiquid: cannot be sold in seven calendar days or less), requires that
classification account for "trading varying portions of a position ... in sizes that
the fund would reasonably anticipate trading", and caps illiquid investments at 15% of
net assets. That rule applies to registered open-end funds and In-Kind ETFs in the
United States only; it is quoted here as the reference definition of a
days-to-liquidate bucket, not as a constraint on other account types.

Limitations (documented, deliberate)
------------------------------------
- **ADV is an input, not a forecast.** The caller supplies it. A 20-day mean taken
  across a holiday stretch, an index-rebalance print, or a single block trade will
  overstate continuously available volume and therefore the cap. Feed a conservative
  or stress-haircut ADV if the exit that matters is the one during a drawdown.
- **Book depth is a snapshot, not a guarantee.** Displayed size can be pulled between
  the snapshot and the order. The depth cap is a sanity check against instruments
  whose ADV is inflated by a few block prints, not a substitute for the ADV cap.
- **One unit of ``price`` is one unit of ``adv_shares_20d``.** For futures and options
  pass the price *per contract* (quote times the contract multiplier) and ADV in
  contracts; passing a quote unadjusted for the multiplier oversizes the position by
  the multiplier.
- **Single instrument.** No correlation, sector, or portfolio aggregation: several
  independently liquidity-capped positions in the same crowded factor still exit
  through one door. See ``correlation-aware-exposure-limits``.
- **Not an impact or cost model.** It returns a size, never a predicted cost. See
  ``transaction-cost-analysis-tca-integration``.
- **Lot and tick rounding are out of scope.** Share counts are floored to whole units
  so the result can never exceed the cap; venue lot sizes are applied downstream by
  ``minimum-fill-size-and-lot-rounding-logic``.
"""
import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: Averaging window for ADV, matching Delegated Regulation (EU) 2016/1052 Art. 3(3).
ADV_WINDOW_TRADING_DAYS = 20

#: Participation ceiling of the EU/UK buy-back safe harbour (ibid.), for reference only.
MAR_BUYBACK_MAX_PARTICIPATION_PCT = 25.0

#: Policy defaults. Not regulatory limits — see the module docstring.
DEFAULT_MAX_PARTICIPATION_PCT = 10.0
DEFAULT_MAX_DTL_DAYS = 1.0
DEFAULT_MAX_BOOK_DEPTH_MULTIPLE = 1.0

#: Context label used in configuration-time validation errors.
_CONFIG_CONTEXT = "sizer configuration"

#: Which limit produced the returned size.
CONSTRAINT_NONE = "none"
CONSTRAINT_ADV_DTL = "adv_dtl"
CONSTRAINT_BOOK_DEPTH = "book_depth"


@dataclass
class LiquiditySizingResult:
    symbol: str
    target_capital_usd: float
    target_shares: float                 # Signed, unfloored. Negative = short.
    adv_shares_20d: float
    price: float
    max_participation_pct: float
    dtl_days_target: float               # Sessions to exit the *requested* size.
    dtl_days_final: float                # Sessions to exit the *returned* size.
    liquidity_capped_shares: float       # Signed, floored to whole units.
    liquidity_capped_capital_usd: float
    scaling_factor: float                # |final| / |target|; < 1.0 also from flooring.
    is_liquidity_constrained: bool       # True only when a liquidity cap actually bound.
    binding_constraint: str              # CONSTRAINT_NONE | _ADV_DTL | _BOOK_DEPTH
    max_shares_adv_dtl: float            # Unsigned ADV/DTL cap, before flooring.
    message: str
    book_depth_shares: Optional[float] = None
    max_shares_book_depth: Optional[float] = None


def _require_finite(value: float, name: str, context: str) -> float:
    """
    Rejects NaN and +/-Inf before they reach the arithmetic.

    A non-finite input previously survived the ``price <= 0`` guard (every comparison
    against NaN is False), took the *uncapped* branch — because ``nan > cap`` is also
    False — and returned a NaN share count reported as "Liquidity Sizing OK" with a
    scaling factor of 1.0. A pre-trade size limit that silently emits NaN when handed
    corrupt reference data is worse than one that is absent, because the caller has
    been told the position passed.
    """
    if isinstance(value, (str, bytes)):
        # A numeric string here almost always means an unparsed JSON/CSV field upstream.
        # float() would coerce it silently and hide the typing bug in the data feed.
        raise ValueError(f"{name} must be a number, not a string ({context}), got {value!r}.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a real number ({context}), got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite ({context}), got {value!r}.")
    return numeric


def _require_positive(value: float, name: str, context: str) -> float:
    numeric = _require_finite(value, name, context)
    if numeric <= 0.0:
        raise ValueError(f"{name} must be > 0 ({context}), got {numeric}.")
    return numeric


class LiquidityPositionSizer:
    """
    Caps a target position at the size the instrument's liquidity can actually
    support, measured as days-to-liquidate at a bounded participation rate and,
    optionally, as a multiple of displayed book depth.

    The cap binds on the *magnitude* of the request, and the sign is preserved: a
    short is capped exactly as the mirror-image long is. Covering a short in a name
    that cannot absorb the flow is at least as hard as selling the long, so a sizer
    that let negative targets through unbounded would leave the worst case unguarded.
    """

    def __init__(
        self,
        max_participation_pct: float = DEFAULT_MAX_PARTICIPATION_PCT,
        max_dtl_days: float = DEFAULT_MAX_DTL_DAYS,
        max_book_depth_multiple: float = DEFAULT_MAX_BOOK_DEPTH_MULTIPLE,
    ) -> None:
        """
        Args:
            max_participation_pct: Share of ADV the policy will consume per session,
                in percent (10.0 = 10%). Must be in (0, 100]. A negative value used to
                be accepted and silently produced a *negative* cap, turning a capped
                long into a short.
            max_dtl_days: Sessions allowed to reach flat at that participation rate.
                Must be > 0. Together these two set the real control,
                ``max_shares = (pct/100) * ADV * dtl`` — a cap on size relative to ADV.
            max_book_depth_multiple: Multiples of the supplied book depth a position
                may reach, applied only when ``book_depth_shares`` is passed. Must
                be > 0. The 1.0 default is a policy choice with no external basis:
                calibrate it from your own execution data.
        """
        self.max_participation_pct = _require_positive(
            max_participation_pct, "max_participation_pct", _CONFIG_CONTEXT)
        if self.max_participation_pct > 100.0:
            raise ValueError(
                f"max_participation_pct must be <= 100.0, got {self.max_participation_pct}. "
                "A cap above the instrument's entire average daily volume is not a cap.")
        self.max_dtl_days = _require_positive(max_dtl_days, "max_dtl_days", _CONFIG_CONTEXT)
        self.max_book_depth_multiple = _require_positive(
            max_book_depth_multiple, "max_book_depth_multiple", _CONFIG_CONTEXT)

    def calculate_size(
        self,
        symbol: str,
        target_capital_usd: float,
        price: float,
        adv_shares_20d: float,
        book_depth_shares: Optional[float] = None,
    ) -> LiquiditySizingResult:
        """
        Returns the liquidity-capped position for one instrument.

        Args:
            symbol: Instrument identifier, used for logging and audit.
            target_capital_usd: Requested notional in the currency of ``price``.
                May be negative for a short; the magnitude is capped and the sign
                preserved. Zero returns a zero position rather than raising.
            price: Price per unit of ``adv_shares_20d`` — for derivatives, per
                contract including the multiplier. Must be finite and > 0.
            adv_shares_20d: Average daily volume in the same units, conventionally
                over the trailing 20 trading days. Must be finite and > 0. Supply a
                stressed value if the exit being sized is a stressed exit.
            book_depth_shares: Optional snapshot of displayed depth the caller
                considers reachable. When supplied, the position is additionally
                capped at ``max_book_depth_multiple`` times this figure.

        Returns:
            A ``LiquiditySizingResult``. The returned share count is floored to whole
            units, so it never exceeds either the request or any cap.

        Raises:
            ValueError: On a blank or non-string symbol; a non-finite, non-numeric, or
                string-typed value anywhere; a non-positive price, ADV, or depth; or an
                intermediate that overflows to infinity. Sizing fails loudly rather
                than emitting a position derived from bad data — MiFID II RTS 6 Art. 15
                requires pre-trade volume limits to act as hard blocks, and a limit
                that cannot evaluate its inputs has not blocked anything.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}.")

        _symbol_context = f"symbol={symbol!r}"
        price = _require_positive(price, "price", _symbol_context)
        adv_shares_20d = _require_positive(adv_shares_20d, "adv_shares_20d", _symbol_context)
        target_capital_usd = _require_finite(target_capital_usd, "target_capital_usd", _symbol_context)
        if book_depth_shares is not None:
            book_depth_shares = _require_positive(book_depth_shares, "book_depth_shares", _symbol_context)

        raw_target_shares = target_capital_usd / price
        # Finite inputs can still overflow: a sub-normal price or an absurd DTL budget
        # produces an infinite share count or an infinite "cap", and a cap of infinity
        # caps nothing. Fail rather than emit an audit record full of inf.
        raw_target_shares = _require_finite(
            raw_target_shares, "target_capital_usd / price", _symbol_context)
        target_magnitude = abs(raw_target_shares)
        sign = -1.0 if raw_target_shares < 0.0 else 1.0

        # Sessions of trading the policy allows itself to consume, and the resulting
        # size cap. daily_capacity is strictly positive: both factors are validated
        # above, so DTL never divides by zero and needs no fabricated floor.
        daily_capacity_shares = (self.max_participation_pct / 100.0) * adv_shares_20d
        max_shares_adv_dtl = _require_finite(
            daily_capacity_shares * self.max_dtl_days, "ADV/DTL share cap", _symbol_context)

        max_shares_book_depth: Optional[float] = None
        if book_depth_shares is not None:
            max_shares_book_depth = _require_finite(
                self.max_book_depth_multiple * book_depth_shares,
                "book-depth share cap", _symbol_context)

        binding_cap = max_shares_adv_dtl
        binding_constraint = CONSTRAINT_ADV_DTL
        if max_shares_book_depth is not None and max_shares_book_depth < binding_cap:
            binding_cap = max_shares_book_depth
            binding_constraint = CONSTRAINT_BOOK_DEPTH

        is_constrained = target_magnitude > binding_cap
        if not is_constrained:
            binding_constraint = CONSTRAINT_NONE

        # Floored, never rounded: round(9_999.999, 2) returns 10_000.0, which is above
        # the cap it was supposed to enforce. A risk limit may only ever be approached
        # from below.
        final_magnitude = math.floor(min(target_magnitude, binding_cap))
        # `sign * 0.0` is -0.0, which reads as a short of nothing in an audit record.
        final_shares = sign * final_magnitude if final_magnitude else 0.0
        final_capital = final_shares * price

        scaling_factor = 1.0 if target_magnitude == 0.0 else final_magnitude / target_magnitude
        dtl_days_target = target_magnitude / daily_capacity_shares
        dtl_days_final = final_magnitude / daily_capacity_shares

        if is_constrained:
            reason = (
                f"book depth ({book_depth_shares:,.0f} shares x {self.max_book_depth_multiple})"
                if binding_constraint == CONSTRAINT_BOOK_DEPTH
                else f"DTL <= {self.max_dtl_days}d at {self.max_participation_pct}% ADV"
            )
            # Share counts, not just percentages: at the margin a cap that removes one
            # share still rounds to "100.0%" and would read as no cap at all.
            msg = (
                f"LIQUIDITY CAP APPLIED for '{symbol}': {raw_target_shares:,.2f} -> "
                f"{final_shares:,.0f} shares ({target_capital_usd:,.2f} -> {final_capital:,.2f}) "
                f"to enforce {reason}; DTL {dtl_days_target:.2f}d -> {dtl_days_final:.2f}d."
            )
            logger.warning(msg)
        else:
            msg = (
                f"Liquidity sizing OK for '{symbol}': DTL={dtl_days_final:.2f}d "
                f"<= {self.max_dtl_days}d at {self.max_participation_pct}% ADV."
            )
            logger.info(msg)

        # A zero result is a real answer, not a no-op, and it is easy to miss when the
        # caller only reads is_liquidity_constrained: either the cap or the flooring
        # left no whole unit to trade. Surface it in both branches.
        if final_magnitude == 0.0 and target_magnitude > 0.0:
            logger.warning(
                "'%s' admits no position: min(target %.4f, cap %.4f) shares floors to zero. "
                "Widen the policy deliberately or drop the instrument; never round up.",
                symbol, target_magnitude, binding_cap)

        return LiquiditySizingResult(
            symbol=symbol,
            target_capital_usd=round(target_capital_usd, 2),
            target_shares=round(raw_target_shares, 2),
            adv_shares_20d=round(adv_shares_20d, 2),
            price=round(price, 2),
            max_participation_pct=self.max_participation_pct,
            dtl_days_target=round(dtl_days_target, 2),
            dtl_days_final=round(dtl_days_final, 2),
            liquidity_capped_shares=final_shares,
            liquidity_capped_capital_usd=round(final_capital, 2),
            scaling_factor=round(scaling_factor, 4),
            is_liquidity_constrained=is_constrained,
            binding_constraint=binding_constraint,
            max_shares_adv_dtl=max_shares_adv_dtl,
            book_depth_shares=book_depth_shares,
            max_shares_book_depth=max_shares_book_depth,
            message=msg,
        )
