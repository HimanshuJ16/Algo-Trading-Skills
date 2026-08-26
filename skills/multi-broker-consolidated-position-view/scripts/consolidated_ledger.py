"""
multi-broker-consolidated-position-view: multi-broker position aggregator, symbol/FX
normalizer, and position reconciliation audit engine.

Takes point-in-time position snapshots from several broker adapters, resolves
broker-specific tickers to canonical symbols, converts every leg into a single base
currency, and produces one netted + grossed view per canonical symbol, plus a
reconciliation audit against the strategy's own target ledger.

Per-leg valuation (all legs carry a *signed* quantity: negative = short):

    market_value_ccy = quantity * current_price * contract_multiplier
    cost_basis_ccy   = quantity * average_cost  * cost_multiplier
    <value>_base     = <value>_ccy * fx_rates[ccy]

``fx_rates[ccy]`` is defined as **units of base currency per one unit of ccy**, so
with ``base_currency="USD"`` a EUR rate of 1.08 means 1 EUR = 1.08 USD. The base
currency's own rate must be present and exactly 1.0.

This module **fails closed**. There is no default FX table, no 1:1 fallback for an
unknown currency, and no silent coercion of malformed feed rows: a consolidated risk
view that quietly reports the wrong number is worse than one that refuses to produce
a number at all. It is the layer `cross-account-aggregate-risk-view` delegates to for
FX conversion and symbol normalization, so its output feeds firm-wide gross-market-
value caps -- a silent unit error here becomes a silent exposure-limit error there.

Broker feed conventions this module deliberately does not guess at
-----------------------------------------------------------------

- **Contract multipliers are never inferred.** A standard OCC equity option covers
  100 shares (OCC equity options product specification), and index futures carry
  their own multipliers (e.g. CME E-mini S&P 500 at 50x). Valuing an option position
  at ``quantity * price`` understates it 100-fold. The multiplier cannot be derived
  from a symbol string, and corporate actions make it unsafe to assume even the
  standard value: an adjusted option contract can deliver something other than 100
  shares while *keeping* a 100 premium multiplier. The adapter must supply
  ``contract_multiplier`` explicitly; the 1.0 default is correct for cash equities,
  spot FX and spot crypto only.

- **Cost basis is not reported on a common convention across brokers, and this module
  cannot reconcile that.** Two documented examples: IBKR's ``avgCost`` reflects the
  contract multiplier for derivative positions (unlike its per-share ``avgPrice``),
  which is why ``average_cost_includes_multiplier`` exists; and Alpaca computes
  ``avg_entry_price`` by weighted average for intraday positions but by compressed
  FIFO end-of-day. Aggregating those into one ``weighted_avg_cost_base`` produces a
  blended figure whose per-broker inputs were computed under different rules. Treat
  it as an indicative net basis, not as a tax lot or an accounting record -- see
  `fifo-vs-specific-lot-tax-accounting-methods` for lot-level treatment.

- **One broker can report two legs for one symbol.** Binance USD-M futures in hedge
  mode returns separate LONG and SHORT rows for the same symbol in the same account.
  Both are ingested and both are netted; ``broker_breakdown`` therefore holds the
  *net* quantity per broker, and intra-broker offsetting shows up in
  ``gross_quantity`` / ``is_internally_offset`` rather than in the breakdown.

Sources
-------
- OCC, Equity Options product specifications (standard contract = 100 shares);
  OIC, "Splits, Mergers, Spinoffs & Bankruptcies" (adjusted contracts may deliver
  other than 100 shares while retaining a 100 multiplier).
- Interactive Brokers Web API, Portfolio Positions (``avgCost`` reflects the
  multiplier for derivatives; ``avgPrice`` does not).
- Alpaca, "Position Average Entry Price Calculation" (weighted average intraday,
  compressed FIFO end-of-day).
- Binance USD-M Futures, Position Information (``positionAmt`` negative = short;
  hedge mode returns separate LONG/SHORT rows).
- ISO 4217 (currency codes; maintenance agency SIX Financial Information) for the
  three-letter alphabetic code form validated here.
"""
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: ISO 4217 alphabetic codes are three letters. This is a *format* check only --
#: validating membership of the live code list would require a data dependency, so a
#: well-formed but retired or fictional code passes here and fails later at the FX
#: table lookup instead.
_ISO_4217_ALPHA = re.compile(r"^[A-Z]{3}$")

#: Default absolute tolerance, in quantity units, below which a reconciliation
#: difference is treated as float noise rather than a break. Sized for share-quantity
#: feeds; instruments quoted to 8 decimals (crypto) or held in small fractional size
#: need a tighter value via ``quantity_tolerance`` / ``symbol_tolerances``.
DEFAULT_QUANTITY_TOLERANCE = 1e-5


class MissingFxRateError(LookupError):
    """Raised when a position is denominated in a currency absent from the FX table."""


class StaleSnapshotError(ValueError):
    """Raised when a broker snapshot or the FX table is older than the allowed age."""


class UnmappedSymbolError(LookupError):
    """Raised in strict mode when a broker symbol has no canonical mapping."""


class DiscrepancyKind(str, Enum):
    """
    Classification of a reconciliation break. The three cases call for different
    operational responses, so they are not collapsed into one generic message.
    """

    #: Both sides hold the symbol but the quantities differ (partial fill, missed fill).
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    #: The target ledger expects a position that no broker reports (unfilled order, or
    #: an externally-driven exit such as a broker-side forced close-out).
    MISSING_AT_BROKER = "MISSING_AT_BROKER"
    #: A broker reports a position the target ledger does not expect at all (rogue
    #: fill, manual trade, or a symbol-mapping failure creating a phantom symbol).
    UNEXPECTED_AT_BROKER = "UNEXPECTED_AT_BROKER"


def _require_finite(value: float, label: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number, got {value!r}.")
    return numeric


def _require_currency(code: str, label: str) -> str:
    if not isinstance(code, str):
        raise ValueError(f"{label} must be a string ISO 4217 code, got {code!r}.")
    normalized = code.strip().upper()
    if not _ISO_4217_ALPHA.match(normalized):
        raise ValueError(
            f"{label} must be a three-letter ISO 4217 alphabetic code, got {code!r}."
        )
    return normalized


def _require_aware(moment: datetime, label: str) -> datetime:
    if not isinstance(moment, datetime):
        raise ValueError(f"{label} must be a datetime, got {moment!r}.")
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{label} must be timezone-aware -- a naive timestamp cannot be compared "
            "across brokers in different venues/zones without silently assuming one."
        )
    return moment


@dataclass
class RawBrokerPosition:
    """
    One position leg as reported by one broker adapter, before normalization.

    Validated on construction: a malformed feed row raises here rather than
    propagating a NaN or a nonsensical price into a consolidated risk number.

    Attributes:
        broker_name: Adapter/account identifier the leg came from.
        broker_symbol: Broker-native ticker, mapped to a canonical symbol later.
        quantity: **Signed** contract/share count. Negative = short, matching the
            convention of IBKR, Alpaca (``qty``) and Binance (``positionAmt``).
        average_cost: Entry cost per contract in ``currency``. See
            ``average_cost_includes_multiplier``.
        current_price: Mark price per unit in ``currency``. Must be >= 0; zero is
            allowed (a worthless expiring option), negative is not.
        currency: ISO 4217 alphabetic code the prices are quoted in.
        contract_multiplier: Units of underlying per contract (100 for a standard
            equity option, 50 for CME E-mini S&P 500). Must be supplied explicitly
            for derivatives; the 1.0 default is correct for cash equities and spot.
        average_cost_includes_multiplier: True when the broker already folded the
            multiplier into ``average_cost`` (IBKR ``avgCost`` for derivatives).
            Leaving this False on such a feed inflates cost basis by the multiplier
            and can invert the sign of reported unrealized P&L.
        as_of: Timezone-aware time this snapshot was taken at the broker. Required
            when the ledger enforces ``max_snapshot_age``.
    """

    broker_name: str
    broker_symbol: str
    quantity: float
    average_cost: float
    current_price: float
    currency: str = "USD"
    contract_multiplier: float = 1.0
    average_cost_includes_multiplier: bool = False
    as_of: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not str(self.broker_name).strip():
            raise ValueError("broker_name must be a non-empty string.")
        if not str(self.broker_symbol).strip():
            raise ValueError("broker_symbol must be a non-empty string.")
        self.broker_name = str(self.broker_name).strip()
        self.broker_symbol = str(self.broker_symbol).strip()

        self.quantity = _require_finite(self.quantity, "quantity")
        self.average_cost = _require_finite(self.average_cost, "average_cost")
        self.current_price = _require_finite(self.current_price, "current_price")
        if self.current_price < 0.0:
            raise ValueError(
                f"current_price must be >= 0, got {self.current_price} for "
                f"{self.broker_symbol} at {self.broker_name}."
            )

        self.contract_multiplier = _require_finite(
            self.contract_multiplier, "contract_multiplier"
        )
        if self.contract_multiplier <= 0.0:
            raise ValueError(
                f"contract_multiplier must be > 0, got {self.contract_multiplier}."
            )

        self.currency = _require_currency(self.currency, "currency")
        if self.as_of is not None:
            self.as_of = _require_aware(self.as_of, "as_of")

    @property
    def cost_multiplier(self) -> float:
        """Multiplier to apply to ``average_cost`` (1.0 if the broker already did)."""
        return 1.0 if self.average_cost_includes_multiplier else self.contract_multiplier


@dataclass
class ConsolidatedPosition:
    """
    One canonical symbol's netted view across every broker holding it.

    ``total_market_value_base`` is the *signed* net value and can be near zero for a
    fully offset book; ``gross_market_value_base`` sums the absolute value of each leg
    and is the exposure figure a gross-market-value cap should consume.
    """

    canonical_symbol: str
    net_quantity: float
    gross_quantity: float
    total_market_value_base: float
    gross_market_value_base: float
    total_cost_basis_base: float
    weighted_avg_cost_base: Optional[float]
    unrealized_pnl_base: float
    broker_breakdown: Dict[str, float]  # broker_name -> net qty at that broker
    base_currency: str = "USD"
    leg_count: int = 0
    currencies: Tuple[str, ...] = ()
    is_internally_offset: bool = False
    oldest_snapshot_as_of: Optional[datetime] = None
    snapshot_skew_seconds: Optional[float] = None


@dataclass
class ReconciliationDiscrepancy:
    canonical_symbol: str
    expected_qty: float
    actual_broker_qty: float
    discrepancy_qty: float
    kind: DiscrepancyKind
    message: str


class MultiBrokerConsolidatedLedger:
    """
    Aggregates multi-broker raw position snapshots into a consolidated base-currency
    view and audits them against the strategy's target ledger.

    Stateless with respect to positions: every call consolidates exactly the list it
    is given, so results are reproducible and two callers cannot contaminate each
    other. Configuration (symbol map, FX table) is instance state and is validated
    whenever it is mutated.
    """

    def __init__(
        self,
        base_currency: str = "USD",
        symbol_map: Optional[Mapping[str, str]] = None,
        fx_rates: Optional[Mapping[str, float]] = None,
        *,
        strict_symbol_mapping: bool = False,
        quantity_tolerance: float = DEFAULT_QUANTITY_TOLERANCE,
        symbol_tolerances: Optional[Mapping[str, float]] = None,
        fx_rates_as_of: Optional[datetime] = None,
        max_snapshot_age: Optional[timedelta] = None,
    ):
        """
        Args:
            base_currency: Reporting currency. Every ``*_base`` field is denominated
                in it.
            symbol_map: broker_symbol -> canonical_symbol.
            fx_rates: currency -> units of ``base_currency`` per one unit of that
                currency. Required; there is no default table, because a hardcoded
                rate produces a confidently wrong exposure number.
                ``fx_rates[base_currency]`` must be present and exactly 1.0.
            strict_symbol_mapping: When True an unmapped broker symbol raises instead
                of falling back to the upper-cased raw ticker. Recommended in
                production: the fallback is what turns ``AAPL`` and ``AAPL.US`` into
                two separate canonical symbols.
            quantity_tolerance: Absolute quantity tolerance for reconciliation breaks
                and for treating a net position as flat.
            symbol_tolerances: Per-canonical-symbol overrides of ``quantity_tolerance``.
            fx_rates_as_of: Timezone-aware time the FX table was sampled. Checked
                against ``max_snapshot_age`` when both are set.
            max_snapshot_age: When set, every position must carry an ``as_of`` within
                this age of the valuation time, or consolidation raises.
        """
        self.base_currency = _require_currency(base_currency, "base_currency")

        if fx_rates is None:
            raise ValueError(
                "fx_rates is required. This engine ships no default rate table: a "
                "hardcoded rate is stale the moment it is written, and silently "
                "valuing a foreign-currency position at a stale or 1:1 rate misstates "
                "consolidated exposure without any error surfacing."
            )
        self.fx_rates: Dict[str, float] = {}
        for code, rate in fx_rates.items():
            self.set_fx_rate(code, rate)

        if self.base_currency not in self.fx_rates:
            raise ValueError(
                f"fx_rates must contain the base currency {self.base_currency!r} "
                "with a rate of exactly 1.0."
            )
        if self.fx_rates[self.base_currency] != 1.0:
            raise ValueError(
                f"fx_rates[{self.base_currency!r}] must be exactly 1.0 (it is the "
                f"base currency), got {self.fx_rates[self.base_currency]}."
            )

        self.symbol_map: Dict[str, str] = {}
        for broker_symbol, canonical in (symbol_map or {}).items():
            self.register_symbol_mapping(broker_symbol, canonical)

        self.strict_symbol_mapping = bool(strict_symbol_mapping)

        self.quantity_tolerance = _require_finite(
            quantity_tolerance, "quantity_tolerance"
        )
        if self.quantity_tolerance < 0.0:
            raise ValueError(
                f"quantity_tolerance must be >= 0, got {self.quantity_tolerance}."
            )

        self.symbol_tolerances: Dict[str, float] = {}
        for symbol, tolerance in (symbol_tolerances or {}).items():
            tol = _require_finite(tolerance, f"symbol_tolerances[{symbol!r}]")
            if tol < 0.0:
                raise ValueError(
                    f"symbol_tolerances[{symbol!r}] must be >= 0, got {tol}."
                )
            self.symbol_tolerances[str(symbol).strip().upper()] = tol

        self.fx_rates_as_of = (
            _require_aware(fx_rates_as_of, "fx_rates_as_of")
            if fx_rates_as_of is not None
            else None
        )

        if max_snapshot_age is not None:
            if not isinstance(max_snapshot_age, timedelta):
                raise ValueError(
                    f"max_snapshot_age must be a timedelta, got {max_snapshot_age!r}."
                )
            if max_snapshot_age <= timedelta(0):
                raise ValueError(
                    f"max_snapshot_age must be positive, got {max_snapshot_age}."
                )
        self.max_snapshot_age = max_snapshot_age

    # ------------------------------------------------------------------ config

    def register_symbol_mapping(self, broker_symbol: str, canonical_symbol: str) -> None:
        """Registers a broker_symbol -> canonical_symbol normalization mapping."""
        broker = str(broker_symbol).strip()
        canonical = str(canonical_symbol).strip().upper()
        if not broker:
            raise ValueError("broker_symbol must be a non-empty string.")
        if not canonical:
            raise ValueError("canonical_symbol must be a non-empty string.")
        existing = self.symbol_map.get(broker)
        if existing is not None and existing != canonical:
            logger.warning(
                "Symbol mapping for %r changed from %r to %r; consolidated views "
                "produced before and after this change are not comparable.",
                broker, existing, canonical,
            )
        self.symbol_map[broker] = canonical

    def set_fx_rate(self, currency: str, rate: float) -> None:
        """
        Sets ``currency`` -> base-currency rate (base units per one unit of currency).

        Validated here rather than allowing direct dict mutation, so a NaN, zero, or
        negative rate cannot enter the table and silently zero out or sign-flip a
        position's market value.
        """
        code = _require_currency(currency, "currency")
        numeric = _require_finite(rate, f"fx_rates[{code!r}]")
        if numeric <= 0.0:
            raise ValueError(f"fx_rates[{code!r}] must be > 0, got {numeric}.")
        self.fx_rates[code] = numeric

    # -------------------------------------------------------------- internals

    def _to_canonical(self, broker_symbol: str) -> str:
        mapped = self.symbol_map.get(broker_symbol)
        if mapped is not None:
            return mapped
        if self.strict_symbol_mapping:
            raise UnmappedSymbolError(
                f"No canonical mapping registered for broker symbol "
                f"{broker_symbol!r}. Falling back to the raw ticker would split one "
                "economic position across two canonical symbols (e.g. 'AAPL' and "
                "'AAPL.US'), understating netting and overstating symbol count."
            )
        return broker_symbol.strip().upper()

    def _to_base(self, amount: float, currency: str) -> float:
        try:
            rate = self.fx_rates[currency]
        except KeyError:
            raise MissingFxRateError(
                f"No FX rate for {currency!r} -> {self.base_currency}. Refusing to "
                "value this position: assuming 1:1 would misstate its contribution to "
                "consolidated exposure by the full size of the exchange rate."
            ) from None
        return amount * rate

    def _tolerance_for(self, canonical_symbol: str) -> float:
        return self.symbol_tolerances.get(canonical_symbol, self.quantity_tolerance)

    def _check_staleness(
        self,
        raw_positions: Sequence[RawBrokerPosition],
        valuation_time: Optional[datetime],
    ) -> None:
        if self.max_snapshot_age is None:
            return
        if valuation_time is None:
            raise ValueError(
                "valuation_time is required when max_snapshot_age is configured -- "
                "snapshot age cannot be judged against an unspecified 'now'."
            )
        valuation_time = _require_aware(valuation_time, "valuation_time")

        for pos in raw_positions:
            if pos.as_of is None:
                raise StaleSnapshotError(
                    f"Position {pos.broker_symbol!r} from {pos.broker_name!r} carries "
                    "no as_of timestamp, so its age cannot be verified against "
                    f"max_snapshot_age={self.max_snapshot_age}."
                )
            age = valuation_time - pos.as_of
            if age < timedelta(0):
                raise StaleSnapshotError(
                    f"Position {pos.broker_symbol!r} from {pos.broker_name!r} is "
                    f"stamped {pos.as_of.isoformat()}, ahead of valuation_time "
                    f"{valuation_time.isoformat()} -- clock skew between the adapter "
                    "host and this process invalidates the age check."
                )
            if age > self.max_snapshot_age:
                raise StaleSnapshotError(
                    f"Position {pos.broker_symbol!r} from {pos.broker_name!r} is "
                    f"{age} old, exceeding max_snapshot_age={self.max_snapshot_age}. "
                    "Consolidating a stale leg with live legs produces a view that "
                    "was never simultaneously true at any broker."
                )

        if self.fx_rates_as_of is not None:
            fx_age = valuation_time - self.fx_rates_as_of
            if fx_age > self.max_snapshot_age:
                raise StaleSnapshotError(
                    f"FX rate table is {fx_age} old, exceeding "
                    f"max_snapshot_age={self.max_snapshot_age}."
                )

    # -------------------------------------------------------------- public API

    def unmapped_broker_symbols(
        self, raw_positions: Sequence[RawBrokerPosition]
    ) -> List[str]:
        """
        Broker symbols in ``raw_positions`` with no registered canonical mapping,
        sorted. Pure: it inspects the given list and mutates nothing.

        Non-empty output means those legs will be keyed by their raw ticker, which is
        how the same asset ends up counted twice under two canonical symbols.
        """
        return sorted(
            {
                p.broker_symbol
                for p in raw_positions
                if p.broker_symbol not in self.symbol_map
            }
        )

    def consolidate_positions(
        self,
        raw_positions: Sequence[RawBrokerPosition],
        valuation_time: Optional[datetime] = None,
    ) -> Dict[str, ConsolidatedPosition]:
        """
        Consolidates raw multi-broker positions into canonical net and gross
        positions denominated in ``base_currency``.

        Args:
            raw_positions: Validated position legs from every broker adapter.
            valuation_time: Timezone-aware time the view is being struck at. Required
                when ``max_snapshot_age`` is configured.

        Returns:
            canonical_symbol -> ConsolidatedPosition, ordered by symbol.

        Raises:
            MissingFxRateError: A leg's currency is absent from the FX table.
            UnmappedSymbolError: Strict mode, and a broker symbol has no mapping.
            StaleSnapshotError: A leg (or the FX table) breaches ``max_snapshot_age``.
        """
        self._check_staleness(raw_positions, valuation_time)

        unmapped = self.unmapped_broker_symbols(raw_positions)
        if unmapped:
            logger.warning(
                "%d broker symbol(s) have no canonical mapping and were keyed by "
                "their raw ticker: %s. If any of these is the same asset as a mapped "
                "symbol, its exposure is double-counted across two canonical entries.",
                len(unmapped), ", ".join(unmapped),
            )

        grouped: Dict[str, List[RawBrokerPosition]] = {}
        for pos in raw_positions:
            grouped.setdefault(self._to_canonical(pos.broker_symbol), []).append(pos)

        consolidated: Dict[str, ConsolidatedPosition] = {}

        # Sorted so output ordering is deterministic run to run, which matters for
        # diffing consecutive risk snapshots and for reproducible audit records.
        for canon in sorted(grouped):
            pos_list = grouped[canon]

            net_qty = math.fsum(p.quantity for p in pos_list)
            gross_qty = math.fsum(abs(p.quantity) for p in pos_list)

            breakdown: Dict[str, float] = {}
            leg_values: List[float] = []
            leg_costs: List[float] = []

            for p in pos_list:
                breakdown[p.broker_name] = breakdown.get(p.broker_name, 0.0) + p.quantity
                leg_values.append(
                    self._to_base(
                        p.quantity * p.current_price * p.contract_multiplier, p.currency
                    )
                )
                leg_costs.append(
                    self._to_base(
                        p.quantity * p.average_cost * p.cost_multiplier, p.currency
                    )
                )

            total_mkt_val = math.fsum(leg_values)
            gross_mkt_val = math.fsum(abs(v) for v in leg_values)
            total_cost = math.fsum(leg_costs)

            # A flat (or near-flat) net position has no meaningful average cost: the
            # quotient diverges as net_quantity approaches zero, so a book that is
            # long 100 / short 99.999999 would otherwise report a cost per contract in
            # the millions. None says "not defined here"; 0.0 would be read as free.
            # The isfinite guard covers a caller who sets the tolerance to 0: a net of
            # 1e-300 then clears the threshold and the quotient overflows to inf,
            # which would travel into a risk report as a real cost basis.
            tolerance = self._tolerance_for(canon)
            weighted_cost: Optional[float] = None
            if abs(net_qty) > tolerance:
                candidate = total_cost / net_qty
                weighted_cost = candidate if math.isfinite(candidate) else None

            stamps = [p.as_of for p in pos_list if p.as_of is not None]
            oldest = min(stamps) if stamps else None
            # Only reported when *every* leg is timestamped; a skew computed over a
            # subset would understate how far apart the snapshots really are.
            skew = (
                (max(stamps) - min(stamps)).total_seconds()
                if stamps and len(stamps) == len(pos_list)
                else None
            )

            consolidated[canon] = ConsolidatedPosition(
                canonical_symbol=canon,
                net_quantity=net_qty,
                gross_quantity=gross_qty,
                total_market_value_base=total_mkt_val,
                gross_market_value_base=gross_mkt_val,
                total_cost_basis_base=total_cost,
                weighted_avg_cost_base=weighted_cost,
                unrealized_pnl_base=total_mkt_val - total_cost,
                broker_breakdown=breakdown,
                base_currency=self.base_currency,
                leg_count=len(pos_list),
                currencies=tuple(sorted({p.currency for p in pos_list})),
                is_internally_offset=(
                    any(p.quantity > 0 for p in pos_list)
                    and any(p.quantity < 0 for p in pos_list)
                ),
                oldest_snapshot_as_of=oldest,
                snapshot_skew_seconds=skew,
            )

        return consolidated

    def reconcile_against_target(
        self,
        raw_positions: Sequence[RawBrokerPosition],
        expected_target_ledger: Mapping[str, float],
        valuation_time: Optional[datetime] = None,
    ) -> List[ReconciliationDiscrepancy]:
        """
        Audits consolidated broker positions against the strategy's expected target
        quantities, classifying each break by ``DiscrepancyKind``.

        Target ledger keys are canonical symbols and are upper-cased before matching,
        so a lower-cased target key does not manufacture a phantom pair of breaks.

        Returns:
            Breaks sorted by canonical symbol (deterministic ordering). Empty when
            every symbol agrees within tolerance.
        """
        consolidated = self.consolidate_positions(raw_positions, valuation_time)

        targets: Dict[str, float] = {}
        for symbol, qty in expected_target_ledger.items():
            key = str(symbol).strip().upper()
            if not key:
                raise ValueError("expected_target_ledger contains an empty symbol key.")
            if key in targets:
                raise ValueError(
                    f"expected_target_ledger contains duplicate symbol key {key!r} "
                    "after normalization -- the intended target quantity is ambiguous."
                )
            targets[key] = _require_finite(qty, f"expected_target_ledger[{key!r}]")

        discrepancies: List[ReconciliationDiscrepancy] = []

        for symbol in sorted(set(consolidated) | set(targets)):
            actual_qty = (
                consolidated[symbol].net_quantity if symbol in consolidated else 0.0
            )
            expected_qty = targets.get(symbol, 0.0)
            diff = actual_qty - expected_qty
            tolerance = self._tolerance_for(symbol)
            if abs(diff) <= tolerance:
                continue

            if abs(actual_qty) <= tolerance:
                kind = DiscrepancyKind.MISSING_AT_BROKER
            elif abs(expected_qty) <= tolerance:
                kind = DiscrepancyKind.UNEXPECTED_AT_BROKER
            else:
                kind = DiscrepancyKind.QUANTITY_MISMATCH

            # 12 significant digits, not :+.2f -- a 1e-6 BTC break formatted to two
            # decimals prints as "+0.00" and reads like a non-event in the alert that
            # reported it, and at 8 digits the expected/actual pair still renders
            # identically ("1" and "1") for an 8-decimal quantity break.
            msg = (
                f"{kind.value} on {symbol}: expected {expected_qty:.12g}, "
                f"broker total {actual_qty:.12g} (diff {diff:+.12g}, "
                f"tolerance {tolerance:.12g})"
            )
            logger.warning(msg)
            discrepancies.append(
                ReconciliationDiscrepancy(
                    canonical_symbol=symbol,
                    expected_qty=expected_qty,
                    actual_broker_qty=actual_qty,
                    discrepancy_qty=diff,
                    kind=kind,
                    message=msg,
                )
            )

        return discrepancies
