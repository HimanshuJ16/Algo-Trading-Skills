import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MSCI_RATING_MAP = {
    "AAA": 1.000,
    "AA": 0.833,
    "A": 0.667,
    "BBB": 0.500,
    "BB": 0.333,
    "B": 0.167,
    "CCC": 0.000
}

@dataclass
class RawVendorEsgData:
    ticker: str
    msci_rating: Optional[str] = None           # e.g. 'AAA' to 'CCC'
    sustainalytics_risk_score: Optional[float] = None  # 0.0 (best) to 100.0 (worst)
    refinitiv_esg_score: Optional[float] = None # 0.0 (worst) to 100.0 (best)
    has_controversial_weapons: bool = False

@dataclass
class EsgSignalAuditReport:
    ticker: str
    normalized_msci_score: Optional[float]
    normalized_sustainalytics_score: Optional[float]
    normalized_refinitiv_score: Optional[float]
    consensus_esg_score: float                  # 0.0 to 1.0
    vendor_disagreement_dispersion: float       # Standard deviation across normalized scores
    has_high_vendor_disagreement: bool          # True if dispersion > 0.25
    signal: str                                 # 'BULLISH_ESG_LEADER', 'BEARISH_ESG_LAGGARD', 'EXCLUDED_SECTOR', 'NEUTRAL'
    audit_notes: str

class EsgDataSignalEngine:
    """
    Quantitative alternative data engine for normalizing cross-vendor ESG ratings (MSCI, Sustainalytics, Refinitiv),
    calculating consensus scores and vendor disagreement dispersion, and generating ESG factor overlay signals.
    """
    def __init__(self, disagreement_threshold: float = 0.25):
        self.disagreement_threshold = disagreement_threshold

    def normalize_msci(self, rating: Optional[str]) -> Optional[float]:
        if not rating:
            return None
        return MSCI_RATING_MAP.get(rating.upper())

    def normalize_sustainalytics(self, risk_score: Optional[float]) -> Optional[float]:
        if risk_score is None:
            return None
        # 0.0 risk = 1.0 score, 100.0 risk = 0.0 score
        clipped = max(0.0, min(100.0, risk_score))
        return round(1.0 - (clipped / 100.0), 3)

    def normalize_refinitiv(self, esg_score: Optional[float]) -> Optional[float]:
        if esg_score is None:
            return None
        clipped = max(0.0, min(100.0, esg_score))
        return round(clipped / 100.0, 3)

    def analyze_esg_signal(self, data: RawVendorEsgData) -> EsgSignalAuditReport:
        """
        Normalizes scores, computes consensus and dispersion, and emits ESG trading signals.
        """
        # Sector exclusion override
        if data.has_controversial_weapons:
            msg = f"EXCLUDED SECTOR [{data.ticker}]: Flagged for controversial weapons involvement."
            logger.warning(msg)
            return EsgSignalAuditReport(
                ticker=data.ticker, normalized_msci_score=None, normalized_sustainalytics_score=None,
                normalized_refinitiv_score=None, consensus_esg_score=0.0, vendor_disagreement_dispersion=0.0,
                has_high_vendor_disagreement=False, signal="EXCLUDED_SECTOR", audit_notes=msg
            )

        n_msci = self.normalize_msci(data.msci_rating)
        n_sust = self.normalize_sustainalytics(data.sustainalytics_risk_score)
        n_ref = self.normalize_refinitiv(data.refinitiv_esg_score)

        valid_scores = [s for s in [n_msci, n_sust, n_ref] if s is not None]

        if len(valid_scores) == 0:
            msg = f"NO ESG DATA [{data.ticker}]: No valid vendor scores provided."
            return EsgSignalAuditReport(
                ticker=data.ticker, normalized_msci_score=None, normalized_sustainalytics_score=None,
                normalized_refinitiv_score=None, consensus_esg_score=0.0, vendor_disagreement_dispersion=0.0,
                has_high_vendor_disagreement=False, signal="NEUTRAL", audit_notes=msg
            )

        consensus = round(sum(valid_scores) / float(len(valid_scores)), 3)

        # Variance & Dispersion
        if len(valid_scores) > 1:
            var = sum((s - consensus) ** 2 for s in valid_scores) / float(len(valid_scores))
            dispersion = round(math.sqrt(var), 3)
        else:
            dispersion = 0.0

        is_high_disagreement = (dispersion > self.disagreement_threshold)

        # Signal Logic
        if consensus >= 0.75 and not is_high_disagreement:
            signal = "BULLISH_ESG_LEADER"
            notes = f"BULLISH ESG LEADER [{data.ticker}]: High consensus score {consensus:.3f} with low vendor dispersion ({dispersion:.3f})."
            logger.info(notes)
        elif consensus <= 0.30:
            signal = "BEARISH_ESG_LAGGARD"
            notes = f"BEARISH ESG LAGGARD [{data.ticker}]: Low consensus score {consensus:.3f} across vendor ratings."
            logger.warning(notes)
        elif is_high_disagreement:
            signal = "NEUTRAL_HIGH_DISAGREEMENT"
            notes = f"HIGH VENDOR DISAGREEMENT [{data.ticker}]: Vendor dispersion {dispersion:.3f} exceeds threshold ({self.disagreement_threshold}). Rating noise detected."
            logger.warning(notes)
        else:
            signal = "NEUTRAL"
            notes = f"NEUTRAL ESG [{data.ticker}]: Consensus score {consensus:.3f} in average band."

        return EsgSignalAuditReport(
            ticker=data.ticker,
            normalized_msci_score=n_msci,
            normalized_sustainalytics_score=n_sust,
            normalized_refinitiv_score=n_ref,
            consensus_esg_score=consensus,
            vendor_disagreement_dispersion=dispersion,
            has_high_vendor_disagreement=is_high_disagreement,
            signal=signal,
            audit_notes=notes
        )
