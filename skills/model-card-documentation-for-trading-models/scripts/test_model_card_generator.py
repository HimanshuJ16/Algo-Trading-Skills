import json
import math
import unittest

from model_card_generator import (
    ORDER_AFFECTING_MODEL_TYPES,
    REQUIRED_SECTIONS,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    ModelCardError,
    ModelCardGeneratorEngine,
    ModelCardReport,
    ModelGovernanceConfig,
    ModelIdentity,
    ModelLimitations,
    ModelPerformanceMetrics,
    ModelTrainingProvenance,
    ReviewThresholds,
)


class ModelCardTestBase(unittest.TestCase):
    """A fully documented card, so each test can remove exactly one thing."""

    def setUp(self):
        self.engine = ModelCardGeneratorEngine()
        self.identity = ModelIdentity(
            model_id="ML_ALPHA_001",
            name="XGBoost Momentum Alpha",
            version="1.2.0",
            author="Quant Research Team",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Intraday 5-minute equity momentum alpha signal generation.",
            out_of_scope_uses=["Penny stocks < $5.00", "Illiquid post-market sessions"],
        )
        self.provenance = ModelTrainingProvenance(
            training_data_sources=["Polygon.io consolidated trades", "CRSP delisting file"],
            training_window_start="2015-01-02",
            training_window_end="2021-12-31",
            feature_definitions=[
                "ret_5m: log(close_t / close_t-5), shifted one bar",
                "rvol_30m: realised vol of 5m returns over trailing 30m",
            ],
            label_definition="Sign of forward 15-minute return, triple-barrier labelled.",
            retraining_cadence="Quarterly, with out-of-time revalidation.",
        )
        self.performance = ModelPerformanceMetrics(
            sharpe_ratio=2.15,
            sortino_ratio=2.85,
            max_drawdown_pct=12.5,
            annual_return_pct=28.4,
            win_rate_pct=57.2,
            capacity_usd=10_000_000.0,
            evaluation_window="2022-01-03..2024-12-31",
            is_out_of_sample=True,
        )
        self.limitations = ModelLimitations(
            known_failure_modes=[
                "Degrades in the first 5 minutes after the open.",
                "Untested through a halt-and-reopen cycle.",
            ],
            monitoring_signals=["Realised vs expected Sharpe (20d)", "Feature drift PSI"],
        )
        self.governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="2026-05-01",
            validator="Independent Model Validation Group",
            kill_switch_triggers=["Drawdown > 20%", "Inference latency > 50ms"],
            applicable_frameworks=["Internal MRM policy", "MiFID II RTS 6 Article 9"],
        )

    def build(self, **overrides):
        kwargs = dict(
            identity=self.identity,
            performance=self.performance,
            governance=self.governance,
            provenance=self.provenance,
            limitations=self.limitations,
        )
        kwargs.update(overrides)
        return self.engine.generate_model_card(**kwargs)


class TestCompleteCard(ModelCardTestBase):

    def test_fully_documented_card_is_complete_with_no_findings(self):
        report = self.build()
        self.assertIsInstance(report, ModelCardReport)
        self.assertEqual(report.status, STATUS_COMPLETE)
        self.assertTrue(report.is_documentation_complete)
        self.assertEqual(report.blocking_gaps, ())
        self.assertEqual(report.advisory_findings, ())

    def test_card_contains_every_required_section_exactly_once(self):
        """The pre-2.0 SKILL.md claimed 'all 6 required MRM sections'; the card
        rendered three. Assert the count against the declared contract."""
        markdown = self.build().markdown_content
        headings = [ln for ln in markdown.split("\n") if ln.startswith("## ")]
        self.assertEqual(len(REQUIRED_SECTIONS), 6)
        for index, section in enumerate(REQUIRED_SECTIONS, start=1):
            self.assertEqual(markdown.count(f"## {index}. {section}"), 1)
        # Six numbered sections plus the advisory block.
        self.assertEqual(len(headings), 7)

    def test_card_carries_the_non_authorisation_disclaimer(self):
        markdown = self.build().markdown_content
        self.assertIn("not a deployment authorisation", markdown)
        self.assertIn("have no regulatory basis", markdown)

    def test_no_regulatory_compliance_verdict_is_issued(self):
        """A card must never assert that a model is 'SR 26-2 compliant'. SR 26-2
        states it 'does not set forth enforceable standards or prescriptive
        requirements' and applies to banking organizations over $30bn."""
        report = self.build()
        haystack = (report.markdown_content + report.audit_notes).lower()
        for forbidden in ("sr 26-2 compliant", "non_compliant", "non-compliant under"):
            self.assertNotIn(forbidden, haystack)
        self.assertFalse(hasattr(report, "is_mrm_compliant"))

    def test_report_is_deterministic(self):
        first = self.build(as_of_date="2026-06-01")
        second = self.build(as_of_date="2026-06-01")
        self.assertEqual(first.markdown_content, second.markdown_content)
        self.assertEqual(first.to_json(), second.to_json())

    def test_json_payload_round_trips(self):
        report = self.build()
        restored = json.loads(report.to_json())
        self.assertTrue(restored["documentation_status"]["is_documentation_complete"])
        self.assertEqual(
            restored["model_identity"]["model_id"], self.identity.model_id
        )
        self.assertEqual(
            restored["training_provenance"]["label_definition"],
            self.provenance.label_definition,
        )


class TestAdvisoryFindingsAreNeverSwallowed(ModelCardTestBase):
    """Regression for the pre-2.0 defect: a sub-threshold Sharpe was appended to
    an internal deficit list that was discarded whenever the card was otherwise
    compliant, so the audit note read 'APPROVED ... SR 26-2 compliant'."""

    def test_low_sharpe_is_reported_on_an_otherwise_complete_card(self):
        weak = ModelPerformanceMetrics(
            sharpe_ratio=0.10,
            sortino_ratio=0.15,
            max_drawdown_pct=5.0,
            annual_return_pct=1.0,
            win_rate_pct=30.0,
            capacity_usd=1_000_000.0,
            evaluation_window="2022-01-03..2024-12-31",
            is_out_of_sample=True,
        )
        report = self.build(performance=weak)
        self.assertEqual(report.status, STATUS_COMPLETE)
        self.assertEqual(len(report.advisory_findings), 1)
        self.assertIn("below the firm review threshold", report.advisory_findings[0])
        self.assertIn("Sharpe ratio 0.10", report.markdown_content)
        self.assertIn("below the firm review threshold", report.markdown_content)

    def test_excess_drawdown_is_advisory_not_a_documentation_gap(self):
        risky = ModelPerformanceMetrics(
            sharpe_ratio=2.0,
            sortino_ratio=2.4,
            max_drawdown_pct=40.0,
            annual_return_pct=55.0,
            win_rate_pct=51.0,
            capacity_usd=5_000_000.0,
            evaluation_window="2022-01-03..2024-12-31",
            is_out_of_sample=True,
        )
        report = self.build(performance=risky)
        self.assertEqual(report.status, STATUS_COMPLETE)
        self.assertEqual(report.blocking_gaps, ())
        self.assertTrue(
            any("Max drawdown 40.0%" in f for f in report.advisory_findings)
        )

    def test_in_sample_metrics_are_flagged(self):
        in_sample = ModelPerformanceMetrics(
            sharpe_ratio=3.0,
            sortino_ratio=3.5,
            max_drawdown_pct=8.0,
            annual_return_pct=40.0,
            win_rate_pct=60.0,
            capacity_usd=2_000_000.0,
            evaluation_window="2015-01-02..2021-12-31",
            is_out_of_sample=False,
        )
        findings = self.build(performance=in_sample).advisory_findings
        self.assertTrue(any("not marked out-of-sample" in f for f in findings))

    def test_missing_evaluation_window_is_flagged(self):
        no_window = ModelPerformanceMetrics(
            sharpe_ratio=2.0,
            sortino_ratio=2.4,
            max_drawdown_pct=8.0,
            annual_return_pct=20.0,
            win_rate_pct=55.0,
            capacity_usd=2_000_000.0,
            evaluation_window="   ",
            is_out_of_sample=True,
        )
        findings = self.build(performance=no_window).advisory_findings
        self.assertTrue(any("No evaluation window" in f for f in findings))

    def test_thresholds_are_caller_owned(self):
        lenient = ModelCardGeneratorEngine(
            ReviewThresholds(min_sharpe_ratio=0.0, max_drawdown_pct=100.0)
        )
        risky = ModelPerformanceMetrics(
            sharpe_ratio=0.05,
            sortino_ratio=0.05,
            max_drawdown_pct=90.0,
            annual_return_pct=1.0,
            win_rate_pct=20.0,
            capacity_usd=1.0,
            evaluation_window="2022-01-03..2024-12-31",
            is_out_of_sample=True,
        )
        report = lenient.generate_model_card(
            self.identity, risky, self.governance, self.provenance, self.limitations
        )
        self.assertEqual(report.advisory_findings, ())


class TestGovernanceFailsClosed(ModelCardTestBase):
    """Regression: pre-2.0 ModelGovernanceConfig() defaulted to
    is_validated_by_mrm=True with a hard-coded validation_date, so a caller who
    supplied nothing received an unearned validation stamp."""

    def test_default_governance_asserts_nothing(self):
        default = ModelGovernanceConfig()
        self.assertFalse(default.is_validated_by_mrm)
        self.assertIsNone(default.validation_date)
        self.assertEqual(default.validator, "")
        self.assertEqual(default.kill_switch_triggers, [])
        self.assertEqual(default.applicable_frameworks, [])

    def test_default_governance_yields_an_incomplete_card(self):
        report = self.build(governance=ModelGovernanceConfig())
        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertFalse(report.is_documentation_complete)
        self.assertIn("NOT VALIDATED", report.markdown_content)
        self.assertTrue(
            any("no independent validation sign-off" in g.lower() for g in report.blocking_gaps)
        )

    def test_unvalidated_model_is_incomplete(self):
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=False,
            kill_switch_triggers=["Drawdown > 20%"],
        )
        report = self.build(governance=governance)
        self.assertEqual(report.status, STATUS_INCOMPLETE)

    def test_validation_claimed_without_a_date_is_a_gap(self):
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date=None,
            validator="MRM",
            kill_switch_triggers=["Drawdown > 20%"],
        )
        gaps = self.build(governance=governance).blocking_gaps
        self.assertTrue(any("not an ISO-8601 date" in g for g in gaps))

    def test_validation_with_a_malformed_date_is_a_gap(self):
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="01/05/2026",
            validator="MRM",
            kill_switch_triggers=["Drawdown > 20%"],
        )
        gaps = self.build(governance=governance).blocking_gaps
        self.assertTrue(any("not an ISO-8601 date" in g for g in gaps))

    def test_validation_without_a_named_validator_is_a_gap(self):
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="2026-05-01",
            validator="   ",
            kill_switch_triggers=["Drawdown > 20%"],
        )
        gaps = self.build(governance=governance).blocking_gaps
        self.assertTrue(any("no validator is named" in g for g in gaps))

    def test_order_affecting_model_without_kill_switch_is_a_gap(self):
        self.assertIn(self.identity.model_type, ORDER_AFFECTING_MODEL_TYPES)
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="2026-05-01",
            validator="MRM",
            kill_switch_triggers=[],
        )
        gaps = self.build(governance=governance).blocking_gaps
        self.assertTrue(any("kill-switch" in g for g in gaps))

    def test_non_order_affecting_model_does_not_need_a_kill_switch(self):
        risk_model = ModelIdentity(
            model_id="RISK_001",
            name="Portfolio VaR Model",
            version="3.0.0",
            author="Risk",
            model_type="RISK_MODEL",
            asset_class="MULTI_ASSET",
            intended_use="Daily 99% one-day VaR for the consolidated book.",
            out_of_scope_uses=["Intraday limit enforcement"],
        )
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="2026-05-01",
            validator="MRM",
            kill_switch_triggers=[],
        )
        report = self.build(identity=risk_model, governance=governance)
        self.assertEqual(report.status, STATUS_COMPLETE)

    def test_whitespace_only_kill_switch_does_not_count(self):
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="2026-05-01",
            validator="MRM",
            kill_switch_triggers=["   ", ""],
        )
        gaps = self.build(governance=governance).blocking_gaps
        self.assertTrue(any("kill-switch" in g for g in gaps))


class TestValidationStaleness(ModelCardTestBase):

    def test_staleness_is_skipped_without_an_as_of_date(self):
        self.assertEqual(self.build().advisory_findings, ())

    def test_validation_within_the_cadence_is_not_flagged(self):
        # 2026-05-01 -> 2027-05-01 is 365 days; the boundary is not a breach.
        self.assertEqual(self.build(as_of_date="2027-05-01").advisory_findings, ())

    def test_validation_one_day_past_the_cadence_is_flagged(self):
        findings = self.build(as_of_date="2027-05-02").advisory_findings
        self.assertEqual(len(findings), 1)
        self.assertIn("366 days old", findings[0])

    def test_future_validation_date_is_flagged(self):
        findings = self.build(as_of_date="2026-04-01").advisory_findings
        self.assertTrue(any("is after the as-of date" in f for f in findings))

    def test_malformed_as_of_date_skips_the_check_silently(self):
        self.assertEqual(self.build(as_of_date="not-a-date").advisory_findings, ())


class TestMissingDocumentationSections(ModelCardTestBase):

    def test_absent_provenance_blocks_and_says_so_in_the_card(self):
        report = self.build(provenance=None)
        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertTrue(any("not reproducible" in g for g in report.blocking_gaps))
        self.assertIn("This card is not reproducible", report.markdown_content)

    def test_absent_feature_definitions_block(self):
        bare = ModelTrainingProvenance(
            training_data_sources=["Polygon.io"],
            training_window_start="2015-01-02",
            training_window_end="2021-12-31",
            feature_definitions=[],
            label_definition="Forward 15m return sign.",
            retraining_cadence="Quarterly",
        )
        gaps = self.build(provenance=bare).blocking_gaps
        self.assertTrue(any("no feature definitions" in g for g in gaps))

    def test_inverted_training_window_blocks(self):
        inverted = ModelTrainingProvenance(
            training_data_sources=["Polygon.io"],
            training_window_start="2021-12-31",
            training_window_end="2015-01-02",
            feature_definitions=["f: x"],
            label_definition="y",
            retraining_cadence="Quarterly",
        )
        gaps = self.build(provenance=inverted).blocking_gaps
        self.assertTrue(any("ends before it starts" in g for g in gaps))

    def test_non_iso_training_window_blocks(self):
        bad = ModelTrainingProvenance(
            training_data_sources=["Polygon.io"],
            training_window_start="Jan 2015",
            training_window_end="2021-12-31",
            feature_definitions=["f: x"],
            label_definition="y",
            retraining_cadence="Quarterly",
        )
        gaps = self.build(provenance=bad).blocking_gaps
        self.assertTrue(any("training_window_start" in g for g in gaps))

    def test_absent_limitations_block(self):
        report = self.build(limitations=None)
        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertTrue(
            any("Limitations & Known Failure Modes" in g for g in report.blocking_gaps)
        )

    def test_absent_monitoring_signals_block(self):
        limitations = ModelLimitations(
            known_failure_modes=["Degrades after the open."],
            monitoring_signals=[],
        )
        gaps = self.build(limitations=limitations).blocking_gaps
        self.assertTrue(any("ongoing monitoring signal" in g for g in gaps))

    def test_absent_out_of_scope_uses_block(self):
        identity = ModelIdentity(
            model_id="ML_ALPHA_002",
            name="Unbounded Alpha",
            version="0.1.0",
            author="Quant",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Everything.",
            out_of_scope_uses=[],
        )
        gaps = self.build(identity=identity).blocking_gaps
        self.assertTrue(any("no out-of-scope use documented" in g for g in gaps))

    def test_empty_intended_use_blocks(self):
        identity = ModelIdentity(
            model_id="ML_ALPHA_003",
            name="Nameless Purpose",
            version="0.1.0",
            author="Quant",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="   ",
            out_of_scope_uses=["Crypto"],
        )
        gaps = self.build(identity=identity).blocking_gaps
        self.assertTrue(any("intended_use is empty" in g for g in gaps))

    def test_every_gap_is_reported_at_once(self):
        report = self.engine.generate_model_card(
            self.identity, self.performance, ModelGovernanceConfig()
        )
        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertGreaterEqual(len(report.blocking_gaps), 4)


class TestNumericValidation(ModelCardTestBase):
    """Regression: pre-2.0, `nan > 25.0` and `nan < 1.0` are both False, so a card
    with NaN metrics was reported fully compliant."""

    def _metrics(self, **overrides):
        base = dict(
            sharpe_ratio=2.0,
            sortino_ratio=2.4,
            max_drawdown_pct=10.0,
            annual_return_pct=20.0,
            win_rate_pct=55.0,
            capacity_usd=1_000_000.0,
            evaluation_window="2022-01-03..2024-12-31",
            is_out_of_sample=True,
        )
        base.update(overrides)
        return ModelPerformanceMetrics(**base)

    def test_nan_drawdown_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(max_drawdown_pct=float("nan")))

    def test_nan_sharpe_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(sharpe_ratio=float("nan")))

    def test_infinite_sharpe_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(sharpe_ratio=math.inf))

    def test_negative_drawdown_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(max_drawdown_pct=-50.0))

    def test_drawdown_above_one_hundred_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(max_drawdown_pct=101.0))

    def test_win_rate_out_of_range_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(win_rate_pct=120.0))

    def test_negative_capacity_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(capacity_usd=-1.0))

    def test_non_numeric_metric_raises(self):
        with self.assertRaises(ModelCardError):
            self.build(performance=self._metrics(sharpe_ratio="2.0"))

    def test_boundary_values_are_accepted(self):
        report = self.build(
            performance=self._metrics(
                max_drawdown_pct=0.0, win_rate_pct=100.0, capacity_usd=0.0
            )
        )
        self.assertEqual(report.status, STATUS_COMPLETE)

    def test_negative_return_is_permitted(self):
        report = self.build(performance=self._metrics(annual_return_pct=-12.0))
        self.assertIn("| Annualised Return | -12.0% |", report.markdown_content)

    def test_blank_identity_raises(self):
        for field_name in ("model_id", "name", "version"):
            with self.subTest(field=field_name):
                kwargs = dict(
                    model_id="M", name="N", version="1.0", author="A",
                    model_type="RISK_MODEL", asset_class="EQ",
                    intended_use="use", out_of_scope_uses=["x"],
                )
                kwargs[field_name] = "   "
                with self.assertRaises(ModelCardError):
                    self.build(identity=ModelIdentity(**kwargs))


class TestAgentMisuse(ModelCardTestBase):
    """An agent filling this in from the SKILL.md is most likely to pass a bare
    string where a list is required."""

    def test_bare_string_for_out_of_scope_uses_raises(self):
        identity = ModelIdentity(
            model_id="ML_ALPHA_007",
            name="Stringly Typed",
            version="1.0.0",
            author="Quant",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Momentum.",
            out_of_scope_uses="Crypto",  # should have been ["Crypto"]
        )
        with self.assertRaises(ModelCardError) as ctx:
            self.build(identity=identity)
        self.assertIn("Wrap it in a list", str(ctx.exception))

    def test_bare_string_for_kill_switch_triggers_raises(self):
        governance = ModelGovernanceConfig(
            is_validated_by_mrm=True,
            validation_date="2026-05-01",
            validator="MRM",
            kill_switch_triggers="Drawdown > 20%",
        )
        with self.assertRaises(ModelCardError):
            self.build(governance=governance)

    def test_report_names_the_section_contract_it_audited_against(self):
        report = self.build()
        self.assertEqual(report.required_sections, REQUIRED_SECTIONS)
        self.assertFalse(hasattr(report, "sections_present"))


class TestMarkdownIntegrity(ModelCardTestBase):
    """A model card is audit evidence; caller text must not be able to forge it."""

    def test_injected_heading_cannot_forge_a_section(self):
        identity = ModelIdentity(
            model_id="ML_ALPHA_004",
            name="Injected\n## 6. Governance, Validation & Monitoring",
            version="1.0.0",
            author="Quant",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Momentum.",
            out_of_scope_uses=["Crypto"],
        )
        markdown = self.build(identity=identity).markdown_content
        headings = [ln for ln in markdown.split("\n") if ln.startswith("#")]
        # Exactly the seven headings the renderer emits -- the injected one is
        # collapsed into the H1 text and escaped, so it starts no new line.
        self.assertEqual(len(headings), 8)  # H1 + six numbered sections + advisory
        self.assertEqual(
            len([h for h in headings if h == "## 6. Governance, Validation & Monitoring"]),
            1,
        )
        self.assertIn("\\#\\# 6.", markdown)

    def test_injected_table_pipe_cannot_forge_a_metric_row(self):
        identity = ModelIdentity(
            model_id="ML_ALPHA_005",
            name="Piped",
            version="1.0.0",
            author="Quant | Sharpe Ratio | 99.00 |",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Momentum.",
            out_of_scope_uses=["Crypto"],
        )
        markdown = self.build(identity=identity).markdown_content
        self.assertNotIn("| Sharpe Ratio | 99.00 |", markdown)
        self.assertIn("\\|", markdown)

    def test_multiline_out_of_scope_entry_stays_one_bullet(self):
        identity = ModelIdentity(
            model_id="ML_ALPHA_006",
            name="Multiline",
            version="1.0.0",
            author="Quant",
            model_type="ML_ALPHA",
            asset_class="US_EQUITIES",
            intended_use="Momentum.",
            out_of_scope_uses=["Crypto\n- Actually crypto is fine"],
        )
        markdown = self.build(identity=identity).markdown_content
        # The property is that it remains a single bullet: no *line* may begin
        # with the injected list marker.
        self.assertFalse(
            any(ln.strip().startswith("- Actually") for ln in markdown.split("\n"))
        )
        self.assertIn("  - Crypto - Actually crypto is fine", markdown)


if __name__ == "__main__":
    unittest.main()
