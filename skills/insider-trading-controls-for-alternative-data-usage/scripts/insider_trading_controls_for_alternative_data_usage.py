import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class AltDataDatasetSpec:
    dataset_name: str                   # e.g. 'Orbital_Satellite_Parking_Lot_V2'
    data_source_type: str               # 'SATELLITE_IMAGERY', 'CREDIT_CARD_TRANSACTIONS', 'WEB_SCRAPED', 'GEOLOCATION'
    has_mnpi_risk: bool                 # Material Non-Public Information risk flag
    has_vendor_diligence_signoff: bool  # Vendor due diligence representation on file
    is_tos_compliant: bool              # Terms of Service compliant
    is_pii_scrubbed: bool               # Personally Identifiable Information scrubbed
    panel_aggregation_count: int        # Minimum distinct consumer panel count (e.g. 250)
    hours_to_earnings_release: float    # Hours until next earnings release (e.g. 72.0)

@dataclass
class AltDataComplianceReport:
    dataset_name: str
    data_source_type: str
    is_mnpi_cleared: bool
    is_vendor_diligence_cleared: bool
    is_pii_anonymization_cleared: bool
    is_blackout_window_cleared: bool
    risk_classification: str            # 'LOW_RISK_APPROVED', 'BLACKOUT_WINDOW_RESTRICTED', 'REJECTED_MNPI_RISK', 'REJECTED_MISSING_DILIGENCE', 'REJECTED_UNAGGREGATED_PII'
    audit_notes: str

class AltDataInsiderTradingComplianceEngine:
    """
    Compliance governance engine for alternative data usage, auditing SEC Rule 10b-5 MNPI risks,
    PII anonymization thresholds, vendor due diligence sign-offs, and earnings blackout windows.
    """
    def __init__(
        self,
        min_panel_aggregation_count: int = 50,
        earnings_blackout_window_hours: float = 48.0
    ):
        self.min_panel_aggregation_count = min_panel_aggregation_count
        self.earnings_blackout_window_hours = earnings_blackout_window_hours

    def audit_alt_data_dataset(self, spec: AltDataDatasetSpec) -> AltDataComplianceReport:
        """
        Audits alternative dataset spec against SEC Rule 10b-5, Section 204A, and PII privacy standards.
        """
        # 1. Audit SEC Rule 10b-5 MNPI Risk
        if spec.has_mnpi_risk:
            notes = (
                f"ALT-DATA COMPLIANCE REJECTED [{spec.dataset_name}]: SEC Rule 10b-5 MNPI RISK DETECTED! "
                f"Dataset contains Material Non-Public Information. Trading strictly prohibited."
            )
            logger.critical(notes)
            return AltDataComplianceReport(
                dataset_name=spec.dataset_name, data_source_type=spec.data_source_type,
                is_mnpi_cleared=False, is_vendor_diligence_cleared=spec.has_vendor_diligence_signoff,
                is_pii_anonymization_cleared=spec.is_pii_scrubbed, is_blackout_window_cleared=True,
                risk_classification="REJECTED_MNPI_RISK", audit_notes=notes
            )

        # 2. Audit Vendor Due Diligence & Terms of Service (ToS)
        if not spec.has_vendor_diligence_signoff or not spec.is_tos_compliant:
            notes = (
                f"ALT-DATA COMPLIANCE REJECTED [{spec.dataset_name}]: Missing vendor due diligence sign-off "
                f"or Terms of Service (ToS) compliance."
            )
            logger.error(notes)
            return AltDataComplianceReport(
                dataset_name=spec.dataset_name, data_source_type=spec.data_source_type,
                is_mnpi_cleared=True, is_vendor_diligence_cleared=False,
                is_pii_anonymization_cleared=spec.is_pii_scrubbed, is_blackout_window_cleared=True,
                risk_classification="REJECTED_MISSING_DILIGENCE", audit_notes=notes
            )

        # 3. Audit PII Scrubbing & Panel Aggregation
        is_pii_ok = spec.is_pii_scrubbed and (spec.panel_aggregation_count >= self.min_panel_aggregation_count)
        if not is_pii_ok:
            notes = (
                f"ALT-DATA COMPLIANCE REJECTED [{spec.dataset_name}]: PII privacy failure! "
                f"Panel aggregation count ({spec.panel_aggregation_count}) is below minimum requirement ({self.min_panel_aggregation_count})."
            )
            logger.error(notes)
            return AltDataComplianceReport(
                dataset_name=spec.dataset_name, data_source_type=spec.data_source_type,
                is_mnpi_cleared=True, is_vendor_diligence_cleared=True,
                is_pii_anonymization_cleared=False, is_blackout_window_cleared=True,
                risk_classification="REJECTED_UNAGGREGATED_PII", audit_notes=notes
            )

        # 4. Audit Earnings Release Blackout Window
        is_blackout_ok = abs(spec.hours_to_earnings_release) >= self.earnings_blackout_window_hours
        if not is_blackout_ok:
            notes = (
                f"ALT-DATA COMPLIANCE RESTRICTED [{spec.dataset_name}]: Within earnings blackout window "
                f"({abs(spec.hours_to_earnings_release):.1f}h < {self.earnings_blackout_window_hours:.1f}h). Trading signal paused."
            )
            logger.warning(notes)
            return AltDataComplianceReport(
                dataset_name=spec.dataset_name, data_source_type=spec.data_source_type,
                is_mnpi_cleared=True, is_vendor_diligence_cleared=True,
                is_pii_anonymization_cleared=True, is_blackout_window_cleared=False,
                risk_classification="BLACKOUT_WINDOW_RESTRICTED", audit_notes=notes
            )

        notes = (
            f"ALT-DATA COMPLIANCE APPROVED [{spec.dataset_name} - {spec.data_source_type}]: "
            f"Cleared SEC 10b-5 MNPI, vendor diligence, PII panel ({spec.panel_aggregation_count}), and blackout window. Low Risk."
        )
        logger.info(notes)

        return AltDataComplianceReport(
            dataset_name=spec.dataset_name,
            data_source_type=spec.data_source_type,
            is_mnpi_cleared=True,
            is_vendor_diligence_cleared=True,
            is_pii_anonymization_cleared=True,
            is_blackout_window_cleared=True,
            risk_classification="LOW_RISK_APPROVED",
            audit_notes=notes
        )
