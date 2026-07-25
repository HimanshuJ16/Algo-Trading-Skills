"""
backtest-determinism-and-reproducibility: Global random seed injector, deterministic event stream sorter,
and cryptographic trade log audit checksum generator.
"""
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Try importing numpy gracefully
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


@dataclass
class TradeExecutionRecord:
    timestamp: float
    symbol: str
    action: str
    quantity: float
    price: float


@dataclass
class DeterministicAuditReport:
    seed: int
    total_trades: int
    final_equity: float
    trade_log_checksum: str
    is_bit_identical: bool
    message: str


class BacktestDeterminismEngine:
    """
    Enforces deterministic seeding, stream sorting, and audit checksum generation
    to guarantee bit-identical backtest reproducibility.
    """

    def __init__(self, master_seed: int = 42):
        self.master_seed = master_seed
        self.apply_master_seeds(master_seed)

    @staticmethod
    def apply_master_seeds(seed: int = 42) -> None:
        """Injects deterministic seeds across global RNG modules."""
        random.seed(seed)
        os.environ["PYTHONHASHSEED"] = str(seed)
        if HAS_NUMPY:
            np.random.seed(seed)
        logger.info(f"Determinism Engine: Master random seed set to {seed}.")

    def sort_event_stream(self, raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sorts event stream strictly by (timestamp, symbol, sequence_id) for deterministic tie-breaking.
        """
        return sorted(
            raw_events,
            key=lambda e: (
                e.get("timestamp", 0.0),
                str(e.get("symbol", "")).upper(),
                e.get("sequence_id", 0),
            ),
        )

    def generate_trade_checksum(self, trades: List[TradeExecutionRecord]) -> str:
        """
        Generates SHA256 checksum of trade execution log.
        """
        canonical_list = [
            {
                "ts": round(t.timestamp, 6),
                "sym": t.symbol.upper(),
                "act": t.action.upper(),
                "qty": round(t.quantity, 6),
                "px": round(t.price, 6),
            }
            for t in trades
        ]
        dumped = json.dumps(canonical_list, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def audit_reproducibility(
        self,
        trades_run1: List[TradeExecutionRecord],
        trades_run2: List[TradeExecutionRecord],
    ) -> DeterministicAuditReport:
        """
        Compares trade logs across two backtest runs to assert bit-identical reproducibility.
        """
        checksum1 = self.generate_trade_checksum(trades_run1)
        checksum2 = self.generate_trade_checksum(trades_run2)

        is_identical = (checksum1 == checksum2)

        if is_identical:
            msg = f"Reproducibility Verified: Both backtest runs generated bit-identical SHA256 checksum ({checksum1[:12]}...)."
            logger.info(msg)
        else:
            msg = f"REPRODUCIBILITY BREACH: Run 1 ({checksum1[:12]}...) != Run 2 ({checksum2[:12]}...)."
            logger.error(msg)

        final_eq = sum(t.quantity * t.price for t in trades_run1) if trades_run1 else 0.0

        return DeterministicAuditReport(
            seed=self.master_seed,
            total_trades=len(trades_run1),
            final_equity=round(final_eq, 2),
            trade_log_checksum=checksum1,
            is_bit_identical=is_identical,
            message=msg,
        )
