import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class CustodyInsuranceSpec:
    custodian_name: str                 # e.g. 'BitGo Custody' or 'Coinbase Custody'
    firm_hot_wallet_aum_usd: float      # e.g. $2,000,000
    firm_cold_wallet_aum_usd: float     # e.g. $20,000,000
    hot_crime_policy_limit_usd: float   # Hot/Warm Crime insurance limit (e.g. $5,000,000)
    cold_specie_policy_limit_usd: float # Cold Specie insurance limit (e.g. $250,000,000)
    total_custodian_cold_aum_usd: float # Total pooled AUM held by custodian across all clients (e.g. $1,000,000,000)

@dataclass
class CustodyInsuranceReport:
    custodian_name: str
    total_firm_aum_usd: float
    hot_wallet_coverage_pct: float
    cold_wallet_pro_rata_dilution_pct: float
    cold_wallet_effective_coverage_usd: float
    cold_wallet_effective_coverage_pct: float
    total_uninsured_shortfall_usd: float
    net_insured_coverage_pct: float
    status: str                         # 'FULLY_INSURED', 'PARTIALLY_INSURED_SHORTFALL', 'CRITICAL_HOT_WALLET_UNINSURED'
    audit_notes: str

class CustodyInsuranceAssessmentEngine:
    """
    Institutional audit engine for crypto custody insurance, evaluating Specie offline cold vault policies vs
    Crime hot/warm wallet policies, pro-rata pooled limit dilution, and uncovered shortfall risks.
    """
    def __init__(self):
        pass

    def audit_custody_insurance(self, spec: CustodyInsuranceSpec) -> CustodyInsuranceReport:
        """
        Audits firm crypto assets against custodian Crime and Specie insurance policies.
        """
        tot_firm_aum = spec.firm_hot_wallet_aum_usd + spec.firm_cold_wallet_aum_usd
        if tot_firm_aum <= 0:
            raise ValueError("Total firm AUM must be > 0.")

        # 1. Hot Wallet Crime Policy Audit
        hot_aum = spec.firm_hot_wallet_aum_usd
        hot_limit = spec.hot_crime_policy_limit_usd
        hot_covered_usd = min(hot_aum, hot_limit)
        hot_coverage_pct = round((hot_covered_usd / float(hot_aum)) * 100.0, 2) if hot_aum > 0 else 100.0

        # 2. Cold Vault Specie Policy & Pooled Dilution Audit
        cold_aum = spec.firm_cold_wallet_aum_usd
        specie_limit = spec.cold_specie_policy_limit_usd
        total_cust_aum = max(spec.total_custodian_cold_aum_usd, cold_aum)

        # Calculate Pro-Rata Dilution Factor (Specie Policy Limit / Total Custodian AUM)
        dilution_factor = min(1.0, specie_limit / float(total_cust_aum))
        cold_dilution_pct = round(dilution_factor * 100.0, 2)

        # Firm Effective Protected Cold USD
        cold_covered_usd = round(cold_aum * dilution_factor, 2)
        cold_coverage_pct = round((cold_covered_usd / float(cold_aum)) * 100.0, 2) if cold_aum > 0 else 100.0

        # 3. Total Insured vs Uninsured Shortfall
        total_covered_usd = hot_covered_usd + cold_covered_usd
        uninsured_shortfall_usd = round(tot_firm_aum - total_covered_usd, 2)
        net_insured_pct = round((total_covered_usd / float(tot_firm_aum)) * 100.0, 2)

        # Status Classification
        if hot_coverage_pct < 100.0:
            status = "CRITICAL_HOT_WALLET_UNINSURED"
            notes = (
                f"CUSTODY INSURANCE WARNING [{spec.custodian_name}]: Hot Wallet Crime Policy covers only "
                f"{hot_coverage_pct:.1f}% of hot AUM (${hot_covered_usd:,.0f}/${hot_aum:,.0f}). Uninsured hot exposure!"
            )
            logger.critical(notes)
        elif net_insured_pct < 95.0:
            status = "PARTIALLY_INSURED_SHORTFALL"
            notes = (
                f"CUSTODY INSURANCE AUDIT [{spec.custodian_name}]: Net Insured Coverage is {net_insured_pct:.1f}%. "
                f"Cold Vault Pro-Rata Dilution = {cold_dilution_pct:.1f}% (${specie_limit/1e6:.0f}M limit / ${total_cust_aum/1e9:.1f}B custodian AUM). "
                f"Uninsured Shortfall = ${uninsured_shortfall_usd:,.2f} USD."
            )
            logger.warning(notes)
        else:
            status = "FULLY_INSURED"
            notes = (
                f"CUSTODY INSURANCE APPROVED [{spec.custodian_name}]: Firm AUM ${tot_firm_aum:,.0f} is {net_insured_pct:.1f}% insured "
                f"(Hot Crime = {hot_coverage_pct:.1f}%, Cold Specie = {cold_coverage_pct:.1f}%)."
            )
            logger.info(notes)

        return CustodyInsuranceReport(
            custodian_name=spec.custodian_name,
            total_firm_aum_usd=tot_firm_aum,
            hot_wallet_coverage_pct=hot_coverage_pct,
            cold_wallet_pro_rata_dilution_pct=cold_dilution_pct,
            cold_wallet_effective_coverage_usd=cold_covered_usd,
            cold_wallet_effective_coverage_pct=cold_coverage_pct,
            total_uninsured_shortfall_usd=uninsured_shortfall_usd,
            net_insured_coverage_pct=net_insured_pct,
            status=status,
            audit_notes=notes
        )
