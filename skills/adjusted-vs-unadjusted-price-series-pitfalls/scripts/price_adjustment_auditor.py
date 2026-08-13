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
            calendar_date.fromisoformat(normalized_date)
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
        if normalized_action_type == "DIVIDEND" and normalized_ratio < 0.0:
            raise ValueError("dividend amount cannot be negative")

        object.__setattr__(self, "date", normalized_date)
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
    ):
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

        normalized_dates = []
        for current_date in dates:
            if not isinstance(current_date, str):
                raise TypeError("dates must contain ISO date strings")
            normalized_date = current_date.strip()
            try:
                calendar_date.fromisoformat(normalized_date)
            except ValueError as exc:
                raise ValueError("dates must contain ISO date strings") from exc
            if normalized_dates and normalized_date <= normalized_dates[-1]:
                raise ValueError("dates must be strictly increasing")
            normalized_dates.append(normalized_date)

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

    def _action_matches(
        self,
        action: CorporateAction,
        previous_close: float,
        next_open: float,
    ) -> bool:
        observed_ratio = next_open / previous_close
        if action.action_type == "SPLIT":
            expected_ratio = 1.0 / action.ratio
        else:
            expected_ratio = (previous_close - action.ratio) / previous_close
            if expected_ratio <= 0.0:
                return False
        return self._relative_match(
            observed_ratio,
            expected_ratio,
            self.volume_ratio_tolerance_pct,
        )

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
            matching_actions = [
                action
                for action in actions
                if self._action_matches(action, previous_close, next_open)
            ]
            action_match = bool(matching_actions) if actions else None

            if split_actions:
                split_action = split_actions[0]
                likely_cause = f"SPLIT (ratio={split_action.ratio:g})"
                if normalized_volumes[index - 1] > 0.0:
                    observed_volume_ratio = (
                        normalized_volumes[index] / normalized_volumes[index - 1]
                    )
                    volume_consistent = self._relative_match(
                        observed_volume_ratio,
                        split_action.ratio,
                        self.volume_ratio_tolerance_pct,
                    )
                else:
                    volume_consistent = False
            elif dividend_actions:
                dividend_action = dividend_actions[0]
                likely_cause = (
                    f"DIVIDEND_CASH_EVENT (amount={dividend_action.ratio:g})"
                )
                volume_consistent = True
            else:
                likely_cause = "UNKNOWN â€” possible corporate action or data error"
                if normalized_volumes[index - 1] > 0.0:
                    observed_volume_ratio = (
                        normalized_volumes[index] / normalized_volumes[index - 1]
                    )
                    volume_consistent = self._relative_match(
                        observed_volume_ratio,
                        1.0 / observed_ratio,
                        self.volume_ratio_tolerance_pct,
                    )
                else:
                    volume_consistent = False

            if actions:
                if not action_match or not all(
                    self._is_expected_for_mode(action.action_type)
                    for action in matching_actions
                ):
                    unexplained_discontinuities += 1
            else:
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
                )
            )

        if self.series_mode != SeriesAdjustmentMode.UNKNOWN:
            detected_type = self.series_mode.value
        elif discontinuities:
            detected_type = (
                "UNADJUSTED"
                if any(event.action_match for event in discontinuities)
                else "UNKNOWN"
            )
        else:
            detected_type = SeriesAdjustmentMode.UNKNOWN.value

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
        )

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
        if len(declared_modes) > 1 and SeriesAdjustmentMode.UNKNOWN.value not in declared_modes:
            message = f"MIXED DECLARED SERIES MODES in universe: {declared_modes}."
            logger.error(message)
            return False, message

        types = {report.detected_adjustment_type for report in reports}
        if len(types) <= 1:
            return True, f"Universe consistent: all symbols are {next(iter(types))}."
        message = f"MIXED ADJUSTMENT TYPES in universe: {types}."
        logger.error(message)
        return False, message
