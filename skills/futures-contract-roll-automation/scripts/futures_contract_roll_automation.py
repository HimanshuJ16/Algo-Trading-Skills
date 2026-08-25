"""Futures contract roll automation.

Stateless decision engine that evaluates whether an open futures position must be
rolled to the next expiration, and — when it must — describes the calendar-spread
order that performs the roll.

Two families of trigger are evaluated:

* **Liquidity migration** — next-month daily volume and/or open interest overtaking
  the front month. These are *economic* triggers: rolling late costs spread, but
  nothing is forced.
* **Delivery-risk deadlines** — business days remaining to Last Trading Day (LTD)
  and, for physically delivered contracts, to First Notice Day (FND). These are
  *hard* deadlines: missing them can create a delivery obligation.

The two are not interchangeable. For many physically delivered contracts FND
precedes LTD by a wide margin (CBOT grain FND is the last business day of the month
preceding the delivery month, while LTD is the business day before the 15th of the
delivery month), so a rule expressed only in days-to-expiration rolls far too late.
See ``references/standards.md``.

All day counts are **business days** unless stated otherwise.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Spread quoting conventions ------------------------------------------------
# CME publishes no single calendar-spread convention: the standard "SP" strategy is
# listed nearby-first and differenced nearby-minus-deferred, while CME FX calendar
# spreads are quoted far-minus-near and Equity Index rolls are quoted deferred-over-
# nearby. The side of the combination instrument that performs a given roll is
# therefore product-specific and must be configured, never assumed.
NEARBY_MINUS_DEFERRED = "NEARBY_MINUS_DEFERRED"
DEFERRED_MINUS_NEARBY = "DEFERRED_MINUS_NEARBY"
VALID_QUOTING_CONVENTIONS: Tuple[str, ...] = (NEARBY_MINUS_DEFERRED, DEFERRED_MINUS_NEARBY)

# --- Trigger reason codes ------------------------------------------------------
TRIGGER_FIRST_NOTICE = "FIRST_NOTICE_DAY_THRESHOLD"
TRIGGER_DAYS_TO_EXPIRATION = "DAYS_TO_EXPIRATION_THRESHOLD"
TRIGGER_VOLUME_CROSSOVER = "VOLUME_CROSSOVER"
TRIGGER_OPEN_INTEREST_CROSSOVER = "OPEN_INTEREST_CROSSOVER"

# --- Delivery-risk levels ------------------------------------------------------
DELIVERY_RISK_NONE = "NONE"
DELIVERY_RISK_APPROACHING_FIRST_NOTICE = "APPROACHING_FIRST_NOTICE"
DELIVERY_RISK_FIRST_NOTICE_PASSED = "FIRST_NOTICE_PASSED"
DELIVERY_RISK_PAST_LAST_TRADING_DAY = "PAST_LAST_TRADING_DAY"

# --- Report statuses -----------------------------------------------------------
STATUS_HOLD = "HOLD_FRONT_CONTRACT"
STATUS_ROLL_ACTIVE = "ROLL_SIGNAL_ACTIVE"
STATUS_TOO_LATE = "ROLL_TOO_LATE_ESCALATE"


@dataclass
class FuturesContractState:
    """Point-in-time state of one futures contract.

    Args:
        symbol: Exchange contract symbol, e.g. ``'ESH6'``.
        expiration_date_iso: Last Trading Day as ``YYYY-MM-DD``.
        days_to_expiration: **Business** days from the current session to Last
            Trading Day. ``0`` means today *is* the last trading session; negative
            means the contract can no longer be traded.
        daily_volume: Most recent completed session's volume. Single-session volume
            is noisy — see the crossover caveat in ``SKILL.md``.
        open_interest: Most recent published open interest. Exchanges publish OI
            with a one-session lag; feed the value the venue has actually released.
        last_price: Last trade or settlement price, in exchange quote units.
        is_physically_delivered: ``True`` for contracts that settle by physical
            delivery. When set, ``days_to_first_notice`` becomes mandatory.
        days_to_first_notice: **Business** days to First Notice Day. Required for
            physically delivered contracts; ``None`` for cash-settled contracts.
        contract_multiplier: Currency value of one full point of the quoted price
            (e.g. ``50.0`` for E-mini S&P 500). Used only to translate the roll
            basis into position currency.
    """

    symbol: str
    expiration_date_iso: str
    days_to_expiration: int
    daily_volume: int
    open_interest: int
    last_price: float
    # Fields below were appended in 2.0.0 and default to the pre-2.0.0 behaviour,
    # so existing positional construction keeps working unchanged.
    is_physically_delivered: bool = False
    days_to_first_notice: Optional[int] = None
    contract_multiplier: float = 1.0

    def validate(self, role: str) -> None:
        """Raise ``ValueError`` if this contract state cannot be acted on.

        Args:
            role: Human label used in error messages, e.g. ``'front'``.
        """
        if not self.symbol or not self.symbol.strip():
            raise ValueError(f"{role} contract: symbol must be a non-empty string.")
        if not isinstance(self.days_to_expiration, int) or isinstance(
            self.days_to_expiration, bool
        ):
            raise ValueError(
                f"{role} contract {self.symbol}: days_to_expiration must be an integer "
                f"count of business days, got {self.days_to_expiration!r}. A float "
                f"(or NaN) silently disables the expiration comparison."
            )
        if self.days_to_first_notice is not None and (
            not isinstance(self.days_to_first_notice, int)
            or isinstance(self.days_to_first_notice, bool)
        ):
            raise ValueError(
                f"{role} contract {self.symbol}: days_to_first_notice must be an "
                f"integer count of business days or None, got "
                f"{self.days_to_first_notice!r}."
            )
        if not math.isfinite(self.last_price) or self.last_price <= 0:
            raise ValueError(
                f"{role} contract {self.symbol}: last_price must be a finite positive "
                f"number, got {self.last_price!r}."
            )
        if self.daily_volume < 0:
            raise ValueError(
                f"{role} contract {self.symbol}: daily_volume must be >= 0, "
                f"got {self.daily_volume}."
            )
        if self.open_interest < 0:
            raise ValueError(
                f"{role} contract {self.symbol}: open_interest must be >= 0, "
                f"got {self.open_interest}."
            )
        if not math.isfinite(self.contract_multiplier) or self.contract_multiplier <= 0:
            raise ValueError(
                f"{role} contract {self.symbol}: contract_multiplier must be a finite "
                f"positive number, got {self.contract_multiplier!r}."
            )
        if self.is_physically_delivered and self.days_to_first_notice is None:
            raise ValueError(
                f"{role} contract {self.symbol}: days_to_first_notice is required for "
                f"physically delivered contracts. For many products First Notice Day "
                f"precedes Last Trading Day, so days_to_expiration alone cannot bound "
                f"delivery risk."
            )


@dataclass
class CalendarSpreadOrder:
    """A roll expressed as a single combination (calendar spread) order.

    ``spread_price_diff`` is a convention-independent economic quantity
    (``P_next - P_front``). ``quoted_spread_price`` and ``spread_side`` are the
    venue-facing values and depend on ``quoting_convention``.
    """

    spread_symbol: str                  # Human-readable label, e.g. 'ESH6-ESM6'
    front_leg_symbol: str
    next_leg_symbol: str
    front_leg_action: str               # 'SELL' for a long roll, 'BUY' for a short roll
    next_leg_action: str                # 'BUY' for a long roll, 'SELL' for a short roll
    quantity: int
    spread_price_diff: float            # P_next - P_front (deferred minus nearby)
    term_structure: str                 # 'CONTANGO' | 'BACKWARDATION' | 'FLAT'
    # Appended in 2.0.0.
    quoting_convention: str = NEARBY_MINUS_DEFERRED
    quoted_spread_price: float = 0.0    # spread_price_diff re-signed per convention
    spread_side: str = "SELL"           # 'BUY'/'SELL' of the combination instrument
    estimated_roll_cost: float = 0.0    # Position currency; positive = P&L drag


@dataclass
class FuturesRollAuditReport:
    """Structured, loggable outcome of one roll evaluation."""

    root_symbol: str                    # e.g. 'ES'
    front_contract: FuturesContractState
    next_contract: FuturesContractState
    is_roll_triggered: bool
    trigger_reason: str                 # '+'-joined reason codes, or 'NONE'
    calendar_spread_order: Optional[CalendarSpreadOrder]
    status: str                         # See STATUS_* constants
    audit_notes: str
    # Appended in 2.0.0.
    trigger_reasons: Tuple[str, ...] = ()
    delivery_risk_level: str = DELIVERY_RISK_NONE


class FuturesContractRollEngine:
    """Evaluates roll triggers and builds the corresponding calendar spread order.

    The engine is stateless and side-effect free apart from logging: one call
    evaluates one (position, front, next) tuple against the configured thresholds.
    Multi-session confirmation of a volume/OI crossover, if wanted, belongs to the
    caller — see the crossover caveat in ``SKILL.md``.
    """

    def __init__(
        self,
        min_days_to_expiration: int = 5,
        enable_volume_crossover: bool = True,
        enable_open_interest_crossover: bool = True,
        min_days_to_first_notice: int = 2,
        spread_quoting_convention: str = NEARBY_MINUS_DEFERRED,
        spread_price_decimals: Optional[int] = None,
    ) -> None:
        """
        Args:
            min_days_to_expiration: Roll when the front contract has this many
                business days or fewer to Last Trading Day. A default, not a
                standard — calibrate per product.
            enable_volume_crossover: Enable the ``V_next > V_front`` trigger.
            enable_open_interest_crossover: Enable the ``OI_next > OI_front`` trigger.
            min_days_to_first_notice: For physically delivered contracts, roll when
                this many business days or fewer remain to First Notice Day.
            spread_quoting_convention: ``NEARBY_MINUS_DEFERRED`` (CME standard ``SP``
                listing, CBOT Treasuries) or ``DEFERRED_MINUS_NEARBY`` (CME FX
                calendar spreads, Equity Index roll). Determines the sign of
                ``quoted_spread_price`` and which side of the combination instrument
                performs the roll.
            spread_price_decimals: Optional rounding applied to the spread prices.
                ``None`` (default) leaves them unrounded; round to the spread
                instrument's own tick precision, not an arbitrary constant.

        Raises:
            ValueError: On an unknown quoting convention or a negative threshold.
        """
        if spread_quoting_convention not in VALID_QUOTING_CONVENTIONS:
            raise ValueError(
                f"spread_quoting_convention must be one of "
                f"{VALID_QUOTING_CONVENTIONS}, got {spread_quoting_convention!r}."
            )
        if min_days_to_expiration < 0:
            raise ValueError("min_days_to_expiration must be >= 0.")
        if min_days_to_first_notice < 0:
            raise ValueError("min_days_to_first_notice must be >= 0.")
        if spread_price_decimals is not None and spread_price_decimals < 0:
            raise ValueError("spread_price_decimals must be >= 0 or None.")

        self.min_days_to_expiration = min_days_to_expiration
        self.enable_volume_crossover = enable_volume_crossover
        self.enable_open_interest_crossover = enable_open_interest_crossover
        self.min_days_to_first_notice = min_days_to_first_notice
        self.spread_quoting_convention = spread_quoting_convention
        self.spread_price_decimals = spread_price_decimals

    # -- Internal helpers -------------------------------------------------------

    @staticmethod
    def _validate_pair(
        position_side: str,
        position_qty: int,
        front_contract: FuturesContractState,
        next_contract: FuturesContractState,
    ) -> str:
        """Validate the inputs and return the normalised position side."""
        if not isinstance(position_qty, int) or isinstance(position_qty, bool):
            raise ValueError("Position quantity must be an integer number of contracts.")
        if position_qty <= 0:
            raise ValueError("Position quantity must be > 0.")

        side_clean = position_side.upper() if isinstance(position_side, str) else ""
        if side_clean not in ("LONG", "SHORT"):
            raise ValueError("Position side must be 'LONG' or 'SHORT'.")

        front_contract.validate("front")
        next_contract.validate("next")

        if front_contract.symbol == next_contract.symbol:
            raise ValueError(
                f"Front and next contract are the same symbol ({front_contract.symbol}); "
                f"a contract cannot be rolled into itself."
            )
        if next_contract.days_to_expiration <= front_contract.days_to_expiration:
            raise ValueError(
                f"Next contract {next_contract.symbol} "
                f"(DBE={next_contract.days_to_expiration}) does not expire after front "
                f"contract {front_contract.symbol} "
                f"(DBE={front_contract.days_to_expiration}); the arguments are likely "
                f"swapped, which would roll the position into a nearer expiry."
            )
        if front_contract.contract_multiplier != next_contract.contract_multiplier:
            raise ValueError(
                f"Contract multipliers differ between {front_contract.symbol} "
                f"({front_contract.contract_multiplier}) and {next_contract.symbol} "
                f"({next_contract.contract_multiplier}); a calendar spread requires the "
                f"same product on both legs."
            )
        return side_clean

    def _assess_delivery_risk(self, front_contract: FuturesContractState) -> str:
        """Classify how close the front contract is to an unavoidable obligation."""
        if front_contract.days_to_expiration < 0:
            return DELIVERY_RISK_PAST_LAST_TRADING_DAY
        if front_contract.is_physically_delivered:
            dfn = front_contract.days_to_first_notice
            # validate() guarantees dfn is not None for physically delivered contracts.
            if dfn is not None:
                if dfn <= 0:
                    return DELIVERY_RISK_FIRST_NOTICE_PASSED
                if dfn <= self.min_days_to_first_notice:
                    return DELIVERY_RISK_APPROACHING_FIRST_NOTICE
        return DELIVERY_RISK_NONE

    def _resolve_spread_side(self, side_clean: str) -> str:
        """Map the position side to the side of the combination instrument.

        Under ``NEARBY_MINUS_DEFERRED``, buying the spread buys the nearby leg and
        sells the deferred leg, so a long position rolls by *selling* the spread.
        Under ``DEFERRED_MINUS_NEARBY`` the legs are reversed and a long position
        rolls by *buying* the spread.
        """
        if self.spread_quoting_convention == NEARBY_MINUS_DEFERRED:
            return "SELL" if side_clean == "LONG" else "BUY"
        return "BUY" if side_clean == "LONG" else "SELL"

    # -- Public API -------------------------------------------------------------

    def evaluate_and_build_roll_order(
        self,
        root_symbol: str,
        position_side: str,
        position_qty: int,
        front_contract: FuturesContractState,
        next_contract: FuturesContractState,
    ) -> FuturesRollAuditReport:
        """Audit contract liquidity and delivery deadlines, and build the roll order.

        Args:
            root_symbol: Product root, e.g. ``'ES'``.
            position_side: ``'LONG'`` or ``'SHORT'`` (case-insensitive).
            position_qty: Open contracts to roll; must be a positive integer.
            front_contract: Currently held expiration.
            next_contract: Target expiration; must expire after ``front_contract``.

        Returns:
            A :class:`FuturesRollAuditReport`. ``calendar_spread_order`` is ``None``
            when no roll is triggered, and also when the front contract's last
            trading day has already passed — a spread order cannot lift a leg that
            is no longer tradable.

        Raises:
            ValueError: On invalid position parameters, an invalid contract state,
                identical or mis-ordered contracts, or a physically delivered
                contract supplied without ``days_to_first_notice``.
        """
        side_clean = self._validate_pair(
            position_side, position_qty, front_contract, next_contract
        )

        delivery_risk = self._assess_delivery_risk(front_contract)

        reasons: List[str] = []
        if delivery_risk in (
            DELIVERY_RISK_APPROACHING_FIRST_NOTICE,
            DELIVERY_RISK_FIRST_NOTICE_PASSED,
        ):
            reasons.append(TRIGGER_FIRST_NOTICE)
        if front_contract.days_to_expiration <= self.min_days_to_expiration:
            reasons.append(TRIGGER_DAYS_TO_EXPIRATION)
        if self.enable_volume_crossover and (
            next_contract.daily_volume > front_contract.daily_volume
        ):
            reasons.append(TRIGGER_VOLUME_CROSSOVER)
        if self.enable_open_interest_crossover and (
            next_contract.open_interest > front_contract.open_interest
        ):
            reasons.append(TRIGGER_OPEN_INTEREST_CROSSOVER)

        trigger_reason = "+".join(reasons) if reasons else "NONE"

        if delivery_risk == DELIVERY_RISK_PAST_LAST_TRADING_DAY:
            notes = (
                f"FUTURES ROLL TOO LATE [{root_symbol}]: front contract "
                f"{front_contract.symbol} is past its last trading day "
                f"(DBE={front_contract.days_to_expiration}). No calendar spread order "
                f"was built: the front leg is no longer tradable. Escalate to manual "
                f"handling of settlement or delivery."
            )
            logger.critical(notes)
            return FuturesRollAuditReport(
                root_symbol=root_symbol,
                front_contract=front_contract,
                next_contract=next_contract,
                is_roll_triggered=True,
                trigger_reason=trigger_reason,
                calendar_spread_order=None,
                status=STATUS_TOO_LATE,
                audit_notes=notes,
                trigger_reasons=tuple(reasons),
                delivery_risk_level=delivery_risk,
            )

        if not reasons:
            notes = self._build_hold_notes(root_symbol, front_contract, next_contract)
            logger.info(notes)
            return FuturesRollAuditReport(
                root_symbol=root_symbol,
                front_contract=front_contract,
                next_contract=next_contract,
                is_roll_triggered=False,
                trigger_reason="NONE",
                calendar_spread_order=None,
                status=STATUS_HOLD,
                audit_notes=notes,
                trigger_reasons=(),
                delivery_risk_level=delivery_risk,
            )

        spread_order = self._build_spread_order(
            side_clean, position_qty, front_contract, next_contract
        )

        notes = (
            f"FUTURES ROLL SIGNAL ACTIVE [{root_symbol} - Trigger: {trigger_reason}]: "
            f"Rolling {side_clean} {position_qty} contracts from {front_contract.symbol} "
            f"to {next_contract.symbol}. Term Structure: {spread_order.term_structure} "
            f"(basis P_next - P_front = {spread_order.spread_price_diff:+.6g}). "
            f"{spread_order.spread_side} {spread_order.spread_symbol} "
            f"[{spread_order.quoting_convention}] at "
            f"{spread_order.quoted_spread_price:+.6g}, legs "
            f"{spread_order.front_leg_action} {front_contract.symbol} / "
            f"{spread_order.next_leg_action} {next_contract.symbol}. "
            f"Estimated roll basis cost: {spread_order.estimated_roll_cost:+.2f} "
            f"(positive = P&L drag; excludes fees and spread crossing)."
        )
        # Rolling into a target that is itself already inside its own delivery window
        # just moves the problem one contract along.
        target_risk = self._assess_delivery_risk(next_contract)
        if target_risk != DELIVERY_RISK_NONE:
            notes += (
                f" WARNING: roll target {next_contract.symbol} is itself at "
                f"{target_risk}; consider a further-dated expiration."
            )

        if delivery_risk in (
            DELIVERY_RISK_APPROACHING_FIRST_NOTICE,
            DELIVERY_RISK_FIRST_NOTICE_PASSED,
        ) or target_risk != DELIVERY_RISK_NONE:
            if delivery_risk != DELIVERY_RISK_NONE:
                notes += f" DELIVERY RISK: {delivery_risk}."
            logger.warning(notes)
        else:
            logger.info(notes)

        return FuturesRollAuditReport(
            root_symbol=root_symbol,
            front_contract=front_contract,
            next_contract=next_contract,
            is_roll_triggered=True,
            trigger_reason=trigger_reason,
            calendar_spread_order=spread_order,
            status=STATUS_ROLL_ACTIVE,
            audit_notes=notes,
            trigger_reasons=tuple(reasons),
            delivery_risk_level=delivery_risk,
        )

    # -- Construction helpers ---------------------------------------------------

    def _build_hold_notes(
        self,
        root_symbol: str,
        front_contract: FuturesContractState,
        next_contract: FuturesContractState,
    ) -> str:
        """Describe *why* no trigger fired, using only conditions actually evaluated."""
        clauses = [
            f"DBE={front_contract.days_to_expiration}d > "
            f"{self.min_days_to_expiration}d threshold"
        ]
        if self.enable_volume_crossover:
            clauses.append(
                f"front volume ({front_contract.daily_volume:,}) >= next volume "
                f"({next_contract.daily_volume:,})"
            )
        else:
            clauses.append("volume crossover trigger disabled")
        if self.enable_open_interest_crossover:
            clauses.append(
                f"front OI ({front_contract.open_interest:,}) >= next OI "
                f"({next_contract.open_interest:,})"
            )
        else:
            clauses.append("open interest crossover trigger disabled")
        if front_contract.is_physically_delivered:
            clauses.append(
                f"days to first notice={front_contract.days_to_first_notice}d > "
                f"{self.min_days_to_first_notice}d threshold"
            )
        else:
            clauses.append("cash settled (no first notice day)")
        return (
            f"FUTURES ROLL HOLD [{root_symbol}]: front contract "
            f"{front_contract.symbol} remains active. " + ", ".join(clauses) + "."
        )

    def _build_spread_order(
        self,
        side_clean: str,
        position_qty: int,
        front_contract: FuturesContractState,
        next_contract: FuturesContractState,
    ) -> CalendarSpreadOrder:
        """Build the combination order that moves the position to the next expiry."""
        spread_diff = next_contract.last_price - front_contract.last_price
        if spread_diff > 0:
            term_struct = "CONTANGO"
        elif spread_diff < 0:
            term_struct = "BACKWARDATION"
        else:
            term_struct = "FLAT"

        if side_clean == "LONG":
            front_act, next_act = "SELL", "BUY"
        else:
            front_act, next_act = "BUY", "SELL"

        if self.spread_quoting_convention == NEARBY_MINUS_DEFERRED:
            quoted = front_contract.last_price - next_contract.last_price
        else:
            quoted = spread_diff
        if self.spread_price_decimals is not None:
            quoted = round(quoted, self.spread_price_decimals)
            spread_diff = round(spread_diff, self.spread_price_decimals)

        # A long pays the deferred-minus-nearby basis to stay on; a short receives it.
        direction = 1.0 if side_clean == "LONG" else -1.0
        roll_cost = (
            direction * spread_diff * position_qty * front_contract.contract_multiplier
        )

        return CalendarSpreadOrder(
            # Label only. The tradable spread instrument ID must come from the
            # venue's security definition, never from string concatenation.
            spread_symbol=f"{front_contract.symbol}-{next_contract.symbol}",
            front_leg_symbol=front_contract.symbol,
            next_leg_symbol=next_contract.symbol,
            front_leg_action=front_act,
            next_leg_action=next_act,
            quantity=position_qty,
            spread_price_diff=spread_diff,
            term_structure=term_struct,
            quoting_convention=self.spread_quoting_convention,
            quoted_spread_price=quoted,
            spread_side=self._resolve_spread_side(side_clean),
            estimated_roll_cost=roll_cost,
        )
