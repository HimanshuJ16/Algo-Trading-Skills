import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class BridgeProfile:
    bridge_id: str
    name: str
    bridge_type: str                   # 'CANONICAL_ROLLUP', 'LIQUIDITY_POOL', 'LOCK_MINT'
    finality_delay_minutes: float      # e.g. 15.0 mins (Stargate) vs 10,080 mins (Arbitrum 7d)
    max_nav_pct_cap: float             # e.g. 0.15 (15% NAV limit)
    current_inflight_usd: float
    audit_score_pct: float             # Security audit score (e.g. 95.0%)

@dataclass
class BridgeTransferDecision:
    selected_bridge_id: str
    is_approved: bool
    is_rerouted: bool
    projected_nav_pct: float
    depeg_pct: float
    reason: str

class CrossChainBridgeRiskManager:
    """
    Crypto risk manager for evaluating cross-chain bridge security, wrapped token de-pegging,
    finality SLAs, and enforcing in-flight capital caps.
    """
    def __init__(
        self,
        portfolio_nav_usd: float = 1_000_000.0,
        max_depeg_threshold_pct: float = 1.0,
        max_allowed_finality_minutes: float = 120.0,
        bridges: List[BridgeProfile] = None
    ):
        self.portfolio_nav_usd = portfolio_nav_usd
        self.max_depeg_threshold_pct = max_depeg_threshold_pct
        self.max_allowed_finality_minutes = max_allowed_finality_minutes
        self.bridges: Dict[str, BridgeProfile] = {b.bridge_id: b for b in (bridges or [])}

    def register_bridge(self, bridge: BridgeProfile):
        self.bridges[bridge.bridge_id] = bridge

    def calculate_depeg_pct(self, native_price: float, wrapped_price: float) -> float:
        """
        Calculates de-peg percentage: |wrapped_price - native_price| / native_price * 100
        """
        if native_price <= 0:
            return 0.0
        return round(float(abs(wrapped_price - native_price) / native_price * 100.0), 2)

    def evaluate_bridge_transfer(
        self,
        target_bridge_id: str,
        transfer_amount_usd: float,
        native_price: float,
        wrapped_price: float
    ) -> BridgeTransferDecision:
        """
        Evaluates proposed cross-chain transfer against de-peg limits, in-flight NAV caps, and finality SLAs.
        """
        if target_bridge_id not in self.bridges:
            raise ValueError(f"Unknown bridge ID {target_bridge_id}")

        depeg_pct = self.calculate_depeg_pct(native_price, wrapped_price)
        target_b = self.bridges[target_bridge_id]

        # 1. Audit Wrapped Token De-Peg (Systemic Halt on De-Peg)
        if depeg_pct >= self.max_depeg_threshold_pct:
            logger.critical(
                f"WRAPPED ASSET DE-PEG DETECTED on {target_bridge_id}: De-peg = {depeg_pct:.2f}% >= Threshold {self.max_depeg_threshold_pct}%! Blocking routing."
            )
            return BridgeTransferDecision(
                selected_bridge_id=target_bridge_id,
                is_approved=False,
                is_rerouted=False,
                projected_nav_pct=0.0,
                depeg_pct=depeg_pct,
                reason=f"Wrapped token de-peg of {depeg_pct:.2f}% exceeds safety threshold of {self.max_depeg_threshold_pct}%."
            )

        # 2. Audit In-Flight Capital NAV Cap and Finality SLA
        proj_inflight = target_b.current_inflight_usd + transfer_amount_usd
        proj_nav_pct = proj_inflight / self.portfolio_nav_usd

        is_cap_breached = proj_nav_pct > target_b.max_nav_pct_cap
        is_finality_breached = target_b.finality_delay_minutes > self.max_allowed_finality_minutes

        if not is_cap_breached and not is_finality_breached:
            return BridgeTransferDecision(
                selected_bridge_id=target_bridge_id,
                is_approved=True,
                is_rerouted=False,
                projected_nav_pct=round(proj_nav_pct * 100, 2),
                depeg_pct=depeg_pct,
                reason="Transfer approved on primary target bridge."
            )

        # Cap or Finality Breached -> Search Failover
        logger.warning(
            f"Bridge {target_bridge_id} limit breached (NAV Cap: {proj_nav_pct*100:.1f}%, Finality: {target_b.finality_delay_minutes}m). Searching failover..."
        )
        failover = self._find_compliant_failover(target_bridge_id, transfer_amount_usd, depeg_pct)
        if failover:
            return failover

        return BridgeTransferDecision(
            selected_bridge_id=target_bridge_id,
            is_approved=False,
            is_rerouted=False,
            projected_nav_pct=round(proj_nav_pct * 100, 2),
            depeg_pct=depeg_pct,
            reason="Primary bridge limits breached and no compliant secondary bridge available."
        )

    def _find_compliant_failover(
        self,
        exclude_bridge_id: str,
        transfer_amount_usd: float,
        depeg_pct: float
    ) -> Optional[BridgeTransferDecision]:
        for bid, b in self.bridges.items():
            if bid == exclude_bridge_id:
                continue
            if b.finality_delay_minutes > self.max_allowed_finality_minutes:
                continue
            proj_inflight = b.current_inflight_usd + transfer_amount_usd
            proj_nav_pct = proj_inflight / self.portfolio_nav_usd
            if proj_nav_pct <= b.max_nav_pct_cap:
                return BridgeTransferDecision(
                    selected_bridge_id=bid,
                    is_approved=True,
                    is_rerouted=True,
                    projected_nav_pct=round(proj_nav_pct * 100, 2),
                    depeg_pct=depeg_pct,
                    reason=f"Primary bridge {exclude_bridge_id} breached. Re-routed to secondary bridge {bid}."
                )
        return None
