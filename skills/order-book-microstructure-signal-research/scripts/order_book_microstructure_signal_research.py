import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OrderBookTick:
    timestamp_ns: int
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float

@dataclass
class MicrostructureFeatures:
    timestamp_ns: int
    symbol: str
    mid_price: float
    micro_price: float
    micro_price_dev: float              # MicroPrice - MidPrice
    voi: float                          # Depth Volume Imbalance [-1.0, +1.0]
    ofi: float                          # Order Flow Imbalance (delta V_bid - delta V_ask)

@dataclass
class MicrostructureSignalReport:
    symbol: str
    total_ticks_analyzed: int
    ic_ofi_forward_return: float        # Pearson IC between OFI and forward return
    ic_micro_price_dev_return: float    # Pearson IC between MicroPrice dev and forward return
    hit_ratio_pct: float                # % of correct direction predictions
    status: str                         # 'PREDICTIVE_ALPHA_FOUND', 'WEAK_SIGNAL'
    audit_notes: str

class OrderBookMicrostructureSignalResearchEngine:
    """
    Order book microstructure signal research engine calculating Order Flow Imbalance (OFI),
    Volume-Weighted Micro-Price, and Depth Imbalance, and evaluating predictive Information Coefficients (IC).
    """
    def __init__(self, forward_horizon_ticks: int = 5):
        self.forward_horizon_ticks = forward_horizon_ticks

    def extract_features(
        self, ticks: List[OrderBookTick]
    ) -> List[MicrostructureFeatures]:
        """
        Extracts OFI, VOI, and Micro-Price deviation features from order book tick series.
        """
        features: List[MicrostructureFeatures] = []
        if not ticks:
            return features

        prev_bid_p = ticks[0].bid_price
        prev_bid_q = ticks[0].bid_qty
        prev_ask_p = ticks[0].ask_price
        prev_ask_q = ticks[0].ask_qty

        for tick in ticks:
            # 1. Compute Mid Price & Micro-Price
            total_depth = tick.bid_qty + tick.ask_qty
            if total_depth <= 0:
                mid = (tick.bid_price + tick.ask_price) / 2.0
                micro = mid
                voi = 0.0
            else:
                mid = (tick.bid_price + tick.ask_price) / 2.0
                micro = (tick.bid_qty * tick.ask_price + tick.ask_qty * tick.bid_price) / total_depth
                voi = (tick.bid_qty - tick.ask_qty) / total_depth

            micro_dev = micro - mid

            # 2. Compute Order Flow Imbalance (OFI)
            # Bid delta: if bid_p > prev_bid_p => bid_q; if bid_p == prev_bid_p => bid_q - prev_bid_q; else 0
            if tick.bid_price > prev_bid_p:
                delta_b = tick.bid_qty
            elif tick.bid_price == prev_bid_p:
                delta_b = tick.bid_qty - prev_bid_q
            else:
                delta_b = 0.0

            # Ask delta: if ask_p < prev_ask_p => ask_q; if ask_p == prev_ask_p => ask_q - prev_ask_q; else 0
            if tick.ask_price < prev_ask_p:
                delta_a = tick.ask_qty
            elif tick.ask_price == prev_ask_p:
                delta_a = tick.ask_qty - prev_ask_q
            else:
                delta_a = 0.0

            ofi = delta_b - delta_a

            features.append(MicrostructureFeatures(
                timestamp_ns=tick.timestamp_ns,
                symbol=tick.symbol,
                mid_price=round(mid, 4),
                micro_price=round(micro, 4),
                micro_price_dev=round(micro_dev, 4),
                voi=round(voi, 4),
                ofi=round(ofi, 2)
            ))

            prev_bid_p = tick.bid_price
            prev_bid_q = tick.bid_qty
            prev_ask_p = tick.ask_price
            prev_ask_q = tick.ask_qty

        return features

    def evaluate_signal_efficacy(
        self, ticks: List[OrderBookTick]
    ) -> MicrostructureSignalReport:
        """
        Calculates Pearson Information Coefficient (IC) and Hit Ratio between microstructure signals and forward returns.
        """
        if len(ticks) <= self.forward_horizon_ticks + 5:
            raise ValueError(f"Need at least {self.forward_horizon_ticks + 5} ticks for efficacy research.")

        features = self.extract_features(ticks)
        symbol = ticks[0].symbol
        n = len(ticks) - self.forward_horizon_ticks

        ofi_vals: List[float] = []
        dev_vals: List[float] = []
        fwd_returns: List[float] = []
        hits = 0

        for i in range(n):
            f = features[i]
            cur_mid = f.mid_price
            fwd_mid = (ticks[i + self.forward_horizon_ticks].bid_price + ticks[i + self.forward_horizon_ticks].ask_price) / 2.0

            ret = (fwd_mid - cur_mid) / cur_mid if cur_mid > 0 else 0.0

            ofi_vals.append(f.ofi)
            dev_vals.append(f.micro_price_dev)
            fwd_returns.append(ret)

            # Hit check: sign(OFI) == sign(ret)
            if (f.ofi > 0 and ret > 0) or (f.ofi < 0 and ret < 0) or (f.ofi == 0 and ret == 0):
                hits += 1

        ic_ofi = self._pearson_correlation(ofi_vals, fwd_returns)
        ic_dev = self._pearson_correlation(dev_vals, fwd_returns)
        hit_ratio_pct = (hits / float(n)) * 100.0

        status = "PREDICTIVE_ALPHA_FOUND" if (ic_ofi >= 0.05 and hit_ratio_pct >= 52.0) else "WEAK_SIGNAL"

        notes = (
            f"MICROSTRUCTURE SIGNAL AUDIT [{symbol} - {status}]: Ticks = {len(ticks)}, "
            f"Forward Horizon = {self.forward_horizon_ticks} ticks. IC(OFI, Ret) = {ic_ofi:+.4f}, "
            f"IC(MicroDev, Ret) = {ic_dev:+.4f}, Hit Ratio = {hit_ratio_pct:.2f}%."
        )

        if status == "PREDICTIVE_ALPHA_FOUND":
            logger.info(notes)
        else:
            logger.warning(notes)

        return MicrostructureSignalReport(
            symbol=symbol,
            total_ticks_analyzed=len(ticks),
            ic_ofi_forward_return=round(ic_ofi, 4),
            ic_micro_price_dev_return=round(ic_dev, 4),
            hit_ratio_pct=round(hit_ratio_pct, 2),
            status=status,
            audit_notes=notes
        )

    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> float:
        n = len(x)
        if n < 2:
            return 0.0
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        var_x = sum((x[i] - mean_x) ** 2 for i in range(n))
        var_y = sum((y[i] - mean_y) ** 2 for i in range(n))

        denom = math.sqrt(var_x * var_y)
        if denom == 0:
            return 0.0
        return cov / denom
