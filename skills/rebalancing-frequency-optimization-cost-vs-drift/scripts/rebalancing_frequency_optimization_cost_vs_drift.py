"""
rebalancing-frequency-optimization-cost-vs-drift: threshold ("no-trade band")
rebalancing optimizer that weighs a quadratic drift penalty against the estimated
transaction cost of the trade it would actually place.

Two independent rules can trigger a rebalance:

1. **Tolerance band breach.** If the largest single-asset absolute weight drift
   ``max_i |w_i_current - w_i_target|`` reaches ``max_drift_threshold_pct``, rebalance
   regardless of economics. This is the risk-mandate rule.
2. **Net economic benefit.** If ``drift_cost - transaction_cost > 0`` *and* the largest
   drift is at least ``min_trade_threshold_pct``, rebalance. The second condition exists
   so that micro-drifts never trigger a trade whose fixed frictions the model does not
   capture.

    drift_cost      = lambda_drift * horizon * sum_i (w_i_current - w_i_target)^2 * V
    transaction_cost = sum_i traded_weight_i * V * (fee_bps_i + slippage_bps_i) / 10_000
    net_benefit      = drift_cost - transaction_cost

If a rule fires but every leg is filtered out — the drift is already inside the
destination boundary, or no leg clears the minimum trade size — the status is downgraded
to ``REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES`` and ``rebalance_recommended`` is ``False``.
A trigger is never reported alongside an empty trade list. When that downgrade follows a
*band breach* it is a mandate breach this engine cannot remediate: it is logged at
``WARNING`` and must be escalated rather than read as a flat book.

``transaction_cost`` is priced on the trades this engine would *actually* emit — after
the destination shrink and the minimum-leg filters below — not on the raw drift. Pricing
the raw drift would overstate the cost of a partial rebalance and bias the decision
toward doing nothing.

Rebalance destination (Leland / Vanguard "destination point")
------------------------------------------------------------
Leland (1999) shows that under proportional transaction costs the optimal policy is a
no-trade region around the target weights: hold while inside, and when outside trade
back to the *boundary* of the region rather than all the way to the target. Vanguard's
target-date research implements exactly this as a threshold/destination pair — their
"200/175" policy trades only when drift reaches 200 bps and then only back to 175 bps,
because "selecting a destination closer to the threshold can help reduce the size of
rebalancing trades and lower the associated transaction costs".

``Config.destination_drift_pct`` exposes that destination. Left at ``None`` the engine
rebalances fully to target (destination = 0), which is the conventional but higher-
turnover corner case. When set, every drift is shrunk by the *same* factor
``destination / max_drift``, so the asset with the largest drift lands exactly on the
destination and the active weights still sum to zero. Clamping each leg independently
would break that budget identity for three or more assets and produce a trade set whose
post-trade weights do not sum to one.

Note this is a uniform-shrink generalisation of "trade to the boundary", not a
reproduction of Leland's exact multi-asset no-trade region, which is a numerically
computed object depending on the covariance matrix and per-asset cost asymmetry. This
engine does not compute that region.

Limitations (documented, deliberate)
------------------------------------
- **The drift penalty is not tracking error.** ``sum_i d_i^2`` is the squared L2 norm of
  the active-weight vector. It equals tracking-error variance only if the asset
  covariance matrix is ``sigma^2 * I`` — uncorrelated assets of equal variance. Real
  portfolios are neither, so a 2% drift in two highly correlated sleeves is penalised the
  same as a 2% drift between stocks and bonds, which it should not be. Use a covariance-
  aware measure when correlation matters.
- **lambda_drift has no canonical value and carries units.** It converts squared active
  weight into a currency-denominated cost *per evaluation period*. It is not a
  dimensionless knob: it implicitly bundles risk aversion and asset variance. It must be
  calibrated, and ``drift_horizon_periods`` must express how long the drift is expected to
  persist before the next evaluation. Comparing a per-period drift penalty against a
  one-shot transaction cost without setting both is dimensionally meaningless and biases
  toward over-trading as the evaluation interval shortens.
- **Single period, no forecast.** There is no return, volatility, or drift forecast; the
  decision is made purely on the current snapshot.
- **Costs are proportional only.** Fees and slippage are linear in notional. Fixed
  per-order costs, tiered commissions, borrow costs, and square-root market impact are
  not modelled, so the engine underprices very small and very large orders alike.
- **Taxes are out of scope.** Leland's model includes a capital-gains component; this one
  does not. Rebalancing a taxable account on this output alone can realise gains that
  dwarf the modelled saving — see ``cross-strategy-tax-lot-optimization``.
- **Trades are gross of cost and assume no external cash flow.** Sell and buy notionals
  net to zero by construction, so the transaction cost itself is unfunded. A caller
  settling in cash must reserve for it separately.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Absolute tolerance for the "weights sum to 1" and "current_weight agrees with
#: asset_value_usd" consistency checks. 1e-6 of portfolio weight is 0.0001 bps — tight
#: enough to catch a genuinely inconsistent snapshot, loose enough for float error.
DEFAULT_WEIGHT_TOLERANCE = 1e-6


@dataclass
class Config:
    """Rebalancing policy parameters. Every threshold here is a house choice, not an
    industry standard; see ``references/standards.md``."""

    enabled: bool = True

    #: Penalty applied to squared active weight, **per evaluation period**, in currency
    #: units of portfolio value. No canonical value exists — calibrate it.
    drift_penalty_lambda: float = 1.0

    #: Number of evaluation periods the drift is assumed to persist before the next
    #: rebalancing decision. Scales the drift penalty so it is comparable to the one-shot
    #: transaction cost. Must be > 0.
    drift_horizon_periods: float = 1.0

    #: Tolerance band half-width. A single-asset absolute drift **at or above** this value
    #: triggers a rebalance regardless of the cost/benefit comparison.
    max_drift_threshold_pct: float = 0.05

    #: Largest drift must reach this before the net-benefit rule may trigger a rebalance.
    #: Guards against micro-drift churn the proportional cost model underprices.
    min_trade_threshold_pct: float = 0.01

    #: Post-trade destination drift (Leland boundary / Vanguard destination point).
    #: ``None`` rebalances fully to target. When set, must be < max_drift_threshold_pct,
    #: otherwise a triggering breach would imply no trade.
    destination_drift_pct: Optional[float] = None

    #: A leg whose traded weight is below this is dropped from the trade set. Prevents
    #: a breach on one asset from dragging negligible legs along with it.
    min_leg_trade_pct: float = 0.0005

    #: A leg whose traded notional is below this is dropped from the trade set.
    min_leg_trade_usd: float = 0.0

    #: Tolerance for input consistency checks (weight sums, value/weight agreement).
    weight_tolerance: float = DEFAULT_WEIGHT_TOLERANCE

    def validate(self) -> None:
        """Raise ``ValueError`` if the policy parameters are internally inconsistent."""
        for name in (
            "drift_penalty_lambda", "drift_horizon_periods", "max_drift_threshold_pct",
            "min_trade_threshold_pct", "min_leg_trade_pct", "min_leg_trade_usd",
            "weight_tolerance",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Config.{name} must be a finite number, got {value!r}")

        if self.drift_penalty_lambda < 0.0:
            raise ValueError("Config.drift_penalty_lambda must be >= 0")
        if self.drift_horizon_periods <= 0.0:
            raise ValueError("Config.drift_horizon_periods must be > 0")
        if not 0.0 < self.max_drift_threshold_pct <= 1.0:
            raise ValueError("Config.max_drift_threshold_pct must be in (0, 1]")
        if not 0.0 <= self.min_trade_threshold_pct <= self.max_drift_threshold_pct:
            raise ValueError(
                "Config.min_trade_threshold_pct must be in "
                "[0, max_drift_threshold_pct]"
            )
        if self.min_leg_trade_pct < 0.0 or self.min_leg_trade_usd < 0.0:
            raise ValueError("Config minimum-leg thresholds must be >= 0")
        if self.weight_tolerance < 0.0:
            raise ValueError("Config.weight_tolerance must be >= 0")

        if self.destination_drift_pct is not None:
            dest = self.destination_drift_pct
            if not isinstance(dest, (int, float)) or not math.isfinite(dest):
                raise ValueError(
                    f"Config.destination_drift_pct must be finite or None, got {dest!r}"
                )
            if dest < 0.0:
                raise ValueError("Config.destination_drift_pct must be >= 0")
            if dest >= self.max_drift_threshold_pct:
                raise ValueError(
                    "Config.destination_drift_pct must be < max_drift_threshold_pct; "
                    f"got destination={dest} >= threshold={self.max_drift_threshold_pct}. "
                    "A destination at or beyond the band edge means a breach would "
                    "generate no trade."
                )


@dataclass
class AssetWeight:
    """One portfolio sleeve. ``current_weight`` must agree with
    ``asset_value_usd / total_portfolio_value`` — the engine cross-checks them rather
    than trusting one and sizing trades off the other."""

    symbol: str
    target_weight: float                  # e.g. 0.50 for 50%
    current_weight: float                 # e.g. 0.60 for 60%
    asset_value_usd: float
    fee_rate_bps: float = 5.0             # commission, basis points of traded notional
    estimated_slippage_bps: float = 5.0   # market impact, basis points of traded notional


@dataclass
class RebalanceTradeOrder:
    symbol: str
    action: str                  # 'BUY' or 'SELL'
    weight_delta_pct: float      # signed current-minus-target drift, in percent
    trade_amount_usd: float      # always positive; direction is carried by `action`
    traded_weight_pct: float     # portfolio weight actually traded, in percent
    residual_drift_pct: float    # signed drift remaining after the trade, in percent


@dataclass
class RebalanceOptimizationReport:
    total_portfolio_value_usd: float
    total_drift_cost_usd: float
    total_transaction_cost_usd: float
    net_economic_benefit_usd: float
    max_single_drift_pct: float
    rebalance_recommended: bool
    proposed_trades: List[RebalanceTradeOrder]
    #: One of ``REBALANCE_TRIGGERED_MAX_DRIFT``, ``REBALANCE_TRIGGERED_NET_BENEFIT``,
    #: ``NO_REBALANCE_WITHIN_BAND``, ``REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES``,
    #: ``ENGINE_DISABLED``, ``NO_ASSETS``. ``rebalance_recommended`` is True only for the
    #: two ``REBALANCE_TRIGGERED_*`` statuses, and only ever alongside a non-empty
    #: ``proposed_trades``.
    status: str
    audit_notes: str
    #: Post-trade drift of the largest-drift asset, in percent. Equals 0.0 for a full
    #: rebalance to target and ``destination_drift_pct`` when a destination is set.
    destination_drift_pct: float = 0.0
    #: Legs dropped by the minimum-leg filters, as ``(symbol, reason)``.
    suppressed_legs: List[str] = None

    def __post_init__(self) -> None:
        if self.suppressed_legs is None:
            self.suppressed_legs = []


class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled


class RebalancingFrequencyOptimizerEngine:
    """
    Threshold ("no-trade band") portfolio rebalancing optimizer weighing a quadratic
    drift penalty against the transaction cost of the trade it would actually place.

    Raises ``ValueError`` on malformed configuration or an internally inconsistent
    portfolio snapshot. It never returns a report built from non-finite or
    budget-violating inputs: a silently wrong trade list is worse than an exception.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.config.validate()

    # ------------------------------------------------------------------ validation

    def _validate_assets(self, assets: List[AssetWeight]) -> float:
        """Validate the snapshot and return the total portfolio value."""
        seen = set()
        for a in assets:
            if not isinstance(a.symbol, str) or not a.symbol.strip():
                raise ValueError(f"AssetWeight.symbol must be a non-empty string, got {a.symbol!r}")
            if a.symbol in seen:
                raise ValueError(
                    f"Duplicate symbol {a.symbol!r} in portfolio snapshot; drift and "
                    "trades would be double-counted"
                )
            seen.add(a.symbol)

            for name in ("target_weight", "current_weight", "asset_value_usd",
                         "fee_rate_bps", "estimated_slippage_bps"):
                value = getattr(a, name)
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(
                        f"{a.symbol}: {name} must be a finite number, got {value!r}"
                    )

            if a.fee_rate_bps < 0.0 or a.estimated_slippage_bps < 0.0:
                raise ValueError(
                    f"{a.symbol}: fee_rate_bps and estimated_slippage_bps must be >= 0"
                )

        tol = self.config.weight_tolerance

        target_sum = math.fsum(a.target_weight for a in assets)
        if abs(target_sum - 1.0) > tol:
            raise ValueError(
                f"target_weight must sum to 1.0 (got {target_sum!r}); a target book that "
                "does not sum to one has no feasible rebalance"
            )

        current_sum = math.fsum(a.current_weight for a in assets)
        if abs(current_sum - 1.0) > tol:
            raise ValueError(
                f"current_weight must sum to 1.0 (got {current_sum!r}); otherwise the "
                "generated buys and sells do not net to zero"
            )

        total_value = math.fsum(a.asset_value_usd for a in assets)
        if not math.isfinite(total_value) or total_value <= 0.0:
            raise ValueError(
                f"Total portfolio value must be finite and > 0, got {total_value!r}"
            )

        for a in assets:
            implied = a.asset_value_usd / total_value
            if abs(implied - a.current_weight) > tol:
                raise ValueError(
                    f"{a.symbol}: current_weight={a.current_weight!r} disagrees with "
                    f"asset_value_usd/total={implied!r} (tolerance {tol}). Drift is "
                    "measured from the weight but trades are sized from the value, so "
                    "an inconsistent snapshot produces wrongly sized orders."
                )

        return total_value

    # -------------------------------------------------------------------- reporting

    @staticmethod
    def _empty_report(status: str, notes: str) -> RebalanceOptimizationReport:
        return RebalanceOptimizationReport(
            total_portfolio_value_usd=0.0,
            total_drift_cost_usd=0.0,
            total_transaction_cost_usd=0.0,
            net_economic_benefit_usd=0.0,
            max_single_drift_pct=0.0,
            rebalance_recommended=False,
            proposed_trades=[],
            status=status,
            audit_notes=notes,
        )

    # ------------------------------------------------------------------------ main

    def optimize_rebalancing(
        self, assets: List[AssetWeight]
    ) -> RebalanceOptimizationReport:
        """
        Evaluate drift cost against transaction cost and return a rebalancing decision.

        Raises:
            ValueError: the snapshot is malformed, non-finite, contains duplicate
                symbols, or has weights that do not sum to one or do not agree with the
                supplied asset values.
        """
        if not self.config.enabled:
            return self._empty_report(
                "ENGINE_DISABLED", "Rebalancing optimizer engine is disabled."
            )

        if not assets:
            return self._empty_report(
                "NO_ASSETS", "No assets provided for rebalancing optimization."
            )

        total_portfolio_val = self._validate_assets(assets)

        drifts = [(a, a.current_weight - a.target_weight) for a in assets]
        max_drift = max(abs(d) for _, d in drifts)
        total_drift_sq = math.fsum(d * d for _, d in drifts)

        # Shrink factor placing the largest-drift asset on the destination boundary.
        # Applying one factor to every leg keeps sum(residual drift) == 0, so post-trade
        # weights still sum to one. Independent per-leg clamping would not.
        destination = self.config.destination_drift_pct
        if destination is None or max_drift <= 0.0:
            shrink = 0.0
        else:
            shrink = min(1.0, destination / max_drift)

        proposed_trades: List[RebalanceTradeOrder] = []
        suppressed_legs: List[str] = []
        total_tx_cost = 0.0

        for a, drift in drifts:
            traded_weight = abs(drift) * (1.0 - shrink)
            trade_amount_usd = traded_weight * total_portfolio_val

            if (traded_weight < self.config.min_leg_trade_pct
                    or trade_amount_usd < self.config.min_leg_trade_usd):
                if traded_weight > 0.0:
                    suppressed_legs.append(a.symbol)
                continue

            total_cost_bps = a.fee_rate_bps + a.estimated_slippage_bps
            total_tx_cost += trade_amount_usd * (total_cost_bps / 10000.0)

            residual_drift = drift * shrink
            proposed_trades.append(
                RebalanceTradeOrder(
                    symbol=a.symbol,
                    action="SELL" if drift > 0 else "BUY",
                    weight_delta_pct=round(drift * 100.0, 4),
                    trade_amount_usd=round(trade_amount_usd, 2),
                    traded_weight_pct=round(traded_weight * 100.0, 4),
                    residual_drift_pct=round(residual_drift * 100.0, 4),
                )
            )

        total_drift_cost = (
            self.config.drift_penalty_lambda
            * self.config.drift_horizon_periods
            * total_drift_sq
            * total_portfolio_val
        )
        net_benefit = total_drift_cost - total_tx_cost

        max_drift_breached = max_drift >= self.config.max_drift_threshold_pct
        net_benefit_positive = (
            net_benefit > 0.0 and max_drift >= self.config.min_trade_threshold_pct
        )
        rebalance_recommended = max_drift_breached or net_benefit_positive

        if max_drift_breached:
            status = "REBALANCE_TRIGGERED_MAX_DRIFT"
        elif net_benefit_positive:
            status = "REBALANCE_TRIGGERED_NET_BENEFIT"
        else:
            status = "NO_REBALANCE_WITHIN_BAND"

        blocked_note = ""
        if rebalance_recommended and not proposed_trades:
            # A trigger fired but every leg was filtered out — either the drift is
            # already inside the destination boundary, or the minimum-leg thresholds
            # suppressed everything. Reporting rebalance_recommended=True alongside an
            # empty trade list would tell the caller a rebalance is under way when
            # nothing will be executed.
            status = "REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES"
            rebalance_recommended = False
            if max_drift_breached:
                # A live mandate breach that cannot be remediated by this engine. This
                # must escalate, not disappear into a quiet "no rebalance".
                blocked_note = (
                    f" MANDATE BREACH UNREMEDIATED: max drift {max_drift * 100.0:.2f}% "
                    f">= band {self.config.max_drift_threshold_pct * 100.0:.2f}% but no "
                    "leg cleared the minimum trade size. Escalate; do not treat as flat."
                )
            else:
                blocked_note = (
                    " Net benefit was positive but drift is already inside the "
                    "destination boundary, so there is nothing to trade."
                )
        elif not rebalance_recommended:
            # The costed trade set stays in the report as the evaluated alternative, but
            # nothing is emitted for execution.
            proposed_trades = []

        notes = (
            f"REBALANCING OPTIMIZATION [{status}]: Portfolio Val = ${total_portfolio_val:,.2f}, "
            f"Max Single Drift = {max_drift * 100.0:.2f}%, Total Drift Cost = ${total_drift_cost:,.2f}, "
            f"Total Tx Cost = ${total_tx_cost:,.2f}, Net Benefit = ${net_benefit:,.2f}."
        )
        if destination is not None:
            notes += (
                f" Destination boundary = {destination * 100.0:.2f}% "
                f"(shrink factor {shrink:.4f})."
            )
        if suppressed_legs:
            notes += f" Legs below minimum trade size, not traded: {', '.join(suppressed_legs)}."
        notes += blocked_note

        if max_drift_breached and status == "REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES":
            logger.warning(notes)
        else:
            logger.info(notes)

        return RebalanceOptimizationReport(
            total_portfolio_value_usd=round(total_portfolio_val, 2),
            total_drift_cost_usd=round(total_drift_cost, 2),
            total_transaction_cost_usd=round(total_tx_cost, 2),
            net_economic_benefit_usd=round(net_benefit, 2),
            max_single_drift_pct=round(max_drift * 100.0, 4),
            rebalance_recommended=rebalance_recommended,
            proposed_trades=proposed_trades,
            status=status,
            audit_notes=notes,
            destination_drift_pct=round(max_drift * shrink * 100.0, 4),
            suppressed_legs=suppressed_legs,
        )
