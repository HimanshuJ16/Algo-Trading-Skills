"""Corporate-action audit helpers for historical price-series integrity."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as calendar_date
from enum import Enum
import logging
import math
from numbers import Real
from typing import Optional


logger = logging.getLogger(__name__)

# Inference outcome used when a matched cash-dividend discontinuity proves the
# series is not total-return adjusted but cannot separate raw from split-adjusted.
NOT_TOTAL_RETURN_ADJUSTED = "NOT_TOTAL_RETURN_ADJUSTED"


class SeriesAdjustmentMode(Enum):
    UNADJUSTED = "UNADJUSTED"
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"
    TOTAL_RETURN_ADJUSTED = "TOTAL_RETURN_ADJUSTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    date: str
    action_type: str
    ratio: float

    def __post_init__(self) -> None:
        normalized_date = self.date.strip() if isinstance(self.date, str) else ""
        if not normalized_date:
            raise ValueError("corporate action date must be a non-empty ISO date")
        try:
            canonical_date = calendar_date.fromisoformat(normalized_date).isoformat()
        except ValueError as exc:
            raise ValueError("corporate action date must be ISO formatted") from exc

        normalized_action_type = (
            self.action_type.strip().upper()
            if isinstance(self.action_type, str)
            else ""
        )
        if normalized_action_type not in {"SPLIT", "DIVIDEND"}:
            raise ValueError("action_type must be SPLIT or DIVIDEND")
        if isinstance(self.ratio, bool) or not isinstance(self.ratio, Real):
            raise TypeError("corporate action ratio must be a real number")
        normalized_ratio = float(self.ratio)
        if not math.isfinite(normalized_ratio) or normalized_ratio <= 0.0:
            raise ValueError("corporate action ratio must be finite and positive")

        object.__setattr__(self, "date", canonical_date)
        object.__setattr__(self, "action_type", normalized_action_type)
        object.__setattr__(self, "ratio", normalized_ratio)


@dataclass(frozen=True, slots=True)
class DiscontinuityEvent:
    date: str
    prev_close: float
    next_open: float
    pct_change: float
    likely_cause: str
    volume_consistent: bool
    observed_price_ratio: float
    action_match: Optional[bool] = None
    action_types: tuple[str, ...] = ()
    expected_price_ratio: Optional[float] = None


@dataclass(frozen=True, slots=True)
class AdjustmentAuditReport:
    symbol: str
    total_bars: int
    discontinuities_found: list[DiscontinuityEvent]
    detected_adjustment_type: str
    has_look_ahead_bias_risk: bool
    is_consistent: bool
    message: str
    declared_series_mode: str = SeriesAdjustmentMode.UNKNOWN.value
    unexplained_discontinuities: int = 0
    boundary_source: str = "NEXT_OPEN"


class PriceAdjustmentAuditor:
    """Audit corporate-action discontinuities and split-adjustment transforms.

    The auditor does not infer point-in-time vendor history. Look-ahead risk
    requires an as-of corporate-action and adjustment-factor policy outside the
    OHLCV series itself.
    """

    def __init__(
        self,
        discontinuity_threshold_pct: float = 30.0,
        volume_ratio_tolerance_pct: float = 25.0,
        series_mode: SeriesAdjustmentMode = SeriesAdjustmentMode.UNKNOWN,
        price_match_tolerance_pct: float = 5.0,
    ):
        """Configure detection thresholds.

        ``price_match_tolerance_pct`` is the relative tolerance applied to the
        ex-date price ratio when testing whether known corporate actions explain
        a jump. It is deliberately separate from, and much tighter than,
        ``volume_ratio_tolerance_pct``: the ex-date price factor is mechanical,
        whereas traded volume around an ex-date varies widely. The 5% default
        only absorbs ordinary overnight drift; tighten it for high-quality
        vendor data. Neither tolerance substitutes for vendor factor
        reconciliation.
        """
        self.threshold_pct = self._finite_real(
            discontinuity_threshold_pct, "discontinuity_threshold_pct"
        )
        if self.threshold_pct <= 0.0:
            raise ValueError("discontinuity_threshold_pct must be positive")
        self.volume_ratio_tolerance_pct = self._finite_real(
            volume_ratio_tolerance_pct, "volume_ratio_tolerance_pct"
        )
        if self.volume_ratio_tolerance_pct < 0.0:
            raise ValueError("volume_ratio_tolerance_pct cannot be negative")
        self.price_match_tolerance_pct = self._finite_real(
            price_match_tolerance_pct, "price_match_tolerance_pct"
        )
        if self.price_match_tolerance_pct < 0.0:
            raise ValueError("price_match_tolerance_pct cannot be negative")
        self.series_mode = self._coerce_series_mode(series_mode)

    @staticmethod
    def _finite_real(value: object, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"{field_name} must be a real number")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError(f"{field_name} must be finite")
        return numeric_value

    @staticmethod
    def _coerce_series_mode(mode: SeriesAdjustmentMode) -> SeriesAdjustmentMode:
        if isinstance(mode, SeriesAdjustmentMode):
            return mode
        if isinstance(mode, str):
            try:
                return SeriesAdjustmentMode(mode.strip().upper())
            except ValueError as exc:
                raise ValueError(f"unknown series adjustment mode: {mode}") from exc
        raise TypeError("series_mode must be a SeriesAdjustmentMode or string")

    @staticmethod
    def _relative_match(actual: float, expected: float, tolerance_pct: float) -> bool:
        denominator = max(abs(expected), 1e-12)
        return abs(actual - expected) / denominator * 100.0 <= tolerance_pct

    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        return symbol.strip().upper()

    def _validate_series(
        self,
        closes: Sequence[float],
        volumes: Sequence[float],
        dates: Sequence[str],
        opens: Optional[Sequence[float]],
    ) -> tuple[list[float], list[float], list[str], list[float]]:
        if not closes:
            raise ValueError("price series cannot be empty")
        if len(closes) != len(volumes) or len(closes) != len(dates):
            raise ValueError("Closes, volumes, and dates lists must be of equal length")
        if opens is not None and len(opens) != len(closes):
            raise ValueError("opens must have the same length as closes")

        normalized_dates: list[str] = []
        previous_parsed: Optional[calendar_date] = None
        for current_date in dates:
            if not isinstance(current_date, str):
                raise TypeError("dates must contain ISO date strings")
            try:
                parsed_date = calendar_date.fromisoformat(current_date.strip())
            except ValueError as exc:
                raise ValueError("dates must contain ISO date strings") from exc
            if previous_parsed is not None and parsed_date <= previous_parsed:
                raise ValueError("dates must be strictly increasing")
            previous_parsed = parsed_date
            # Canonical YYYY-MM-DD keys so corporate-action lookup cannot miss a
            # bar because the vendor used a different accepted ISO spelling.
            normalized_dates.append(parsed_date.isoformat())

        normalized_closes = []
        normalized_volumes = []
        normalized_opens = []
        for index, (close, volume) in enumerate(zip(closes, volumes)):
            normalized_close = self._finite_real(close, f"closes[{index}]")
            if normalized_close <= 0.0:
                raise ValueError("closing prices must be greater than zero")
            normalized_volume = self._finite_real(volume, f"volumes[{index}]")
            if normalized_volume < 0.0:
                raise ValueError("volumes cannot be negative")
            normalized_closes.append(normalized_close)
            normalized_volumes.append(normalized_volume)
            if opens is None:
                normalized_opens.append(normalized_close)
            else:
                normalized_open = self._finite_real(opens[index], f"opens[{index}]")
                if normalized_open <= 0.0:
                    raise ValueError("opening prices must be greater than zero")
                normalized_opens.append(normalized_open)

        return normalized_closes, normalized_volumes, normalized_dates, normalized_opens

    @staticmethod
    def _expected_price_ratio(
        actions: Sequence[CorporateAction],
        previous_close: float,
    ) -> Optional[float]:
        """Composite ex-date price factor for every action sharing one ex-date.

        Individual factors are multiplied and same-date cash dividends are
        summed, following the documented vendor convention in
        ``references/standards.md``. A split factor is ``1 / ratio``; the cash
        factor is ``(previous_close - total_dividend) / previous_close``, which
        assumes the dividend is quoted on the same pre-split share basis as
        ``previous_close``. Returns ``None`` when no expectation can be formed
        (no actions, or a cash amount implying a non-positive price).
        """
        if not actions:
            return None
        expected_ratio = 1.0
        total_dividend = 0.0
        for action in actions:
            if action.action_type == "SPLIT":
                expected_ratio /= action.ratio
            else:
                total_dividend += action.ratio
        if total_dividend > 0.0:
            cash_factor = (previous_close - total_dividend) / previous_close
            if cash_factor <= 0.0:
                return None
            expected_ratio *= cash_factor
        return expected_ratio

    def _is_expected_for_mode(self, action_type: str) -> bool:
        if self.series_mode == SeriesAdjustmentMode.UNADJUSTED:
            return True
        if self.series_mode == SeriesAdjustmentMode.SPLIT_ADJUSTED:
            return action_type == "DIVIDEND"
        if self.series_mode == SeriesAdjustmentMode.TOTAL_RETURN_ADJUSTED:
            return False
        return True

    def detect_discontinuities(
        self,
        symbol: str,
        closes: Sequence[float],
        volumes: Sequence[float],
        dates: Sequence[str],
        known_actions: Optional[Sequence[CorporateAction]] = None,
        *,
        opens: Optional[Sequence[float]] = None,
    ) -> AdjustmentAuditReport:
        normalized_symbol = self._validate_symbol(symbol)
        normalized_closes, normalized_volumes, normalized_dates, normalized_opens = (
            self._validate_series(closes, volumes, dates, opens)
        )

        action_map: dict[str, list[CorporateAction]] = {}
        for action in known_actions or []:
            if not isinstance(action, CorporateAction):
                raise TypeError("known_actions must contain CorporateAction values")
            action_map.setdefault(action.date, []).append(action)

        discontinuities: list[DiscontinuityEvent] = []
        unexplained_discontinuities = 0
        has_look_ahead_bias = False
        for index in range(1, len(normalized_closes)):
            previous_close = normalized_closes[index - 1]
            next_open = normalized_opens[index]
            observed_ratio = next_open / previous_close
            pct_change = (observed_ratio - 1.0) * 100.0
            if abs(pct_change) < self.threshold_pct:
                continue

            current_date = normalized_dates[index]
            actions = action_map.get(current_date, [])
            split_actions = [action for action in actions if action.action_type == "SPLIT"]
            dividend_actions = [
                action for action in actions if action.action_type == "DIVIDEND"
            ]
            expected_price_ratio = self._expected_price_ratio(actions, previous_close)
            if not actions:
                action_match = None
            elif expected_price_ratio is None:
                action_match = False
            else:
                action_match = self._relative_match(
                    observed_ratio,
                    expected_price_ratio,
                    self.price_match_tolerance_pct,
                )

            causes = [f"SPLIT (ratio={action.ratio:g})" for action in split_actions]
            causes.extend(
                f"DIVIDEND_CASH_EVENT (amount={action.ratio:g})"
                for action in dividend_actions
            )

            volume_consistent = True
            if split_actions:
                likely_cause = " + ".join(causes)
                expected_volume_ratio: Optional[float] = 1.0
                for action in split_actions:
                    expected_volume_ratio *= action.ratio
            elif dividend_actions:
                likely_cause = " + ".join(causes)
                # A cash distribution does not change shares outstanding, so the
                # traded-volume series needs no split-style rescaling.
                expected_volume_ratio = None
            else:
                likely_cause = "UNKNOWN - no corporate action recorded on this date"
                expected_volume_ratio = 1.0 / observed_ratio

            if expected_volume_ratio is not None:
                if normalized_volumes[index - 1] > 0.0:
                    observed_volume_ratio = (
                        normalized_volumes[index] / normalized_volumes[index - 1]
                    )
                    volume_consistent = self._relative_match(
                        observed_volume_ratio,
                        expected_volume_ratio,
                        self.volume_ratio_tolerance_pct,
                    )
                else:
                    volume_consistent = False

            if not action_match or not all(
                self._is_expected_for_mode(action.action_type) for action in actions
            ):
                unexplained_discontinuities += 1

            if (
                self.series_mode == SeriesAdjustmentMode.TOTAL_RETURN_ADJUSTED
                and dividend_actions
                and action_match
            ):
                has_look_ahead_bias = True

            discontinuities.append(
                DiscontinuityEvent(
                    date=current_date,
                    prev_close=previous_close,
                    next_open=next_open,
                    pct_change=round(pct_change, 6),
                    likely_cause=likely_cause,
                    volume_consistent=volume_consistent,
                    observed_price_ratio=round(observed_ratio, 8),
                    action_match=action_match,
                    action_types=tuple(action.action_type for action in actions),
                    expected_price_ratio=(
                        None
                        if expected_price_ratio is None
                        else round(expected_price_ratio, 8)
                    ),
                )
            )

        if self.series_mode != SeriesAdjustmentMode.UNKNOWN:
            detected_type = self.series_mode.value
        else:
            detected_type = self._infer_adjustment_type(discontinuities)

        boundary_source = "NEXT_OPEN" if opens is not None else "PRIOR_CLOSE_FALLBACK"
        if opens is None:
            logger.warning(
                "No opens supplied for '%s': discontinuities measured close-to-close, "
                "which can understate or hide overnight ex-date gaps.",
                normalized_symbol,
            )

        if discontinuities:
            message = (
                f"DISCONTINUITY DETECTED for '{normalized_symbol}': "
                f"{len(discontinuities)} jumps >= {self.threshold_pct:g}%; "
                f"{unexplained_discontinuities} unexplained under "
                f"{self.series_mode.value}."
            )
            logger.warning(message)
        else:
            message = (
                f"No discontinuities detected for '{normalized_symbol}'. "
                f"Series mode remains {self.series_mode.value}; continuity does not "
                "prove adjustment provenance."
            )

        return AdjustmentAuditReport(
            symbol=normalized_symbol,
            total_bars=len(normalized_closes),
            discontinuities_found=discontinuities,
            detected_adjustment_type=detected_type,
            has_look_ahead_bias_risk=has_look_ahead_bias,
            is_consistent=(len(discontinuities) == 0),
            message=message,
            declared_series_mode=self.series_mode.value,
            unexplained_discontinuities=unexplained_discontinuities,
            boundary_source=boundary_source,
        )

    @staticmethod
    def _infer_adjustment_type(
        discontinuities: Sequence[DiscontinuityEvent],
    ) -> str:
        """Infer only what the observed jumps actually prove.

        A matched raw split jump proves the series is neither split-adjusted nor
        total-return adjusted. A matched cash-dividend jump only proves the
        series is not total-return adjusted; it cannot separate raw from
        split-adjusted. Absence of jumps proves nothing.
        """
        matched = [event for event in discontinuities if event.action_match]
        if any("SPLIT" in event.action_types for event in matched):
            return SeriesAdjustmentMode.UNADJUSTED.value
        if any("DIVIDEND" in event.action_types for event in matched):
            return NOT_TOTAL_RETURN_ADJUSTED
        return SeriesAdjustmentMode.UNKNOWN.value

    def apply_split_adjustment(
        self,
        closes: Sequence[float],
        volumes: Sequence[float],
        split_index: int,
        split_ratio: float,
    ) -> tuple[list[float], list[float]]:
        """Backward-adjust bars before ``split_index`` without rounding loss."""
        if len(closes) != len(volumes):
            raise ValueError("closes and volumes must have equal length")
        if (
            isinstance(split_index, bool)
            or not isinstance(split_index, int)
            or not 0 <= split_index <= len(closes)
        ):
            raise ValueError("split_index must be within the series bounds")
        normalized_ratio = self._finite_real(split_ratio, "split_ratio")
        if normalized_ratio <= 0.0:
            raise ValueError("split_ratio must be greater than zero")

        adjusted_closes = []
        adjusted_volumes = []
        for index, (close, volume) in enumerate(zip(closes, volumes)):
            normalized_close = self._finite_real(close, f"closes[{index}]")
            normalized_volume = self._finite_real(volume, f"volumes[{index}]")
            if normalized_close <= 0.0 or normalized_volume < 0.0:
                raise ValueError("prices must be positive and volumes non-negative")
            if index < split_index:
                adjusted_closes.append(normalized_close / normalized_ratio)
                adjusted_volumes.append(normalized_volume * normalized_ratio)
            else:
                adjusted_closes.append(normalized_close)
                adjusted_volumes.append(normalized_volume)

        return adjusted_closes, adjusted_volumes

    def validate_universe_consistency(
        self,
        reports: Sequence[AdjustmentAuditReport],
    ) -> tuple[bool, str]:
        if not reports:
            return True, "Universe consistent: no reports supplied."

        declared_modes = {report.declared_series_mode for report in reports}
        known_modes = declared_modes - {SeriesAdjustmentMode.UNKNOWN.value}
        if len(known_modes) > 1:
            message = f"MIXED DECLARED SERIES MODES in universe: {sorted(known_modes)}."
            logger.error(message)
            return False, message

        types = {report.detected_adjustment_type for report in reports}
        if len(types) <= 1:
            return True, f"Universe consistent: all symbols are {next(iter(types))}."
        message = f"MIXED ADJUSTMENT TYPES in universe: {types}."
        logger.error(message)
        return False, message
