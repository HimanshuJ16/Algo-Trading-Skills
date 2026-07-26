import logging
from dataclasses import dataclass
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

@dataclass
class FirmAggregateRiskReport:
    total_firm_nav_usd: float
    total_gmv_usd: float
    net_positions: Dict[str, float]
    aggregate_margin_utilization_pct: float
    internal_offsetting_friction_symbols: List[str]
    is_compliant: bool
    violations: List[str]

class CrossAccountRiskAggregator:
    """
    Consolidates holdings, cash, and margin across sub-accounts/prime brokers into a firm-wide
    aggregate risk view, enforcing GMV caps and flagging internal offsetting trades.
    """
    def __init__(self, max_firm_gmv_limit_usd: float = 2_000_000.0, max_margin_utilization_pct: float = 80.0):
        self.max_firm_gmv_limit_usd = max_firm_gmv_limit_usd
        self.max_margin_utilization_pct = max_margin_utilization_pct
        self.accounts: Dict[str, SubAccountState] = {}

    def register_account(self, account: SubAccountState):
        self.accounts[account.account_id] = account

    def aggregate_firm_risk(self, market_prices: Dict[str, float]) -> FirmAggregateRiskReport:
        """
        Computes consolidated firm-wide risk metrics across all sub-accounts.
        """
        net_positions: Dict[str, float] = {}
        total_cash = 0.0
        total_margin_used = 0.0
        total_margin_limit = 0.0

        long_accounts: Dict[str, Set[str]] = {}
        short_accounts: Dict[str, Set[str]] = {}

        for acc in self.accounts.values():
            total_cash += acc.cash_usd
            total_margin_used += acc.margin_used_usd
            total_margin_limit += acc.margin_limit_usd

            for symbol, qty in acc.positions.items():
                if qty == 0:
                    continue
                net_positions[symbol] = net_positions.get(symbol, 0.0) + qty

                if qty > 0:
                    if symbol not in long_accounts:
                        long_accounts[symbol] = set()
                    long_accounts[symbol].add(acc.account_id)
                elif qty < 0:
                    if symbol not in short_accounts:
                        short_accounts[symbol] = set()
                    short_accounts[symbol].add(acc.account_id)

        # Compute GMV and NAV
        total_gmv = 0.0
        position_value_sum = 0.0
        for symbol, qty in net_positions.items():
            price = market_prices.get(symbol, 0.0)
            val = qty * price
            position_value_sum += val
            total_gmv += abs(val)

        total_firm_nav = total_cash + position_value_sum
        margin_utilization_pct = (total_margin_used / total_margin_limit * 100.0) if total_margin_limit > 0 else 0.0

        # Audit Internal Offsetting Friction (Concurrent Long in Acc 1 & Short in Acc 2)
        internal_offsetting = [
            sym for sym in long_accounts 
            if sym in short_accounts
        ]

        violations = []
        if total_gmv > self.max_firm_gmv_limit_usd:
            violations.append(f"Firm GMV limit breached: ${total_gmv:,.2f} > Limit ${self.max_firm_gmv_limit_usd:,.2f}")
        if margin_utilization_pct > self.max_margin_utilization_pct:
            violations.append(f"Firm margin utilization limit breached: {margin_utilization_pct:.1f}% > {self.max_margin_utilization_pct}%")

        is_compliant = len(violations) == 0

        return FirmAggregateRiskReport(
            total_firm_nav_usd=round(total_firm_nav, 2),
            total_gmv_usd=round(total_gmv, 2),
            net_positions=net_positions,
            aggregate_margin_utilization_pct=round(margin_utilization_pct, 2),
            internal_offsetting_friction_symbols=internal_offsetting,
            is_compliant=is_compliant,
            violations=violations
        )

    def evaluate_pre_trade_order(
        self,
        account_id: str,
        symbol: str,
        proposed_qty: float,
        price: float,
        market_prices: Dict[str, float]
    ) -> Tuple[bool, str]:
        """
        Evaluates proposed order in account_id against firm-wide aggregate risk caps.
        """
        if account_id not in self.accounts:
            return False, f"Unknown sub-account {account_id}"

        # Create temporary state copy for pre-trade audit
        temp_aggregator = CrossAccountRiskAggregator(self.max_firm_gmv_limit_usd, self.max_margin_utilization_pct)
        for acc_id, acc in self.accounts.items():
            new_positions = dict(acc.positions)
            if acc_id == account_id:
                new_positions[symbol] = new_positions.get(symbol, 0.0) + proposed_qty

            temp_aggregator.register_account(SubAccountState(
                account_id=acc.account_id,
                broker_name=acc.broker_name,
                cash_usd=acc.cash_usd,
                margin_used_usd=acc.margin_used_usd,
                margin_limit_usd=acc.margin_limit_usd,
                positions=new_positions
            ))

        updated_prices = dict(market_prices)
        updated_prices[symbol] = price

        report = temp_aggregator.aggregate_firm_risk(updated_prices)
        if not report.is_compliant:
            return False, f"Pre-trade order rejected: {'; '.join(report.violations)}"

        return True, "Pre-trade order approved across firm-wide aggregate limits."
