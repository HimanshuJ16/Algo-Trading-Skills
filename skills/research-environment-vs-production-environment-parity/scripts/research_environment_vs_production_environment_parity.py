import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ResearchEnvironmentVsProductionEnvironmentParityConfig:
    """Legacy config class for backward compatibility."""
    data_path: str = "default.csv"
    max_signal_diff_pct: float = 0.001    # Max 0.1% tolerance
    max_timing_skew_ms: float = 5.0

@dataclass
class EnvironmentSnapshot:
    env_type: str                         # 'RESEARCH', 'PRODUCTION'
    python_version: str
    package_versions: Dict[str, str]      # e.g. {'numpy': '1.24.3', 'pandas': '2.0.1'}
    float_precision: str                  # 'float32', 'float64'
    feature_definitions: Dict[str, str]   # feature_name -> code_hash or formula

@dataclass
class ParityDiscrepancy:
    component: str                        # 'PACKAGE', 'FLOAT_PRECISION', 'FEATURE', 'SIGNAL_OUTPUT'
    item_name: str
    research_val: str
    production_val: str
    severity: str                         # 'CRITICAL', 'WARNING'

@dataclass
class EnvironmentParityReport:
    is_parity_achieved: bool
    discrepancies: List[ParityDiscrepancy]
    total_discrepancies: int
    critical_discrepancies: int
    status: str                           # 'PARITY_VERIFIED', 'PARITY_BREACHED'
    audit_notes: str

class ResearchEnvironmentVsProductionEnvironmentParity:
    """Legacy class retained for backward compatibility."""
    def __init__(self, config: ResearchEnvironmentVsProductionEnvironmentParityConfig):
        self.config = config

    def process(self) -> bool:
        return True

class ResearchEnvironmentVsProductionEnvironmentParityEngine:
    """
    Parity auditor verifying exact functional, data, dependency, and signal output parity
    between Research (backtesting/offline) and Production (live execution) environments.
    """
    def __init__(self, config: Optional[ResearchEnvironmentVsProductionEnvironmentParityConfig] = None):
        self.config = config or ResearchEnvironmentVsProductionEnvironmentParityConfig()

    def audit_environment_parity(
        self,
        research_env: EnvironmentSnapshot,
        prod_env: EnvironmentSnapshot,
        test_signals: Optional[List[Tuple[float, float]]] = None # List of (research_signal, prod_signal)
    ) -> EnvironmentParityReport:
        """
        Audits Python versions, package dependency versions, floating-point precision,
        feature definitions, and live vs research signal output discrepancies.
        """
        discrepancies: List[ParityDiscrepancy] = []

        # 1. Python Version Check
        if research_env.python_version != prod_env.python_version:
            discrepancies.append(ParityDiscrepancy(
                component="PACKAGE",
                item_name="python_version",
                research_val=research_env.python_version,
                production_val=prod_env.python_version,
                severity="WARNING"
            ))

        # 2. Package Dependency Check
        all_pkgs = set(research_env.package_versions.keys()) | set(prod_env.package_versions.keys())
        for pkg in sorted(all_pkgs):
            r_ver = research_env.package_versions.get(pkg, "NOT_INSTALLED")
            p_ver = prod_env.package_versions.get(pkg, "NOT_INSTALLED")
            if r_ver != p_ver:
                discrepancies.append(ParityDiscrepancy(
                    component="PACKAGE",
                    item_name=pkg,
                    research_val=r_ver,
                    production_val=p_ver,
                    severity="CRITICAL" if r_ver == "NOT_INSTALLED" or p_ver == "NOT_INSTALLED" else "WARNING"
                ))

        # 3. Precision Check
        if research_env.float_precision != prod_env.float_precision:
            discrepancies.append(ParityDiscrepancy(
                component="FLOAT_PRECISION",
                item_name="float_precision",
                research_val=research_env.float_precision,
                production_val=prod_env.float_precision,
                severity="CRITICAL"
            ))

        # 4. Feature Definition Check
        all_feats = set(research_env.feature_definitions.keys()) | set(prod_env.feature_definitions.keys())
        for feat in sorted(all_feats):
            r_def = research_env.feature_definitions.get(feat, "MISSING")
            p_def = prod_env.feature_definitions.get(feat, "MISSING")
            if r_def != p_def:
                discrepancies.append(ParityDiscrepancy(
                    component="FEATURE",
                    item_name=feat,
                    research_val=r_def,
                    production_val=p_def,
                    severity="CRITICAL"
                ))

        # 5. Signal Output Parity Check
        if test_signals:
            for idx, (r_sig, p_sig) in enumerate(test_signals):
                diff = abs(r_sig - p_sig)
                rel_diff = diff / max(abs(r_sig), 1e-5)
                if rel_diff > self.config.max_signal_diff_pct:
                    discrepancies.append(ParityDiscrepancy(
                        component="SIGNAL_OUTPUT",
                        item_name=f"signal_sample_{idx}",
                        research_val=str(r_sig),
                        production_val=str(p_sig),
                        severity="CRITICAL"
                    ))

        critical_count = sum(1 for d in discrepancies if d.severity == "CRITICAL")
        total_count = len(discrepancies)
        is_parity = critical_count == 0
        status = "PARITY_VERIFIED" if is_parity else "PARITY_BREACHED"

        notes = (
            f"ENVIRONMENT PARITY [{status}]: Total Discrepancies = {total_count}, "
            f"Critical = {critical_count}."
        )

        if not is_parity:
            logger.warning(notes)
        else:
            logger.info(notes)

        return EnvironmentParityReport(
            is_parity_achieved=is_parity,
            discrepancies=discrepancies,
            total_discrepancies=total_count,
            critical_discrepancies=critical_count,
            status=status,
            audit_notes=notes
        )
