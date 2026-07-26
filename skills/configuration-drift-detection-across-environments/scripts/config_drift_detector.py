import logging
from dataclasses import dataclass
from typing import Dict, List, Any, Set

logger = logging.getLogger(__name__)

@dataclass
class DriftItem:
    key_path: str
    baseline_value: Any
    target_value: Any
    severity: str                     # 'CRITICAL', 'WARNING', 'ALLOWED'
    description: str

@dataclass
class ConfigAuditReport:
    is_compliant: bool
    total_keys_audited: int
    critical_drift_count: int
    warning_drift_count: int
    allowed_override_count: int
    drift_items: List[DriftItem]

class ConfigurationDriftDetector:
    """
    Compares target environment configuration against a Golden Source baseline,
    categorizing discrepancies by severity (CRITICAL, WARNING, ALLOWED).
    """
    def __init__(self, allowed_overrides: Set[str] = None):
        self.allowed_overrides = allowed_overrides or {
            "env_name", "environment", "api_url", "broker_endpoint",
            "log_level", "port", "host", "db_name"
        }

    def _flatten_dict(self, d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
        """
        Recursively flattens nested dictionary into dot-separated keys.
        """
        items: List[Tuple[str, Any]] = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def audit(self, golden_baseline: Dict[str, Any], target_config: Dict[str, Any]) -> ConfigAuditReport:
        """
        Audits target_config against golden_baseline and returns a ConfigAuditReport.
        """
        flat_baseline = self._flatten_dict(golden_baseline)
        flat_target = self._flatten_dict(target_config)

        all_keys = set(flat_baseline.keys()).union(set(flat_target.keys()))
        drift_items: List[DriftItem] = []

        critical_count = 0
        warning_count = 0
        allowed_count = 0

        for key in sorted(all_keys):
            last_token = key.split('.')[-1]
            
            if key not in flat_target:
                # Key present in baseline but missing in target -> CRITICAL
                item = DriftItem(
                    key_path=key,
                    baseline_value=flat_baseline[key],
                    target_value=None,
                    severity="CRITICAL",
                    description=f"Missing key '{key}' in target environment."
                )
                drift_items.append(item)
                critical_count += 1
                
            elif key not in flat_baseline:
                # Extra key present in target -> WARNING
                item = DriftItem(
                    key_path=key,
                    baseline_value=None,
                    target_value=flat_target[key],
                    severity="WARNING",
                    description=f"Extra key '{key}' present in target environment."
                )
                drift_items.append(item)
                warning_count += 1
                
            else:
                base_val = flat_baseline[key]
                targ_val = flat_target[key]
                
                if base_val != targ_val or type(base_val) != type(targ_val):
                    if last_token in self.allowed_overrides or key in self.allowed_overrides:
                        severity = "ALLOWED"
                        allowed_count += 1
                        desc = f"Whitelisted environment override for '{key}'."
                    else:
                        severity = "CRITICAL"
                        critical_count += 1
                        desc = f"Value mismatch for '{key}': Golden={base_val} ({type(base_val).__name__}) vs Target={targ_val} ({type(targ_val).__name__})."

                    drift_items.append(DriftItem(
                        key_path=key,
                        baseline_value=base_val,
                        target_value=targ_val,
                        severity=severity,
                        description=desc
                    ))

        is_compliant = (critical_count == 0)

        if not is_compliant:
            logger.error(f"Configuration Audit FAILED: {critical_count} CRITICAL drift items detected.")
        else:
            logger.info("Configuration Audit PASSED.")

        return ConfigAuditReport(
            is_compliant=is_compliant,
            total_keys_audited=len(all_keys),
            critical_drift_count=critical_count,
            warning_drift_count=warning_count,
            allowed_override_count=allowed_count,
            drift_items=drift_items
        )
