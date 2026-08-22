"""Pre-trade gate for the CFTC Regulation 4.13(a)(3) de minimis CPO exemption.

Scope: this module evaluates ONLY the quantitative trading tests of
17 CFR 4.13(a)(3)(ii) -- the 5% margin test (A) and the 100% net notional
test (B). It does not evaluate the non-quantitative conditions of the
exemption (private offering, participant eligibility, marketing restriction)
nor the notice-filing obligations of 17 CFR 4.13(b). See SKILL.md.
"""
import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 17 CFR 4.13(a)(3)(ii)(A) and (B).
MARGIN_TEST_THRESHOLD = 0.05
NOTIONAL_TEST_THRESHOLD = 1.00

# Absolute tolerance used when checking that a signed exposure delta does not
# drive a projected aggregate below zero purely through floating-point noise.
_NEGATIVE_AGGREGATE_TOLERANCE = 1e-6


@dataclass
class PortfolioState:
    """Current pool state, all values in the pool's reporting currency.

    liquidation_value:
        Liquidation value of the pool's portfolio *after* taking into account
        unrealized profits and losses, as required by 4.13(a)(3)(ii).
    current_commodity_initial_margin:
        Aggregate initial margin, option premiums, and required minimum
        security deposit for retail forex of all open commodity interest
        positions. Non-negative.
    current_commodity_notional:
        Aggregate notional value of all open commodity interest positions,
        computed per 4.13(a)(3)(ii)(B). Non-negative (exposure magnitude;
        direction is not encoded in the sign -- see ProposedTrade).
    """
    liquidation_value: float
    current_commodity_initial_margin: float
    current_commodity_notional: float


@dataclass
class ProposedTrade:
    """A trade whose effect on the pool's aggregates is being tested.

    required_initial_margin and notional_value are SIGNED DELTAS applied to
    the corresponding portfolio aggregate:

      * positive -- the trade adds exposure (opening or increasing a position);
      * negative -- the trade releases exposure (closing or offsetting a
        position), expressed as the magnitude released.

    Direction of the position (long vs short) is NOT encoded in the sign: a new
    short future adds positive notional exactly like a new long future, because
    this engine aggregates gross exposure (see references/standards.md on
    netting).
    """
    is_commodity_interest: bool
    required_initial_margin: float
    notional_value: float


@dataclass(frozen=True)
class ComplianceDecision:
    """Auditable record of a single 4.13(a)(3)(ii) evaluation."""
    allowed: bool
    reason: str
    passes_margin_test: bool
    passes_notional_test: bool
    projected_commodity_initial_margin: float
    projected_commodity_notional: float
    margin_ratio: float
    notional_ratio: float


def _require_finite(value: float, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return numeric


def _require_non_negative(value: float, label: str) -> float:
    numeric = _require_finite(value, label)
    if numeric < 0:
        raise ValueError(f"{label} must be non-negative, got {numeric!r}")
    return numeric


class CftcCpoComplianceEngine:
    """Evaluates a proposed trade against 17 CFR 4.13(a)(3)(ii).

    A pool operator relying on the de minimis exemption must, at all times,
    satisfy AT LEAST ONE of the two tests, each determined at the time the
    most recent position was established:

      (A) aggregate initial margin, premiums, and required minimum security
          deposit for retail forex <= 5% of liquidation value; or
      (B) aggregate net notional value <= 100% of liquidation value.

    Because the tests are measured when a position is established, this engine
    is a pre-trade gate: it evaluates the aggregates the pool *would* hold if
    the proposed trade were executed.

    Conservatism: the engine aggregates GROSS notional. 4.13(a)(3)(ii)(B)
    permits netting futures on the same underlying commodity across designated
    contract markets and foreign boards of trade, and swaps cleared on the same
    derivatives clearing organization. Feeding gross figures can therefore
    reject a trade the rule would have permitted; it never permits a trade the
    rule forbids. Callers that net upstream should pass already-netted figures.
    """

    def __init__(
        self,
        margin_threshold: float = MARGIN_TEST_THRESHOLD,
        notional_threshold: float = NOTIONAL_TEST_THRESHOLD,
    ) -> None:
        self.margin_threshold = _require_non_negative(margin_threshold, "margin_threshold")
        self.notional_threshold = _require_non_negative(notional_threshold, "notional_threshold")
        # Tighter thresholds are a legitimate internal buffer; looser ones are not
        # available under 4.13(a)(3)(ii) and must be visible in the audit trail.
        if (self.margin_threshold > MARGIN_TEST_THRESHOLD
                or self.notional_threshold > NOTIONAL_TEST_THRESHOLD):
            logger.warning(
                "Engine configured with thresholds looser than 17 CFR 4.13(a)(3)(ii) "
                "(margin %.4f vs %.4f, notional %.4f vs %.4f); decisions do not evidence "
                "the de minimis exemption.",
                self.margin_threshold, MARGIN_TEST_THRESHOLD,
                self.notional_threshold, NOTIONAL_TEST_THRESHOLD,
            )

    def check_trade_compliance(self, portfolio: PortfolioState, trade: ProposedTrade) -> bool:
        """Return True if the trade may be routed under the de minimis exemption.

        Fail-closed wrapper around :meth:`evaluate_trade`: structurally invalid
        input (NaN, infinity, negative aggregates) is logged and blocks the
        trade rather than propagating into the order path.
        """
        try:
            return self.evaluate_trade(portfolio, trade).allowed
        except ValueError as exc:
            logger.critical("Trade BLOCKED: invalid CPO exemption inputs (%s).", exc)
            return False

    def evaluate_trade(self, portfolio: PortfolioState, trade: ProposedTrade) -> ComplianceDecision:
        """Evaluate the trade and return an auditable :class:`ComplianceDecision`.

        Raises:
            ValueError: if any input is non-finite, if a portfolio aggregate is
                negative, or if the trade's signed deltas would drive a
                projected aggregate below zero (an inconsistent position book).
        """
        liquidation_value = _require_finite(portfolio.liquidation_value, "liquidation_value")
        current_margin = _require_non_negative(
            portfolio.current_commodity_initial_margin, "current_commodity_initial_margin")
        current_notional = _require_non_negative(
            portfolio.current_commodity_notional, "current_commodity_notional")
        margin_delta = _require_finite(trade.required_initial_margin, "required_initial_margin")
        notional_delta = _require_finite(trade.notional_value, "notional_value")

        if not trade.is_commodity_interest:
            # Securities, cash bonds, and spot positions are outside the
            # numerator of both tests; they still contribute to liquidation
            # value, which the caller supplies.
            return self._decision(
                allowed=True,
                reason="Not a commodity interest; outside the 4.13(a)(3)(ii) numerators.",
                projected_margin=current_margin,
                projected_notional=current_notional,
                liquidation_value=liquidation_value,
                log=False,
            )

        projected_margin = self._project(current_margin, margin_delta, "initial margin")
        projected_notional = self._project(current_notional, notional_delta, "notional")

        # A trade that does not increase either aggregate can never move the
        # pool further outside the exemption. Blocking such a trade would trap
        # an already-breaching pool in the state that requires registration, so
        # it is allowed even when the pool currently fails both tests or has a
        # non-positive liquidation value.
        if (projected_margin <= current_margin
                and projected_notional <= current_notional
                and (projected_margin < current_margin or projected_notional < current_notional)):
            return self._decision(
                allowed=True,
                reason="Risk-reducing trade; does not increase either 4.13(a)(3)(ii) aggregate.",
                projected_margin=projected_margin,
                projected_notional=projected_notional,
                liquidation_value=liquidation_value,
                log=False,
            )

        if liquidation_value <= 0:
            return self._decision(
                allowed=False,
                reason=(
                    "Liquidation value is zero or negative; no de minimis headroom "
                    "can be established for new exposure."
                ),
                projected_margin=projected_margin,
                projected_notional=projected_notional,
                liquidation_value=liquidation_value,
                log=True,
            )

        # Compare cross-multiplied rather than dividing, so that a position
        # sitting exactly on a threshold is not rejected by rounding.
        passes_margin = projected_margin <= self.margin_threshold * liquidation_value
        passes_notional = projected_notional <= self.notional_threshold * liquidation_value

        if passes_margin or passes_notional:
            carried_by = "margin test (A)" if passes_margin else "notional test (B)"
            return self._decision(
                allowed=True,
                reason=f"Within de minimis limits via {carried_by}.",
                projected_margin=projected_margin,
                projected_notional=projected_notional,
                liquidation_value=liquidation_value,
                passes_margin=passes_margin,
                passes_notional=passes_notional,
                log=False,
            )

        return self._decision(
            allowed=False,
            reason=(
                "Both 4.13(a)(3)(ii) tests fail; executing would forfeit the de minimis "
                "exemption and operate the pool as an unregistered CPO."
            ),
            projected_margin=projected_margin,
            projected_notional=projected_notional,
            liquidation_value=liquidation_value,
            passes_margin=passes_margin,
            passes_notional=passes_notional,
            log=True,
        )

    @staticmethod
    def _project(current: float, delta: float, label: str) -> float:
        projected = current + delta
        if projected < -_NEGATIVE_AGGREGATE_TOLERANCE:
            raise ValueError(
                f"Trade releases more aggregate {label} ({delta}) than the pool holds "
                f"({current}); position book and proposed trade are inconsistent")
        return max(projected, 0.0)

    def _decision(
        self,
        allowed: bool,
        reason: str,
        projected_margin: float,
        projected_notional: float,
        liquidation_value: float,
        passes_margin: Optional[bool] = None,
        passes_notional: Optional[bool] = None,
        log: bool = False,
    ) -> ComplianceDecision:
        if liquidation_value > 0:
            margin_ratio = projected_margin / liquidation_value
            notional_ratio = projected_notional / liquidation_value
        else:
            margin_ratio = math.inf if projected_margin > 0 else 0.0
            notional_ratio = math.inf if projected_notional > 0 else 0.0

        if passes_margin is None:
            passes_margin = liquidation_value > 0 and (
                projected_margin <= self.margin_threshold * liquidation_value)
        if passes_notional is None:
            passes_notional = liquidation_value > 0 and (
                projected_notional <= self.notional_threshold * liquidation_value)

        decision = ComplianceDecision(
            allowed=allowed,
            reason=reason,
            passes_margin_test=passes_margin,
            passes_notional_test=passes_notional,
            projected_commodity_initial_margin=projected_margin,
            projected_commodity_notional=projected_notional,
            margin_ratio=margin_ratio,
            notional_ratio=notional_ratio,
        )
        message = (
            "CPO de minimis evaluation: allowed=%s liquidation_value=%r "
            "projected_margin=%r (ratio %.6f, limit %.4f, pass=%s) "
            "projected_notional=%r (ratio %.6f, limit %.4f, pass=%s) -- %s"
        )
        args = (
            allowed, liquidation_value,
            projected_margin, margin_ratio, self.margin_threshold, passes_margin,
            projected_notional, notional_ratio, self.notional_threshold, passes_notional,
            reason,
        )
        if log:
            logger.critical(message, *args)
        else:
            logger.info(message, *args)
        return decision
