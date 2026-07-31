import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ModelIdentity:
    model_id: str
    name: str
    version: str
    author: str
    model_type: str                     # 'ML_ALPHA', 'EXECUTION_ALGO', 'RISK_MODEL'
    asset_class: str                    # 'US_EQUITIES', 'CRYPTO_SPOT', 'FX_FUTURES'
    intended_use: str
    out_of_scope_uses: List[str]

@dataclass
class ModelPerformanceMetrics:
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float             # e.g. 12.5%
    annual_return_pct: float            # e.g. 24.0%
    win_rate_pct: float                 # e.g. 58.0%
    capacity_usd: float                 # Max capital capacity in USD (e.g. $10,000,000)

@dataclass
class ModelGovernanceConfig:
    sr_compliant: bool = True           # Compliant with SR 26-2 / SR 11-7 MRM framework
    is_validated_by_mrm: bool = True    # Independent MRM validation sign-off
    validation_date: str = "2026-05-01"
    kill_switch_triggers: List[str] = field(default_factory=lambda: ["Drawdown > 20%", "Latency > 50ms"])

@dataclass
class ModelCardReport:
    model_id: str
    name: str
    version: str
    markdown_content: str
    json_payload: Dict[str, str]
    is_mrm_compliant: bool
    status: str                         # 'MODEL_CARD_GENERATED_COMPLIANT', 'MODEL_CARD_NON_COMPLIANT_DEFICIT'
    audit_notes: str

class ModelCardGeneratorEngine:
    """
    Quantitative model governance and Model Card generation engine producing SR 26-2 / MRM compliant documentation
    for machine learning alphas, execution algorithms, and risk models.
    """
    def __init__(self):
        pass

    def generate_model_card(
        self,
        identity: ModelIdentity,
        performance: ModelPerformanceMetrics,
        governance: ModelGovernanceConfig
    ) -> ModelCardReport:
        """
        Generates SR 26-2 compliant Model Card documentation in Markdown and JSON formats.
        """
        if not identity.model_id or not identity.name:
            raise ValueError("Model Identity must specify model_id and name.")

        # Audit MRM Governance Compliance
        is_compliant = True
        deficit_reasons = []

        if not governance.is_validated_by_mrm:
            is_compliant = False
            deficit_reasons.append("Missing Independent Model Risk Management (MRM) validation sign-off.")

        if not identity.intended_use or not identity.out_of_scope_uses:
            is_compliant = False
            deficit_reasons.append("Missing Intended Use or Out-of-Scope Use specifications.")

        if performance.sharpe_ratio < 1.0:
            deficit_reasons.append(f"Low Sharpe Ratio ({performance.sharpe_ratio:.2f} < 1.0 threshold).")

        if performance.max_drawdown_pct > 25.0:
            is_compliant = False
            deficit_reasons.append(f"High Max Drawdown ({performance.max_drawdown_pct:.1f}% > 25.0% limit).")

        # Synthesize Markdown Model Card
        md_lines = [
            f"# Model Card: {identity.name} (v{identity.version})",
            f"**Model ID**: `{identity.model_id}` | **Author**: {identity.author} | **Type**: `{identity.model_type}`",
            f"**Asset Class**: `{identity.asset_class}` | **MRM Validated**: `{governance.is_validated_by_mrm}` ({governance.validation_date})",
            "",
            "## 1. Intended Use & Scope",
            f"- **Intended Use**: {identity.intended_use}",
            "- **Out-of-Scope Uses**:",
        ]
        for out in identity.out_of_scope_uses:
            md_lines.append(f"  - 🚫 {out}")

        md_lines.extend([
            "",
            "## 2. Quantitative Performance & Backtest Metrics",
            f"| Metric | Value |",
            f"|---|---|",
            f"| **Sharpe Ratio** | `{performance.sharpe_ratio:.2f}` |",
            f"| **Sortino Ratio** | `{performance.sortino_ratio:.2f}` |",
            f"| **Max Drawdown** | `{performance.max_drawdown_pct:.1f}%` |",
            f"| **Annualized Return** | `{performance.annual_return_pct:.1f}%` |",
            f"| **Win Rate** | `{performance.win_rate_pct:.1f}%` |",
            f"| **Max Capacity (USD)** | `${performance.capacity_usd:,.2f}` |",
            "",
            "## 3. Governance & Risk Controls (SR 26-2)",
            f"- **SR 26-2 / MRM Framework**: `{'COMPLIANT' if governance.sr_compliant else 'NON_COMPLIANT'}`",
            "- **Kill-Switch Trigger Conditions**:",
        ])
        for ks in governance.kill_switch_triggers:
            md_lines.append(f"  - ⚡ {ks}")

        markdown_doc = "\n".join(md_lines)

        # JSON Payload
        json_payload = {
            "model_identity": asdict(identity),
            "performance_metrics": asdict(performance),
            "governance_config": asdict(governance),
            "is_mrm_compliant": is_compliant
        }

        if is_compliant:
            status = "MODEL_CARD_GENERATED_COMPLIANT"
            notes = f"MODEL CARD APPROVED [{identity.model_id}]: SR 26-2 compliant. Sharpe={performance.sharpe_ratio:.2f}, MaxDD={performance.max_drawdown_pct:.1f}%."
            logger.info(notes)
        else:
            status = "MODEL_CARD_NON_COMPLIANT_DEFICIT"
            notes = f"MODEL CARD DEFICIT [{identity.model_id}]: Non-compliant under SR 26-2. Deficits: {deficit_reasons}."
            logger.warning(notes)

        return ModelCardReport(
            model_id=identity.model_id,
            name=identity.name,
            version=identity.version,
            markdown_content=markdown_doc,
            json_payload=json_payload,
            is_mrm_compliant=is_compliant,
            status=status,
            audit_notes=notes
        )
