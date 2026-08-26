"""Adviser-side trading-eligibility gate for onboarded alternative datasets.

Scope: US. The controls modelled here support an investment adviser's
Section 204A obligation to maintain and enforce written policies reasonably
designed to prevent the misuse of material non-public information (MNPI), and
the firm-policy restrictions layered on top of it.

Two of the four gates enforce *firm policy*, not law. No regulator prescribes a
minimum panel-aggregation count or an earnings blackout window for research
data; see ``references/standards.md``. The defaults below are engineering
defaults and must be calibrated and recorded by the firm.

This module produces evidence for a compliance decision. It is not legal advice
and does not determine insider-trading liability, which turns on breach of a
duty of trust or confidence (United States v. O'Hagan, 521 U.S. 642 (1997);
17 CFR 240.10b5-2), not on materiality alone.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Control identifiers recorded on a report so an auditor can see exactly which
# gate failed, rather than inferring it from a coarse classification string.
CONTROL_MNPI_PROVENANCE = "MNPI_PROVENANCE"
CONTROL_VENDOR_DILIGENCE_SIGNOFF = "VENDOR_DILIGENCE_SIGNOFF"
CONTROL_TERMS_OF_SERVICE = "TERMS_OF_SERVICE"
CONTROL_PII_SCRUBBING = "PII_SCRUBBING"
CONTROL_PANEL_AGGREGATION = "PANEL_AGGREGATION"
CONTROL_EARNINGS_BLACKOUT = "EARNINGS_BLACKOUT"


class AltDataComplianceError(ValueError):
    """Raised when a dataset spec or engine threshold cannot be audited.

    Refusing to score unusable input is deliberate: an audit record that looks
    authoritative but was computed from a mistyped field is worse than no
    record at all.
    """


def _require_bool(value: object, field_name: str) -> None:
    """Reject non-``bool`` compliance answers instead of coercing them.

    A dataset spec assembled from CSV, JSON, or an LLM tool call routinely
    carries ``'no'`` / ``'false'`` strings. Every non-empty string is truthy, so
    coercion silently turns a "no" answer on a positive control (vendor
    sign-off, ToS compliance, PII scrubbing) into a pass. That is a fail-open on
    the exact controls this gate exists to enforce.
    """
    if not isinstance(value, bool):
        raise AltDataComplianceError(
            f"{field_name} must be a bool, got {type(value).__name__} "
            f"({value!r}). Map the source answer to True/False explicitly; "
            f"truthy strings such as 'no' would otherwise pass this control."
        )


@dataclass
class AltDataDatasetSpec:
    """A dataset already cleared through vendor onboarding, presented for trading.

    Every boolean must be a real ``bool`` reflecting evidence the firm holds --
    not a vendor assertion. The SEC's App Annie order (Admin. Proc. 34-92975,
    2021-09-14) is the canonical case of a vendor's own written representations
    about aggregation and anonymisation being false.

    ``hours_to_earnings_release`` is a signed distance in hours to the nearest
    scheduled earnings release: positive before, negative after. The blackout
    gate compares its absolute value, so the window is two-sided. Pass ``None``
    when no earnings release is scheduled inside the firm's monitoring horizon;
    the gate then clears.
    """

    dataset_name: str                            # e.g. 'Orbital_Satellite_Parking_Lot_V2'
    data_source_type: str                        # 'SATELLITE_IMAGERY', 'CREDIT_CARD_TRANSACTIONS', 'WEB_SCRAPED', 'GEOLOCATION'
    has_mnpi_risk: bool                          # Provenance implies a breached duty of trust/confidence
    has_vendor_diligence_signoff: bool           # Current, documented diligence record on file
    is_tos_compliant: bool                       # Collection consistent with the source's terms
    is_pii_scrubbed: bool                        # Direct/indirect identifiers removed and verified
    panel_aggregation_count: int                 # Distinct contributors behind each published observation
    hours_to_earnings_release: Optional[float]   # Signed hours to nearest earnings release; None = none scheduled

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Raise ``AltDataComplianceError`` unless every field is auditable.

        Called at construction and again by the engine. Dataclass fields stay
        mutable, so validating only in ``__post_init__`` would let a caller
        assign ``spec.is_pii_scrubbed = 'no'`` afterwards and reopen the exact
        truthy-string fail-open this guards against.
        """
        for field_name in ("dataset_name", "data_source_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise AltDataComplianceError(
                    f"{field_name} must be a non-empty string, got {value!r}."
                )

        for field_name in (
            "has_mnpi_risk",
            "has_vendor_diligence_signoff",
            "is_tos_compliant",
            "is_pii_scrubbed",
        ):
            _require_bool(getattr(self, field_name), field_name)

        # bool is a subclass of int; a True/False panel count is a mapping error.
        if isinstance(self.panel_aggregation_count, bool) or not isinstance(
            self.panel_aggregation_count, int
        ):
            raise AltDataComplianceError(
                "panel_aggregation_count must be an int, got "
                f"{type(self.panel_aggregation_count).__name__} "
                f"({self.panel_aggregation_count!r})."
            )
        if self.panel_aggregation_count < 0:
            raise AltDataComplianceError(
                "panel_aggregation_count must be non-negative, got "
                f"{self.panel_aggregation_count}."
            )

        if self.hours_to_earnings_release is not None:
            if isinstance(self.hours_to_earnings_release, bool) or not isinstance(
                self.hours_to_earnings_release, (int, float)
            ):
                raise AltDataComplianceError(
                    "hours_to_earnings_release must be a number or None, got "
                    f"{type(self.hours_to_earnings_release).__name__} "
                    f"({self.hours_to_earnings_release!r})."
                )
            if not math.isfinite(self.hours_to_earnings_release):
                raise AltDataComplianceError(
                    "hours_to_earnings_release must be finite; got "
                    f"{self.hours_to_earnings_release!r}. Use None to record "
                    "'no scheduled earnings release'."
                )


@dataclass
class AltDataComplianceReport:
    """Per-control audit record.

    Every ``is_*_cleared`` flag reflects a control that was actually evaluated
    against this spec. A rejection on one gate never asserts a pass on another.
    """

    dataset_name: str
    data_source_type: str
    is_mnpi_cleared: bool
    is_vendor_diligence_cleared: bool
    is_pii_anonymization_cleared: bool
    is_blackout_window_cleared: bool
    risk_classification: str            # 'LOW_RISK_APPROVED', 'BLACKOUT_WINDOW_RESTRICTED', 'REJECTED_MNPI_RISK', 'REJECTED_MISSING_DILIGENCE', 'REJECTED_UNAGGREGATED_PII'
    audit_notes: str
    failed_controls: Tuple[str, ...] = ()


class AltDataInsiderTradingComplianceEngine:
    """Gates trading on an onboarded alternative dataset.

    Vendor onboarding triage (legal rights, CFAA/scraping posture, ToS review,
    anonymisation methodology) belongs to
    ``alternative-data-vendor-due-diligence-checklist``, which emits the
    diligence record this engine expects to already exist. This engine is the
    adviser-side trading gate applied at, and after, ingestion.

    Thresholds are firm policy. ``min_panel_aggregation_count`` is a
    k-anonymity-style cell-size floor; neither GDPR (Recital 26) nor the
    CCPA/CPRA (Cal. Civ. Code 1798.140(b), (m)) prescribes a number.
    ``earnings_blackout_window_hours`` has no regulatory basis at all -- the only
    codified waiting periods in this area are the Rule 10b5-1(c) cooling-off
    periods, which govern insider trading plans, not research data.
    """

    def __init__(
        self,
        min_panel_aggregation_count: int = 50,
        earnings_blackout_window_hours: float = 48.0,
    ) -> None:
        if isinstance(min_panel_aggregation_count, bool) or not isinstance(
            min_panel_aggregation_count, int
        ):
            raise AltDataComplianceError(
                "min_panel_aggregation_count must be an int, got "
                f"{type(min_panel_aggregation_count).__name__}."
            )
        if min_panel_aggregation_count < 1:
            raise AltDataComplianceError(
                "min_panel_aggregation_count must be at least 1; a threshold of "
                f"{min_panel_aggregation_count} would clear a single-subject feed."
            )

        if isinstance(earnings_blackout_window_hours, bool) or not isinstance(
            earnings_blackout_window_hours, (int, float)
        ):
            raise AltDataComplianceError(
                "earnings_blackout_window_hours must be a number, got "
                f"{type(earnings_blackout_window_hours).__name__}."
            )
        if (
            not math.isfinite(earnings_blackout_window_hours)
            or earnings_blackout_window_hours < 0
        ):
            raise AltDataComplianceError(
                "earnings_blackout_window_hours must be a finite, non-negative "
                f"number of hours, got {earnings_blackout_window_hours!r}. Use "
                "0.0 to disable the blackout gate."
            )

        self.min_panel_aggregation_count = min_panel_aggregation_count
        self.earnings_blackout_window_hours = float(earnings_blackout_window_hours)

    def audit_alt_data_dataset(self, spec: AltDataDatasetSpec) -> AltDataComplianceReport:
        """Evaluate all four controls, then classify by severity.

        Controls are evaluated independently and unconditionally so that every
        flag on the returned report is a tested result. Short-circuiting on the
        first failure would leave later flags asserting a clearance that was
        never checked -- in an MNPI rejection, for instance, silently reporting
        the earnings blackout as cleared for a dataset one hour from a release.
        """
        if not isinstance(spec, AltDataDatasetSpec):
            raise AltDataComplianceError(
                f"spec must be an AltDataDatasetSpec, got {type(spec).__name__}."
            )
        spec.validate()

        failed: List[str] = []
        reasons: List[str] = []

        # 1. MNPI provenance. Materiality alone is not the test: liability under
        #    Rule 10b-5 requires a breached duty of trust or confidence. The flag
        #    should be set when provenance implies such a breach (leaked or
        #    hacked material, data supplied under confidentiality obligations to
        #    an issuer, an insider or tippee source), and defaulted to True when
        #    provenance is unknown.
        is_mnpi_cleared = not spec.has_mnpi_risk
        if not is_mnpi_cleared:
            failed.append(CONTROL_MNPI_PROVENANCE)
            reasons.append(
                "MNPI provenance risk flagged (Rule 10b-5 / Section 204A): the "
                "dataset's origin implies a breach of a duty of trust or "
                "confidence"
            )

        # 2. Vendor diligence sign-off and terms-of-service posture. Distinct
        #    exposures: a missing sign-off is a Section 204A policy failure,
        #    while a terms breach is contract/tort exposure that can also supply
        #    the confidentiality duty whose breach makes the data MNPI.
        is_vendor_diligence_cleared = (
            spec.has_vendor_diligence_signoff and spec.is_tos_compliant
        )
        if not spec.has_vendor_diligence_signoff:
            failed.append(CONTROL_VENDOR_DILIGENCE_SIGNOFF)
            reasons.append("no current vendor due diligence sign-off on file")
        if not spec.is_tos_compliant:
            failed.append(CONTROL_TERMS_OF_SERVICE)
            reasons.append(
                "collection is not compliant with the source's terms of service"
            )

        # 3. PII scrubbing and panel aggregation. Reported separately: a
        #    500,000-contributor panel that was never scrubbed fails scrubbing,
        #    not aggregation, and the audit note must say so.
        is_panel_sufficient = (
            spec.panel_aggregation_count >= self.min_panel_aggregation_count
        )
        is_pii_anonymization_cleared = spec.is_pii_scrubbed and is_panel_sufficient
        if not spec.is_pii_scrubbed:
            failed.append(CONTROL_PII_SCRUBBING)
            reasons.append("PII scrubbing not verified for this dataset")
        if not is_panel_sufficient:
            failed.append(CONTROL_PANEL_AGGREGATION)
            reasons.append(
                f"panel aggregation count ({spec.panel_aggregation_count}) is "
                f"below the firm-policy minimum "
                f"({self.min_panel_aggregation_count})"
            )

        # 4. Earnings blackout. Firm policy, two-sided around the release.
        if spec.hours_to_earnings_release is None:
            is_blackout_window_cleared = True
        else:
            is_blackout_window_cleared = (
                abs(spec.hours_to_earnings_release)
                >= self.earnings_blackout_window_hours
            )
            if not is_blackout_window_cleared:
                failed.append(CONTROL_EARNINGS_BLACKOUT)
                reasons.append(
                    f"within the firm-policy earnings blackout window "
                    f"({abs(spec.hours_to_earnings_release):.1f}h from release "
                    f"< {self.earnings_blackout_window_hours:.1f}h)"
                )

        detail = "; ".join(reasons)

        if not is_mnpi_cleared:
            classification = "REJECTED_MNPI_RISK"
            notes = (
                f"ALT-DATA COMPLIANCE REJECTED [{spec.dataset_name}]: "
                f"{detail}. Trading prohibited pending legal review."
            )
            logger.critical(notes)
        elif not is_vendor_diligence_cleared:
            classification = "REJECTED_MISSING_DILIGENCE"
            notes = (
                f"ALT-DATA COMPLIANCE REJECTED [{spec.dataset_name}]: {detail}."
            )
            logger.error(notes)
        elif not is_pii_anonymization_cleared:
            classification = "REJECTED_UNAGGREGATED_PII"
            notes = (
                f"ALT-DATA COMPLIANCE REJECTED [{spec.dataset_name}]: {detail}."
            )
            logger.error(notes)
        elif not is_blackout_window_cleared:
            classification = "BLACKOUT_WINDOW_RESTRICTED"
            notes = (
                f"ALT-DATA COMPLIANCE RESTRICTED [{spec.dataset_name}]: {detail}. "
                f"Trading signal paused until the window clears."
            )
            logger.warning(notes)
        else:
            classification = "LOW_RISK_APPROVED"
            notes = (
                f"ALT-DATA COMPLIANCE APPROVED "
                f"[{spec.dataset_name} - {spec.data_source_type}]: cleared MNPI "
                f"provenance, vendor diligence and terms of service, PII "
                f"scrubbing with panel aggregation "
                f"({spec.panel_aggregation_count}), and the earnings blackout "
                f"window. Low risk."
            )
            logger.info(notes)

        return AltDataComplianceReport(
            dataset_name=spec.dataset_name,
            data_source_type=spec.data_source_type,
            is_mnpi_cleared=is_mnpi_cleared,
            is_vendor_diligence_cleared=is_vendor_diligence_cleared,
            is_pii_anonymization_cleared=is_pii_anonymization_cleared,
            is_blackout_window_cleared=is_blackout_window_cleared,
            risk_classification=classification,
            audit_notes=notes,
            failed_controls=tuple(failed),
        )
