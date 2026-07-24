"""
counterparty-and-broker-concentration-risk: Limits exposure to any single broker/custodian
to bound counterparty risk, not just market risk.
"""
from dataclasses import dataclass, field
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Counterparty:
    name: str
    capital_held: float  # USD held at this counterparty


@dataclass
class ConcentrationCheckResult:
    approved: bool
    counterparty: str
    current_concentration_pct: float
    projected_concentration_pct: float
    max_allowed_pct: float
    rejection_reason: Optional[str] = None


class CounterpartyConcentrationMonitor:
    """
    Monitors and enforces counterparty concentration limits across
    multiple brokers/custodians.
    """

    def __init__(self, max_single_counterparty_pct: float = 0.40):
        """
        Args:
            max_single_counterparty_pct: Maximum capital at any single counterparty (default 40%).
        """
        self.max_single_counterparty_pct = max_single_counterparty_pct
        self.counterparties: Dict[str, Counterparty] = {}

    def register_counterparty(self, name: str, capital_held: float = 0.0) -> None:
        """Register a counterparty with initial capital held."""
        self.counterparties[name] = Counterparty(name=name, capital_held=capital_held)

    def update_capital(self, name: str, capital_held: float) -> None:
        """Update capital held at a counterparty."""
        if name not in self.counterparties:
            raise ValueError(f"Unknown counterparty: '{name}'")
        self.counterparties[name].capital_held = capital_held

    def get_total_aum(self) -> float:
        """Calculate total AUM across all counterparties."""
        return sum(c.capital_held for c in self.counterparties.values())

    def check_allocation(
        self,
        counterparty_name: str,
        additional_capital: float,
    ) -> ConcentrationCheckResult:
        """Check if additional capital allocation to a counterparty is allowed."""
        if counterparty_name not in self.counterparties:
            return ConcentrationCheckResult(
                approved=False,
                counterparty=counterparty_name,
                current_concentration_pct=0.0,
                projected_concentration_pct=0.0,
                max_allowed_pct=self.max_single_counterparty_pct,
                rejection_reason=f"Unknown counterparty: '{counterparty_name}'",
            )

        total_aum = self.get_total_aum() + additional_capital
        if total_aum <= 0:
            return ConcentrationCheckResult(
                approved=False,
                counterparty=counterparty_name,
                current_concentration_pct=0.0,
                projected_concentration_pct=0.0,
                max_allowed_pct=self.max_single_counterparty_pct,
                rejection_reason="Total AUM is zero or negative.",
            )

        current_capital = self.counterparties[counterparty_name].capital_held
        current_conc = current_capital / (total_aum - additional_capital) if (total_aum - additional_capital) > 0 else 0.0
        projected_capital = current_capital + additional_capital
        projected_conc = projected_capital / total_aum

        if projected_conc > self.max_single_counterparty_pct + 1e-9:
            reason = (
                f"COUNTERPARTY CONCENTRATION BREACH: '{counterparty_name}' projected at "
                f"{projected_conc:.1%} of AUM (${projected_capital:,.0f} / ${total_aum:,.0f}), "
                f"exceeding limit {self.max_single_counterparty_pct:.0%}. Transfer blocked."
            )
            logger.warning(reason)
            return ConcentrationCheckResult(
                approved=False,
                counterparty=counterparty_name,
                current_concentration_pct=current_conc,
                projected_concentration_pct=projected_conc,
                max_allowed_pct=self.max_single_counterparty_pct,
                rejection_reason=reason,
            )

        return ConcentrationCheckResult(
            approved=True,
            counterparty=counterparty_name,
            current_concentration_pct=current_conc,
            projected_concentration_pct=projected_conc,
            max_allowed_pct=self.max_single_counterparty_pct,
        )

    def get_concentration_report(self) -> List[Dict]:
        """Generate concentration report for all counterparties."""
        total_aum = self.get_total_aum()
        report = []
        for name, cp in self.counterparties.items():
            conc = cp.capital_held / total_aum if total_aum > 0 else 0.0
            report.append({
                "counterparty": name,
                "capital_held": cp.capital_held,
                "concentration_pct": conc,
                "limit_pct": self.max_single_counterparty_pct,
                "breached": conc > self.max_single_counterparty_pct,
            })
        return report
