"""
prime-brokerage-multi-venue-consolidation: multi-venue execution consolidator and
prime-broker give-up payload builder.

Takes the fills a strategy generated across several executing brokers/venues, nets
them per instrument, accumulates the fee legs, and emits one give-up instruction per
execution for the prime broker (PB) to claim, clear and settle.

Scope and units
---------------
Every quantity is unsigned and carries its direction in ``side``; ``signed_quantity``
derives the sign. Netting is keyed on ``(symbol, currency)`` and additionally requires
a single ``contract_multiplier`` per key, because a ticker quoted in two currencies --
or an option ticker whose contract was adjusted to deliver a non-standard number of
shares -- is two different instruments that must not be summed.

    notional_i          = quantity_i * price_i * contract_multiplier_i
    net_quantity        = sum(signed_quantity_i)
    gross_quantity      = sum(quantity_i)
    vwap                = sum(quantity_i * price_i) / gross_quantity
    residual_notional   = |net_quantity| * vwap * contract_multiplier
    offset_ratio_pct    = (1 - |net_quantity| / gross_quantity) * 100

``offset_ratio_pct`` and ``notional_offset_pct_by_currency`` describe how much of the
gross traded exposure offsets internally once the flow is consolidated into one PB
account. **They are not margin savings.** This module runs no margin model: a
requirement under Regulation T strategy-based margin (12 CFR 220), under FINRA Rule
4210(g) portfolio margin, or under a cross-margin arrangement between clearing
organizations is a function of the PB's approved methodology and of which positions
are actually eligible to offset in a single account -- not of a traded-quantity ratio.
Emitting a "margin savings %" from execution data alone would be a capital claim the
inputs cannot support.

There is no FX conversion here either. Amounts are accumulated per currency and never
summed across currencies; see `multi-currency-pnl-and-fx-conversion` and
`multi-broker-consolidated-position-view` for base-currency valuation.

Failure modes this module refuses to paper over
-----------------------------------------------

- **A side value that is not exactly BUY or SELL raises.** Treating anything that is
  not "BUY" as a sell turns a typo into a position of the wrong sign.
- **A repeated ``execution_id`` raises**, within a batch and (by default) across
  calls on the same engine. A give-up queue replayed after a reconnect otherwise
  double-claims every fill at the PB.
- **A mixed currency or mixed multiplier under one symbol raises** rather than being
  netted into a meaningless aggregate.
- **Non-finite or negative quantities/prices raise on construction**, so a bad feed
  row fails at the boundary instead of poisoning a payload the PB will act on.

Give-up timeliness
------------------
Submission deadlines are venue- and clearing-house-specific and are therefore an
input (``giveup_cutoffs``), never a constant in this file. Two anchors for setting
them:

- US cash equities: SEC Rule 15c6-2 (17 CFR 240.15c6-2) requires broker-dealers to
  have written agreements or policies to complete allocation, confirmation and
  affirmation "as soon as technologically practicable and no later than the end of
  the day on trade date"; DTC's affirmation cut-off is 9:00 p.m. ET on trade date.
  The SIFMA Form 150 prime brokerage agreement, reflecting the SEC's 25 January 1994
  prime brokerage no-action letter, then gives the PB a disaffirmance ("DK") deadline
  of 3:00 p.m. ET on T+1 for trades affirmed by 9:00 a.m. that day and close of
  business on T+1 otherwise; a DK'd trade stays a customer trade on the *executing*
  broker's books.
- Listed futures give-ups: the FIA International Uniform Give-Up Agreement (executed
  electronically via FIA Tech EGUS) governs the relationship, and allocation/claim
  deadlines come from the clearing house rulebook, not from the equity timeline.

Sources
-------
- 17 CFR 240.15c6-2, "Same-day allocation, confirmation, and affirmation"
  (compliance date 28 May 2024, alongside the T+1 cycle under 17 CFR 240.15c6-1).
- SEC Division of Market Regulation, prime brokerage no-action letter to the Prime
  Broker Committee, 25 January 1994 (Regulation T Section 220.11 treatment; minimum
  net equity of $500,000, or $100,000 where the account is managed by a registered
  investment adviser, restorable within five business days).
- SIFMA Prime Brokerage Agreement, Form 150 (DTC ID confirmation by 12:00 noon ET on
  T+1; disaffirmance deadlines; disaffirmed trades remain the executing broker's).
- FINRA Rule 4210(g), portfolio margin; 12 CFR 220 (Regulation T).
- FIA Tech EGUS / FIA International Uniform Give-Up Agreement (futures give-ups).
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
VALID_SIDES = (SIDE_BUY, SIDE_SELL)

#: Status values reported by :meth:`consolidate_venue_executions`.
STATUS_NO_EXECUTIONS = "NO_EXECUTIONS_PROVIDED"
STATUS_SUCCESSFUL = "CONSOLIDATION_SUCCESSFUL"
STATUS_LATE_GIVEUP = "CONSOLIDATION_SUCCESSFUL_LATE_GIVEUP"


class DuplicateExecutionError(ValueError):
    """Raised when an ``execution_id`` is submitted for give-up more than once."""


class MixedInstrumentError(ValueError):
    """Raised when one symbol is submitted under two currencies or two multipliers."""


def _require_finite(value: float, label: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number, got {value!r}.")
    return numeric


def _require_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string, got {value!r}.")
    return value.strip()


def _require_currency(code: str, label: str) -> str:
    text = _require_identifier(code, label).upper()
    if len(text) != 3 or not text.isalpha():
        raise ValueError(
            f"{label} must be a three-letter ISO 4217 alphabetic code, got {code!r}."
        )
    return text


def _require_trade_date(value: str, label: str) -> str:
    """Validate an ISO 8601 ``YYYY-MM-DD`` trade date and return it normalized."""
    text = _require_identifier(value, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"{label} must be an ISO 8601 date (YYYY-MM-DD), got {value!r}."
        ) from exc
    return parsed.isoformat()


def _require_aware(moment: datetime, label: str) -> datetime:
    if not isinstance(moment, datetime):
        raise ValueError(f"{label} must be a datetime, got {moment!r}.")
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{label} must be timezone-aware -- a give-up cut-off compared against a "
            "naive timestamp silently assumes the reader's local zone."
        )
    return moment


@dataclass
class VenueExecution:
    """
    One fill executed away from the prime broker, to be given up for PB clearing.

    Attributes:
        execution_id: Unique per fill. Reused ids are rejected, because the PB claims
            each give-up instruction independently.
        executing_broker: Broker that executed and will give the trade up.
        venue_id: Execution venue (exchange, ECN, ATS) the fill printed on.
        symbol: Instrument identifier, upper-cased on construction.
        side: Exactly ``'BUY'`` or ``'SELL'`` (case-insensitive on input).
        quantity: **Unsigned** shares/contracts, strictly positive.
        price: Fill price per unit in ``currency``; zero allowed, negative is not.
        trade_date: ISO 8601 ``YYYY-MM-DD`` execution date, used to select the
            give-up cut-off.
        currency: ISO 4217 code the fill is priced in. Part of the netting key.
        contract_multiplier: Units of underlying per contract (100 for a standard
            OCC equity option, 50 for CME E-mini S&P 500). Part of the netting key;
            the 1.0 default is correct for cash equities and spot only.
        executing_broker_commission: Total third-party execution commission for this
            fill, in ``currency``. Separate from the PB clearing fee -- a give-up
            costs both.
    """

    execution_id: str
    executing_broker: str
    venue_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    trade_date: str
    currency: str = "USD"
    contract_multiplier: float = 1.0
    executing_broker_commission: float = 0.0

    def __post_init__(self) -> None:
        self.execution_id = _require_identifier(self.execution_id, "execution_id")
        self.executing_broker = _require_identifier(
            self.executing_broker, "executing_broker"
        )
        self.venue_id = _require_identifier(self.venue_id, "venue_id")
        self.symbol = _require_identifier(self.symbol, "symbol").upper()

        side = _require_identifier(self.side, "side").upper()
        if side not in VALID_SIDES:
            raise ValueError(
                f"side must be one of {VALID_SIDES}, got {self.side!r}. A value that "
                "is not recognised is never assumed to be a sell."
            )
        self.side = side

        self.quantity = _require_finite(self.quantity, "quantity")
        if self.quantity <= 0.0:
            raise ValueError(
                f"quantity must be > 0 (direction is carried by side), got "
                f"{self.quantity} for {self.execution_id}."
            )

        self.price = _require_finite(self.price, "price")
        if self.price < 0.0:
            raise ValueError(
                f"price must be >= 0, got {self.price} for {self.execution_id}."
            )

        self.trade_date = _require_trade_date(self.trade_date, "trade_date")
        self.currency = _require_currency(self.currency, "currency")

        self.contract_multiplier = _require_finite(
            self.contract_multiplier, "contract_multiplier"
        )
        if self.contract_multiplier <= 0.0:
            raise ValueError(
                f"contract_multiplier must be > 0, got {self.contract_multiplier}."
            )

        self.executing_broker_commission = _require_finite(
            self.executing_broker_commission, "executing_broker_commission"
        )
        if self.executing_broker_commission < 0.0:
            raise ValueError(
                "executing_broker_commission must be >= 0, got "
                f"{self.executing_broker_commission} for {self.execution_id}."
            )

    @property
    def instrument_key(self) -> str:
        """Netting key: same ticker in another currency is another instrument."""
        return f"{self.symbol}.{self.currency}"

    @property
    def signed_quantity(self) -> float:
        """Quantity signed by side: positive for BUY, negative for SELL."""
        return self.quantity if self.side == SIDE_BUY else -self.quantity

    @property
    def notional(self) -> float:
        """Unsigned traded notional in ``currency``, multiplier applied."""
        return self.quantity * self.price * self.contract_multiplier


@dataclass
class PrimeBrokerSpec:
    """
    The prime broker the flow is consolidated into.

    Attributes:
        prime_broker_name: PB legal/short name recorded on every give-up instruction.
        pb_account_id: Account the give-ups are claimed into.
        clearing_fee_per_unit: PB clearing fee per share/contract, charged in
            ``fee_currency`` regardless of the currency the fill is priced in.
        fee_currency: ISO 4217 code ``clearing_fee_per_unit`` is denominated in.
    """

    prime_broker_name: str
    pb_account_id: str
    clearing_fee_per_unit: float = 0.0005
    fee_currency: str = "USD"

    def __post_init__(self) -> None:
        self.prime_broker_name = _require_identifier(
            self.prime_broker_name, "prime_broker_name"
        )
        self.pb_account_id = _require_identifier(self.pb_account_id, "pb_account_id")
        self.clearing_fee_per_unit = _require_finite(
            self.clearing_fee_per_unit, "clearing_fee_per_unit"
        )
        if self.clearing_fee_per_unit < 0.0:
            raise ValueError(
                f"clearing_fee_per_unit must be >= 0, got {self.clearing_fee_per_unit}."
            )
        self.fee_currency = _require_currency(self.fee_currency, "fee_currency")


@dataclass
class NettedInstrumentPosition:
    """
    One ``(symbol, currency)`` instrument netted across every executing broker/venue.

    ``offset_ratio_pct`` is the share of gross traded quantity that cancels internally
    once the flow sits in one PB account. It is an execution/operational statistic,
    not a margin figure.
    """

    symbol: str
    currency: str
    contract_multiplier: float
    net_quantity: float
    gross_quantity: float
    buy_quantity: float
    sell_quantity: float
    gross_notional: float
    vwap: float
    residual_notional_at_vwap: float
    offset_ratio_pct: float
    execution_count: int
    broker_breakdown: Dict[str, float] = field(default_factory=dict)
    venue_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def is_internally_offset(self) -> bool:
        """True when buys and sells partially cancel -- gross exceeds |net|."""
        return self.gross_quantity > abs(self.net_quantity)


@dataclass
class PBConsolidationReport:
    """
    Result of one consolidation run. All monetary figures are per currency; nothing
    in this report is summed across currencies.
    """

    prime_broker_name: str
    pb_account_id: str
    total_executions_consolidated: int
    netted_positions: Dict[str, NettedInstrumentPosition]
    gross_notional_by_currency: Dict[str, float]
    residual_notional_by_currency: Dict[str, float]
    notional_offset_pct_by_currency: Dict[str, float]
    total_clearing_fees: float
    clearing_fee_currency: str
    executing_broker_commissions_by_currency: Dict[str, float]
    giveup_payload: List[Dict[str, Any]]
    trade_dates: List[str]
    late_giveup_execution_ids: List[str]
    status: str
    audit_notes: str

    def net_quantity(self, symbol: str, currency: str = "USD") -> float:
        """Signed net quantity for one instrument; 0.0 if it was not traded."""
        key = f"{symbol.strip().upper()}.{currency.strip().upper()}"
        position = self.netted_positions.get(key)
        return position.net_quantity if position is not None else 0.0


class PrimeBrokerageMultiVenueConsolidationEngine:
    """
    Consolidates multi-venue fills into one prime-brokerage give-up batch.

    The engine remembers which ``execution_id`` values it has already emitted a
    give-up instruction for, so a queue replayed after a reconnect raises instead of
    double-claiming. Registration is atomic: a batch that fails validation registers
    nothing and can be corrected and resubmitted.
    """

    def __init__(
        self,
        pb_spec: PrimeBrokerSpec,
        *,
        enforce_cross_batch_idempotency: bool = True,
    ) -> None:
        if not isinstance(pb_spec, PrimeBrokerSpec):
            raise ValueError(
                "pb_spec must be a PrimeBrokerSpec -- the PB name and account id are "
                "written onto every give-up instruction and are not defaulted."
            )
        self.pb_spec = pb_spec
        self.enforce_cross_batch_idempotency = bool(enforce_cross_batch_idempotency)
        self._submitted_execution_ids: set = set()

    def submitted_execution_ids(self) -> frozenset:
        """Execution ids already given up through this engine instance."""
        return frozenset(self._submitted_execution_ids)

    def reset_submitted_execution_ids(self) -> None:
        """Clear the idempotency ledger (e.g. at a new trade-date boundary)."""
        self._submitted_execution_ids = set()

    def consolidate_venue_executions(
        self,
        executions: Sequence[VenueExecution],
        *,
        submitted_at: Optional[datetime] = None,
        giveup_cutoffs: Optional[Mapping[str, datetime]] = None,
    ) -> PBConsolidationReport:
        """
        Net the fills, price the fee legs, and build the PB give-up payload.

        Args:
            executions: Validated fills executed away from the prime broker.
            submitted_at: Timezone-aware moment the batch is being handed to the PB.
                Required when ``giveup_cutoffs`` is supplied.
            giveup_cutoffs: Timezone-aware submission deadline per ISO trade date.
                Deadlines are venue/clearing-house specific and are never defaulted;
                when supplied, every trade date in the batch must be covered, so a
                missing entry fails loudly rather than skipping the check.

        Returns:
            A :class:`PBConsolidationReport`.

        Raises:
            DuplicateExecutionError: An ``execution_id`` repeats within the batch, or
                was already given up through this engine.
            MixedInstrumentError: One symbol appears under two currencies or two
                contract multipliers.
            ValueError: Malformed input, or a cut-off configuration that would leave
                part of the batch unchecked.
        """
        executions = list(executions)
        for item in executions:
            if not isinstance(item, VenueExecution):
                raise ValueError(
                    f"executions must contain VenueExecution instances, got {item!r}."
                )

        if not executions:
            return self._empty_report()

        self._assert_unique(executions)
        trade_dates = sorted({ex.trade_date for ex in executions})
        cutoff_by_date = self._validate_cutoffs(
            trade_dates, submitted_at, giveup_cutoffs
        )

        positions = self._net_positions(executions)
        gross_by_ccy: Dict[str, float] = {}
        commissions_by_ccy: Dict[str, float] = {}
        total_clearing_fees = 0.0
        late_ids: List[str] = []
        payload: List[Dict[str, Any]] = []

        for ex in executions:
            gross_by_ccy[ex.currency] = gross_by_ccy.get(ex.currency, 0.0) + ex.notional
            if ex.executing_broker_commission:
                commissions_by_ccy[ex.currency] = (
                    commissions_by_ccy.get(ex.currency, 0.0)
                    + ex.executing_broker_commission
                )
            clearing_fee = ex.quantity * self.pb_spec.clearing_fee_per_unit
            total_clearing_fees += clearing_fee

            is_late = False
            if cutoff_by_date is not None:
                is_late = submitted_at > cutoff_by_date[ex.trade_date]
                if is_late:
                    late_ids.append(ex.execution_id)

            payload.append(
                {
                    "give_up_id": f"GU_{ex.execution_id}",
                    "execution_id": ex.execution_id,
                    "pb_account": self.pb_spec.pb_account_id,
                    "prime_broker": self.pb_spec.prime_broker_name,
                    "executing_broker": ex.executing_broker,
                    "venue": ex.venue_id,
                    "symbol": ex.symbol,
                    "side": ex.side,
                    "quantity": ex.quantity,
                    "price": ex.price,
                    "contract_multiplier": ex.contract_multiplier,
                    "currency": ex.currency,
                    "notional": round(ex.notional, 2),
                    "executing_broker_commission": round(
                        ex.executing_broker_commission, 4
                    ),
                    "pb_clearing_fee": round(clearing_fee, 4),
                    "pb_clearing_fee_currency": self.pb_spec.fee_currency,
                    "trade_date": ex.trade_date,
                    "submitted_after_cutoff": is_late,
                }
            )

        residual_by_ccy: Dict[str, float] = {}
        for position in positions.values():
            residual_by_ccy[position.currency] = (
                residual_by_ccy.get(position.currency, 0.0)
                + position.residual_notional_at_vwap
            )

        offset_pct_by_ccy = {
            ccy: round((1.0 - residual_by_ccy.get(ccy, 0.0) / gross) * 100.0, 4)
            for ccy, gross in gross_by_ccy.items()
            if gross > 0.0
        }

        status = STATUS_LATE_GIVEUP if late_ids else STATUS_SUCCESSFUL
        gross_summary = {k: round(v, 2) for k, v in gross_by_ccy.items()}
        commission_summary = {k: round(v, 4) for k, v in commissions_by_ccy.items()}
        notes = (
            f"PB CONSOLIDATION [{self.pb_spec.prime_broker_name} "
            f"({self.pb_spec.pb_account_id})]: executions={len(executions)}, "
            f"instruments={len(positions)}, trade_dates={','.join(trade_dates)}, "
            f"gross_notional={gross_summary}, "
            f"notional_offset_pct={offset_pct_by_ccy}, "
            f"pb_clearing_fees={round(total_clearing_fees, 4)} "
            f"{self.pb_spec.fee_currency}, "
            f"executing_broker_commissions={commission_summary}, "
            f"late_giveups={len(late_ids)}. Offset percentages are traded-notional "
            "netting ratios, not margin savings."
        )

        if late_ids:
            logger.warning(
                "%d give-up instruction(s) built after the submission cut-off: %s",
                len(late_ids),
                ",".join(late_ids),
            )
        logger.info(notes)

        if self.enforce_cross_batch_idempotency:
            self._submitted_execution_ids.update(ex.execution_id for ex in executions)

        return PBConsolidationReport(
            prime_broker_name=self.pb_spec.prime_broker_name,
            pb_account_id=self.pb_spec.pb_account_id,
            total_executions_consolidated=len(executions),
            netted_positions=positions,
            gross_notional_by_currency=gross_summary,
            residual_notional_by_currency={
                k: round(v, 2) for k, v in residual_by_ccy.items()
            },
            notional_offset_pct_by_currency=offset_pct_by_ccy,
            total_clearing_fees=round(total_clearing_fees, 4),
            clearing_fee_currency=self.pb_spec.fee_currency,
            executing_broker_commissions_by_currency=commission_summary,
            giveup_payload=payload,
            trade_dates=trade_dates,
            late_giveup_execution_ids=late_ids,
            status=status,
            audit_notes=notes,
        )

    def _empty_report(self) -> PBConsolidationReport:
        notes = "No venue executions submitted for PB consolidation."
        logger.info(notes)
        return PBConsolidationReport(
            prime_broker_name=self.pb_spec.prime_broker_name,
            pb_account_id=self.pb_spec.pb_account_id,
            total_executions_consolidated=0,
            netted_positions={},
            gross_notional_by_currency={},
            residual_notional_by_currency={},
            notional_offset_pct_by_currency={},
            total_clearing_fees=0.0,
            clearing_fee_currency=self.pb_spec.fee_currency,
            executing_broker_commissions_by_currency={},
            giveup_payload=[],
            trade_dates=[],
            late_giveup_execution_ids=[],
            status=STATUS_NO_EXECUTIONS,
            audit_notes=notes,
        )

    def _assert_unique(self, executions: Iterable[VenueExecution]) -> None:
        seen: set = set()
        for ex in executions:
            if ex.execution_id in seen:
                raise DuplicateExecutionError(
                    f"execution_id {ex.execution_id!r} appears twice in one batch; "
                    "the PB would claim the fill twice."
                )
            seen.add(ex.execution_id)

        if self.enforce_cross_batch_idempotency:
            replayed = sorted(seen & self._submitted_execution_ids)
            if replayed:
                raise DuplicateExecutionError(
                    f"execution_id(s) {replayed} were already given up through this "
                    "engine. Resubmitting after a reconnect double-claims the fills; "
                    "reconcile against the PB before retrying."
                )

    @staticmethod
    def _validate_cutoffs(
        trade_dates: Sequence[str],
        submitted_at: Optional[datetime],
        giveup_cutoffs: Optional[Mapping[str, datetime]],
    ) -> Optional[Dict[str, datetime]]:
        if giveup_cutoffs is None:
            if submitted_at is not None:
                raise ValueError(
                    "submitted_at was supplied without giveup_cutoffs, so no "
                    "timeliness check would run. Supply both or neither."
                )
            return None

        if submitted_at is None:
            raise ValueError("giveup_cutoffs requires submitted_at to compare against.")
        _require_aware(submitted_at, "submitted_at")

        resolved: Dict[str, datetime] = {}
        for trade_date in trade_dates:
            if trade_date not in giveup_cutoffs:
                raise ValueError(
                    f"giveup_cutoffs has no deadline for trade date {trade_date!r}; "
                    "an uncovered trade date would be silently reported as on time."
                )
            resolved[trade_date] = _require_aware(
                giveup_cutoffs[trade_date], f"giveup_cutoffs[{trade_date!r}]"
            )
        return resolved

    @staticmethod
    def _net_positions(
        executions: Sequence[VenueExecution],
    ) -> Dict[str, NettedInstrumentPosition]:
        symbol_currencies: Dict[str, set] = {}
        for ex in executions:
            symbol_currencies.setdefault(ex.symbol, set()).add(ex.currency)
        for symbol, currencies in sorted(symbol_currencies.items()):
            if len(currencies) > 1:
                raise MixedInstrumentError(
                    f"symbol {symbol!r} was submitted in currencies "
                    f"{sorted(currencies)}. The same ticker priced in two currencies "
                    "is two instruments and must not be netted."
                )

        buckets: Dict[str, List[VenueExecution]] = {}
        for ex in executions:
            buckets.setdefault(ex.instrument_key, []).append(ex)

        positions: Dict[str, NettedInstrumentPosition] = {}
        for key, fills in buckets.items():
            multipliers = {ex.contract_multiplier for ex in fills}
            if len(multipliers) > 1:
                raise MixedInstrumentError(
                    f"{key} was submitted with contract multipliers "
                    f"{sorted(multipliers)}. An adjusted contract delivering a "
                    "non-standard number of units is a separate instrument."
                )
            multiplier = fills[0].contract_multiplier

            buy_qty = sum(ex.quantity for ex in fills if ex.side == SIDE_BUY)
            sell_qty = sum(ex.quantity for ex in fills if ex.side == SIDE_SELL)
            gross_qty = buy_qty + sell_qty
            net_qty = buy_qty - sell_qty
            gross_notional = sum(ex.notional for ex in fills)
            # VWAP is quoted per unit of underlying, so the multiplier that was
            # applied in `notional` is divided back out here.
            vwap = gross_notional / (gross_qty * multiplier)
            residual = abs(net_qty) * vwap * multiplier

            broker_breakdown: Dict[str, float] = {}
            venue_breakdown: Dict[str, float] = {}
            for ex in fills:
                broker_breakdown[ex.executing_broker] = (
                    broker_breakdown.get(ex.executing_broker, 0.0) + ex.signed_quantity
                )
                venue_breakdown[ex.venue_id] = (
                    venue_breakdown.get(ex.venue_id, 0.0) + ex.signed_quantity
                )

            positions[key] = NettedInstrumentPosition(
                symbol=fills[0].symbol,
                currency=fills[0].currency,
                contract_multiplier=multiplier,
                net_quantity=net_qty,
                gross_quantity=gross_qty,
                buy_quantity=buy_qty,
                sell_quantity=sell_qty,
                gross_notional=round(gross_notional, 2),
                vwap=vwap,
                residual_notional_at_vwap=residual,
                offset_ratio_pct=round((1.0 - abs(net_qty) / gross_qty) * 100.0, 4),
                execution_count=len(fills),
                broker_breakdown=broker_breakdown,
                venue_breakdown=venue_breakdown,
            )
        return positions
