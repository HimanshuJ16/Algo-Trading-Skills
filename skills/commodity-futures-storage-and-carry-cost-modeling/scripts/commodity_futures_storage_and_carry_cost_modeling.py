"""
commodity-futures-storage-and-carry-cost-modeling: Cost-of-carry pricing and
convenience-yield extraction for physical commodity futures.

The full-carry price (S0 + PV of fixed storage) * exp((r + c) * T) is the *upper*
no-arbitrage bound for a consumption commodity, not an equality. Only a market
futures price above that bound is an enforceable arbitrage (cash-and-carry: buy
spot, store, sell futures). A price below it merely implies a positive convenience
yield, which is the normal state of a backwardated market and cannot be arbitraged
without borrowing the physical commodity. See references/standards.md.
"""
import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Below this maturity the 1/T term in the implied-yield inversion amplifies any
# spot/futures timestamp mismatch into an implausible annualised yield.
MIN_RELIABLE_MATURITY_YEARS = 1.0 / 365.0

# Rates within this band of zero are treated as zero when integrating the present
# value of a continuously accrued fixed storage charge (removes the 0/0 limit).
ZERO_RATE_EPSILON = 1e-12


@dataclass
class CarryCostResult:
    spot_price: float
    futures_market_price: float
    theoretical_futures_price: float      # priced at the caller's baseline convenience yield
    full_carry_price: float               # no-arbitrage UPPER bound (implied y == 0)
    time_to_maturity_years: float
    implied_convenience_yield: float
    regime: str                           # 'CONTANGO', 'BACKWARDATION' or 'FLAT'
    basis: float                          # Spot - Futures
    is_arbitrage_opportunity: bool        # True only for enforceable cash-and-carry
    arbitrage_type: Optional[str]         # 'CASH_AND_CARRY' or None
    convenience_yield_bound_violated: bool  # implied y < 0: futures above full carry
    reverse_carry_candidate: bool         # cheap vs baseline view; NOT a no-arb violation


def _require_positive_finite(name: str, value: float) -> float:
    """Rejects NaN/Inf as well as non-positive values.

    ``nan <= 0`` evaluates to False, so a plain positivity check lets NaN through
    and the model returns NaN prices alongside a confidently wrong regime label
    instead of failing.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    if numeric <= 0:
        raise ValueError(f"{name} must be positive, got {numeric}.")
    return numeric


def _require_finite(name: str, value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return numeric


class CommodityCarryCostModel:
    """
    Cost of Carry and Convenience Yield Pricing Engine for Commodity Futures.

    Full carry:  F_full = (S0 + U) * exp((r + c) * T)
    With yield:  F(y)   = F_full * exp(-y * T)
    Implied y:   y      = ln(F_full / F_market) / T

    ``c`` is a proportional storage rate (fraction of spot per year). ``U`` is the
    present value of a fixed storage charge in currency per unit per year, which is
    how exchanges actually regulate physical storage (CBOT caps grain storage in
    fractions of a cent per bushel per day). Either or both may be used; with
    ``storage_cost_per_unit_per_year = 0`` the model reduces to the standard
    proportional form S0 * exp((r + c - y) * T).
    """

    def __init__(
        self,
        risk_free_rate: float = 0.05,
        storage_cost_rate: float = 0.02,
        storage_cost_per_unit_per_year: float = 0.0,
    ) -> None:
        # Continuously compounded annual rate. The caller MUST convert it to the
        # same day-count basis used for time_to_maturity_years.
        self.risk_free_rate = _require_finite("risk_free_rate", risk_free_rate)
        # Annualised proportional storage cost (e.g. 0.02 == 2% of spot per year).
        self.storage_cost_rate = _require_finite("storage_cost_rate", storage_cost_rate)
        # Annualised fixed storage charge in currency per unit (e.g. USD per barrel).
        self.storage_cost_per_unit_per_year = _require_finite(
            "storage_cost_per_unit_per_year", storage_cost_per_unit_per_year
        )
        if self.storage_cost_per_unit_per_year < 0:
            raise ValueError("storage_cost_per_unit_per_year must not be negative.")

    def present_value_of_fixed_storage(self, time_to_maturity_years: float) -> float:
        """
        PV of a fixed storage charge accruing continuously at
        ``storage_cost_per_unit_per_year`` over [0, T], discounted at the risk-free
        rate:  U = q * (1 - exp(-rT)) / r, with the limit U -> q * T as r -> 0.
        """
        T = _require_positive_finite("time_to_maturity_years", time_to_maturity_years)
        q = self.storage_cost_per_unit_per_year
        if q == 0.0:
            return 0.0
        r = self.risk_free_rate
        if abs(r) < ZERO_RATE_EPSILON:
            return q * T
        return q * (1.0 - math.exp(-r * T)) / r

    def calculate_full_carry_price(
        self,
        spot_price: float,
        time_to_maturity_years: float,
    ) -> float:
        """
        Full-carry (zero convenience yield) futures price. For a consumption
        commodity this is the no-arbitrage UPPER bound on the futures price, because
        the cash-and-carry leg (buy spot, store, sell futures) is executable by
        anyone with capital and storage. The mirror-image lower bound is not
        enforceable without borrowing the physical commodity.
        """
        S0 = _require_positive_finite("spot_price", spot_price)
        T = _require_positive_finite("time_to_maturity_years", time_to_maturity_years)
        U = self.present_value_of_fixed_storage(T)
        return (S0 + U) * math.exp((self.risk_free_rate + self.storage_cost_rate) * T)

    def calculate_theoretical_futures_price(
        self,
        spot_price: float,
        time_to_maturity_years: float,
        convenience_yield: float,
    ) -> float:
        """
        Theoretical futures price at an assumed convenience yield:
        F = (S0 + U) * exp((r + c) * T) * exp(-y * T).

        The convenience yield is an assumption supplied by the caller, not an
        observable, so this price is a view. A deviation from it is not by itself
        an arbitrage.
        """
        y = _require_finite("convenience_yield", convenience_yield)
        T = _require_positive_finite("time_to_maturity_years", time_to_maturity_years)
        full_carry = self.calculate_full_carry_price(spot_price, T)
        return full_carry * math.exp(-y * T)

    def extract_implied_convenience_yield(
        self,
        spot_price: float,
        futures_market_price: float,
        time_to_maturity_years: float,
    ) -> float:
        """
        Solves for the implied convenience yield y from the observed futures price:
        y = ln(F_full / F_market) / T.

        A negative result means the futures price sits above full carry, i.e. the
        consumption-asset no-arbitrage bound is violated. In practice that is far
        more often a stale or non-synchronous spot quote, an understated storage or
        financing cost, or a spot grade that is not deliverable against the
        contract, than a real riskless profit.
        """
        F = _require_positive_finite("futures_market_price", futures_market_price)
        T = _require_positive_finite("time_to_maturity_years", time_to_maturity_years)
        if T < MIN_RELIABLE_MATURITY_YEARS:
            logger.warning(
                "time_to_maturity_years=%.6g is below one day; the 1/T inversion "
                "makes the implied convenience yield highly sensitive to quote noise.",
                T,
            )
        full_carry = self.calculate_full_carry_price(spot_price, T)
        return math.log(full_carry / F) / T

    def evaluate_market(
        self,
        spot_price: float,
        futures_market_price: float,
        time_to_maturity_years: float,
        expected_baseline_convenience_yield: float = 0.01,
        transaction_cost_pct: float = 0.005,
    ) -> CarryCostResult:
        """
        Classifies the term-structure regime and audits the futures price against
        the full-carry no-arbitrage bound.

        ``is_arbitrage_opportunity`` is set only when the futures price exceeds full
        carry by more than ``transaction_cost_pct``, the one direction a holder of
        capital and storage can actually enforce. A futures price *below* the
        baseline-view price sets ``reverse_carry_candidate`` instead: reverse
        cash-and-carry requires selling the physical short, which is generally
        impossible for a consumption commodity and is only actionable for an
        existing inventory holder or in a commodity with a genuine lease market
        such as gold. It is deliberately not reported as an arbitrage.
        """
        S0 = _require_positive_finite("spot_price", spot_price)
        F = _require_positive_finite("futures_market_price", futures_market_price)
        T = _require_positive_finite("time_to_maturity_years", time_to_maturity_years)
        y_baseline = _require_finite(
            "expected_baseline_convenience_yield", expected_baseline_convenience_yield
        )
        tc = _require_finite("transaction_cost_pct", transaction_cost_pct)
        if tc < 0:
            raise ValueError("transaction_cost_pct must not be negative.")

        full_carry = self.calculate_full_carry_price(S0, T)
        implied_y = self.extract_implied_convenience_yield(S0, F, T)
        theoretical_f = full_carry * math.exp(-y_baseline * T)

        if F > S0:
            regime = "CONTANGO"
        elif F < S0:
            regime = "BACKWARDATION"
        else:
            regime = "FLAT"

        bound_violated = implied_y < 0.0
        if bound_violated:
            logger.warning(
                "Implied convenience yield %.6f is negative: futures %.4f is above "
                "full carry %.4f. Verify spot/futures timestamp sync, deliverability "
                "and storage/financing inputs before acting.",
                implied_y, F, full_carry,
            )

        # Enforceable leg only: futures rich to full carry beyond round-trip costs.
        is_arbitrage = F > full_carry * (1.0 + tc)
        arb_type = "CASH_AND_CARRY" if is_arbitrage else None

        # Directional view versus the caller's baseline yield, not a no-arb breach.
        reverse_candidate = F < theoretical_f * (1.0 - tc)

        return CarryCostResult(
            spot_price=round(S0, 4),
            futures_market_price=round(F, 4),
            theoretical_futures_price=round(theoretical_f, 4),
            full_carry_price=round(full_carry, 4),
            time_to_maturity_years=round(T, 6),
            implied_convenience_yield=round(implied_y, 6),
            regime=regime,
            basis=round(S0 - F, 4),
            is_arbitrage_opportunity=is_arbitrage,
            arbitrage_type=arb_type,
            convenience_yield_bound_violated=bound_violated,
            reverse_carry_candidate=reverse_candidate,
        )
