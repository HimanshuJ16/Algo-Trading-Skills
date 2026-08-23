"""Pre-transfer risk gate for cross-chain bridge capital movements.

Monitors wrapped-token de-pegging against the native asset, enforces
per-bridge in-flight NAV caps and finality-latency SLAs, and reroutes
to the lowest-risk compliant secondary bridge when the primary fails a
check. Failover candidates are ranked by audit score (descending), then
finality delay (ascending).

Empirical grounding: Chainalysis counted $2B stolen across 13
cross-chain bridge hacks as of Aug 2022 — 69% of all funds stolen in
2022 to that point — including Ronin (over $600M, five of nine
validator keys compromised), Wormhole (~120,000 wETH, over $320M,
minted without collateral) and Nomad ($190M).
Canonical rollup withdrawals to L1 take days, and the two chains do not
share one constant: Optimism documents a "minimum 7-day wait" on the
Standard Bridge (10,080 min), Arbitrum a 6.4-day assertion challenge
period plus a seven-day canonical-withdrawal safeguard. L2-native
finality is separate (~2 s soft, ~15-30 min hard on OP Stack), and
third-party/fast bridges bypass the window at the cost of trusting an
intermediary. Set the SLA per transfer path, not per protocol name.

Advisory and stateless: `evaluate_bridge_transfer` never mutates bridge
state. `current_inflight_usd` is an *input*, so the caller must record an
executed transfer (re-register the bridge with the new in-flight figure)
before evaluating the next one. Evaluating repeatedly against a stale
in-flight balance approves each transfer independently and walks straight
through the NAV cap in aggregate.

Defaults, not standards: the 15% NAV cap, 1.0% de-peg halt, 120-minute
finality SLA, and audit gates are engineering defaults — calibrate to
the fund's mandate before automation. Latency figures (e.g. "15 min
Stargate") are illustrative: actual bridge latency depends on the
chain pair and configuration.

Empirical figures below are dated: bridges were the dominant crypto theft
category in 2022, but by 2025 centralized-service and personal-wallet
compromises dominated stolen value (Chainalysis). Bridge exploits remain
catastrophic when they land; the caps exist for tail loss, not because
bridges are today's leading vector.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _require_finite(name: str, value: float) -> None:
    """Rejects non-numeric and non-finite values.

    bool is excluded deliberately: it is an int subclass, so `True` would
    otherwise sail through as a 1-USD transfer amount. Non-numeric input
    (a JSON string price, None from a dead feed) raises ValueError rather
    than TypeError so callers have one exception type to handle.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


def _require_positive(name: str, value: float) -> None:
    _require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def _require_non_negative(name: str, value: float) -> None:
    _require_finite(name, value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")


@dataclass
class BridgeProfile:
    bridge_id: str
    name: str
    bridge_type: str                   # 'CANONICAL_ROLLUP', 'LIQUIDITY_POOL', 'LOCK_MINT'
    finality_delay_minutes: float      # e.g. 15.0 mins (illustrative) vs 10,080 (7-day rollup L1 withdrawal)
    max_nav_pct_cap: float             # e.g. 0.15 (15% NAV limit)
    current_inflight_usd: float
    audit_score_pct: float             # Security audit score in [0, 100] (e.g. 95.0%)

    def __post_init__(self) -> None:
        for field_name in ("bridge_id", "name", "bridge_type"):
            field_value = getattr(self, field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(
                    f"{field_name} must be a non-empty string, got {field_value!r}"
                )
        _require_non_negative(f"finality_delay_minutes for {self.bridge_id!r}",
                              self.finality_delay_minutes)
        _require_finite(f"max_nav_pct_cap for {self.bridge_id!r}", self.max_nav_pct_cap)
        if not 0.0 < self.max_nav_pct_cap <= 1.0:
            raise ValueError(
                f"max_nav_pct_cap must be within (0, 1] for {self.bridge_id!r}, "
                f"got {self.max_nav_pct_cap}"
            )
        _require_non_negative(f"current_inflight_usd for {self.bridge_id!r}",
                              self.current_inflight_usd)
        _require_finite(f"audit_score_pct for {self.bridge_id!r}", self.audit_score_pct)
        if not 0.0 <= self.audit_score_pct <= 100.0:
            raise ValueError(
                f"audit_score_pct must be within [0, 100] for {self.bridge_id!r}, "
                f"got {self.audit_score_pct}"
            )


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
    Crypto risk manager for evaluating cross-chain bridge security, wrapped
    token de-pegging, finality SLAs, and enforcing in-flight capital caps
    with lowest-risk (audit-ranked) failover routing.

    Decisions are advisory and the manager is stateless with respect to
    executed transfers: an approval does NOT increment
    `current_inflight_usd`. Re-register the bridge with the updated
    in-flight balance after execution (`register_bridge` replaces the
    profile) — otherwise every subsequent evaluation is measured against
    the same stale balance and the aggregate NAV cap is never enforced.

    Unit convention: `max_depeg_threshold_pct` and `audit_score_pct` are
    percentage points (1.0 = 1%, 95.0 = 95%), while `max_nav_pct_cap` is
    a fraction in (0, 1] (0.15 = 15%).
    """
    def __init__(
        self,
        portfolio_nav_usd: float = 1_000_000.0,
        max_depeg_threshold_pct: float = 1.0,
        max_allowed_finality_minutes: float = 120.0,
        bridges: Optional[List[BridgeProfile]] = None,
        min_audit_score_pct: float = 0.0
    ):
        _require_positive("portfolio_nav_usd", portfolio_nav_usd)
        _require_non_negative("max_depeg_threshold_pct", max_depeg_threshold_pct)
        _require_positive("max_allowed_finality_minutes", max_allowed_finality_minutes)
        _require_non_negative("min_audit_score_pct", min_audit_score_pct)
        if min_audit_score_pct > 100.0:
            raise ValueError(
                f"min_audit_score_pct must be <= 100, got {min_audit_score_pct}"
            )
        self.portfolio_nav_usd = portfolio_nav_usd
        self.max_depeg_threshold_pct = max_depeg_threshold_pct
        self.max_allowed_finality_minutes = max_allowed_finality_minutes
        self.min_audit_score_pct = min_audit_score_pct
        self.bridges: Dict[str, BridgeProfile] = {b.bridge_id: b for b in (bridges or [])}

    def register_bridge(self, bridge: BridgeProfile) -> None:
        """Registers or replaces a bridge profile.

        Replacement is the in-flight update mechanism: after executing an
        approved transfer, re-register the bridge with the new
        `current_inflight_usd` so the next evaluation sees it.
        """
        if bridge.bridge_id in self.bridges:
            logger.warning(
                "Bridge %s already registered; replacing existing profile.",
                bridge.bridge_id,
            )
        self.bridges[bridge.bridge_id] = bridge

    def calculate_depeg_pct(self, native_price: float, wrapped_price: float) -> float:
        """
        De-peg percentage: |wrapped - native| / native * 100, rounded to 2dp
        for reporting.

        Prices must be positive and finite: a dead or malformed price feed
        is a data error, not evidence of parity — it raises ValueError
        rather than silently reporting 0.0% de-peg.

        Note: this is the *display* value. The halt gate compares the exact
        value (`_exact_depeg_pct`) — rounding to 2dp before the comparison
        silently disables any threshold finer than ~0.005%.
        """
        return round(self._exact_depeg_pct(native_price, wrapped_price), 2)

    def _exact_depeg_pct(self, native_price: float, wrapped_price: float) -> float:
        """Unrounded de-peg percentage used for threshold comparisons."""
        _require_positive("native_price", native_price)
        _require_positive("wrapped_price", wrapped_price)
        return float(abs(wrapped_price - native_price) / native_price * 100.0)

    def _is_bridge_eligible(self, bridge: BridgeProfile, transfer_amount_usd: float) -> bool:
        """Finality SLA, audit gate, and projected in-flight NAV cap check."""
        if bridge.finality_delay_minutes > self.max_allowed_finality_minutes:
            return False
        if bridge.audit_score_pct < self.min_audit_score_pct:
            return False
        projected_inflight = bridge.current_inflight_usd + transfer_amount_usd
        return projected_inflight / self.portfolio_nav_usd <= bridge.max_nav_pct_cap

    def evaluate_bridge_transfer(
        self,
        target_bridge_id: str,
        transfer_amount_usd: float,
        native_price: float,
        wrapped_price: float
    ) -> BridgeTransferDecision:
        """
        Evaluates a proposed cross-chain transfer against de-peg limits,
        in-flight NAV caps, finality SLAs, and the audit gate, rerouting to
        the lowest-risk compliant secondary bridge when the primary fails.
        """
        if target_bridge_id not in self.bridges:
            raise ValueError(f"Unknown bridge ID {target_bridge_id}")
        _require_positive("transfer_amount_usd", transfer_amount_usd)

        exact_depeg_pct = self._exact_depeg_pct(native_price, wrapped_price)
        depeg_pct = round(exact_depeg_pct, 2)
        target_b = self.bridges[target_bridge_id]
        proj_inflight = target_b.current_inflight_usd + transfer_amount_usd
        proj_nav_pct = proj_inflight / self.portfolio_nav_usd

        # 1. Audit Wrapped Token De-Peg (systemic halt on de-peg — no reroute:
        #    the wrapped asset is impaired on every bridge that mints it).
        #    Compared on the exact value: gating on the 2dp display figure
        #    would approve a real de-peg under any sub-0.005% threshold.
        if exact_depeg_pct >= self.max_depeg_threshold_pct:
            logger.critical(
                f"WRAPPED ASSET DE-PEG DETECTED on {target_bridge_id}: De-peg = {depeg_pct:.2f}% >= Threshold {self.max_depeg_threshold_pct}%! Blocking routing."
            )
            return BridgeTransferDecision(
                selected_bridge_id=target_bridge_id,
                is_approved=False,
                is_rerouted=False,
                # The transfer is blocked, but the projected exposure is still
                # recorded: a 0.0 here reads as "no capital at risk" in the
                # audit trail.
                projected_nav_pct=round(proj_nav_pct * 100, 2),
                depeg_pct=depeg_pct,
                reason=f"Wrapped token de-peg of {depeg_pct:.2f}% exceeds safety threshold of {self.max_depeg_threshold_pct}%."
            )

        # 2. Audit In-Flight Capital NAV Cap, Finality SLA, and Audit Gate
        primary_eligible = self._is_bridge_eligible(target_b, transfer_amount_usd)

        if primary_eligible:
            return BridgeTransferDecision(
                selected_bridge_id=target_bridge_id,
                is_approved=True,
                is_rerouted=False,
                projected_nav_pct=round(proj_nav_pct * 100, 2),
                depeg_pct=depeg_pct,
                reason="Transfer approved on primary target bridge."
            )

        # Primary ineligible -> failover to the lowest-risk compliant bridge
        logger.warning(
            f"Bridge {target_bridge_id} ineligible (NAV Cap: {proj_nav_pct*100:.1f}%, "
            f"Finality: {target_b.finality_delay_minutes}m, "
            f"Audit: {target_b.audit_score_pct:.0f}%). Searching failover..."
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
        # Rank by audit score (descending), then finality delay (ascending);
        # registration order breaks remaining ties for determinism.
        candidates = sorted(
            (b for bid, b in self.bridges.items() if bid != exclude_bridge_id),
            key=lambda b: (-b.audit_score_pct, b.finality_delay_minutes),
        )
        for b in candidates:
            if not self._is_bridge_eligible(b, transfer_amount_usd):
                continue
            proj_inflight = b.current_inflight_usd + transfer_amount_usd
            return BridgeTransferDecision(
                selected_bridge_id=b.bridge_id,
                is_approved=True,
                is_rerouted=True,
                projected_nav_pct=round(proj_inflight / self.portfolio_nav_usd * 100, 2),
                depeg_pct=depeg_pct,
                reason=f"Primary bridge {exclude_bridge_id} breached. Re-routed to secondary bridge {b.bridge_id}."
            )
        return None
