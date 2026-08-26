"""
meta-strategy-signal-arbitration: pre-routing arbitration layer for portfolios
running several independent sub-strategies over a shared symbol universe.

For one symbol, the layer collapses N sub-strategy requests into at most one
executable order:

    veto      = any(signal.is_risk_veto)                 -> flat, no order
    S_cons    = sum_k(w_k * S_k * C_k) / sum_k(w_k)      in [-1, +1]
    gross     = sum_k |N_k|
    net       = sum_k  N_k
    netted    = gross - |net|                            notional never routed
    savings   = netted * cost_bps / 10_000
    deadband  = |S_cons - S_prev| < eps                  -> suppress rebalance

``savings`` is an *estimate of cost avoided*, not a realised P&L figure, and it is
only meaningful if ``target_notional_usd`` carries the exposure **change** each
sub-strategy is requesting. See Limitations.

Why opposing internal orders must not both reach the venue
----------------------------------------------------------
The cost argument (two crossings of the spread instead of one) is the smaller half.
The larger half is self-match / wash-trade exposure, and a meta-arbitration layer
changes a firm's position on it in a way worth stating plainly:

- **FINRA Rule 5210, Supplementary Material .02 (Self-Trades)** -- US equities.
  "Transactions resulting from orders that originate from unrelated algorithms or
  separate and distinct trading strategies within the same firm would generally be
  considered bona fide self-trades." The same provision *requires* members to have
  "policies and procedures in place that are reasonably designed to review their
  trading activity for, and prevent, a pattern or practice of self-trades resulting
  from orders originating from a single algorithm or trading desk, or related
  algorithms or trading desks." (Amended effective 2017-04-03, SR-FINRA-2017-004.)
  https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210
- **CME/CBOT/NYMEX/COMEX Rule 534 ("Wash Trades Prohibited")** -- US futures.
  Rule text: "No person shall place or accept buy and sell orders in the same
  product and expiration month ... where the person knows or reasonably should know
  that the purpose of the orders is to avoid taking a bona fide market position
  exposed to market risk." The accompanying Market Regulation Advisory Notice
  distinguishes algorithms run by *fully independent* trading groups with no
  knowledge of one another's orders (not wash trades, though the firm "must be able
  to demonstrate the independence") from "otherwise independent algorithms ...
  operated and/or controlled by the same individual or team" trading against each
  other on more than an incidental basis, which "may be deemed to violate the
  prohibition" and for which the exchange recommends employing functionality to
  minimise self-matching. Self-Match Prevention (SMP) is explicitly **optional**,
  and no numeric "incidental" threshold is prescribed. Q&A text verified against
  RA1411-5 as filed with the CFTC; the currently operative notice in this series is
  CME Group RA2008-5 (effective trade date 2020-09-17), whose amendment concerned an
  audit-trail tag reference rather than substantive guidance. Confirm the live
  notice before relying on Q&A numbering.
  https://www.cftc.gov/sites/default/files/filings/orgrules/14/12/rule121714comexdcm012.pdf

The consequence for anything built on this module: **routing an arbitrated batch is
a coordinated act.** Once sub-strategies feed a common arbitrator they are related
algorithms under shared control, so the "unrelated algorithms / fully independent
groups" reading is the harder one for the firm to hold. Netting here is the control
that makes the question moot -- the opposing interest is offset in the firm's own
book and never becomes an order -- which is why bypassing the arbitrator "so the
strategies stay independent" is the wrong instinct. It removes the mitigation
without restoring the independence.

None of the above substitutes for venue-level SMP configuration or for the
broker/market-access pre-trade risk controls; this layer sits upstream of both.

Limitations (read before acting on an output)
---------------------------------------------
- **Flat-start assumption in the savings figure.** ``gross_notional_usd`` is
  ``sum |target_notional_usd|``, which equals traded notional only when that field
  carries an exposure *change*. Feed absolute position targets from a non-flat book
  and both ``gross_notional_usd`` and ``internal_netting_savings_usd`` overstate
  what would actually have been traded. The engine has no view of current positions
  and cannot detect the mistake.
- **``estimated_transaction_cost_bps`` is a one-way, all-in cost per unit of
  notional**, not a quoted spread. SEC Regulation NMS defines the average effective
  spread as "double the amount of difference between the execution price and the
  midpoint of the national best bid and national best offer" (17 CFR 242.600(b)),
  i.e. the cost of crossing measured against mid is *half* the quoted spread. A
  caller who passes a full quoted spread in bps overstates savings by roughly 2x.
  https://www.law.cornell.edu/cfr/text/17/242.600
- **No per-strategy fill allocation.** Netting destroys the one-to-one link between
  a sub-strategy's request and an execution. This module reports the net order only;
  attributing fills and P&L back to sub-strategies is a separate step, and skipping
  it silently corrupts per-strategy performance measurement.
- **The deadband gates on signal, not on notional.** A large change in requested
  notional at an unchanged consensus signal is suppressed.
  ``current_consensus_signal`` defaults to 0.0, so the first call for a symbol
  compares against flat.
- **No cross-symbol netting and no portfolio view.** One symbol per call, by design.
- **Validation is fail-closed.** Malformed input raises ``ValueError`` and no order
  is produced. Callers must treat that as "do not trade this symbol" -- never as a
  retryable condition, and never swallow it and fall through to the sub-strategies'
  raw orders, which is exactly the un-netted routing this module exists to prevent.
- **Stateless per call.** The engine holds only its two configuration values and
  mutates nothing during arbitration, so concurrent calls are safe provided the
  configuration is not reassigned underneath them.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "SubStrategySignal",
    "StrategyWeightConfig",
    "MetaStrategyArbitrationReport",
    "MetaStrategySignalArbitratorEngine",
    "STATUS_NETTED_ORDER",
    "STATUS_VETO_RISK_OFF",
    "STATUS_DEADBAND_SUPPRESSED",
]

#: An arbitrated net order was produced and is eligible for routing.
STATUS_NETTED_ORDER = "ARBITRATION_NETTED_ORDER_GENERATED"

#: At least one sub-strategy raised a risk-off veto; target exposure is flat.
STATUS_VETO_RISK_OFF = "ARBITRATION_VETO_RISK_OFF"

#: Consensus moved less than the deadband; the existing position is left alone.
STATUS_DEADBAND_SUPPRESSED = "DEADBAND_REBALANCING_SUPPRESSED"

#: Basis points per unit. 1 bp = 0.01% = 1/10_000.
BPS_DENOMINATOR = 10_000.0


@dataclass
class SubStrategySignal:
    """One sub-strategy's request for a single symbol."""

    strategy_id: str
    symbol: str
    raw_signal: float                   # Direction & strength, [-1.0, +1.0]
    conviction_score: float             # Confidence, [0.0, 1.0]
    target_notional_usd: float          # Requested exposure CHANGE (+ long, - short)
    is_risk_veto: bool = False          # True = emergency risk-off / stop loss


@dataclass
class StrategyWeightConfig:
    """Capital allocation weight for one sub-strategy."""

    strategy_id: str
    weight: float                       # Allocation weight, > 0 (e.g. 0.50)
    priority_rank: int = 1              # Informational; 1 = highest priority


@dataclass
class MetaStrategyArbitrationReport:
    """Structured, auditable outcome of one arbitration pass."""

    symbol: str
    total_strategies_count: int
    gross_notional_usd: float            # Sum of absolute sub-strategy requests
    net_executable_notional_usd: float   # Net arbitrated order value (0.0 = no order)
    internal_netting_savings_usd: float  # Est. cost avoided by netting THIS order
    consensus_signal: float              # Weighted consensus signal, [-1.0, +1.0]
    is_risk_veto_active: bool
    status: str                          # One of the STATUS_* constants
    audit_notes: str


def _require_finite(label: str, value: float) -> float:
    """Reject NaN/Inf before it can reach an order size."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number, got {value!r}.")
    return numeric


class MetaStrategySignalArbitratorEngine:
    """
    Collapses conflicting sub-strategy signals for one symbol into at most one net
    order, enforcing risk-off veto precedence and a rebalancing deadband.

    Validation is fail-closed: any malformed input raises ``ValueError`` and no
    order is produced.
    """

    def __init__(
        self,
        deadband_threshold: float = 0.05,
        estimated_transaction_cost_bps: float = 10.0,
    ) -> None:
        """
        Args:
            deadband_threshold: Minimum absolute consensus-signal move required to
                rebalance. Must be finite and >= 0; 0.0 disables suppression.
            estimated_transaction_cost_bps: One-way all-in cost per unit of notional,
                in basis points (spread crossing measured against mid, plus fees).
                Must be finite and >= 0. See the module Limitations on half-spread.
        """
        deadband = _require_finite("deadband_threshold", deadband_threshold)
        cost_bps = _require_finite(
            "estimated_transaction_cost_bps", estimated_transaction_cost_bps
        )
        if deadband < 0.0:
            raise ValueError(f"deadband_threshold must be >= 0, got {deadband}.")
        if cost_bps < 0.0:
            raise ValueError(
                f"estimated_transaction_cost_bps must be >= 0, got {cost_bps}."
            )

        self.deadband_threshold: float = deadband
        self.estimated_transaction_cost_bps: float = cost_bps

    @staticmethod
    def _validate_signals(symbol: str, signals: List[SubStrategySignal]) -> None:
        """Reject empty batches, cross-symbol contamination, duplicates, bad ranges."""
        if not signals:
            raise ValueError("Sub-strategy signals list cannot be empty.")

        seen: Set[str] = set()
        for signal in signals:
            if signal.symbol != symbol:
                raise ValueError(
                    f"Signal from {signal.strategy_id!r} is for symbol {signal.symbol!r}, "
                    f"not the arbitrated symbol {symbol!r}. Netting across symbols would "
                    "produce an order for an exposure no strategy requested."
                )
            if signal.strategy_id in seen:
                raise ValueError(
                    f"Duplicate signal for strategy_id {signal.strategy_id!r} on {symbol!r}; "
                    "each strategy must submit one netted request per symbol."
                )
            seen.add(signal.strategy_id)

            raw = _require_finite(f"raw_signal for {signal.strategy_id!r}", signal.raw_signal)
            conviction = _require_finite(
                f"conviction_score for {signal.strategy_id!r}", signal.conviction_score
            )
            _require_finite(
                f"target_notional_usd for {signal.strategy_id!r}", signal.target_notional_usd
            )

            if not -1.0 <= raw <= 1.0:
                raise ValueError(
                    f"raw_signal for {signal.strategy_id!r} must be within [-1.0, 1.0], "
                    f"got {raw}."
                )
            if not 0.0 <= conviction <= 1.0:
                raise ValueError(
                    f"conviction_score for {signal.strategy_id!r} must be within [0.0, 1.0], "
                    f"got {conviction}."
                )

    @staticmethod
    def _build_weight_map(
        weights: List[StrategyWeightConfig],
        signals: List[SubStrategySignal],
    ) -> Dict[str, float]:
        """
        Map strategy_id -> weight, requiring an explicit weight for every signal.

        A missing weight is a configuration error, not a default: silently treating
        an unrecognised strategy_id as weight 1.0 lets a single typo outvote every
        correctly configured strategy and can invert the sign of the consensus.
        """
        weight_map: Dict[str, float] = {}
        for config in weights:
            if config.strategy_id in weight_map:
                raise ValueError(
                    f"Duplicate weight config for strategy_id {config.strategy_id!r}."
                )
            weight = _require_finite(f"weight for {config.strategy_id!r}", config.weight)
            if weight <= 0.0:
                raise ValueError(
                    f"weight for {config.strategy_id!r} must be > 0, got {weight}."
                )
            weight_map[config.strategy_id] = weight

        unknown = sorted({s.strategy_id for s in signals if s.strategy_id not in weight_map})
        if unknown:
            raise ValueError(
                f"No allocation weight configured for strategy_id(s) {unknown}. "
                "Every signalling strategy must have an explicit weight."
            )
        return weight_map

    @staticmethod
    def _aggregate(
        signals: List[SubStrategySignal],
        weight_map: Dict[str, float],
    ) -> Tuple[float, float, float]:
        """Return (consensus_signal, gross_notional, net_notional)."""
        total_weight = 0.0
        weighted_signal_sum = 0.0
        gross_notional = 0.0
        net_notional = 0.0

        for signal in signals:
            weight = weight_map[signal.strategy_id]
            total_weight += weight
            weighted_signal_sum += signal.raw_signal * signal.conviction_score * weight
            gross_notional += abs(signal.target_notional_usd)
            net_notional += signal.target_notional_usd

        # total_weight > 0 is guaranteed: every weight is validated > 0 and the
        # signal list is non-empty, so this cannot divide by zero.
        consensus_signal = round(weighted_signal_sum / total_weight, 4)
        return consensus_signal, round(gross_notional, 2), round(net_notional, 2)

    def arbitrate_strategy_signals(
        self,
        symbol: str,
        weights: List[StrategyWeightConfig],
        signals: List[SubStrategySignal],
        current_consensus_signal: float = 0.0,
    ) -> MetaStrategyArbitrationReport:
        """
        Arbitrate sub-strategy signals for one symbol.

        Args:
            symbol: The symbol being arbitrated. Every signal must carry this symbol.
            weights: Allocation weights; one entry per signalling strategy_id.
            signals: One request per sub-strategy for ``symbol``.
            current_consensus_signal: Consensus in force from the previous pass, used
                for the deadband comparison. Defaults to 0.0 (flat).

        Returns:
            A ``MetaStrategyArbitrationReport``. ``net_executable_notional_usd`` is
            0.0 on both the veto and deadband paths; always branch on ``status``,
            never on the notional alone -- 0.0 means "do not route an order", which
            is not the same instruction as "flatten to zero exposure".

        Raises:
            ValueError: On any malformed input. Fail-closed: no order is produced,
                and the caller must not fall through to un-netted sub-strategy orders.
        """
        self._validate_signals(symbol, signals)
        _require_finite("current_consensus_signal", current_consensus_signal)
        weight_map = self._build_weight_map(weights, signals)

        consensus_signal, gross_notional, net_notional = self._aggregate(signals, weight_map)

        # 1. Risk-Off Veto Audit -- absolute precedence over every alpha signal.
        risk_veto_signals = [s for s in signals if s.is_risk_veto]
        if risk_veto_signals:
            veto_strat_ids = [s.strategy_id for s in risk_veto_signals]
            notes = (
                f"ARBITRATION VETO [{symbol}]: Emergency Risk-Off Veto triggered by "
                f"strategy(ies) {veto_strat_ids}. Target exposure forced flat; no "
                "alpha-driven order generated."
            )
            logger.critical(notes)
            return MetaStrategyArbitrationReport(
                symbol=symbol,
                total_strategies_count=len(signals),
                gross_notional_usd=gross_notional,
                net_executable_notional_usd=0.0,
                internal_netting_savings_usd=0.0,
                # 0.0 = flat target, NOT -1.0. A veto means "hold no risk here";
                # -1.0 is a maximum-conviction SHORT, and a downstream sizer reading
                # the consensus would open exactly the position the veto meant to stop.
                consensus_signal=0.0,
                is_risk_veto_active=True,
                status=STATUS_VETO_RISK_OFF,
                audit_notes=notes,
            )

        net_executable_notional = net_notional

        # 2. Internal netting savings on the notional that never reaches a venue.
        #    max(0.0, ...) guards the sub-cent rounding case only; |net| <= gross
        #    holds by the triangle inequality.
        internal_netted_volume = max(0.0, gross_notional - abs(net_executable_notional))
        netting_savings_usd = round(
            internal_netted_volume * (self.estimated_transaction_cost_bps / BPS_DENOMINATOR), 2
        )

        # 3. Deadband Filter Audit.
        signal_delta = abs(consensus_signal - current_consensus_signal)
        if signal_delta < self.deadband_threshold:
            notes = (
                f"DEADBAND SUPPRESSED [{symbol}]: Signal delta ({signal_delta:.4f}) is below "
                f"deadband threshold ({self.deadband_threshold}). Order rebalancing skipped; "
                "existing position left in place."
            )
            logger.info(notes)
            return MetaStrategyArbitrationReport(
                symbol=symbol,
                total_strategies_count=len(signals),
                gross_notional_usd=gross_notional,
                net_executable_notional_usd=0.0,
                # No order is routed, so netting avoided no cost on this pass.
                # Booking the netted volume here would inflate a TCA savings tally
                # with cost that deadband suppression, not netting, avoided.
                internal_netting_savings_usd=0.0,
                consensus_signal=consensus_signal,
                is_risk_veto_active=False,
                status=STATUS_DEADBAND_SUPPRESSED,
                audit_notes=notes,
            )

        # 4. Arbitrated netted order approved.
        notes = (
            f"ARBITRATION SUCCESS [{symbol}]: Consolidated {len(signals)} strategy signals. "
            f"Gross Notional = ${gross_notional:,.2f}, Net Executable Notional = "
            f"${net_executable_notional:,.2f}. Internal netting avoided "
            f"${internal_netted_volume:,.2f} of routed notional, an estimated "
            f"${netting_savings_usd:,.2f} in transaction costs at "
            f"{self.estimated_transaction_cost_bps:g} bps. "
            f"Consensus Signal = {consensus_signal:+.4f}."
        )
        logger.info(notes)

        return MetaStrategyArbitrationReport(
            symbol=symbol,
            total_strategies_count=len(signals),
            gross_notional_usd=gross_notional,
            net_executable_notional_usd=net_executable_notional,
            internal_netting_savings_usd=netting_savings_usd,
            consensus_signal=consensus_signal,
            is_risk_veto_active=False,
            status=STATUS_NETTED_ORDER,
            audit_notes=notes,
        )
