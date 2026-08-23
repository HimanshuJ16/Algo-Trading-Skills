"""
cross-account-aggregate-risk-view: firm-wide consolidation of positions,
cash, and margin across sub-accounts/prime brokers, enforcement of aggregate
GMV caps and margin-utilization limits, and detection of internal long/short
offsetting friction between sub-accounts.

Conventions:
- GMV is GROSS ACROSS SYMBOLS but NET WITHIN A SYMBOL: GMV = sum_s |Q_net(s) * P(s)|.
  Offsetting long/short legs of the same symbol across accounts net to one economic
  exposure and are flagged separately as internal offsetting friction.
- FAIL-CLOSED PRICING: a held symbol with a missing, non-finite, or non-positive
  price cannot be valued. It is listed in ``unvalued_symbols``, forces a violation,
  and blocks approval -- GMV is never silently computed against a $0.00 placeholder.
- MARGIN IS NOT MODELLED: this engine consolidates the margin figures the broker
  reports; it does not compute Reg T / portfolio-margin / SPAN requirements. A
  pre-trade check projects the order's margin impact only if the caller passes
  ``additional_margin_usd``; otherwise the margin cap gates existing balances only.

Thread safety: account registration and aggregation snapshots are serialized
internally, so an aggregation never observes a half-updated registry. The
caller must still serialize check-then-place sequences if orders can arrive
concurrently from multiple threads, and must update balances by re-registering
an account rather than mutating a registered ``SubAccountState`` in place.
"""
import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class SubAccountState:
    account_id: str
    broker_name: str
    cash_usd: float
    margin_used_usd: float
    margin_limit_usd: float
    positions: Dict[str, float]        # Symbol -> Quantity (Positive for Long, Negative for Short)

    def __post_init__(self):
        """Validate at construction so malformed account records fail fast
        instead of crashing (or worse, partially aggregating) later."""
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError(f"account_id must be a non-empty string, got {self.account_id!r}")
        if not isinstance(self.broker_name, str):
            raise ValueError(f"broker_name must be a string, got {self.broker_name!r}")
        for attr in ("cash_usd", "margin_used_usd", "margin_limit_usd"):
            value = getattr(self, attr)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{attr} must be a finite number, got {value!r}")
        if self.margin_used_usd < 0:
            raise ValueError(f"margin_used_usd must be >= 0, got {self.margin_used_usd!r}")
        if self.margin_limit_usd < 0:
            raise ValueError(f"margin_limit_usd must be >= 0, got {self.margin_limit_usd!r}")
        if not isinstance(self.positions, dict):
            raise ValueError(f"positions must be a dict of {{symbol: qty}}, got {type(self.positions)!r}")
        for symbol, qty in self.positions.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError(f"position symbols must be non-empty strings, got {symbol!r}")
            if not isinstance(qty, (int, float)) or not math.isfinite(float(qty)):
                raise ValueError(f"position quantity for {symbol!r} must be finite, got {qty!r}")

@dataclass
class FirmAggregateRiskReport:
    total_firm_nav_usd: float
    total_gmv_usd: float
    net_positions: Dict[str, float]
    aggregate_margin_utilization_pct: float
    internal_offsetting_friction_symbols: List[str]
    is_compliant: bool
    violations: List[str]
    # Held symbols excluded from GMV/NAV because their market price was missing,
    # non-finite, or non-positive. Non-empty => totals are UNDERSTATED and the
    # report is non-compliant by construction.
    unvalued_symbols: List[str] = field(default_factory=list)

class CrossAccountRiskAggregator:
    """
    Consolidates holdings, cash, and margin across sub-accounts/prime brokers into a firm-wide
    aggregate risk view, enforcing GMV caps and flagging internal offsetting trades.

    Re-registering an ``account_id`` REPLACES the stored record (including its
    positions) -- that is the intended balance-update mechanism. Static offsetting
    positions flagged here are a capital-efficiency issue; they are not themselves
    regulatory wash trades (which attach to executions without a change of beneficial
    ownership) -- see the wash-trade-and-spoofing-self-detection skill for the
    execution-level audit.
    """
    def __init__(self, max_firm_gmv_limit_usd: float = 2_000_000.0, max_margin_utilization_pct: float = 80.0):
        if not isinstance(max_firm_gmv_limit_usd, (int, float)) or not math.isfinite(float(max_firm_gmv_limit_usd)) \
                or max_firm_gmv_limit_usd <= 0:
            raise ValueError(
                f"max_firm_gmv_limit_usd must be a positive finite number, got {max_firm_gmv_limit_usd!r}"
            )
        if not isinstance(max_margin_utilization_pct, (int, float)) or not math.isfinite(float(max_margin_utilization_pct)) \
                or not (0 < max_margin_utilization_pct <= 100):
            raise ValueError(
                f"max_margin_utilization_pct must be in (0, 100], got {max_margin_utilization_pct!r}"
            )
        self.max_firm_gmv_limit_usd = max_firm_gmv_limit_usd
        self.max_margin_utilization_pct = max_margin_utilization_pct
        self.accounts: Dict[str, SubAccountState] = {}
        # Guards the account registry: an aggregation/pre-trade check snapshots
        # under this lock and never sees a half-updated registry.
        self._lock = threading.RLock()

    def register_account(self, account: SubAccountState):
        with self._lock:
            self.accounts[account.account_id] = account

    def _snapshot_accounts(self) -> List[SubAccountState]:
        """Copy registry + position dicts under the lock so aggregation works on
        a consistent point-in-time view even while feeds update other threads."""
        with self._lock:
            return [
                SubAccountState(
                    account_id=acc.account_id,
                    broker_name=acc.broker_name,
                    cash_usd=acc.cash_usd,
                    margin_used_usd=acc.margin_used_usd,
                    margin_limit_usd=acc.margin_limit_usd,
                    positions=dict(acc.positions),
                )
                for acc in self.accounts.values()
            ]

    def aggregate_firm_risk(self, market_prices: Dict[str, float]) -> FirmAggregateRiskReport:
        """
        Computes consolidated firm-wide risk metrics across all sub-accounts.

        Fail-closed pricing: any held symbol whose price is absent, non-finite,
        or non-positive is reported in ``unvalued_symbols`` and produces a
        compliance violation (GMV/NAV are understated without it).
        """
        if not isinstance(market_prices, dict):
            raise ValueError(f"market_prices must be a dict of {{symbol: price}}, got {type(market_prices)!r}")

        accounts = self._snapshot_accounts()

        net_positions: Dict[str, float] = {}
        total_cash = 0.0
        total_margin_used = 0.0
        total_margin_limit = 0.0

        long_accounts: Dict[str, Set[str]] = {}
        short_accounts: Dict[str, Set[str]] = {}

        for acc in accounts:
            total_cash += acc.cash_usd
            total_margin_used += acc.margin_used_usd
            total_margin_limit += acc.margin_limit_usd

            for symbol, qty in acc.positions.items():
                if qty == 0:
                    continue
                net_positions[symbol] = net_positions.get(symbol, 0.0) + qty

                if qty > 0:
                    long_accounts.setdefault(symbol, set()).add(acc.account_id)
                elif qty < 0:
                    short_accounts.setdefault(symbol, set()).add(acc.account_id)

        # Compute GMV and NAV, refusing to value held symbols on bad/absent prices.
        total_gmv = 0.0
        position_value_sum = 0.0
        unvalued_symbols: List[str] = []
        for symbol, qty in net_positions.items():
            price = market_prices.get(symbol)
            if price is None or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or price <= 0:
                unvalued_symbols.append(symbol)
                continue
            val = qty * float(price)
            position_value_sum += val
            total_gmv += abs(val)

        total_firm_nav = total_cash + position_value_sum

        if total_margin_limit > 0:
            margin_utilization_pct = total_margin_used / total_margin_limit * 100.0
        elif total_margin_used > 0:
            # Capacity never synced / configured: utilization is unassessable.
            # Report 100% (used exceeds any admissible capacity) and violate --
            # never report a reassuring 0.0%.
            margin_utilization_pct = 100.0
        else:
            margin_utilization_pct = 0.0

        # Audit Internal Offsetting Friction (Concurrent Long in Acc 1 & Short in Acc 2)
        internal_offsetting = sorted(
            sym for sym in long_accounts
            if sym in short_accounts
        )

        violations = []
        if unvalued_symbols:
            violations.append(
                "Unvalued positions - missing or invalid market price for: "
                + ", ".join(sorted(unvalued_symbols))
                + "; GMV/NAV understated"
            )
        if total_gmv > self.max_firm_gmv_limit_usd:
            violations.append(f"Firm GMV limit breached: ${total_gmv:,.2f} > Limit ${self.max_firm_gmv_limit_usd:,.2f}")
        if margin_utilization_pct > self.max_margin_utilization_pct:
            violations.append(f"Firm margin utilization limit breached: {margin_utilization_pct:.1f}% > {self.max_margin_utilization_pct}%")
        if total_margin_used > 0 and total_margin_limit == 0:
            violations.append(
                f"Firm margin capacity undefined: ${total_margin_used:,.2f} margin used against $0 aggregate margin limit"
            )

        if violations:
            logger.warning("Firm-wide aggregate risk violations: %s", "; ".join(violations))

        is_compliant = len(violations) == 0

        return FirmAggregateRiskReport(
            total_firm_nav_usd=round(total_firm_nav, 2),
            total_gmv_usd=round(total_gmv, 2),
            net_positions=net_positions,
            aggregate_margin_utilization_pct=round(margin_utilization_pct, 2),
            internal_offsetting_friction_symbols=internal_offsetting,
            is_compliant=is_compliant,
            violations=violations,
            unvalued_symbols=unvalued_symbols,
        )

    def evaluate_pre_trade_order(
        self,
        account_id: str,
        symbol: str,
        proposed_qty: float,
        price: float,
        market_prices: Dict[str, float],
        additional_margin_usd: float = 0.0
    ) -> Tuple[bool, str]:
        """
        Evaluates proposed order in account_id against firm-wide aggregate risk caps.

        ``additional_margin_usd`` is the incremental margin the order is expected to
        consume in that sub-account (negative to model margin RELEASED by a
        risk-reducing order; the projected account figure is floored at 0). It
        defaults to 0.0, which means **margin utilization is NOT projected** for the
        order -- the margin cap can then only fire on a breach that already exists in
        the registered balances. Supply this argument whenever the firm-wide margin
        utilization cap is meant to gate new orders; the caller owns the broker- and
        product-specific margin calculation (Reg T, portfolio margin, SPAN, exchange
        initial margin), which this engine does not model.

        Raises ValueError for a non-finite quantity, a non-finite/non-positive price,
        a blank symbol, a non-dict ``market_prices``, or a non-finite
        ``additional_margin_usd`` (caller bugs); returns (False, reason) for policy
        rejections such as unknown accounts or post-trade limit breaches.
        Risk-reducing orders are evaluated on their exact projected net position and
        can be approved even while the firm is currently over a cap.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        if not isinstance(proposed_qty, (int, float)) or not math.isfinite(float(proposed_qty)):
            raise ValueError(f"proposed_qty must be a finite number, got {proposed_qty!r}")
        if not isinstance(price, (int, float)) or not math.isfinite(float(price)) or price <= 0:
            raise ValueError(f"price must be a positive finite number, got {price!r}")
        if not isinstance(market_prices, dict):
            raise ValueError(f"market_prices must be a dict of {{symbol: price}}, got {type(market_prices)!r}")
        if not isinstance(additional_margin_usd, (int, float)) or not math.isfinite(float(additional_margin_usd)):
            raise ValueError(
                f"additional_margin_usd must be a finite number, got {additional_margin_usd!r}"
            )

        with self._lock:
            if account_id not in self.accounts:
                return False, f"Unknown sub-account {account_id}"

        # Create temporary state copy for pre-trade audit
        temp_aggregator = CrossAccountRiskAggregator(self.max_firm_gmv_limit_usd, self.max_margin_utilization_pct)
        for acc in self._snapshot_accounts():
            new_positions = dict(acc.positions)
            new_margin_used = acc.margin_used_usd
            if acc.account_id == account_id:
                new_positions[symbol] = new_positions.get(symbol, 0.0) + proposed_qty
                # Margin used can never go below zero, however much the order releases.
                new_margin_used = max(0.0, acc.margin_used_usd + float(additional_margin_usd))

            temp_aggregator.register_account(SubAccountState(
                account_id=acc.account_id,
                broker_name=acc.broker_name,
                cash_usd=acc.cash_usd,
                margin_used_usd=new_margin_used,
                margin_limit_usd=acc.margin_limit_usd,
                positions=new_positions
            ))

        updated_prices = dict(market_prices)
        updated_prices[symbol] = price

        report = temp_aggregator.aggregate_firm_risk(updated_prices)
        if not report.is_compliant:
            return False, f"Pre-trade order rejected: {'; '.join(report.violations)}"

        return True, "Pre-trade order approved across firm-wide aggregate limits."
