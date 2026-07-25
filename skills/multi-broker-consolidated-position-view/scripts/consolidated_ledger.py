"""
multi-broker-consolidated-position-view: Multi-broker position aggregator, symbol/FX normalizer,
and position reconciliation audit engine.
"""
from dataclasses import dataclass, field
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RawBrokerPosition:
    broker_name: str
    broker_symbol: str
    quantity: float
    average_cost: float
    current_price: float
    currency: str = "USD"


@dataclass
class ConsolidatedPosition:
    canonical_symbol: str
    net_quantity: float
    gross_quantity: float
    total_market_value_usd: float
    weighted_avg_cost_usd: float
    unrealized_pnl_usd: float
    broker_breakdown: Dict[str, float]  # broker_name -> qty


@dataclass
class ReconciliationDiscrepancy:
    canonical_symbol: str
    expected_qty: float
    actual_broker_qty: float
    discrepancy_qty: float
    message: str


class MultiBrokerConsolidatedLedger:
    """
    Aggregates multi-broker raw position snapshots into a consolidated base-currency (USD) view
    and performs position reconciliation audits.
    """

    def __init__(
        self,
        base_currency: str = "USD",
        symbol_map: Optional[Dict[str, str]] = None,
        fx_rates: Optional[Dict[str, float]] = None,
    ):
        self.base_currency = base_currency
        self.symbol_map = symbol_map or {}  # broker_symbol -> canonical_symbol
        self.fx_rates = fx_rates or {"USD": 1.0, "EUR": 1.08, "GBP": 1.28, "CAD": 0.73, "INR": 0.012}

    def register_symbol_mapping(self, broker_symbol: str, canonical_symbol: str) -> None:
        """Registers symbol mapping for normalization."""
        self.symbol_map[broker_symbol] = canonical_symbol

    def _to_canonical(self, broker_symbol: str) -> str:
        return self.symbol_map.get(broker_symbol, broker_symbol.upper())

    def _convert_to_usd(self, amount: float, currency: str) -> float:
        rate = self.fx_rates.get(currency.upper(), 1.0)
        return amount * rate

    def consolidate_positions(self, raw_positions: List[RawBrokerPosition]) -> Dict[str, ConsolidatedPosition]:
        """
        Consolidates raw multi-broker positions into canonical net and gross positions in USD.
        """
        grouped: Dict[str, List[RawBrokerPosition]] = {}
        for pos in raw_positions:
            canon = self._to_canonical(pos.broker_symbol)
            if canon not in grouped:
                grouped[canon] = []
            grouped[canon].append(pos)

        consolidated: Dict[str, ConsolidatedPosition] = {}

        for canon, pos_list in grouped.items():
            net_qty = sum(p.quantity for p in pos_list)
            gross_qty = sum(abs(p.quantity) for p in pos_list)

            breakdown: Dict[str, float] = {}
            total_mkt_val_usd = 0.0
            total_cost_usd = 0.0

            for p in pos_list:
                breakdown[p.broker_name] = breakdown.get(p.broker_name, 0.0) + p.quantity
                p_val_usd = self._convert_to_usd(p.quantity * p.current_price, p.currency)
                p_cost_usd = self._convert_to_usd(p.quantity * p.average_cost, p.currency)

                total_mkt_val_usd += p_val_usd
                total_cost_usd += p_cost_usd

            weighted_cost = (total_cost_usd / net_qty) if net_qty != 0 else 0.0
            unrealized_pnl = total_mkt_val_usd - total_cost_usd

            consolidated[canon] = ConsolidatedPosition(
                canonical_symbol=canon,
                net_quantity=net_qty,
                gross_quantity=gross_qty,
                total_market_value_usd=total_mkt_val_usd,
                weighted_avg_cost_usd=weighted_cost,
                unrealized_pnl_usd=unrealized_pnl,
                broker_breakdown=breakdown,
            )

        return consolidated

    def reconcile_against_target(
        self,
        raw_positions: List[RawBrokerPosition],
        expected_target_ledger: Dict[str, float],
    ) -> List[ReconciliationDiscrepancy]:
        """
        Audits consolidated broker positions against expected strategy target quantities.
        """
        consolidated = self.consolidate_positions(raw_positions)
        all_symbols = set(consolidated.keys()).union(set(expected_target_ledger.keys()))
        discrepancies: List[ReconciliationDiscrepancy] = []

        for symbol in all_symbols:
            actual_qty = consolidated[symbol].net_quantity if symbol in consolidated else 0.0
            expected_qty = expected_target_ledger.get(symbol, 0.0)

            diff = actual_qty - expected_qty
            if abs(diff) > 1e-5:
                msg = (
                    f"POSITION DISCREPANCY on {symbol}: Expected {expected_qty}, "
                    f"Actual broker total {actual_qty} (Diff: {diff:+.2f})"
                )
                logger.warning(msg)
                discrepancies.append(ReconciliationDiscrepancy(
                    canonical_symbol=symbol,
                    expected_qty=expected_qty,
                    actual_broker_qty=actual_qty,
                    discrepancy_qty=diff,
                    message=msg,
                ))

        return discrepancies
