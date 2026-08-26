"""
market-data-cost-optimization-tiered-subscriptions: a *recommendation* engine that
assigns each symbol in a trading universe to a market data service tier from its
current trading relevance, and prices the resulting subscription change against a
caller-supplied fee schedule.

Three service tiers are modelled, ordered by richness and cost:

    TIER1_DIRECT_L3    full-depth / order-by-order direct feed
    TIER2_SIP_L1       consolidated real-time top-of-book (SIP or vendor L1)
    TIER3_DELAYED_EOD  delayed or end-of-day data

The tier decision is a *safety* decision before it is a cost decision. Delayed data
is by construction unusable for order placement, so a symbol the strategy might
trade must never be recommended to TIER3 -- see ``determine_optimal_tier``.

HOW MARKET DATA IS ACTUALLY PRICED (verified 2026-08 -- read before trusting a
savings number this module reports)
-----------------------------------------------------------------------------
The intuition behind this skill -- "stop paying for feeds you do not use" -- is
sound, but the fee structures it has to act on are mostly **not** metered per
symbol. On US equities the dominant charges are per firm, per subscriber
(device/user), and per non-display application, and a single entitlement covers the
entire security universe:

- **Nasdaq TotalView** (depth of book): Professional/Corporate **$80.50 per
  subscriber per month** effective 2025-01-01 ($84.00 for 2026), and the price
  list's own "Security Coverage" column for that entitlement reads *"Nasdaq, NYSE,
  and Other Regional Issues"* -- the whole market, not a symbol list. **Nasdaq Depth
  Non-Display (Direct Access Only)** is $396.00 per subscriber for 1-39 subscribers
  and then flat per-firm ($15,840 / $31,680 / $75,000 per firm at 40 / 100 / 250+).
  https://www.nasdaqtrader.com/content/ProductsServices/PriceList/Nasdaq_US_Equities_Price_List_2025_2026_2027.pdf
- **UTP Plan** (SIP for Nasdaq-listed securities), UTP Data Policies, September
  2023: Real-Time Direct Access **$2,500/month per firm**; Real-Time Non-Display Use
  on its own behalf **$3,500/month per firm**; Non-Display for an Electronic Trading
  System **$3,500/month per Electronic Trading System**; Delayed External
  Redistributor $250/month per firm; and **"End-of-Day Usage is not currently fee
  liable."**  https://www.utpplan.com/DOC/datapolicies.pdf
- The one genuinely per-security dimension in that document is the **Per Query
  Policy**: "Vendors are to charge the applicable per query rate for each data pull
  associated with one security", at **$0.0075 per query**, and it is **capped** --
  3,200 quotes / $24 per month for a Professional Subscriber, 134 quotes / $1 per
  month for a Nonprofessional. Per-symbol savings therefore *saturate* at the cap.
- **Bloomberg B-PIPE** publishes no per-symbol rate card; pricing is
  contract-negotiated over data fields, exchanges, redistribution rights and
  consuming applications.
- **LSEG (Refinitiv) DACS** is the entitlement *enforcement and usage reporting*
  system, not a billing engine. A tier decision this module recommends only becomes
  a real cost change once it is applied in DACS/EMRS and reflected in the monthly
  usage report to the exchange.

Consequences this module is built around:

1. ``TIER_COSTS`` below is an **illustrative placeholder schedule with no market
   basis**. Pass your own contracted, symbol-metered rates via
   ``tier_monthly_costs_usd``. The engine logs a warning if you do not.
2. Only the genuinely symbol-metered part of a bill can move. Per-firm, per-
   subscriber and non-display fees do not shrink when the symbol count shrinks, so
   pass them as ``fixed_monthly_platform_cost_usd`` and read
   ``total_savings_percentage_including_fixed`` -- not ``savings_percentage`` --
   when reporting a data-spend reduction to a budget owner.
3. **Fees are not prorated.** UTP Data Policies states plainly: "All fees are
   subject to change and fees will not be prorated." A demotion applied part-way
   through a billing period saves nothing in that period, and re-promoting next
   period costs a full period again. ``min_days_before_demotion`` exists to damp
   that churn; it never blocks a promotion.

Delayed-data reference points, for sizing what TIER3 actually gives up: CME Group
publishes website quotes delayed "at least 10 minutes"
(https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html), while MiFIR
Article 13(2) requires EU trading venues, APAs and systematic internalisers to make
the Article 13(1) data available "free of charge 15 minutes after publication"
(https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mifir/article-13-obligation-make-pre-trade-and).

Limitations
-----------
- **Recommendations, not actions.** Nothing here changes an entitlement. Apply the
  output through your entitlement system and your vendor's change process.
- **No entitlement/licensing check.** Promoting to TIER1 may require a non-display
  or professional-subscriber licence that this module knows nothing about. Gate
  promotions behind ``market-data-entitlement-and-licensing-per-venue``.
- **Single billing currency.** All costs are treated as USD-denominated and
  additive; no FX, tax, or contractual minimum-commitment handling.
- **Activity is an input, not an inference.** ``has_active_signal`` and
  ``days_since_last_trade`` must be supplied by the caller as of the audit date;
  the engine does not read positions, signals, or trade history.
"""
import logging
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

#: Full-depth / order-by-order direct feed.
TIER1_DIRECT_L3 = "TIER1_DIRECT_L3"

#: Consolidated real-time top-of-book (SIP or vendor L1).
TIER2_SIP_L1 = "TIER2_SIP_L1"

#: Delayed or end-of-day data. Unusable for order placement.
TIER3_DELAYED_EOD = "TIER3_DELAYED_EOD"

#: Tier ordering, poorest data to richest. Used to classify a change as a promotion
#: or a demotion independently of price, so a mispriced schedule cannot invert the
#: direction of a decision.
TIER_RANK: Dict[str, int] = {
    TIER3_DELAYED_EOD: 1,
    TIER2_SIP_L1: 2,
    TIER1_DIRECT_L3: 3,
}

#: Lowest tier that can carry a live order. TIER3 is delayed data by definition.
MIN_TRADEABLE_TIER_RANK = TIER_RANK[TIER2_SIP_L1]

#: **Illustrative placeholder schedule -- these are not market rates.** No exchange,
#: SIP or vendor cited in the module docstring prices depth-of-book or consolidated
#: data at a flat per-symbol monthly rate. These values exist only so the module has
#: a runnable default; supply ``tier_monthly_costs_usd`` from your own contract.
TIER_COSTS: Dict[str, float] = {
    TIER1_DIRECT_L3: 1000.0,
    TIER2_SIP_L1: 150.0,
    TIER3_DELAYED_EOD: 5.0,
}

ACTION_MAINTAIN = "MAINTAIN"
ACTION_PROMOTE = "PROMOTE"
ACTION_DEMOTE = "DEMOTE"

#: A demotion the activity rules called for but that was withheld because the symbol
#: has not yet sat in its current tier for ``min_days_before_demotion`` days.
ACTION_HOLD_MIN_DWELL = "HOLD_MIN_DWELL"

STATUS_COST_OPTIMIZATION_SUCCESS = "COST_OPTIMIZATION_SUCCESS"
STATUS_NO_SAVINGS_FOUND = "NO_SAVINGS_FOUND"

#: Net spend rises. Not a failure: promotions demanded by live positions or signals
#: legitimately cost more than the subscriptions they replace.
STATUS_NET_COST_INCREASE = "NET_COST_INCREASE"


@dataclass
class SymbolSubscriptionSpec:
    """One symbol's current subscription and its trading relevance as of the audit.

    Attributes:
        symbol: Instrument identifier. Must be non-empty; compared case-sensitively
            for duplicate detection.
        current_tier: One of ``TIER1_DIRECT_L3`` / ``TIER2_SIP_L1`` /
            ``TIER3_DELAYED_EOD``. Whitespace and case are normalised; any other
            value is rejected rather than silently priced.
        has_active_position: True if the book currently holds a position.
        has_active_signal: True if a strategy currently has a live signal on the
            symbol -- i.e. it may generate an order before the next audit. A live
            signal alone is enough to keep the symbol off delayed data.
        days_since_last_trade: Whole days since the last fill, or ``None`` for
            "never traded". Must be >= 0 when supplied.
        days_in_current_tier: Whole days the symbol has been on ``current_tier``.
            Only consulted when ``min_days_before_demotion`` is configured; defaults
            to 0, which never satisfies a positive dwell requirement.
    """

    symbol: str
    current_tier: str
    has_active_position: bool
    has_active_signal: bool
    days_since_last_trade: Optional[int]
    days_in_current_tier: int = 0


@dataclass
class TierOptimizationDecision:
    """Recommended tier for one symbol and its symbol-metered cost delta.

    ``monthly_savings_usd`` is signed: positive for a demotion that reduces
    symbol-metered cost, negative for a promotion that raises it.
    """

    symbol: str
    previous_tier: str
    recommended_tier: str
    action: str
    previous_cost_usd: float
    recommended_cost_usd: float
    monthly_savings_usd: float
    rationale: str


@dataclass
class MarketDataCostReport:
    """Aggregate result of one audit.

    ``baseline_monthly_spend_usd`` / ``optimized_monthly_spend_usd`` and
    ``savings_percentage`` cover **only the symbol-metered** portion of the bill.
    The ``*_total_*`` fields add ``fixed_monthly_platform_cost_usd``, which is the
    per-firm / per-subscriber / non-display spend that a tier change does not move;
    ``total_savings_percentage_including_fixed`` is the figure to quote as a
    data-spend reduction.
    """

    total_symbols_audited: int
    promotions_count: int
    demotions_count: int
    dwell_holds_count: int
    baseline_monthly_spend_usd: float
    optimized_monthly_spend_usd: float
    total_monthly_savings_usd: float
    savings_percentage: float
    fixed_monthly_platform_cost_usd: float
    baseline_total_monthly_spend_usd: float
    optimized_total_monthly_spend_usd: float
    total_savings_percentage_including_fixed: float
    decisions: List[TierOptimizationDecision]
    status: str
    audit_notes: str


class MarketDataCostOptimizerEngine:
    """Recommends a market data tier per symbol and prices the change.

    Args:
        demotion_inactivity_days_threshold: A symbol with no position and no live
            signal stays on TIER2 while its last fill is within this many days, and
            is only recommended for TIER3 beyond it.
        tier_monthly_costs_usd: Symbol-metered monthly cost per tier, from your
            contract. Must supply a finite, non-negative value for all three tiers.
            Defaults to the illustrative ``TIER_COSTS`` placeholder, which logs a
            warning because those numbers have no market basis.
        fixed_monthly_platform_cost_usd: Monthly market data spend that does *not*
            scale with the symbol count -- per-firm access, per-subscriber
            entitlements, non-display licences, connectivity. Carried through the
            report so a savings percentage is not quoted against a base that
            excludes the majority of the bill.
        min_days_before_demotion: Minimum days a symbol must have spent in its
            current tier before a demotion is recommended. Exists because exchange
            and SIP fees are not prorated, so churn inside a billing period costs
            money and saves none. 0 (the default) disables the guard. Promotions are
            never withheld.

    Raises:
        ValueError: On any invalid constructor argument.
    """

    def __init__(
        self,
        demotion_inactivity_days_threshold: int = 30,
        tier_monthly_costs_usd: Optional[Mapping[str, float]] = None,
        fixed_monthly_platform_cost_usd: float = 0.0,
        min_days_before_demotion: int = 0,
    ) -> None:
        if isinstance(demotion_inactivity_days_threshold, bool) or not isinstance(
            demotion_inactivity_days_threshold, int
        ):
            raise ValueError("demotion_inactivity_days_threshold must be an int.")
        if demotion_inactivity_days_threshold < 0:
            raise ValueError("demotion_inactivity_days_threshold must be >= 0.")

        if isinstance(min_days_before_demotion, bool) or not isinstance(
            min_days_before_demotion, int
        ):
            raise ValueError("min_days_before_demotion must be an int.")
        if min_days_before_demotion < 0:
            raise ValueError("min_days_before_demotion must be >= 0.")

        if isinstance(fixed_monthly_platform_cost_usd, bool) or not isinstance(
            fixed_monthly_platform_cost_usd, (int, float)
        ):
            raise ValueError("fixed_monthly_platform_cost_usd must be numeric.")
        fixed_cost = float(fixed_monthly_platform_cost_usd)
        if not math.isfinite(fixed_cost) or fixed_cost < 0:
            raise ValueError("fixed_monthly_platform_cost_usd must be finite and >= 0.")

        if tier_monthly_costs_usd is None:
            logger.warning(
                "MarketDataCostOptimizerEngine is using the illustrative TIER_COSTS "
                "placeholder schedule (%s). These are not market rates, and no exchange "
                "or SIP cited in this module prices depth-of-book data per symbol. Pass "
                "tier_monthly_costs_usd from your contracted schedule before quoting any "
                "savings figure.",
                TIER_COSTS,
            )
            schedule: Mapping[str, float] = TIER_COSTS
        else:
            schedule = tier_monthly_costs_usd

        validated: Dict[str, float] = {}
        for tier in TIER_RANK:
            if tier not in schedule:
                raise ValueError(f"tier_monthly_costs_usd is missing tier '{tier}'.")
            cost = schedule[tier]
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                raise ValueError(f"tier_monthly_costs_usd['{tier}'] must be numeric.")
            cost = float(cost)
            if not math.isfinite(cost) or cost < 0:
                raise ValueError(
                    f"tier_monthly_costs_usd['{tier}'] must be finite and >= 0, got {cost!r}."
                )
            validated[tier] = cost

        self.demotion_inactivity_days_threshold = demotion_inactivity_days_threshold
        self.tier_monthly_costs_usd = validated
        self.fixed_monthly_platform_cost_usd = fixed_cost
        self.min_days_before_demotion = min_days_before_demotion

    @staticmethod
    def _normalize_spec(spec: SymbolSubscriptionSpec) -> SymbolSubscriptionSpec:
        """Validates one spec and returns a copy with ``current_tier`` normalised.

        Raises:
            ValueError: On a blank symbol, an unrecognised tier, a non-bool activity
                flag, or a negative day count. An unrecognised tier is rejected
                rather than defaulted, because pricing a typo at the most expensive
                tier manufactures a baseline and therefore phantom savings.
        """
        if not isinstance(spec, SymbolSubscriptionSpec):
            raise ValueError(f"Expected SymbolSubscriptionSpec, got {type(spec).__name__}.")

        symbol = spec.symbol.strip() if isinstance(spec.symbol, str) else ""
        if not symbol:
            raise ValueError("SymbolSubscriptionSpec.symbol must be a non-empty string.")

        if not isinstance(spec.current_tier, str):
            raise ValueError(f"{symbol}: current_tier must be a string.")
        tier = spec.current_tier.strip().upper()
        if tier not in TIER_RANK:
            raise ValueError(
                f"{symbol}: unknown current_tier {spec.current_tier!r}. "
                f"Expected one of {sorted(TIER_RANK)}."
            )

        for flag_name in ("has_active_position", "has_active_signal"):
            if not isinstance(getattr(spec, flag_name), bool):
                raise ValueError(f"{symbol}: {flag_name} must be a bool.")

        days = spec.days_since_last_trade
        if days is not None:
            if isinstance(days, bool) or not isinstance(days, int):
                raise ValueError(
                    f"{symbol}: days_since_last_trade must be an int or None "
                    f"(None meaning never traded)."
                )
            if days < 0:
                raise ValueError(f"{symbol}: days_since_last_trade must be >= 0, got {days}.")

        dwell = spec.days_in_current_tier
        if isinstance(dwell, bool) or not isinstance(dwell, int):
            raise ValueError(f"{symbol}: days_in_current_tier must be an int.")
        if dwell < 0:
            raise ValueError(f"{symbol}: days_in_current_tier must be >= 0, got {dwell}.")

        return replace(spec, symbol=symbol, current_tier=tier)

    def determine_optimal_tier(self, spec: SymbolSubscriptionSpec) -> str:
        """Returns the tier the symbol's current trading relevance requires.

        The ordering is deliberate and safety-first:

        1. Position **and** live signal -> ``TIER1_DIRECT_L3``. The symbol is being
           actively worked, so depth is warranted.
        2. Position **or** live signal -> ``TIER2_SIP_L1``. A live signal with no
           position still means an order may be sent before the next audit, so the
           symbol must stay on real-time data even if it has not traded in months.
           Routing it to delayed data would produce exactly the failure this skill
           exists to prevent.
        3. Last fill within ``demotion_inactivity_days_threshold`` ->
           ``TIER2_SIP_L1``. Recently traded names are treated as still in play.
        4. Otherwise -> ``TIER3_DELAYED_EOD``.

        ``days_since_last_trade is None`` means never traded, and never keeps a
        symbol on a real-time tier by itself.
        """
        if spec.has_active_position and spec.has_active_signal:
            return TIER1_DIRECT_L3
        if spec.has_active_position or spec.has_active_signal:
            return TIER2_SIP_L1
        if (
            spec.days_since_last_trade is not None
            and spec.days_since_last_trade <= self.demotion_inactivity_days_threshold
        ):
            return TIER2_SIP_L1
        return TIER3_DELAYED_EOD

    def optimize_market_data_costs(
        self, subscriptions: List[SymbolSubscriptionSpec]
    ) -> MarketDataCostReport:
        """Audits a universe of subscriptions and prices the recommended changes.

        Args:
            subscriptions: One spec per symbol. Must be non-empty and free of
                duplicate symbols -- a duplicate would double-count that symbol's
                spend on both sides of the comparison.

        Returns:
            A :class:`MarketDataCostReport`. Every cost field is symbol-metered
            unless its name contains ``total`` or ``fixed``.

        Raises:
            ValueError: On an empty list, a duplicate symbol, or any invalid spec.
        """
        if not subscriptions:
            raise ValueError("Subscription list cannot be empty.")

        decisions: List[TierOptimizationDecision] = []
        baseline_spend = 0.0
        optimized_spend = 0.0
        promotions = 0
        demotions = 0
        dwell_holds = 0
        seen_symbols: Dict[str, int] = {}

        for position, raw_spec in enumerate(subscriptions):
            spec = self._normalize_spec(raw_spec)
            if spec.symbol in seen_symbols:
                raise ValueError(
                    f"Duplicate symbol {spec.symbol!r} at positions "
                    f"{seen_symbols[spec.symbol]} and {position}; "
                    f"spend would be double-counted."
                )
            seen_symbols[spec.symbol] = position

            curr_tier = spec.current_tier
            target_tier = self.determine_optimal_tier(spec)
            curr_rank = TIER_RANK[curr_tier]
            target_rank = TIER_RANK[target_tier]
            last_trade = (
                "never" if spec.days_since_last_trade is None
                else f"{spec.days_since_last_trade}d ago"
            )

            if target_rank > curr_rank:
                final_tier = target_tier
                action = ACTION_PROMOTE
                promotions += 1
                rationale = (
                    f"Trading relevance (position={spec.has_active_position}, "
                    f"signal={spec.has_active_signal}) requires {target_tier}; "
                    f"promoting from {curr_tier}."
                )
            elif target_rank < curr_rank:
                if spec.days_in_current_tier < self.min_days_before_demotion:
                    # Fees are not prorated: demoting mid-period saves nothing now and
                    # costs a full period again on re-promotion.
                    final_tier = curr_tier
                    action = ACTION_HOLD_MIN_DWELL
                    dwell_holds += 1
                    rationale = (
                        f"{target_tier} indicated but held on {curr_tier}: "
                        f"{spec.days_in_current_tier}d in tier < min_days_before_demotion="
                        f"{self.min_days_before_demotion}, and fees are not prorated."
                    )
                else:
                    final_tier = target_tier
                    action = ACTION_DEMOTE
                    demotions += 1
                    rationale = (
                        f"Trading relevance (position={spec.has_active_position}, "
                        f"signal={spec.has_active_signal}, last fill {last_trade}) needs only "
                        f"{target_tier}; demoting from {curr_tier}."
                    )
            else:
                final_tier = curr_tier
                action = ACTION_MAINTAIN
                rationale = f"{curr_tier} already matches trading relevance."

            # Safety invariant. A dwell hold can only retain a *richer* tier and a
            # promotion is never withheld, so a tradeable symbol cannot reach here.
            if (spec.has_active_position or spec.has_active_signal) and (
                TIER_RANK[final_tier] < MIN_TRADEABLE_TIER_RANK
            ):
                raise RuntimeError(
                    f"Invariant violated: {spec.symbol} is tradeable (position="
                    f"{spec.has_active_position}, signal={spec.has_active_signal}) but was "
                    f"recommended delayed tier {final_tier}."
                )

            prev_cost = self.tier_monthly_costs_usd[curr_tier]
            new_cost = self.tier_monthly_costs_usd[final_tier]
            baseline_spend += prev_cost
            optimized_spend += new_cost

            decisions.append(
                TierOptimizationDecision(
                    symbol=spec.symbol,
                    previous_tier=curr_tier,
                    recommended_tier=final_tier,
                    action=action,
                    previous_cost_usd=prev_cost,
                    recommended_cost_usd=new_cost,
                    monthly_savings_usd=round(prev_cost - new_cost, 2),
                    rationale=rationale,
                )
            )

        total_savings = round(baseline_spend - optimized_spend, 2)
        savings_pct = (
            round((total_savings / baseline_spend) * 100.0, 2) if baseline_spend > 0 else 0.0
        )

        fixed = self.fixed_monthly_platform_cost_usd
        baseline_total = baseline_spend + fixed
        optimized_total = optimized_spend + fixed
        total_pct = (
            round((total_savings / baseline_total) * 100.0, 2) if baseline_total > 0 else 0.0
        )

        if total_savings > 0:
            status = STATUS_COST_OPTIMIZATION_SUCCESS
            notes = (
                f"MARKET DATA COST OPTIMIZATION: symbol-metered spend ${baseline_spend:,.2f} -> "
                f"${optimized_spend:,.2f}/mo (saved ${total_savings:,.2f}/mo = {savings_pct:.2f}% "
                f"of symbol-metered spend, {total_pct:.2f}% of ${baseline_total:,.2f} total spend "
                f"including ${fixed:,.2f} fixed). Promotions = {promotions}, "
                f"Demotions = {demotions}, Dwell holds = {dwell_holds}."
            )
        elif total_savings < 0:
            status = STATUS_NET_COST_INCREASE
            notes = (
                f"MARKET DATA COST AUDIT: net symbol-metered spend RISES ${-total_savings:,.2f}/mo "
                f"(${baseline_spend:,.2f} -> ${optimized_spend:,.2f}) because {promotions} "
                f"symbol(s) require a richer tier for a live position or signal. Data coverage "
                f"takes precedence over cost. Demotions = {demotions}, Dwell holds = {dwell_holds}."
            )
        else:
            status = STATUS_NO_SAVINGS_FOUND
            notes = (
                f"MARKET DATA COST AUDIT: no net symbol-metered change "
                f"(${baseline_spend:,.2f}/mo). Promotions = {promotions}, "
                f"Demotions = {demotions}, Dwell holds = {dwell_holds}."
            )
        logger.info(notes)

        return MarketDataCostReport(
            total_symbols_audited=len(subscriptions),
            promotions_count=promotions,
            demotions_count=demotions,
            dwell_holds_count=dwell_holds,
            baseline_monthly_spend_usd=round(baseline_spend, 2),
            optimized_monthly_spend_usd=round(optimized_spend, 2),
            total_monthly_savings_usd=total_savings,
            savings_percentage=savings_pct,
            fixed_monthly_platform_cost_usd=round(fixed, 2),
            baseline_total_monthly_spend_usd=round(baseline_total, 2),
            optimized_total_monthly_spend_usd=round(optimized_total, 2),
            total_savings_percentage_including_fixed=total_pct,
            decisions=decisions,
            status=status,
            audit_notes=notes,
        )
