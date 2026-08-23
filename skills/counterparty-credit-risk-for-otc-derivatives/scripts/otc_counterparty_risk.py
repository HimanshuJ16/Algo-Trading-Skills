"""SA-CCR-grounded counterparty credit risk engine for bilateral OTC derivative netting sets.

Grounded in BCBS 279, "The standardised approach for measuring counterparty
credit risk exposures" (Basel Committee on Banking Supervision, March 2014,
rev. April 2014), https://www.bis.org/publ/bcbs279.pdf, consolidated into the
Basel Framework as CRE52:

- Replacement cost (margined netting set), BCBS 279 para 144:
      RC = max(V - C, TH + MTA - NICA, 0)
  An unmargined netting set (or one modelled without a CSA) is represented by
  TH = MTA = NICA = 0, which degenerates to the unmargined form of para 136:
      RC = max(V - C, 0)
- PFE multiplier, para 149:
      multiplier = min(1, 0.05 + 0.95 * exp((V - C) / (2 * 0.95 * AddOn)))
- Exposure at default, para 128:
      EAD = alpha * (RC + PFE), with alpha = 1.4

Deliberate simplifications (do not use for regulatory capital reporting):
- The trade-level add-on aggregate is approximated by
  sum(notional_i * supervisory_factor_i): no duration-based adjusted
  notionals, supervisory deltas, maturity factors, or hedging-set
  correlation aggregation (BCBS 279 paras 151-184).
- Consequently the para 129 cap of margined EAD at unmargined EAD is not
  applied (the cap only binds because margined maturity factors are below 1,
  which this engine does not model).
- Collateral is taken at value without haircuts, and the CSA is assumed
  one-way (the counterparty posts; we never do).
- CVA is a single-period, undiscounted proxy, CVA = (1 - R) * EAD * PD, not
  the canonical time-bucketed sum of discounted expected exposures.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)

# SA-CCR supervisory factors, BCBS 279 Table 2 (para 183). Credit single-name
# factors are rating-dependent (0.38% to 6.0%) and must be looked up per
# counterparty; "basis" positions halve and "volatility" positions multiply by
# 5 the factor of the primary risk factor. Crypto has no SA-CCR asset class.
SA_CCR_SUPERVISORY_FACTORS: Dict[str, float] = {
    "INTEREST_RATE": 0.005,        # 0.50%
    "FX": 0.04,                    # 4.0%
    "EQUITY_SINGLE": 0.32,         # 32%
    "EQUITY_INDEX": 0.20,          # 20%
    "CREDIT_INDEX_IG": 0.0038,     # 0.38%
    "CREDIT_INDEX_SG": 0.0106,     # 1.06%
    "COMMODITY_ELECTRICITY": 0.40, # 40%
    "COMMODITY_OTHER": 0.18,       # 18%
}

_PFE_MULTIPLIER_FLOOR = 0.05


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


@dataclass
class OtcContract:
    contract_id: str
    asset_class: str                   # 'EQUITY', 'FX', 'RATES', 'COMMODITY', 'CREDIT', 'CRYPTO'
    notional_usd: float
    mtm_value_usd: float
    sa_ccr_add_on_factor: float        # supervisory factor, e.g. 0.32 (equity single-name), 0.04 (FX)

    def __post_init__(self) -> None:
        if not self.contract_id or not self.contract_id.strip():
            raise ValueError("contract_id must be a non-empty string")
        if not self.asset_class or not self.asset_class.strip():
            raise ValueError(f"asset_class must be a non-empty string for {self.contract_id!r}")
        _require_finite(f"notional_usd for {self.contract_id!r}", self.notional_usd)
        if self.notional_usd < 0:
            raise ValueError(f"notional_usd must be >= 0 for {self.contract_id!r}, got {self.notional_usd}")
        _require_finite(f"mtm_value_usd for {self.contract_id!r}", self.mtm_value_usd)
        _require_finite(f"sa_ccr_add_on_factor for {self.contract_id!r}", self.sa_ccr_add_on_factor)
        if not 0.0 <= self.sa_ccr_add_on_factor <= 1.0:
            raise ValueError(
                f"sa_ccr_add_on_factor must be within [0, 1] for {self.contract_id!r}, "
                f"got {self.sa_ccr_add_on_factor}"
            )


@dataclass
class CsaTerms:
    netting_set_id: str
    threshold_usd: float               # Uncollateralized threshold (e.g. $100,000)
    minimum_transfer_amount: float     # MTA (e.g. $50,000)
    posted_collateral_usd: float
    counterparty_pd: float            # Annual Probability of Default (e.g. 0.02 = 2%)
    recovery_rate: float               # e.g. 0.40 (40%, ISDA CDS Standard Model convention)
    net_independent_collateral_usd: float = 0.0  # NICA: net independent collateral received (may be negative)

    def __post_init__(self) -> None:
        if not self.netting_set_id or not self.netting_set_id.strip():
            raise ValueError("netting_set_id must be a non-empty string")
        for name in ("threshold_usd", "minimum_transfer_amount", "posted_collateral_usd"):
            value = getattr(self, name)
            _require_finite(f"{name} for {self.netting_set_id!r}", value)
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        _require_finite(f"net_independent_collateral_usd for {self.netting_set_id!r}",
                        self.net_independent_collateral_usd)
        _require_finite(f"counterparty_pd for {self.netting_set_id!r}", self.counterparty_pd)
        if not 0.0 <= self.counterparty_pd <= 1.0:
            raise ValueError(f"counterparty_pd must be within [0, 1], got {self.counterparty_pd}")
        _require_finite(f"recovery_rate for {self.netting_set_id!r}", self.recovery_rate)
        if not 0.0 <= self.recovery_rate <= 1.0:
            raise ValueError(f"recovery_rate must be within [0, 1], got {self.recovery_rate}")


@dataclass
class OtcRiskReport:
    netting_set_id: str
    gross_mtm_usd: float
    net_mtm_usd: float
    netted_current_exposure_usd: float
    potential_future_exposure_usd: float
    exposure_at_default_usd: float
    cva_usd: float
    is_margin_call_triggered: bool
    margin_call_amount_usd: float
    is_credit_limit_breached: bool
    pfe_multiplier: float = 1.0


class OtcCounterpartyRiskEngine:
    """
    Evaluates Counterparty Credit Risk (CCR) for bilateral OTC derivatives,
    calculating SA-CCR replacement cost, PFE (add-on x multiplier), EAD
    (alpha x (RC + PFE)), CVA, and CSA margin calls.

    The PFE add-on aggregate is a simplified sum of notional x supervisory
    factor; see the module docstring for the exact simplifications.
    """

    def __init__(self, max_ead_limit_usd: float = 1_000_000.0, alpha: float = 1.4):
        _require_finite("max_ead_limit_usd", max_ead_limit_usd)
        if max_ead_limit_usd <= 0:
            raise ValueError(f"max_ead_limit_usd must be > 0, got {max_ead_limit_usd}")
        _require_finite("alpha", alpha)
        if alpha <= 0:
            raise ValueError(f"alpha must be > 0, got {alpha}")
        self.max_ead_limit_usd = max_ead_limit_usd
        # BCBS 279 para 128: EAD = alpha * (RC + PFE) with alpha = 1.4.
        self.alpha = alpha

    def calculate_current_exposure(self, contracts: List[OtcContract], csa: CsaTerms) -> float:
        """
        Replacement cost per BCBS 279 para 144 (margined netting set):
            RC = max(V - C, TH + MTA - NICA, 0)
        where V is net MTM, C is collateral held, and NICA is net independent
        collateral received. With TH = MTA = NICA = 0 this degenerates to the
        unmargined form RC = max(V - C, 0) of para 136.
        """
        net_mtm = sum(c.mtm_value_usd for c in contracts)
        rc = max(
            net_mtm - csa.posted_collateral_usd,
            csa.threshold_usd + csa.minimum_transfer_amount - csa.net_independent_collateral_usd,
            0.0,
        )
        return float(rc)

    def calculate_pfe(self, contracts: List[OtcContract]) -> float:
        """
        Add-on aggregate across contracts: AddOn = sum(Notional_i * SF_i).

        Simplified: no adjusted notionals, supervisory deltas, maturity
        factors, or hedging-set aggregation (BCBS 279 paras 151-184).
        Use SA_CCR_SUPERVISORY_FACTORS for canonical supervisory factors.
        """
        add_on = sum(c.notional_usd * c.sa_ccr_add_on_factor for c in contracts)
        return float(add_on)

    @staticmethod
    def calculate_pfe_multiplier(net_mtm_usd: float, posted_collateral_usd: float,
                                 add_on_aggregate: float) -> float:
        """
        SA-CCR PFE multiplier, BCBS 279 para 149:
            multiplier = min(1, Floor + (1 - Floor) * exp((V - C) / (2 * (1 - Floor) * AddOn)))
        with Floor = 5%. Recognises over-collateralisation (V - C < 0) by
        scaling down the add-on; returns exactly 1.0 whenever V - C >= 0.
        """
        _require_finite("net_mtm_usd", net_mtm_usd)
        _require_finite("posted_collateral_usd", posted_collateral_usd)
        _require_finite("add_on_aggregate", add_on_aggregate)
        if add_on_aggregate <= 0.0:
            return 1.0
        net_collateral = net_mtm_usd - posted_collateral_usd
        if net_collateral >= 0.0:
            return 1.0
        exponent = net_collateral / (2.0 * (1.0 - _PFE_MULTIPLIER_FLOOR) * add_on_aggregate)
        multiplier = _PFE_MULTIPLIER_FLOOR + (1.0 - _PFE_MULTIPLIER_FLOOR) * math.exp(exponent)
        return float(min(1.0, multiplier))

    def calculate_cva(self, ead: float, pd: float, recovery_rate: float) -> float:
        """
        Single-period, undiscounted CVA proxy: CVA = (1 - R) * EAD * PD.

        The canonical risk-neutral CVA discounts time-bucketed expected
        exposures weighted by marginal default probabilities; this proxy uses
        EAD as the exposure measure and a single cumulative PD. Treat the
        result as an upper-bound approximation for short horizons only.
        """
        _require_finite("ead", ead)
        if ead < 0:
            raise ValueError(f"ead must be >= 0, got {ead}")
        _require_finite("pd", pd)
        if not 0.0 <= pd <= 1.0:
            raise ValueError(f"pd must be within [0, 1], got {pd}")
        _require_finite("recovery_rate", recovery_rate)
        if not 0.0 <= recovery_rate <= 1.0:
            raise ValueError(f"recovery_rate must be within [0, 1], got {recovery_rate}")
        loss_given_default = 1.0 - recovery_rate
        return float(loss_given_default * ead * pd)

    def analyze_netting_set(
        self,
        contracts: List[OtcContract],
        csa: CsaTerms
    ) -> OtcRiskReport:
        """
        Audits an entire ISDA netting set and returns an OtcRiskReport.
        """
        if not contracts:
            raise ValueError("contracts must be a non-empty list")

        gross_mtm = sum(abs(c.mtm_value_usd) for c in contracts)
        net_mtm = sum(c.mtm_value_usd for c in contracts)

        rc = self.calculate_current_exposure(contracts, csa)
        add_on = self.calculate_pfe(contracts)
        multiplier = self.calculate_pfe_multiplier(net_mtm, csa.posted_collateral_usd, add_on)
        pfe = multiplier * add_on
        ead = self.alpha * (rc + pfe)

        cva = self.calculate_cva(ead, csa.counterparty_pd, csa.recovery_rate)

        # CSA Margin Call Audit (BCBS 279 para 140 footnotes 8-9): the delivery
        # amount closes the gap above threshold; a transfer is required only
        # when the delivery amount is at least the Minimum Transfer Amount.
        uncollateralized_raw = net_mtm - csa.posted_collateral_usd
        margin_call_amount = max(0.0, uncollateralized_raw - csa.threshold_usd)

        is_margin_call = margin_call_amount >= csa.minimum_transfer_amount
        is_limit_breached = ead > self.max_ead_limit_usd

        if is_limit_breached:
            logger.error(
                f"OTC Credit Limit Breached for {csa.netting_set_id}: EAD ${ead:,.2f} > Limit ${self.max_ead_limit_usd:,.2f}"
            )

        return OtcRiskReport(
            netting_set_id=csa.netting_set_id,
            gross_mtm_usd=round(gross_mtm, 2),
            net_mtm_usd=round(net_mtm, 2),
            netted_current_exposure_usd=round(rc, 2),
            potential_future_exposure_usd=round(pfe, 2),
            exposure_at_default_usd=round(ead, 2),
            cva_usd=round(cva, 2),
            is_margin_call_triggered=is_margin_call,
            margin_call_amount_usd=round(margin_call_amount if is_margin_call else 0.0, 2),
            is_credit_limit_breached=is_limit_breached,
            pfe_multiplier=round(multiplier, 6)
        )
