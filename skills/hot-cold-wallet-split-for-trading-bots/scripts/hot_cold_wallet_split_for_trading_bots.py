"""
hot-cold-wallet-split-for-trading-bots: treasury allocation engine that audits the
Hot/Cold split of a trading operation's crypto balances and proposes sweep or
refill transfers.

What this module is and is not
------------------------------
It is a **stateless, point-in-time policy evaluator**. It takes a snapshot of
balances plus the permissions of the trading API key and returns a *proposal*.
It does not sign, broadcast, or track transactions, and it has no view of the
chain. Executing a proposal is the caller's responsibility, and the caller owns
the idempotency of that execution (see ``pending_transfer_*`` below).

Where the thresholds come from
------------------------------
The defaults (15% target hot, 25% sweep trigger, 5% refill floor) are
**engineering defaults with no regulatory basis**. No US regulator prescribes a
numeric hot/cold split: NYDFS 23 NYCRR Part 200 and its custodial-structure
guidance impose custody and segregation duties without a percentage.

Numeric caps that *are* mandatory apply to **client** virtual assets held by a
licensed platform, not to a proprietary trading treasury, and they are far
stricter than these defaults:

===================  =========================================  ==============
Jurisdiction         Instrument                                 Max hot
===================  =========================================  ==============
Hong Kong            SFC VATP Guidelines (in force 2023-06-01)  2%
Japan                Payment Services Act / FSA                 5%
South Korea          Virtual Asset User Protection Act          20%
===================  =========================================  ==============

If any such cap binds you, set ``regulatory_max_hot_ratio`` so the ceiling is
enforced as a distinct, separately-escalated breach rather than being buried in
an ordinary rebalance. See ``references/standards.md`` for citations.

API key permissions
-------------------
``enableWithdrawals=false`` alone does **not** make a key unable to move funds.
Binance's Get API Key Permission endpoint also exposes ``enableInternalTransfer``
(transfers between the caller's own Binance account types) and
``permitsUniversalTransfer`` (transfers across Binance products); either is a
fund-movement path. This engine treats all three as fund-moving permissions.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Rebalance actions.
ACTION_SWEEP_TO_COLD = "SWEEP_TO_COLD"
ACTION_REFILL_HOT_FROM_COLD = "REFILL_HOT_FROM_COLD"
ACTION_HOLD_BALANCES = "HOLD_BALANCES"
ACTION_SECURITY_ALERT = "SECURITY_ALERT_WITHDRAW_ENABLED"

#: Report statuses.
STATUS_BALANCED = "PORTFOLIO_BALANCED"
STATUS_REBALANCE_REQUIRED = "REBALANCE_REQUIRED"
STATUS_SECURITY_ALERT = "CRITICAL_SECURITY_ALERT"
STATUS_REGULATORY_BREACH = "REGULATORY_HOT_CAP_BREACH"

#: Tolerance for float comparison when deciding whether a proposal was capped by
#: the funding wallet's balance. Sub-cent, so it can never mask a real shortfall.
_FUNDING_EPSILON = 1e-9


class HotColdWalletError(ValueError):
    """Raised when balances or engine configuration are invalid.

    Subclasses :class:`ValueError` so callers that already catch ``ValueError``
    keep working. A treasury control must fail loudly: a ``NaN`` balance or an
    inverted threshold band must never be evaluated into a confident-looking
    ``PORTFOLIO_BALANCED``.
    """


@dataclass
class WalletBalances:
    """Point-in-time snapshot of treasury balances and API key permissions.

    All amounts are USD-denominated and must be finite and non-negative. Convert
    each asset to USD with a single, consistent mark before constructing this;
    the engine has no price source and cannot detect a stale mark.

    Pending transfers are transfers already submitted but not yet settled. They
    are still inside the treasury, so they do not change the total -- but they do
    change the hot balance the treasury is *converging* to. Supplying them is
    what stops a scheduled audit from re-proposing a sweep that is already in
    flight; leaving them at zero reproduces the naive behaviour.

    A pending amount must still be counted in its *source* wallet: an unsettled
    sweep is part of ``hot_wallet_usd``, an unsettled refill part of
    ``cold_vault_usd``. Many balance feeds debit a transfer the moment it is
    broadcast -- passing it as pending as well would double-count it, so that
    incoherence is rejected rather than silently skewing the ratio.
    """

    hot_wallet_usd: float
    cold_vault_usd: float
    warm_buffer_usd: float = 0.0
    #: True if the trading key can withdraw to an external address.
    api_key_withdraw_permission_enabled: bool = False
    #: Binance ``enableInternalTransfer`` or equivalent: moves funds between the
    #: operator's own account types. A fund-movement path even with withdrawals off.
    api_key_internal_transfer_enabled: bool = False
    #: Binance ``permitsUniversalTransfer`` or equivalent: moves funds across products.
    api_key_universal_transfer_enabled: bool = False
    #: Tri-state. ``None`` means "not assessed" and is reported as such rather
    #: than being silently counted as hardened.
    api_key_ip_restricted: Optional[bool] = None
    #: Submitted-but-unsettled hot -> cold sweep.
    pending_transfer_to_cold_usd: float = 0.0
    #: Submitted-but-unsettled cold -> hot refill.
    pending_transfer_to_hot_usd: float = 0.0


@dataclass
class HotColdWalletAuditReport:
    """Structured, auditable result of one treasury evaluation."""

    total_portfolio_usd: float
    hot_wallet_usd: float
    cold_vault_usd: float
    current_hot_ratio: float             # observed hot / total, ignoring in-flight transfers
    target_hot_ratio: float
    rebalance_action: str                # one of the ACTION_* constants
    proposed_transfer_usd: float
    is_api_key_secure: bool              # no fund-moving permission enabled; NOT a full key audit
    status: str                          # one of the STATUS_* constants
    audit_notes: str
    # --- appended fields (all defaulted, so construction stays backward compatible) ---
    warm_buffer_usd: float = 0.0
    #: Hot ratio after netting in-flight transfers. This is what decisions use.
    effective_hot_ratio: float = 0.0
    max_hot_ratio_threshold: float = 0.0
    min_hot_ratio_threshold: float = 0.0
    regulatory_max_hot_ratio: Optional[float] = None
    #: False when the proposal was capped by the funding wallet's actual balance.
    is_transfer_fully_fundable: bool = True
    security_findings: List[str] = field(default_factory=list)


def _floor_to_cents(value: float) -> float:
    """Round a transfer amount DOWN to whole cents.

    Always rounding down guarantees a proposal never exceeds the availability it
    was capped against, so an executor cannot be handed an amount the funding
    wallet is a fraction of a cent short of.
    """
    return math.floor(value * 100.0) / 100.0


def _validate_amount(value: float, name: str) -> float:
    """Reject non-numeric, non-finite, or negative money amounts."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise HotColdWalletError(f"{name} must be a real number, got {value!r}.") from exc
    if not math.isfinite(amount):
        raise HotColdWalletError(
            f"{name} must be finite, got {amount!r}. A NaN or infinite balance would "
            "otherwise compare False against every threshold and yield a false "
            "'balanced' verdict."
        )
    if amount < 0.0:
        raise HotColdWalletError(f"{name} must be >= 0, got {amount}.")
    return amount


def _validate_ratio(value: float, name: str) -> float:
    """Reject non-finite ratios or ratios outside [0, 1]."""
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise HotColdWalletError(f"{name} must be a real number, got {value!r}.") from exc
    if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
        raise HotColdWalletError(f"{name} must be a finite fraction in [0, 1], got {value!r}.")
    return ratio


class HotColdWalletManagerEngine:
    """Audits the Hot/Cold balance split and proposes sweep or refill transfers.

    Thresholds are validated as a coherent band on construction: an engine whose
    sweep target sits at or above its own sweep trigger would propose a "fix"
    that leaves the treasury still in breach, so that configuration is rejected.
    """

    def __init__(
        self,
        target_hot_ratio: float = 0.15,
        max_hot_ratio_threshold: float = 0.25,   # ratio strictly above this triggers a sweep
        min_hot_ratio_threshold: float = 0.05,   # ratio strictly below this triggers a refill
        regulatory_max_hot_ratio: Optional[float] = None,
    ) -> None:
        self.target_hot_ratio = _validate_ratio(target_hot_ratio, "target_hot_ratio")
        self.max_hot_ratio_threshold = _validate_ratio(
            max_hot_ratio_threshold, "max_hot_ratio_threshold"
        )
        self.min_hot_ratio_threshold = _validate_ratio(
            min_hot_ratio_threshold, "min_hot_ratio_threshold"
        )

        if not (
            self.min_hot_ratio_threshold
            < self.target_hot_ratio
            < self.max_hot_ratio_threshold
        ):
            raise HotColdWalletError(
                "Thresholds must satisfy min < target < max, got "
                f"min={self.min_hot_ratio_threshold}, target={self.target_hot_ratio}, "
                f"max={self.max_hot_ratio_threshold}."
            )

        if regulatory_max_hot_ratio is None:
            self.regulatory_max_hot_ratio: Optional[float] = None
        else:
            cap = _validate_ratio(regulatory_max_hot_ratio, "regulatory_max_hot_ratio")
            if cap <= self.min_hot_ratio_threshold:
                raise HotColdWalletError(
                    f"regulatory_max_hot_ratio={cap} is at or below the operating floor "
                    f"min_hot_ratio_threshold={self.min_hot_ratio_threshold}; the treasury "
                    "would breach the mandated ceiling whenever it refilled to its own "
                    "floor. Lower the floor to sit under the cap."
                )
            self.regulatory_max_hot_ratio = cap

        # A mandated ceiling binds ahead of the engine's own operating band.
        self._effective_sweep_trigger = (
            self.max_hot_ratio_threshold
            if self.regulatory_max_hot_ratio is None
            else min(self.max_hot_ratio_threshold, self.regulatory_max_hot_ratio)
        )
        self._effective_target = (
            self.target_hot_ratio
            if self.regulatory_max_hot_ratio is None
            else min(self.target_hot_ratio, self.regulatory_max_hot_ratio)
        )

    def _audit_fund_moving_permissions(self, balances: WalletBalances) -> List[str]:
        """Return one finding per fund-moving permission enabled on the trading key."""
        findings: List[str] = []
        if balances.api_key_withdraw_permission_enabled:
            findings.append(
                "Withdrawal permission ('enableWithdrawals') is ENABLED: the key can send "
                "funds to an external address."
            )
        if balances.api_key_internal_transfer_enabled:
            findings.append(
                "Internal transfer permission ('enableInternalTransfer') is ENABLED: the "
                "key can move funds between the operator's own account types even with "
                "withdrawals disabled."
            )
        if balances.api_key_universal_transfer_enabled:
            findings.append(
                "Universal transfer permission ('permitsUniversalTransfer') is ENABLED: "
                "the key can move funds across products even with withdrawals disabled."
            )
        return findings

    def audit_and_rebalance_treasury(
        self, balances: WalletBalances
    ) -> HotColdWalletAuditReport:
        """Audit balances and API key permissions and propose a rebalance transfer.

        Raises:
            HotColdWalletError: if any balance is non-finite or negative, or if the
                total portfolio value is not strictly positive.
        """
        hot = _validate_amount(balances.hot_wallet_usd, "hot_wallet_usd")
        cold = _validate_amount(balances.cold_vault_usd, "cold_vault_usd")
        warm = _validate_amount(balances.warm_buffer_usd, "warm_buffer_usd")
        pending_to_cold = _validate_amount(
            balances.pending_transfer_to_cold_usd, "pending_transfer_to_cold_usd"
        )
        pending_to_hot = _validate_amount(
            balances.pending_transfer_to_hot_usd, "pending_transfer_to_hot_usd"
        )

        # A pending transfer is money still counted in its *source* wallet. If the
        # balance feed has already debited the broadcast amount, passing it here too
        # double-counts it; the resulting incoherence is caught rather than silently
        # producing a nonsense ratio.
        if pending_to_cold > hot:
            raise HotColdWalletError(
                f"pending_transfer_to_cold_usd={pending_to_cold} exceeds "
                f"hot_wallet_usd={hot}. Hot balance must still include funds committed "
                "to an unsettled outbound sweep; if your balance feed already debits "
                "broadcast transfers, do not also pass them as pending."
            )
        if pending_to_hot > cold:
            raise HotColdWalletError(
                f"pending_transfer_to_hot_usd={pending_to_hot} exceeds "
                f"cold_vault_usd={cold}. Cold balance must still include funds committed "
                "to an unsettled refill; if your balance feed already debits broadcast "
                "transfers, do not also pass them as pending."
            )

        total = hot + cold + warm
        if not math.isfinite(total):
            # Individually finite balances can still overflow when summed, which would
            # drive every ratio to 0.0 and propose an absurd refill.
            raise HotColdWalletError(
                f"Total portfolio value overflowed to {total}; balances are implausibly large."
            )
        if total <= 0.0:
            raise HotColdWalletError("Total portfolio value must be > 0.")

        # Decisions run on unrounded ratios; rounding is presentation only. Rounding
        # first would let a 25.004% breach round down to exactly the 25% cap and
        # silently pass the control.
        current_hot_ratio = hot / total
        # In-flight transfers stay inside the treasury, so they move the hot balance
        # without changing the total. Netting them is what makes repeated audits
        # idempotent instead of stacking duplicate sweeps.
        effective_hot = max(0.0, hot - pending_to_cold + pending_to_hot)
        effective_hot_ratio = effective_hot / total

        total_r = round(total, 2)
        ratio_r = round(current_hot_ratio, 4)
        effective_ratio_r = round(effective_hot_ratio, 4)

        fund_moving_findings = self._audit_fund_moving_permissions(balances)
        security_findings = list(fund_moving_findings)
        if balances.api_key_ip_restricted is False:
            security_findings.append(
                "Key is not IP-restricted: harden with an IP allowlist (Binance requires "
                "one before withdrawals can be enabled at all)."
            )
        elif balances.api_key_ip_restricted is None:
            security_findings.append(
                "IP restriction not assessed (api_key_ip_restricted is None); this audit "
                "cannot confirm the key is network-scoped."
            )

        if fund_moving_findings:
            notes = (
                "CRITICAL SECURITY ALERT: trading bot API key holds fund-moving "
                "permissions. Revoke them before trading. "
                + " ".join(fund_moving_findings)
            )
            logger.critical(notes)
            return HotColdWalletAuditReport(
                total_portfolio_usd=total_r,
                hot_wallet_usd=hot,
                cold_vault_usd=cold,
                current_hot_ratio=ratio_r,
                target_hot_ratio=self.target_hot_ratio,
                rebalance_action=ACTION_SECURITY_ALERT,
                proposed_transfer_usd=0.0,
                is_api_key_secure=False,
                status=STATUS_SECURITY_ALERT,
                audit_notes=notes,
                warm_buffer_usd=warm,
                effective_hot_ratio=effective_ratio_r,
                max_hot_ratio_threshold=self.max_hot_ratio_threshold,
                min_hot_ratio_threshold=self.min_hot_ratio_threshold,
                regulatory_max_hot_ratio=self.regulatory_max_hot_ratio,
                security_findings=security_findings,
            )

        target_hot_usd = total * self._effective_target
        is_fully_fundable = True

        if effective_hot_ratio > self._effective_sweep_trigger:
            requested = effective_hot - target_hot_usd
            # A sweep can only move coins physically sitting in the hot wallet; an
            # inbound refill still in flight has not landed yet, and coins already
            # committed to an outbound sweep cannot be committed twice.
            available = max(0.0, hot - pending_to_cold)
            transfer_usd = _floor_to_cents(min(requested, available))
            is_fully_fundable = requested <= available + _FUNDING_EPSILON
            action = ACTION_SWEEP_TO_COLD
            breached_regulatory_cap = (
                self.regulatory_max_hot_ratio is not None
                and effective_hot_ratio > self.regulatory_max_hot_ratio
            )
            status = (
                STATUS_REGULATORY_BREACH if breached_regulatory_cap
                else STATUS_REBALANCE_REQUIRED
            )
            notes = (
                f"HOT WALLET SWEEP REQUIRED: effective Hot Ratio "
                f"{effective_hot_ratio * 100:.2f}% exceeds sweep trigger "
                f"{self._effective_sweep_trigger * 100:.2f}%. Proposing sweep of "
                f"${transfer_usd:,.2f} from Hot Wallet to Cold Vault."
            )
            if breached_regulatory_cap:
                notes += (
                    " REGULATORY CAP BREACHED: mandated maximum is "
                    f"{self.regulatory_max_hot_ratio * 100:.2f}%."
                )
            if not is_fully_fundable:
                notes += (
                    f" PROPOSAL CAPPED: ${requested:,.2f} required but only "
                    f"${available:,.2f} is settled and uncommitted in the Hot Wallet."
                )
            if breached_regulatory_cap:
                logger.critical(notes)
            else:
                logger.warning(notes)

        elif effective_hot_ratio < self.min_hot_ratio_threshold:
            requested = target_hot_usd - effective_hot
            # A refill draws on the Cold Vault, and coins already committed to an
            # in-flight refill cannot be committed twice.
            available = max(0.0, cold - pending_to_hot)
            transfer_usd = _floor_to_cents(min(requested, available))
            is_fully_fundable = requested <= available + _FUNDING_EPSILON
            action = ACTION_REFILL_HOT_FROM_COLD
            status = STATUS_REBALANCE_REQUIRED
            notes = (
                f"HOT WALLET REFILL REQUIRED: effective Hot Ratio "
                f"{effective_hot_ratio * 100:.2f}% is below min floor "
                f"{self.min_hot_ratio_threshold * 100:.2f}%. Requesting multisig refill of "
                f"${transfer_usd:,.2f} from Cold Vault to Hot Wallet."
            )
            if not is_fully_fundable:
                notes += (
                    f" PROPOSAL CAPPED: ${requested:,.2f} required but only "
                    f"${available:,.2f} is available and uncommitted in the Cold Vault."
                )
            logger.warning(notes)

        else:
            action = ACTION_HOLD_BALANCES
            status = STATUS_BALANCED
            transfer_usd = 0.0
            notes = (
                f"TREASURY BALANCED: effective Hot Ratio = "
                f"{effective_hot_ratio * 100:.2f}% is within band "
                f"[{self.min_hot_ratio_threshold * 100:.2f}% - "
                f"{self._effective_sweep_trigger * 100:.2f}%]. No transfers required."
            )
            logger.info(notes)

        return HotColdWalletAuditReport(
            total_portfolio_usd=total_r,
            hot_wallet_usd=hot,
            cold_vault_usd=cold,
            current_hot_ratio=ratio_r,
            target_hot_ratio=self.target_hot_ratio,
            rebalance_action=action,
            proposed_transfer_usd=transfer_usd,
            is_api_key_secure=True,
            status=status,
            audit_notes=notes,
            warm_buffer_usd=warm,
            effective_hot_ratio=effective_ratio_r,
            max_hot_ratio_threshold=self.max_hot_ratio_threshold,
            min_hot_ratio_threshold=self.min_hot_ratio_threshold,
            regulatory_max_hot_ratio=self.regulatory_max_hot_ratio,
            is_transfer_fully_fundable=is_fully_fundable,
            security_findings=security_findings,
        )
