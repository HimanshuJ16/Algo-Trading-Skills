"""
model-card-documentation-for-trading-models: builds a structured Model Card for a
quantitative trading model and reports, explicitly, which governance sections are
still undocumented.

What this module does and does not claim
----------------------------------------
* **It checks documentation completeness, not regulatory compliance.** The engine
  can verify that a section was filled in. It cannot verify that the contents are
  true, that a validation actually took place, or that any supervisor would accept
  the result. ``MODEL_CARD_COMPLETE`` therefore means *the card has no empty
  mandatory section* -- it is **not** a deployment authorisation and **not** an
  attestation of compliance with any rule. This distinction is why the status
  vocabulary changed in v2.0.0; see ``references/standards.md``.

* **SR 26-2 is non-binding guidance with a narrow audience.** The April 17, 2026
  interagency guidance (Board of Governors of the Federal Reserve System, FDIC,
  OCC), issued as SR letter 26-2 and superseding SR 11-7 and SR 21-8, states that
  it "does not set forth enforceable standards or prescriptive requirements;
  accordingly, non-compliance with this guidance will not result in supervisory
  criticism against a banking organization," and that it "is expected to be most
  relevant to banking organizations with over $30 billion in total assets." A
  hedge fund, proprietary trading firm or asset manager is not a banking
  organization and cannot be "SR 26-2 compliant" or "SR 26-2 non-compliant". The
  guidance is used here as a *structure* for what a model card should cover, not
  as a certification this engine can issue.

* **No performance threshold in this module has a regulatory basis.** SR 26-2
  prescribes no Sharpe ratio, no drawdown limit and no capacity floor.
  ``ReviewThresholds`` is firm risk policy, its defaults are illustrative, and a
  breach is recorded as an *advisory finding* -- never as a compliance failure,
  and never as a reason to call a card incomplete.

* **The section taxonomy adapts Mitchell et al.** "Model Cards for Model
  Reporting" (FAT* '19) proposes Model Details / Intended Use / Factors / Metrics
  / Evaluation Data / Training Data / Quantitative Analyses / Ethical
  Considerations / Caveats and Recommendations, and states the sections "may be
  tailored depending on the model, context, and stakeholders". ``REQUIRED_SECTIONS``
  is that tailoring for trading models; it is a convention of this skill, not a
  published standard.

* **Deterministic.** Nothing here reads the wall clock. Staleness is evaluated
  only against a caller-supplied ``as_of_date``, so identical inputs always
  produce an identical card and the artefact is reproducible for audit.
"""
from dataclasses import asdict, dataclass, field
from datetime import date
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Section headings every card must carry. Adapted from Mitchell et al. (2019)
#: and aligned to the SR 26-2 lifecycle (development and use, validation,
#: ongoing monitoring, governance). A convention of this skill, not a standard.
REQUIRED_SECTIONS: Tuple[str, ...] = (
    "Model Details",
    "Intended Use & Out-of-Scope Uses",
    "Training Data & Feature Lineage",
    "Evaluation & Performance",
    "Limitations & Known Failure Modes",
    "Governance, Validation & Monitoring",
)

#: Model types whose output reaches the order path. A card for one of these is
#: treated as incomplete while its kill-switch conditions are undocumented.
ORDER_AFFECTING_MODEL_TYPES: Tuple[str, ...] = ("ML_ALPHA", "EXECUTION_ALGO")

STATUS_COMPLETE = "MODEL_CARD_COMPLETE"
STATUS_INCOMPLETE = "MODEL_CARD_INCOMPLETE"


class ModelCardError(ValueError):
    """Raised when inputs are unusable: absent identity, or a non-finite or
    out-of-range metric. Missing *documentation* is reported as a blocking gap
    rather than raised -- enumerating what is undocumented is the whole purpose
    of the engine."""


@dataclass(frozen=True)
class ReviewThresholds:
    """Firm risk-review policy. **No regulatory basis** -- SR 26-2 sets no
    performance thresholds. Defaults are illustrative; override them with the
    numbers your risk committee actually approved. Breaching one produces an
    advisory finding, never a compliance verdict.

    ``max_validation_age_days`` defaults to 365 because EU investment firms
    engaged in algorithmic trading must perform a self-assessment and validation
    annually under RTS 6 (Commission Delegated Regulation (EU) 2017/589)
    Article 9. Firms outside that scope should set their own cadence.
    """
    min_sharpe_ratio: float = 1.0
    max_drawdown_pct: float = 25.0
    max_validation_age_days: int = 365


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
class ModelTrainingProvenance:
    """Feature lineage. Without it a card cannot be reproduced, which is the
    failure mode this skill exists to prevent. ``provenance`` is an optional
    argument so pre-2.0 callers still run -- the card is then reported incomplete
    rather than silently published without lineage."""
    training_data_sources: List[str]
    training_window_start: str          # ISO-8601 date
    training_window_end: str            # ISO-8601 date
    feature_definitions: List[str]      # 'feature_name: transformation applied'
    label_definition: str
    retraining_cadence: str


@dataclass
class ModelPerformanceMetrics:
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float             # 0-100, e.g. 12.5
    annual_return_pct: float            # may be negative
    win_rate_pct: float                 # 0-100
    capacity_usd: float                 # >= 0
    evaluation_window: str = ""         # e.g. '2019-01-01..2024-12-31'
    is_out_of_sample: bool = False      # False => figures may be in-sample; flagged


@dataclass
class ModelLimitations:
    """SR 26-2 section V: "Users of model output benefit from understanding and
    communicating limitations, monitoring performance, periodically reviewing
    relevance"."""
    known_failure_modes: List[str]
    monitoring_signals: List[str]       # what ongoing monitoring actually watches


@dataclass
class ModelGovernanceConfig:
    """Defaults fail **closed**. Pre-2.0 this dataclass defaulted to
    ``is_validated_by_mrm=True`` with a hard-coded ``validation_date``, so a
    caller who supplied no governance information at all received a card
    asserting an independent validation that had never happened."""
    is_validated_by_mrm: bool = False
    validation_date: Optional[str] = None       # ISO-8601 date
    validator: str = ""                         # individual or function that signed off
    kill_switch_triggers: List[str] = field(default_factory=list)
    applicable_frameworks: List[str] = field(default_factory=list)


@dataclass
class ModelCardReport:
    model_id: str
    name: str
    version: str
    markdown_content: str
    json_payload: Dict[str, Any]
    is_documentation_complete: bool
    status: str                                 # STATUS_COMPLETE | STATUS_INCOMPLETE
    blocking_gaps: Tuple[str, ...]              # empty sections -> card incomplete
    advisory_findings: Tuple[str, ...]          # policy observations, always surfaced
    required_sections: Tuple[str, ...]      # the section contract this card was audited against
    audit_notes: str

    def to_json(self, indent: int = 2) -> str:
        """Canonical (sorted-key) JSON of the card payload, for audit storage."""
        return json.dumps(self.json_payload, indent=indent, sort_keys=True)


def _escape_md(value: Any) -> str:
    """Neutralise caller text so it cannot forge structure in an audit artefact.

    A model card is evidence. An ``intended_use`` string containing a newline and
    '## Governance, Validation & Monitoring' would otherwise inject a heading the
    engine never generated and never checked.

    Newlines collapse to spaces, so caller text can never reach the start of a
    line; ``#``, backtick and pipe are escaped as well, so it cannot imitate a
    heading, a code span or a table row even mid-line.
    """
    text = str(value)
    text = text.replace("\\", "\\\\")
    for char in ("`", "|", "#"):
        text = text.replace(char, "\\" + char)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelCardError(f"{label} must be a non-empty string.")


def _require_finite(value: Any, label: str) -> float:
    """Reject NaN and infinity.

    Pre-2.0 this check did not exist, and because every comparison against NaN is
    False, a card with ``max_drawdown_pct=nan`` was reported as fully compliant.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelCardError(f"{label} must be a real number, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ModelCardError(f"{label} must be finite, got {value!r}.")
    return numeric


def _require_range(value: Any, label: str, low: float, high: float) -> float:
    numeric = _require_finite(value, label)
    if not low <= numeric <= high:
        raise ModelCardError(f"{label} must lie in [{low}, {high}], got {numeric}.")
    return numeric


def _parse_iso_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _non_empty_items(values: Optional[Sequence[Any]]) -> List[str]:
    """Normalise a list-valued field to its non-blank entries.

    A bare ``str`` is rejected rather than iterated: ``out_of_scope_uses="Crypto"``
    would otherwise be read as six single-character entries and rendered as six
    bullets, which looks like a populated section and is not one.
    """
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise ModelCardError(
            f"Expected a list of strings, got a bare {type(values).__name__}: "
            f"{values!r}. Wrap it in a list."
        )
    return [str(v) for v in values if str(v).strip()]


class ModelCardGeneratorEngine:
    """Builds a Model Card for a trading model and enumerates what is still
    undocumented.

    The engine issues no regulatory verdict. It reports two independent things and
    never lets one mask the other:

    * ``blocking_gaps`` -- mandatory sections with no content. These make the card
      incomplete.
    * ``advisory_findings`` -- observations about the documented model (metrics
      outside firm policy, in-sample figures, a stale validation). These do **not**
      make the card incomplete, and they are surfaced on every card, including
      complete ones. Pre-2.0 a below-threshold Sharpe ratio was recorded internally
      and then discarded whenever the card was otherwise compliant, so the finding
      never reached the reader.
    """

    def __init__(self, thresholds: Optional[ReviewThresholds] = None) -> None:
        self.thresholds = thresholds or ReviewThresholds()

    def generate_model_card(
        self,
        identity: ModelIdentity,
        performance: ModelPerformanceMetrics,
        governance: ModelGovernanceConfig,
        provenance: Optional[ModelTrainingProvenance] = None,
        limitations: Optional[ModelLimitations] = None,
        as_of_date: Optional[str] = None,
    ) -> ModelCardReport:
        """Render the card and audit it.

        Args:
            as_of_date: ISO-8601 date used *only* to age the validation date. Omit
                it to skip the staleness check. The engine never reads the system
                clock, so an identical call always yields an identical card.

        Raises:
            ModelCardError: identity fields absent, or a metric that is
                non-numeric, non-finite or outside its admissible range.
        """
        self._validate_identity(identity)
        self._validate_performance(performance)

        blocking: List[str] = []
        advisory: List[str] = []

        self._audit_scope(identity, blocking)
        self._audit_provenance(provenance, blocking)
        self._audit_limitations(limitations, blocking)
        self._audit_governance(identity, governance, blocking, advisory, as_of_date)
        self._audit_performance_policy(performance, advisory)

        markdown_doc = self._render_markdown(
            identity, performance, governance, provenance, limitations, advisory
        )

        json_payload: Dict[str, Any] = {
            "model_identity": asdict(identity),
            "training_provenance": asdict(provenance) if provenance else None,
            "performance_metrics": asdict(performance),
            "limitations": asdict(limitations) if limitations else None,
            "governance_config": asdict(governance),
            "documentation_status": {
                "is_documentation_complete": not blocking,
                "required_sections": list(REQUIRED_SECTIONS),
                "blocking_gaps": list(blocking),
                "advisory_findings": list(advisory),
            },
        }

        if blocking:
            status = STATUS_INCOMPLETE
            notes = (
                f"MODEL CARD INCOMPLETE [{identity.model_id}]: "
                f"{len(blocking)} mandatory section(s) undocumented; "
                f"{len(advisory)} advisory finding(s)."
            )
            logger.warning("%s Gaps: %s", notes, blocking)
        else:
            status = STATUS_COMPLETE
            notes = (
                f"MODEL CARD COMPLETE [{identity.model_id}]: all "
                f"{len(REQUIRED_SECTIONS)} mandatory sections documented; "
                f"{len(advisory)} advisory finding(s). Documentation completeness "
                f"is not a deployment authorisation."
            )
            logger.info("%s", notes)
        if advisory:
            logger.warning("Advisory findings [%s]: %s", identity.model_id, advisory)

        return ModelCardReport(
            model_id=identity.model_id,
            name=identity.name,
            version=identity.version,
            markdown_content=markdown_doc,
            json_payload=json_payload,
            is_documentation_complete=not blocking,
            status=status,
            blocking_gaps=tuple(blocking),
            advisory_findings=tuple(advisory),
            required_sections=REQUIRED_SECTIONS,
            audit_notes=notes,
        )

    # ---------------------------------------------------------------- validation

    @staticmethod
    def _validate_identity(identity: ModelIdentity) -> None:
        _require_text(identity.model_id, "model_id")
        _require_text(identity.name, "name")
        _require_text(identity.version, "version")

    @staticmethod
    def _validate_performance(performance: ModelPerformanceMetrics) -> None:
        _require_finite(performance.sharpe_ratio, "sharpe_ratio")
        _require_finite(performance.sortino_ratio, "sortino_ratio")
        _require_finite(performance.annual_return_pct, "annual_return_pct")
        _require_range(performance.max_drawdown_pct, "max_drawdown_pct", 0.0, 100.0)
        _require_range(performance.win_rate_pct, "win_rate_pct", 0.0, 100.0)
        _require_range(performance.capacity_usd, "capacity_usd", 0.0, math.inf)

    # -------------------------------------------------------------------- audits

    @staticmethod
    def _audit_scope(identity: ModelIdentity, blocking: List[str]) -> None:
        if not str(identity.intended_use).strip():
            blocking.append("Intended Use & Out-of-Scope Uses: intended_use is empty.")
        if not _non_empty_items(identity.out_of_scope_uses):
            blocking.append(
                "Intended Use & Out-of-Scope Uses: no out-of-scope use documented. "
                "SR 26-2 section IV: 'Using a model beyond its intended purpose "
                "introduces additional uncertainty and risk.'"
            )

    @staticmethod
    def _audit_provenance(
        provenance: Optional[ModelTrainingProvenance], blocking: List[str]
    ) -> None:
        if provenance is None:
            blocking.append(
                "Training Data & Feature Lineage: no provenance supplied; the card "
                "is not reproducible."
            )
            return
        if not _non_empty_items(provenance.training_data_sources):
            blocking.append(
                "Training Data & Feature Lineage: no training data source listed."
            )
        if not _non_empty_items(provenance.feature_definitions):
            blocking.append(
                "Training Data & Feature Lineage: no feature definitions; the "
                "transformations cannot be reproduced."
            )
        if not str(provenance.label_definition).strip():
            blocking.append("Training Data & Feature Lineage: label_definition is empty.")
        for field_name in ("training_window_start", "training_window_end"):
            raw = getattr(provenance, field_name)
            if _parse_iso_date(raw) is None:
                blocking.append(
                    f"Training Data & Feature Lineage: {field_name} "
                    f"({raw!r}) is not an ISO-8601 date."
                )
        start = _parse_iso_date(provenance.training_window_start)
        end = _parse_iso_date(provenance.training_window_end)
        if start and end and start > end:
            blocking.append(
                "Training Data & Feature Lineage: training window ends before it starts."
            )
        if not str(provenance.retraining_cadence).strip():
            blocking.append("Training Data & Feature Lineage: retraining_cadence is empty.")

    @staticmethod
    def _audit_limitations(
        limitations: Optional[ModelLimitations], blocking: List[str]
    ) -> None:
        if limitations is None:
            blocking.append(
                "Limitations & Known Failure Modes: none supplied. A card claiming a "
                "model has no limitations is the claim least likely to be true."
            )
            return
        if not _non_empty_items(limitations.known_failure_modes):
            blocking.append(
                "Limitations & Known Failure Modes: no failure mode documented."
            )
        if not _non_empty_items(limitations.monitoring_signals):
            blocking.append(
                "Governance, Validation & Monitoring: no ongoing monitoring signal "
                "documented. SR 26-2 section V treats ongoing monitoring as a "
                "component of validation, not an optional extra."
            )

    def _audit_governance(
        self,
        identity: ModelIdentity,
        governance: ModelGovernanceConfig,
        blocking: List[str],
        advisory: List[str],
        as_of_date: Optional[str],
    ) -> None:
        if not governance.is_validated_by_mrm:
            blocking.append(
                "Governance, Validation & Monitoring: no independent validation "
                "sign-off recorded."
            )
        else:
            validated_on = _parse_iso_date(governance.validation_date)
            if validated_on is None:
                blocking.append(
                    "Governance, Validation & Monitoring: is_validated_by_mrm is True "
                    f"but validation_date ({governance.validation_date!r}) is not an "
                    "ISO-8601 date."
                )
            if not str(governance.validator).strip():
                blocking.append(
                    "Governance, Validation & Monitoring: is_validated_by_mrm is True "
                    "but no validator is named."
                )
            reference = _parse_iso_date(as_of_date) if as_of_date else None
            if reference and validated_on:
                if validated_on > reference:
                    advisory.append(
                        f"Validation date {validated_on.isoformat()} is after the "
                        f"as-of date {reference.isoformat()}."
                    )
                else:
                    age_days = (reference - validated_on).days
                    if age_days > self.thresholds.max_validation_age_days:
                        advisory.append(
                            f"Validation is {age_days} days old (> "
                            f"{self.thresholds.max_validation_age_days}-day review cadence)."
                        )

        if str(identity.model_type).upper() in ORDER_AFFECTING_MODEL_TYPES and (
            not _non_empty_items(governance.kill_switch_triggers)
        ):
            blocking.append(
                "Governance, Validation & Monitoring: model_type "
                f"{identity.model_type!r} reaches the order path but no kill-switch "
                "trigger is documented."
            )

    def _audit_performance_policy(
        self, performance: ModelPerformanceMetrics, advisory: List[str]
    ) -> None:
        if performance.sharpe_ratio < self.thresholds.min_sharpe_ratio:
            advisory.append(
                f"Sharpe ratio {performance.sharpe_ratio:.2f} is below the firm review "
                f"threshold of {self.thresholds.min_sharpe_ratio:.2f}."
            )
        if performance.max_drawdown_pct > self.thresholds.max_drawdown_pct:
            advisory.append(
                f"Max drawdown {performance.max_drawdown_pct:.1f}% exceeds the firm "
                f"review threshold of {self.thresholds.max_drawdown_pct:.1f}%."
            )
        if not performance.is_out_of_sample:
            advisory.append(
                "Performance figures are not marked out-of-sample; they may be "
                "in-sample backtest results."
            )
        if not str(performance.evaluation_window).strip():
            advisory.append("No evaluation window recorded for the reported metrics.")

    # ------------------------------------------------------------------ rendering

    def _render_markdown(
        self,
        identity: ModelIdentity,
        performance: ModelPerformanceMetrics,
        governance: ModelGovernanceConfig,
        provenance: Optional[ModelTrainingProvenance],
        limitations: Optional[ModelLimitations],
        advisory: Sequence[str],
    ) -> str:
        def bullets(values: Optional[Sequence[Any]], empty: str) -> List[str]:
            items = _non_empty_items(values)
            if not items:
                return [f"  - _{empty}_"]
            return [f"  - {_escape_md(v)}" for v in items]

        lines: List[str] = [
            f"# Model Card: {_escape_md(identity.name)} (v{_escape_md(identity.version)})",
            "",
            f"## 1. {REQUIRED_SECTIONS[0]}",
            f"- **Model ID**: {_escape_md(identity.model_id)}",
            f"- **Version**: {_escape_md(identity.version)}",
            f"- **Owner / Developer**: {_escape_md(identity.author)}",
            f"- **Model Type**: {_escape_md(identity.model_type)}",
            f"- **Asset Class**: {_escape_md(identity.asset_class)}",
            "- **Frameworks the firm applies to this model**:",
        ]
        lines += bullets(governance.applicable_frameworks, "none declared")

        lines += [
            "",
            f"## 2. {REQUIRED_SECTIONS[1]}",
            f"- **Intended Use**: {_escape_md(identity.intended_use) or '_undocumented_'}",
            "- **Out-of-Scope Uses**:",
        ]
        lines += bullets(identity.out_of_scope_uses, "undocumented")

        lines += ["", f"## 3. {REQUIRED_SECTIONS[2]}"]
        if provenance is None:
            lines.append("- _Undocumented. This card is not reproducible._")
        else:
            lines += [
                f"- **Training Window**: {_escape_md(provenance.training_window_start)}"
                f" to {_escape_md(provenance.training_window_end)}",
                f"- **Label Definition**: {_escape_md(provenance.label_definition)}",
                f"- **Retraining Cadence**: {_escape_md(provenance.retraining_cadence)}",
                "- **Training Data Sources**:",
            ]
            lines += bullets(provenance.training_data_sources, "undocumented")
            lines.append("- **Feature Definitions**:")
            lines += bullets(provenance.feature_definitions, "undocumented")

        sample_basis = (
            "out-of-sample" if performance.is_out_of_sample else "NOT marked out-of-sample"
        )
        lines += [
            "",
            f"## 4. {REQUIRED_SECTIONS[3]}",
            f"- **Evaluation Window**: "
            f"{_escape_md(performance.evaluation_window) or '_undocumented_'}",
            f"- **Basis**: {sample_basis}",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Sharpe Ratio | {performance.sharpe_ratio:.2f} |",
            f"| Sortino Ratio | {performance.sortino_ratio:.2f} |",
            f"| Max Drawdown | {performance.max_drawdown_pct:.1f}% |",
            f"| Annualised Return | {performance.annual_return_pct:.1f}% |",
            f"| Win Rate | {performance.win_rate_pct:.1f}% |",
            f"| Capacity (USD) | ${performance.capacity_usd:,.2f} |",
            "",
            f"## 5. {REQUIRED_SECTIONS[4]}",
            "- **Known Failure Modes**:",
        ]
        lines += bullets(
            limitations.known_failure_modes if limitations else None, "undocumented"
        )

        validation_line = (
            f"{_escape_md(governance.validation_date)} by "
            f"{_escape_md(governance.validator) or 'unnamed validator'}"
            if governance.is_validated_by_mrm
            else "NOT VALIDATED"
        )
        lines += [
            "",
            f"## 6. {REQUIRED_SECTIONS[5]}",
            f"- **Independent Validation Sign-Off**: {validation_line}",
            "- **Ongoing Monitoring Signals**:",
        ]
        lines += bullets(
            limitations.monitoring_signals if limitations else None, "undocumented"
        )
        lines.append("- **Kill-Switch Trigger Conditions**:")
        lines += bullets(governance.kill_switch_triggers, "undocumented")

        lines += ["", "## Advisory Findings"]
        lines += bullets(advisory, "none")
        lines += [
            "",
            "> Completeness of this card is a documentation check only. It is not a "
            "deployment authorisation and not an attestation of compliance with any "
            "rule. The review thresholds applied are firm policy and have no "
            "regulatory basis.",
        ]
        return "\n".join(lines)
