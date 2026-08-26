"""
market-maker-vs-taker-strategy-classification: classifies a strategy's realized
execution posture as predominantly liquidity-adding (maker), liquidity-removing
(taker), or hybrid, and attributes the exchange fees/rebates that posture earned.

What this module measures
-------------------------
It measures *realized fills*. A fill log contains the passive orders that filled;
it cannot contain the passive orders that did not. The maker ratio is therefore a
description of executed flow, not of quoting behaviour, fill probability, or
liquidity provision. See "Not a regulatory market-maker test" below.

Sign convention (applies to every USD amount in this module)
------------------------------------------------------------
    fee_paid_usd > 0  -> the venue CHARGED the desk (fee)
    fee_paid_usd < 0  -> the venue CREDITED the desk (rebate)

so a negative ``net_fees_paid_usd`` (and a negative ``effective_fee_rate_bps``) is
net rebate capture. The same convention is used by
``exchange-fee-tier-and-rebate-structure-analysis``.

Classification basis
--------------------
This is the parameter that decides whether the answer means anything, and there is
no safe default, so it is required.

``ClassificationBasis.QUANTITY``
    Ratio of maker quantity to maker+taker quantity. Correct where the fee is
    levied *per unit* -- US equity venues quote maker rebates and taker fees in
    dollars per share. Share counts are only additive within one instrument, so
    the engine refuses a multi-symbol log on this basis.

``ClassificationBasis.NOTIONAL``
    Ratio of maker notional to maker+taker notional. Correct where the fee is a
    percentage of trade value -- the crypto venue model. Binance and Kraken both
    quote maker/taker rates as a percentage of trade value, tiered on 30-day
    rolling volume. Notional is additive across instruments, so this is the only
    coherent basis for a multi-symbol log.

Neither basis is universally right, which is why neither is the default.

Liquidity categories are not binary
-----------------------------------
FIX ``LastLiquidityInd`` (tag 851) enumerates ``1 = Added Liquidity``,
``2 = Removed Liquidity``, ``3 = Liquidity Routed Out`` and (from FIX 5.0 SP2)
``4 = Auction``. A boolean maker/taker flag cannot express the last two, and
forcing them into ``is_maker=False`` inflates the taker ratio and misattributes
their fees -- routed-out and auction fills are billed under their own rate codes,
not the venue's continuous-book taker rate. Supply ``liquidity_category`` for
those fills and the engine reports them in a separate excluded bucket rather than
silently counting them as taker flow.

Not a regulatory market-maker test
----------------------------------
A high maker ratio is a fee/execution diagnostic. It is not a determination that a
firm is a market maker or a dealer:

- Under MiFID II, an investment firm engaged in algorithmic trading pursues a
  "market making strategy" when, dealing on own account, it posts firm,
  simultaneous two-way quotes of comparable size and at competitive prices
  (Directive 2014/65/EU, Article 17(4)). RTS 8 (Commission Delegated Regulation
  (EU) 2017/578) makes the test one of *quoting presence*: ICE Futures Europe's
  MiFID II market making guidance summarises it as two-way quotes of comparable
  size (sizes diverging by less than 50%) at competitive prices for more than 50%
  of the daily trading hours of continuous trading, for half of the trading days
  over a one-month period. Fills, and the ratio between them, are not the test.
- In the US, the SEC's expanded dealer rules (Rules 3a5-4 and 3a44-2) were vacated
  by the US District Court for the Northern District of Texas on 21 November 2024,
  and the SEC voluntarily dismissed its appeal on 20 February 2025. Do not treat
  any fill-ratio threshold as a registration trigger.

Limitations (documented, deliberate)
------------------------------------
- **Thresholds are conventions, not standards.** No regulator or exchange defines
  a maker-volume-ratio cut-off. The 0.80/0.20 defaults are this repository's
  reporting convention; set them to whatever your desk actually means.
- **Per-contract fee schedules are out of scope.** Venues that price per contract
  by membership/product/venue rather than by liquidity flag (CME Group's futures
  schedules bill this way) have no maker/taker rate to attribute, and an effective
  rate in bps of notional does not describe their cost.
- **No cross-venue normalisation.** Aggregating fills from a per-share venue and a
  percentage-of-value venue into one effective bps figure blends two different
  pricing units. Run one venue at a time when the number has to be actionable.
- **Positive prices only.** Notional is ``price x quantity``; a negative settlement
  price (as seen in WTI futures in April 2020) would invert the sign of every
  derived figure, so non-positive prices are rejected rather than propagated.
- **No adverse-selection, queue-position, or fill-probability modelling.** Rebate
  capture is one term of passive execution cost and often the smaller one; see
  `adverse-selection-measurement-for-passive-orders`.
"""
import logging
import math
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Reported rounding boundaries. All arithmetic is carried in full precision and
#: rounded only when the report is built, so rounding never feeds a comparison.
_USD_DP = 2
_QTY_DP = 8
_RATIO_DP = 6
_BPS_DP = 4

#: A ratio this close to a threshold makes the label an artefact of the cut-off
#: rather than a description of the strategy, so the report says so. 0.005 = half
#: a percentage point of maker share; it is a reporting hint, not a rule.
_BOUNDARY_PROXIMITY_WARNING = 0.005


class LiquidityCategory(str, Enum):
    """
    Liquidity classification of a single fill, mirroring FIX ``LastLiquidityInd``
    (tag 851). ``ADDED`` and ``REMOVED`` are the maker and taker sides; the other
    two are billed under their own rate codes and are excluded from the ratio.
    """

    #: FIX 851 = 1. Passive fill; the maker side.
    ADDED = "ADDED"
    #: FIX 851 = 2. Aggressive fill; the taker side.
    REMOVED = "REMOVED"
    #: FIX 851 = 3. Filled away after being routed out; neither side of this book.
    ROUTED_OUT = "ROUTED_OUT"
    #: FIX 851 = 4. Auction/cross fill; priced under the auction schedule.
    AUCTION = "AUCTION"


class ClassificationBasis(str, Enum):
    """Which weight the maker ratio is computed on. See module docstring."""

    #: Quantity-weighted. Per-unit fee schedules; single instrument only.
    QUANTITY = "QUANTITY"
    #: Notional-weighted. Percentage-of-value fee schedules; multi-instrument safe.
    NOTIONAL = "NOTIONAL"


class StrategyClassification(str, Enum):
    """Labels the engine can assign. Report fields carry the bare string value."""

    PURE_MAKER = "PURE_MAKER_STRATEGY"
    PURE_TAKER = "PURE_TAKER_STRATEGY"
    HYBRID = "HYBRID_MAKER_TAKER_STRATEGY"
    #: No fill in the log added or removed continuous-book liquidity.
    UNCLASSIFIED = "UNCLASSIFIED_NO_MAKER_TAKER_VOLUME"


class TradeLogError(ValueError):
    """
    Raised when a trade log or engine configuration is unusable.

    Subclasses ``ValueError`` so existing callers that catch ``ValueError`` keep
    working.
    """


def _is_finite_number(value: object) -> bool:
    """
    True for a real int/float that is neither NaN nor infinite.

    ``bool`` is excluded deliberately: ``True`` is a valid ``int`` to Python and
    would pass as a price of 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _fmt_usd(amount: float) -> str:
    """Formats a signed amount as -$1,234.56 rather than $-1,234.56."""
    return f"-${abs(amount):,.2f}" if amount < 0 else f"${amount:,.2f}"


def _round_opt(value: Optional[float], digits: int) -> Optional[float]:
    """Rounds a value that may legitimately be absent."""
    return None if value is None else round(value, digits)


@dataclass(frozen=True)
class ExecutedTradeLog:
    """
    One executed fill. Frozen: a fill is an audit record, and validation that can
    be undone by assigning to the field afterwards is not validation.

    ``is_maker`` must be a real ``bool``. Broker REST payloads routinely carry the
    flag as the string ``"false"``, which is truthy in Python and would silently
    book every taker fill as a maker fill, so a non-bool is rejected rather than
    coerced.

    ``executed_price`` and ``quantity`` must both be finite and strictly positive.
    Encode side separately; a negative quantity is treated as a corrupt record, not
    as a sell.

    ``fee_paid_usd`` follows the module sign convention: positive is a fee charged,
    negative is a rebate credited.

    ``liquidity_category`` is optional. When omitted it is derived from
    ``is_maker`` (``ADDED``/``REMOVED``). Supply it explicitly to report a fill
    that is neither -- ``ROUTED_OUT`` or ``AUCTION`` -- in which case ``is_maker``
    is not used for classification and the fill is excluded from the maker ratio.
    """

    trade_id: str
    symbol: str
    is_maker: bool
    executed_price: float
    quantity: float
    fee_paid_usd: float
    liquidity_category: Optional[LiquidityCategory] = None

    def __post_init__(self) -> None:
        if not isinstance(self.trade_id, str) or not self.trade_id.strip():
            raise TradeLogError("trade_id must be a non-empty string.")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise TradeLogError(f"{self.trade_id}: symbol must be a non-empty string.")
        if not isinstance(self.is_maker, bool):
            raise TradeLogError(
                f"{self.trade_id}: is_maker must be a bool, got "
                f"{type(self.is_maker).__name__} ({self.is_maker!r}). Parse broker "
                f"payloads to a real boolean first -- the string 'false' is truthy."
            )
        for label, value in (
            ("executed_price", self.executed_price),
            ("quantity", self.quantity),
        ):
            if not _is_finite_number(value):
                raise TradeLogError(
                    f"{self.trade_id}: {label} must be a finite number, got {value!r}."
                )
            if float(value) <= 0.0:
                raise TradeLogError(
                    f"{self.trade_id}: {label} must be strictly positive, got "
                    f"{value!r}. Encode side separately -- a negative quantity is a "
                    f"corrupt record, not a sell."
                )
        if not _is_finite_number(self.fee_paid_usd):
            raise TradeLogError(
                f"{self.trade_id}: fee_paid_usd must be a finite number "
                f"(positive = fee charged, negative = rebate credited), got "
                f"{self.fee_paid_usd!r}."
            )

        if self.liquidity_category is None:
            object.__setattr__(
                self,
                "liquidity_category",
                LiquidityCategory.ADDED if self.is_maker else LiquidityCategory.REMOVED,
            )
            return

        try:
            normalized = LiquidityCategory(
                self.liquidity_category.value
                if isinstance(self.liquidity_category, Enum)
                else str(self.liquidity_category).strip().upper()
            )
        except ValueError as exc:
            raise TradeLogError(
                f"{self.trade_id}: unknown liquidity_category "
                f"{self.liquidity_category!r}. Expected one of "
                f"{[c.value for c in LiquidityCategory]}."
            ) from exc

        # A contradiction between the two fields means the log was assembled from
        # two sources that disagree; guessing which one is right would silently
        # move volume between the maker and taker buckets.
        expected_is_maker = {
            LiquidityCategory.ADDED: True,
            LiquidityCategory.REMOVED: False,
        }.get(normalized)
        if expected_is_maker is not None and self.is_maker != expected_is_maker:
            raise TradeLogError(
                f"{self.trade_id}: liquidity_category '{normalized.value}' "
                f"contradicts is_maker={self.is_maker}."
            )
        object.__setattr__(self, "liquidity_category", normalized)

    @property
    def notional_usd(self) -> float:
        """Gross notional of the fill. Both inputs are validated finite and positive."""
        return float(self.executed_price) * float(self.quantity)


@dataclass
class StrategyClassificationReport:
    """
    Result of one classification run.

    Ratio fields are ``None`` where the ratio has no meaning rather than 0.0, which
    would read as "entirely taker". ``maker_volume_ratio`` is ``None`` for a
    multi-symbol log because share counts across instruments are not additive.

    The ``*_volume_shares`` fields keep their historical names but hold the fill
    quantity in whatever unit the instrument trades in (shares, contracts, coins).
    On a multi-symbol log they are a sum across units and are reported for
    traceability only -- read the notional fields instead.
    """

    strategy_id: str
    classification_basis: str
    total_trades_count: int
    maker_trades_count: int
    taker_trades_count: int
    excluded_trades_count: int
    total_volume_shares: float
    maker_volume_shares: float
    taker_volume_shares: float
    excluded_volume_shares: float
    maker_volume_ratio: Optional[float]      # quantity basis; None if multi-symbol
    maker_notional_ratio: Optional[float]    # notional basis
    classification_ratio: Optional[float]    # the ratio actually classified on
    total_gross_notional_usd: float
    maker_gross_notional_usd: float
    taker_gross_notional_usd: float
    excluded_gross_notional_usd: float
    net_fees_paid_usd: float                 # + = fee charged, - = rebate credited
    maker_fees_paid_usd: float
    taker_fees_paid_usd: float
    excluded_fees_paid_usd: float
    effective_fee_rate_bps: float            # net fees / gross notional * 10,000
    maker_effective_fee_bps: Optional[float]
    taker_effective_fee_bps: Optional[float]
    symbols: List[str]
    classification: str
    status: str
    audit_notes: str
    warnings: List[str] = field(default_factory=list)


class MarketMakerVsTakerClassifierEngine:
    """
    Classifies realized executions as maker-dominant, taker-dominant, or hybrid on
    an explicit weighting basis, and attributes fees and rebates to each side.

    Thresholds are inclusive at both ends, matching the documented convention:
    ``ratio >= pure_maker_threshold_ratio`` is PURE_MAKER, ``ratio <=
    pure_taker_threshold_ratio`` is PURE_TAKER, anything strictly between is
    HYBRID. Comparison is against the full-precision ratio; the report rounds only
    for display, so a ratio of 0.799996 is never promoted to PURE_MAKER by
    rounding.
    """

    def __init__(
        self,
        classification_basis: ClassificationBasis,
        pure_maker_threshold_ratio: float = 0.80,
        pure_taker_threshold_ratio: float = 0.20,
    ) -> None:
        try:
            self.classification_basis = ClassificationBasis(
                classification_basis.value
                if isinstance(classification_basis, Enum)
                else str(classification_basis).strip().upper()
            )
        except ValueError as exc:
            raise TradeLogError(
                f"Unknown classification_basis {classification_basis!r}. Expected "
                f"one of {[b.value for b in ClassificationBasis]} -- there is no "
                f"safe default, see the module docstring."
            ) from exc

        for label, value in (
            ("pure_maker_threshold_ratio", pure_maker_threshold_ratio),
            ("pure_taker_threshold_ratio", pure_taker_threshold_ratio),
        ):
            if not _is_finite_number(value):
                raise TradeLogError(f"{label} must be a finite number, got {value!r}.")
            if not 0.0 <= float(value) <= 1.0:
                raise TradeLogError(f"{label} must be within [0, 1], got {value!r}.")
        # Swapped thresholds would make the taker branch unreachable and label
        # every mixed strategy PURE_MAKER, with no error anywhere downstream.
        if float(pure_taker_threshold_ratio) >= float(pure_maker_threshold_ratio):
            raise TradeLogError(
                f"pure_taker_threshold_ratio ({pure_taker_threshold_ratio}) must be "
                f"strictly below pure_maker_threshold_ratio "
                f"({pure_maker_threshold_ratio})."
            )

        self.pure_maker_threshold_ratio = float(pure_maker_threshold_ratio)
        self.pure_taker_threshold_ratio = float(pure_taker_threshold_ratio)

    def classify_strategy_executions(
        self,
        strategy_id: str,
        trades: Sequence[ExecutedTradeLog],
    ) -> StrategyClassificationReport:
        """
        Audits a fill log and returns the classification, both ratios, and the
        per-side fee attribution.

        Raises ``TradeLogError`` (a ``ValueError``) on an empty log, a non-fill
        element, or a multi-symbol log submitted on the QUANTITY basis.
        """
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise TradeLogError("strategy_id must be a non-empty string.")
        if isinstance(trades, (str, bytes)) or not isinstance(trades, SequenceABC):
            raise TradeLogError("trades must be a sequence of ExecutedTradeLog.")
        if not trades:
            raise TradeLogError("Executed trade log cannot be empty.")
        seen_trade_ids = set()
        for position, trade in enumerate(trades):
            if not isinstance(trade, ExecutedTradeLog):
                raise TradeLogError(
                    f"trades[{position}] must be an ExecutedTradeLog, got "
                    f"{type(trade).__name__}. Construct one so the fill is "
                    f"validated before it is counted."
                )
            # A repeated id is almost always a paginated fetch that overlapped, and
            # a double-counted fill corrupts every figure in the report silently.
            if trade.trade_id in seen_trade_ids:
                raise TradeLogError(
                    f"Duplicate trade_id '{trade.trade_id}' at trades[{position}]. "
                    f"Deduplicate the log first; if the venue reuses one id across "
                    f"partial fills, key on the per-fill execution id instead."
                )
            seen_trade_ids.add(trade.trade_id)

        symbols = sorted({t.symbol for t in trades})
        if self.classification_basis is ClassificationBasis.QUANTITY and len(symbols) > 1:
            shown = ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else "")
            raise TradeLogError(
                f"QUANTITY basis requires a single instrument; this log spans "
                f"{len(symbols)} symbols ({shown}). Share counts are not additive "
                f"across instruments -- use ClassificationBasis.NOTIONAL or "
                f"classify one symbol at a time."
            )

        counts = {"maker": 0, "taker": 0, "excluded": 0}
        quantity = {"maker": 0.0, "taker": 0.0, "excluded": 0.0}
        notional = {"maker": 0.0, "taker": 0.0, "excluded": 0.0}
        fees = {"maker": 0.0, "taker": 0.0, "excluded": 0.0}

        for trade in trades:
            if trade.liquidity_category is LiquidityCategory.ADDED:
                bucket = "maker"
            elif trade.liquidity_category is LiquidityCategory.REMOVED:
                bucket = "taker"
            else:
                bucket = "excluded"
            counts[bucket] += 1
            quantity[bucket] += float(trade.quantity)
            notional[bucket] += trade.notional_usd
            fees[bucket] += float(trade.fee_paid_usd)

        total_quantity = sum(quantity.values())
        total_notional = sum(notional.values())
        net_fees = sum(fees.values())

        classifiable_quantity = quantity["maker"] + quantity["taker"]
        classifiable_notional = notional["maker"] + notional["taker"]

        quantity_ratio = (
            quantity["maker"] / classifiable_quantity
            if classifiable_quantity > 0.0 and len(symbols) == 1
            else None
        )
        notional_ratio = (
            notional["maker"] / classifiable_notional
            if classifiable_notional > 0.0
            else None
        )
        classification_ratio = (
            quantity_ratio
            if self.classification_basis is ClassificationBasis.QUANTITY
            else notional_ratio
        )
        classification = self._classify(classification_ratio)
        warnings = self._build_warnings(
            classification_ratio, counts, notional, fees, symbols
        )

        effective_fee_bps = (net_fees / total_notional) * 10_000.0
        maker_fee_bps = (
            (fees["maker"] / notional["maker"]) * 10_000.0
            if notional["maker"] > 0.0
            else None
        )
        taker_fee_bps = (
            (fees["taker"] / notional["taker"]) * 10_000.0
            if notional["taker"] > 0.0
            else None
        )

        ratio_text = (
            "n/a" if classification_ratio is None else f"{classification_ratio:.2%}"
        )
        notes = (
            f"STRATEGY CLASSIFICATION [{strategy_id}]: Classified as "
            f"'{classification}' on the {self.classification_basis.value} basis. "
            f"Maker ratio = {ratio_text} of classifiable "
            f"{self.classification_basis.value.lower()} "
            f"({counts['maker']} maker / {counts['taker']} taker / "
            f"{counts['excluded']} excluded fills). "
            f"Gross notional = {_fmt_usd(total_notional)}, net fees = "
            f"{_fmt_usd(net_fees)} (effective rate = {effective_fee_bps:+.4f} bps)."
        )
        logger.info(notes)
        for warning in warnings:
            logger.warning("STRATEGY CLASSIFICATION [%s]: %s", strategy_id, warning)

        return StrategyClassificationReport(
            strategy_id=strategy_id,
            classification_basis=self.classification_basis.value,
            total_trades_count=len(trades),
            maker_trades_count=counts["maker"],
            taker_trades_count=counts["taker"],
            excluded_trades_count=counts["excluded"],
            total_volume_shares=round(total_quantity, _QTY_DP),
            maker_volume_shares=round(quantity["maker"], _QTY_DP),
            taker_volume_shares=round(quantity["taker"], _QTY_DP),
            excluded_volume_shares=round(quantity["excluded"], _QTY_DP),
            maker_volume_ratio=_round_opt(quantity_ratio, _RATIO_DP),
            maker_notional_ratio=_round_opt(notional_ratio, _RATIO_DP),
            classification_ratio=_round_opt(classification_ratio, _RATIO_DP),
            total_gross_notional_usd=round(total_notional, _USD_DP),
            maker_gross_notional_usd=round(notional["maker"], _USD_DP),
            taker_gross_notional_usd=round(notional["taker"], _USD_DP),
            excluded_gross_notional_usd=round(notional["excluded"], _USD_DP),
            net_fees_paid_usd=round(net_fees, _USD_DP),
            maker_fees_paid_usd=round(fees["maker"], _USD_DP),
            taker_fees_paid_usd=round(fees["taker"], _USD_DP),
            excluded_fees_paid_usd=round(fees["excluded"], _USD_DP),
            effective_fee_rate_bps=round(effective_fee_bps, _BPS_DP),
            maker_effective_fee_bps=_round_opt(maker_fee_bps, _BPS_DP),
            taker_effective_fee_bps=_round_opt(taker_fee_bps, _BPS_DP),
            symbols=symbols,
            classification=classification,
            status="STRATEGY_CLASSIFICATION_SUCCESS",
            audit_notes=notes,
            warnings=warnings,
        )

    def _classify(self, ratio: Optional[float]) -> str:
        """Maps a full-precision maker ratio to a label. ``None`` -> UNCLASSIFIED."""
        if ratio is None:
            return StrategyClassification.UNCLASSIFIED.value
        if ratio >= self.pure_maker_threshold_ratio:
            return StrategyClassification.PURE_MAKER.value
        if ratio <= self.pure_taker_threshold_ratio:
            return StrategyClassification.PURE_TAKER.value
        return StrategyClassification.HYBRID.value

    def _build_warnings(
        self,
        classification_ratio: Optional[float],
        counts: dict,
        notional: dict,
        fees: dict,
        symbols: List[str],
    ) -> List[str]:
        """Collects the conditions that make a label or a figure less than it looks."""
        warnings: List[str] = []

        if classification_ratio is None:
            warnings.append(
                "No fill in this log added or removed continuous-book liquidity "
                "(every fill was ROUTED_OUT or AUCTION); no maker ratio exists."
            )
        else:
            for label, threshold in (
                ("pure-maker", self.pure_maker_threshold_ratio),
                ("pure-taker", self.pure_taker_threshold_ratio),
            ):
                if abs(classification_ratio - threshold) <= _BOUNDARY_PROXIMITY_WARNING:
                    warnings.append(
                        f"Maker ratio {classification_ratio:.6f} is within "
                        f"{_BOUNDARY_PROXIMITY_WARNING:.3f} of the {label} threshold "
                        f"({threshold:.4f}); the label is a cut-off artefact, not a "
                        f"robust description of the strategy."
                    )

        if counts["excluded"] > 0:
            warnings.append(
                f"{counts['excluded']} fill(s) totalling "
                f"{_fmt_usd(notional['excluded'])} notional were ROUTED_OUT or "
                f"AUCTION and are excluded from the maker ratio; their "
                f"{_fmt_usd(fees['excluded'])} of fees remain in the net."
            )
        if counts["maker"] > 0 and fees["maker"] > 0.0:
            warnings.append(
                f"Maker fills were charged {_fmt_usd(fees['maker'])}, not credited a "
                f"rebate. A maker-dominant posture only pays if the venue's maker "
                f"rate is negative at your tier -- verify the fee schedule."
            )
        if len(symbols) > 1:
            warnings.append(
                f"Log spans {len(symbols)} symbols; quantity-weighted figures are "
                f"not additive across instruments, so maker_volume_ratio is None."
            )
        return warnings
