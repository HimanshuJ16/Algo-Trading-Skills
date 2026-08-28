"""
options-flow-unusual-activity-detection: screens individual option prints for
"unusual activity" — a trade whose size is large relative to the series' standing
open interest and average daily volume, and whose notional premium is large enough
to plausibly be institutional.

Per-print metrics
-----------------

    V/OI     = trade_volume / open_interest          (open interest of the SERIES)
    V/ADV    = trade_volume / adv                    (ADV of the SERIES)
    premium  = trade_volume * execution_price * contract_multiplier

A print is flagged when all three gates clear their thresholds. Direction is then
inferred from where the print landed relative to the prevailing quote (the "quote
rule"): at/above the ask => buyer-initiated, at/below the bid => seller-initiated.

What the consolidated feed does and does not tell you
-----------------------------------------------------

The OPRA Equity and Index Last Sale message (Binary Data Recipient Interface
Specification, Sec. 6.01) carries exactly: Message Header, Security Symbol,
Expiration Block, Strike Price, Volume, Premium Price, Trade Identifier and
Trading Session Identifier. There is **no aggressor-side field and no
opening/closing position indicator**. Open interest appears only in the Equity and
Index End of Day Summary message (Sec. 6.03).

Three consequences are baked into this module's design:

- **Side is inferred, not observed.** Savickas & Wilson (2003), *On Inferring the
  Direction of Option Trades*, JFQA 38(4) 881-902, find the quote rule signs 83% of
  *classifiable* option trades correctly — the best of the four rules tested, but
  still roughly one in six wrong. Midspread prints are not classifiable at all, and
  outside-quote and reversed-quote prints are systematically misclassified. Every
  directional label this module emits is therefore a probabilistic inference; the
  aggressor side is reported alongside the label so a consumer can discount it.
- **Opening vs closing is unknowable from the trade feed.** A call bought at the ask
  may be a new long, a short call being bought to close, or one leg of a spread or a
  market-maker delta hedge. Volume exceeding open interest implies *some* of the
  print must be opening, which is why the V/OI gate exists — it is a weak proxy, not
  a substitute for the Cboe Open-Close Volume Summary, a separate product that does
  break volume down by buy/sell, open/close and participant type.
- **Open interest is stale by one session.** OCC computes open interest overnight
  from cleared positions and publishes it for the following session; it does not
  move intraday. V/OI therefore always compares today's print to yesterday's OI.

Limitations (documented, deliberate)
------------------------------------

- **The thresholds are heuristics, not standards.** 1.5x V/OI, 2.0x V/ADV and
  $100,000 premium are this library's defaults. No regulator, exchange or standards
  body publishes them. Calibrate per underlying and per liquidity tier: on an
  index/mega-cap name where $100k of premium is routine, these defaults flag noise;
  on an illiquid single name they may never fire.
- **Single-print scope.** ``volume`` is the size of *this* print, not cumulative
  session volume for the series. Feeding cumulative volume silently changes what
  every threshold means — late in a session, cumulative V/OI above 1.5 is ordinary
  for any actively traded series.
- **No multi-leg awareness.** Spread, combo and delta-hedged trades print leg by leg
  on OPRA with no linkage. Savickas & Wilson found index-option complex trades — 15%
  of their sample — to be the worst-classified subset; excluding them lifted the
  quote rule above 87%. A "bullish sweep" here may be the long leg of a vertical.
- **The quote must be the one in force at the print.** This module compares
  ``execution_price`` to the ``bid``/``ask`` it is handed. Supplying a stale or
  non-contemporaneous NBBO produces confidently wrong side labels; the module cannot
  detect that.
- **No sweep detection.** A true sweep is one order filled across several venues
  within a few milliseconds. Reconstructing it requires correlating prints across
  exchanges; this module scores one print at a time. ``UNUSUAL_BULLISH_SWEEP`` names
  the classic screen, not a verified multi-venue sweep.
"""
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Library default: a print must be at least this many times the series' open
#: interest. Heuristic, not a published standard — see module docstring.
DEFAULT_MIN_V_OI_RATIO = 1.5

#: Library default: a print must be at least this many times the series' ADV.
DEFAULT_MIN_V_ADV_RATIO = 2.0

#: Library default: minimum notional premium, in USD, for a print to be considered
#: plausibly institutional.
DEFAULT_MIN_PREMIUM_USD = 100_000.0

#: Standard US listed equity/index option premium multiplier. OCC contract
#: adjustments (splits, mergers, spin-offs) may alter a series' deliverable and
#: multiplier, and non-US markets use their own contract sizes — hence a per-trade
#: field rather than a hard-coded constant.
STANDARD_CONTRACT_MULTIPLIER = 100.0

_CALL_ALIASES = frozenset({"CALL", "C"})
_PUT_ALIASES = frozenset({"PUT", "P"})

# Aggressor labels.
BUY_AT_ASK = "BUY_AT_ASK"
SELL_AT_BID = "SELL_AT_BID"
MID_MARKET = "MID_MARKET"
#: No usable quote (missing, non-positive ask, or crossed bid > ask). Direction
#: cannot be inferred and MUST NOT be guessed.
UNCLASSIFIED = "UNCLASSIFIED"

# Classification labels.
UNUSUAL_BULLISH_SWEEP = "UNUSUAL_BULLISH_SWEEP"
UNUSUAL_BEARISH_SWEEP = "UNUSUAL_BEARISH_SWEEP"
UNUSUAL_BULLISH_BLOCK = "UNUSUAL_BULLISH_BLOCK"
UNUSUAL_BEARISH_BLOCK = "UNUSUAL_BEARISH_BLOCK"
UNUSUAL_FLOW_NEUTRAL = "UNUSUAL_FLOW_NEUTRAL"
UNUSUAL_FLOW_UNCLASSIFIED = "UNUSUAL_FLOW_UNCLASSIFIED"
ROUTINE_FLOW = "ROUTINE_FLOW"


@dataclass
class OptionsTrade:
    """One option print, with the quote in force at the time of the print.

    ``volume`` is the contract count of *this print*, not cumulative session volume.
    Contract counts may arrive as ints or as integral floats (JSON feeds); a
    fractional count is rejected.

    ``open_interest`` and ``adv`` are properties of the option *series* (this exact
    symbol/expiry/strike/right), never of the underlying. ``None`` means the value
    was unavailable for this series — the corresponding gate then cannot be
    evaluated and the print is not flagged. ``0`` means a genuine zero (a newly
    listed series with no standing open interest, or one that has never traded),
    which yields an infinite ratio and clears the gate.
    """
    trade_id: str
    asset_id: str                                # underlying, e.g. 'AAPL'
    option_symbol: str                           # OCC series, e.g. 'AAPL240119C00150000'
    option_type: str                             # 'CALL'/'C' or 'PUT'/'P'
    volume: int                                  # contracts in THIS print
    open_interest: Optional[int]                 # series OI (prior session, OCC)
    adv: Optional[float]                         # series average daily volume
    execution_price: float                       # premium per contract
    bid: Optional[float]                         # quote in force at the print
    ask: Optional[float]
    timestamp: str
    contract_multiplier: float = STANDARD_CONTRACT_MULTIPLIER


@dataclass
class OptionsFlowAnomalyReport:
    trade_id: str
    asset_id: str
    option_symbol: str
    vol_oi_ratio: Optional[float]        # None when open interest was unavailable
    vol_adv_ratio: Optional[float]       # None when ADV was unavailable
    total_premium_usd: float
    aggressor_side: str                  # BUY_AT_ASK | SELL_AT_BID | MID_MARKET | UNCLASSIFIED
    classification: str
    is_unusual: bool
    audit_notes: str
    gates_passed: Tuple[str, ...] = ()          # which size gates cleared
    gates_unevaluable: Tuple[str, ...] = ()     # gates skipped for missing inputs
    #: False whenever the direction shown is an inference the data cannot support
    #: (no usable quote, or a midspread print). Downstream sentiment aggregation
    #: should exclude these rather than treat them as neutral evidence.
    direction_is_inferred: bool = True


def _is_finite_number(value: Any) -> bool:
    """True for a real, finite int/float. Bools are rejected: ``True`` is an int in
    Python and would silently become a price of 1.0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_whole_number(value: Any) -> bool:
    """True for a finite number with no fractional part (int, or an integral float)."""
    return _is_finite_number(value) and float(value).is_integer()


def _normalize_option_type(option_type: str) -> str:
    if not isinstance(option_type, str):
        raise ValueError(f"option_type must be a string, got {type(option_type).__name__}")
    token = option_type.strip().upper()
    if token in _CALL_ALIASES:
        return "CALL"
    if token in _PUT_ALIASES:
        return "PUT"
    raise ValueError(f"option_type must be CALL/C or PUT/P, got {option_type!r}")


def _validate_trade(trade: OptionsTrade) -> str:
    """Validate a print and return its normalized option type.

    Raises ``ValueError`` on input that cannot produce a meaningful score. Bad
    market data must fail loudly here: a silently coerced field becomes a
    confident, wrong trading signal downstream.
    """
    # Contract counts arrive as ints from binary feeds and as floats from JSON APIs,
    # so an integral float is accepted; a fractional contract count is not.
    if not _is_whole_number(trade.volume) or float(trade.volume) <= 0:
        raise ValueError(f"volume must be a positive whole contract count, got {trade.volume!r}")

    if trade.open_interest is not None:
        if not _is_whole_number(trade.open_interest) or float(trade.open_interest) < 0:
            raise ValueError(
                f"open_interest must be a non-negative whole number or None, "
                f"got {trade.open_interest!r}")

    if trade.adv is not None:
        if not _is_finite_number(trade.adv) or float(trade.adv) < 0:
            raise ValueError(f"adv must be a non-negative finite number or None, got {trade.adv!r}")

    if not _is_finite_number(trade.execution_price) or float(trade.execution_price) < 0:
        raise ValueError(
            f"execution_price must be a non-negative finite number, got {trade.execution_price!r}")

    if not _is_finite_number(trade.contract_multiplier) or float(trade.contract_multiplier) <= 0:
        raise ValueError(
            f"contract_multiplier must be a positive finite number, got {trade.contract_multiplier!r}")

    for name, value in (("bid", trade.bid), ("ask", trade.ask)):
        if value is not None and not _is_finite_number(value):
            raise ValueError(f"{name} must be a finite number or None, got {value!r}")

    return _normalize_option_type(trade.option_type)


def classify_aggressor(execution_price: float, bid: Optional[float], ask: Optional[float]) -> str:
    """Quote-rule side inference for one print.

    At/above the ask is buyer-initiated, at/below the bid is seller-initiated,
    strictly inside the spread is unclassifiable by the quote rule (Savickas &
    Wilson 2003). A missing quote, a non-positive ask, or a crossed quote
    (bid > ask) yields ``UNCLASSIFIED``: with no reliable reference price there is
    no defensible side, and defaulting to "buy" would label every quote outage as
    aggressive institutional buying.
    """
    if bid is None or ask is None:
        return UNCLASSIFIED
    if ask <= 0 or bid < 0 or bid > ask:
        return UNCLASSIFIED
    if execution_price >= ask:
        return BUY_AT_ASK
    if execution_price <= bid:
        return SELL_AT_BID
    return MID_MARKET


def _ratio(numerator: float, denominator: Optional[float]) -> Optional[float]:
    """Ratio with an explicit unavailable case.

    A ``None`` denominator (value not supplied) propagates to ``None`` — the gate is
    unevaluable. A zero denominator with a positive numerator is genuinely infinite,
    not "equal to the numerator": returning the raw volume there would compare a
    contract count against a ratio threshold, flagging any 2-contract print on a
    zero-OI series.
    """
    if denominator is None:
        return None
    if denominator <= 0:
        return math.inf
    return numerator / float(denominator)


def _fmt_ratio(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}x"


def _round_optional(value: Optional[float], digits: int) -> Optional[float]:
    if value is None or math.isinf(value):
        return value
    return round(value, digits)


class OptionsFlowUnusualActivityDetectionEngine:
    """Scores option prints for unusual activity on size, notional and inferred side.

    Thresholds are library defaults, not published standards; see the module
    docstring. All three size gates must clear before a print is flagged, and a
    gate whose input is unavailable never clears.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = dict(config or {})
        self.min_v_oi_ratio = self._positive_threshold("min_v_oi_ratio", DEFAULT_MIN_V_OI_RATIO)
        self.min_v_adv_ratio = self._positive_threshold("min_v_adv_ratio", DEFAULT_MIN_V_ADV_RATIO)
        self.min_premium_usd = self._positive_threshold("min_premium_usd", DEFAULT_MIN_PREMIUM_USD)

    def _positive_threshold(self, key: str, default: float) -> float:
        raw = self.config.get(key, default)
        if not _is_finite_number(raw) or float(raw) <= 0:
            raise ValueError(f"config[{key!r}] must be a positive finite number, got {raw!r}")
        return float(raw)

    def detect_unusual_activity(self, trade: OptionsTrade) -> OptionsFlowAnomalyReport:
        """Score a single print. Raises ``ValueError`` on unusable input."""
        opt_type = _validate_trade(trade)

        v_oi = _ratio(float(trade.volume), trade.open_interest)
        v_adv = _ratio(float(trade.volume), trade.adv)
        total_premium = (
            float(trade.volume) * float(trade.execution_price) * float(trade.contract_multiplier)
        )

        gates_passed: List[str] = []
        gates_unevaluable: List[str] = []

        if v_oi is None:
            gates_unevaluable.append("v_oi")
        elif v_oi >= self.min_v_oi_ratio:
            gates_passed.append("v_oi")

        if v_adv is None:
            gates_unevaluable.append("v_adv")
        elif v_adv >= self.min_v_adv_ratio:
            gates_passed.append("v_adv")

        if total_premium >= self.min_premium_usd:
            gates_passed.append("premium")

        is_unusual = len(gates_passed) == 3
        aggressor = classify_aggressor(float(trade.execution_price), trade.bid, trade.ask)
        directional = aggressor in (BUY_AT_ASK, SELL_AT_BID)

        if not is_unusual:
            classification = ROUTINE_FLOW
        elif aggressor == BUY_AT_ASK:
            classification = UNUSUAL_BULLISH_SWEEP if opt_type == "CALL" else UNUSUAL_BEARISH_SWEEP
        elif aggressor == SELL_AT_BID:
            # Selling calls is bearish-to-neutral and selling puts bullish-to-neutral,
            # but only if the print opens a position — which the feed cannot confirm.
            classification = UNUSUAL_BEARISH_BLOCK if opt_type == "CALL" else UNUSUAL_BULLISH_BLOCK
        elif aggressor == MID_MARKET:
            classification = UNUSUAL_FLOW_NEUTRAL
        else:
            classification = UNUSUAL_FLOW_UNCLASSIFIED

        notes = (
            f"OPTIONS FLOW AUDIT [{trade.option_symbol}]: "
            f"Vol/OI = {_fmt_ratio(v_oi)} (min {self.min_v_oi_ratio}x), "
            f"Vol/ADV = {_fmt_ratio(v_adv)} (min {self.min_v_adv_ratio}x), "
            f"Premium = ${total_premium:,.2f} (min ${self.min_premium_usd:,.2f}), "
            f"Aggressor = {aggressor} -> Classification: '{classification}'."
        )
        if gates_unevaluable:
            notes += (
                f" NOT EVALUABLE: {', '.join(gates_unevaluable)} unavailable for this series; "
                "those gates cannot clear."
            )
        if is_unusual and not directional:
            notes += " Direction NOT inferable from the quote - do not read as directional sentiment."

        if is_unusual:
            logger.warning(notes)
        else:
            logger.info(notes)

        return OptionsFlowAnomalyReport(
            trade_id=trade.trade_id,
            asset_id=trade.asset_id,
            option_symbol=trade.option_symbol,
            # Rounded for display only; the gates above compare unrounded values.
            vol_oi_ratio=_round_optional(v_oi, 6),
            vol_adv_ratio=_round_optional(v_adv, 6),
            total_premium_usd=round(total_premium, 2),
            aggressor_side=aggressor,
            classification=classification,
            is_unusual=is_unusual,
            audit_notes=notes,
            gates_passed=tuple(gates_passed),
            gates_unevaluable=tuple(gates_unevaluable),
            direction_is_inferred=directional,
        )

    def scan(
        self, trades: Iterable[OptionsTrade], unusual_only: bool = False
    ) -> List[OptionsFlowAnomalyReport]:
        """Score a batch of prints.

        A print that fails validation is logged and skipped rather than aborting the
        scan — one malformed message must not blind the scanner to every later print.
        Callers needing strict behaviour should call ``detect_unusual_activity``
        directly.
        """
        reports: List[OptionsFlowAnomalyReport] = []
        for trade in trades:
            try:
                report = self.detect_unusual_activity(trade)
            except ValueError as exc:
                logger.error(
                    "Skipping unscoreable options print %s: %s",
                    getattr(trade, "trade_id", "<unknown>"), exc,
                )
                continue
            if unusual_only and not report.is_unusual:
                continue
            reports.append(report)
        return reports
