"""
explainability-for-live-trading-signals: Production-grade ML signal attribution engine,
SHAP/contribution feature ranker, natural language audit generator, and compliance logger.
"""
from dataclasses import dataclass, field
import datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SignalExplainerError(ValueError):
    """Raised when feature contribution mapping is invalid or inconsistent."""
    pass


@dataclass
class FeatureContribution:
    feature_name: str
    feature_value: float
    contribution: float
    direction: str  # "BULLISH", "BEARISH", "NEUTRAL"


@dataclass
class SignalExplanation:
    timestamp: float
    symbol: str
    signal_action: str  # "BUY", "SELL", "HOLD"
    prediction_score: float
    base_value: float
    top_bullish_drivers: List[FeatureContribution]
    top_bearish_drivers: List[FeatureContribution]
    natural_language_summary: str
    all_contributions: Dict[str, float]

    def to_json_audit(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "symbol": self.symbol,
                "action": self.signal_action,
                "score": self.prediction_score,
                "base_value": self.base_value,
                "top_bullish": [
                    {"feature": f.feature_name, "val": f.feature_value, "contrib": f.contribution}
                    for f in self.top_bullish_drivers
                ],
                "top_bearish": [
                    {"feature": f.feature_name, "val": f.feature_value, "contrib": f.contribution}
                    for f in self.top_bearish_drivers
                ],
                "summary": self.natural_language_summary,
            },
            indent=2,
        )


class LiveSignalExplainer:
    """
    Decomposes raw ML trading signal prediction scores into local feature attributions
    and generates compliance-ready natural language audit summaries.
    """

    def __init__(
        self,
        base_value: float = 0.0,
        buy_threshold: float = 0.50,
        sell_threshold: float = -0.50,
        top_n_drivers: int = 3,
    ):
        self.base_value = base_value
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.top_n_drivers = top_n_drivers

    def explain_signal(
        self,
        symbol: str,
        feature_dict: Dict[str, float],
        contributions_dict: Dict[str, float],
        timestamp: Optional[float] = None,
    ) -> SignalExplanation:
        """
        Decomposes signal output score into feature attributions and generates natural language summary.
        """
        if timestamp is None:
            timestamp = datetime.datetime.now().timestamp()

        # 1. Compute total prediction score: BaseValue + sum(contributions)
        sum_contrib = sum(contributions_dict.values())
        prediction_score = round(self.base_value + sum_contrib, 4)

        # 2. Determine signal action
        if prediction_score >= self.buy_threshold:
            action = "BUY"
        elif prediction_score <= self.sell_threshold:
            action = "SELL"
        else:
            action = "HOLD"

        # 3. Classify and rank feature contributions
        bullish: List[FeatureContribution] = []
        bearish: List[FeatureContribution] = []

        for name, contrib in contributions_dict.items():
            f_val = feature_dict.get(name, 0.0)
            if contrib > 0.001:
                bullish.append(
                    FeatureContribution(
                        feature_name=name, feature_value=f_val, contribution=contrib, direction="BULLISH"
                    )
                )
            elif contrib < -0.001:
                bearish.append(
                    FeatureContribution(
                        feature_name=name, feature_value=f_val, contribution=contrib, direction="BEARISH"
                    )
                )

        bullish.sort(key=lambda x: x.contribution, reverse=True)
        bearish.sort(key=lambda x: x.contribution)  # Most negative first

        top_bull = bullish[: self.top_n_drivers]
        top_bear = bearish[: self.top_n_drivers]

        # 4. Generate Natural Language Audit Summary
        summary_parts = [f"{action} signal ({prediction_score:+.2f}) for '{symbol}'"]

        if top_bull:
            bull_str = ", ".join([f"{f.feature_name} ({f.contribution:+.2f})" for f in top_bull])
            summary_parts.append(f"driven by {bull_str}")

        if top_bear:
            bear_str = ", ".join([f"{f.feature_name} ({f.contribution:+.2f})" for f in top_bear])
            summary_parts.append(f"offset by {bear_str}")

        nl_summary = " ".join(summary_parts) + "."

        logger.info(f"Signal Explained [{symbol}]: {nl_summary}")

        return SignalExplanation(
            timestamp=timestamp,
            symbol=symbol.upper(),
            signal_action=action,
            prediction_score=prediction_score,
            base_value=self.base_value,
            top_bullish_drivers=top_bull,
            top_bearish_drivers=top_bear,
            natural_language_summary=nl_summary,
            all_contributions=contributions_dict,
        )
