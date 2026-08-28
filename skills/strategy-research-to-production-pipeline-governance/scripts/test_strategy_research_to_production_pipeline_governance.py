import copy
import logging
import unittest

from strategy_research_to_production_pipeline_governance import (
    Config, Engine,
    GENESIS_HASH,
    PipelineStage,
    PromotionStatus,
    StagePromotionArtifacts,
    StagePromotionDecision,
    StrategyResearchToProductionGovernanceEngine,
    compute_audit_hash,
    verify_audit_hash,
)

#: A fixed timestamp, so every hash assertion in this file is reproducible.
FIXED_TS = "2026-08-28T09:30:00+00:00"

LOGGER_NAME = "strategy_research_to_production_pipeline_governance"

# Keep test output clean without globally disabling logging, which would break
# the assertLogs checks below.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())
logging.getLogger(LOGGER_NAME).propagate = False


def make_artifacts(**overrides) -> StagePromotionArtifacts:
    """A fully-passing artifact bundle, overridable field by field."""
    base = dict(
        git_commit_hash="a1b2c3d4e5f",
        dataset_checksum="sha256:0f1e2d3c",
        backtest_sharpe=2.1,
        backtest_max_drawdown_pct=10.0,
        shadow_tracking_error_pct=2.5,
        paper_trading_days=21,
        has_risk_committee_signoff=True,
        author_id="Quant_Researcher_01",
        validator_id="Risk_CRO_02",
    )
    base.update(overrides)
    return StagePromotionArtifacts(**base)


def gate_names(gates):
    """The gate identifiers from a list of descriptive gate strings."""
    return sorted(g.split(":", 1)[0] for g in gates)


class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())


class GovernanceEngineTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyResearchToProductionGovernanceEngine(
            min_backtest_sharpe=1.50,
            max_backtest_drawdown_pct=15.0,
            max_shadow_tracking_error_pct=5.0,
            min_paper_trading_days=14,
        )

    def promote(self, current, target, artifacts=None, **kw):
        return self.engine.evaluate_stage_promotion(
            kw.pop("strategy_id", "STAT_ARB_PROD_01"),
            current,
            target,
            artifacts if artifacts is not None else make_artifacts(),
            decided_at_utc=kw.pop("decided_at_utc", FIXED_TS),
            **kw,
        )


class TestHappyPath(GovernanceEngineTestBase):

    def test_approved_promotion_to_live_production(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)

        self.assertTrue(decision.is_approved)
        self.assertEqual(
            decision.status_code, PromotionStatus.APPROVED_FOR_PROMOTION.value)
        self.assertEqual(decision.failed_gates, [])
        self.assertEqual(
            gate_names(decision.passed_gates),
            ["DRAWDOWN_GATE", "INDEPENDENCE_GATE", "PAPER_DAYS_GATE",
             "REPRODUCIBILITY_GATE", "RISK_GOVERNANCE_GATE", "SHADOW_TRACKING_GATE",
             "SHARPE_GATE", "STAGE_SEQUENCE_GATE"],
        )

    def test_full_pipeline_walk_approves_every_single_step(self):
        """Each adjacent transition, in order, is approvable on good artifacts."""
        for current, target in zip(
                [PipelineStage.RESEARCH_BACKTEST,
                 PipelineStage.INDEPENDENT_VALIDATION,
                 PipelineStage.PAPER_TRADING_SHADOW,
                 PipelineStage.STAGING_CANARY],
                [PipelineStage.INDEPENDENT_VALIDATION,
                 PipelineStage.PAPER_TRADING_SHADOW,
                 PipelineStage.STAGING_CANARY,
                 PipelineStage.LIVE_PRODUCTION]):
            with self.subTest(transition=f"{current.value}->{target.value}"):
                self.assertTrue(self.promote(current, target).is_approved)

    def test_early_stages_do_not_require_shadow_or_signoff_evidence(self):
        """A strategy entering validation has no paper-trading history yet."""
        artifacts = make_artifacts(
            paper_trading_days=0,
            shadow_tracking_error_pct=99.0,
            has_risk_committee_signoff=False,
            validator_id="",
        )
        decision = self.promote(
            PipelineStage.RESEARCH_BACKTEST,
            PipelineStage.INDEPENDENT_VALIDATION,
            artifacts,
        )
        self.assertTrue(decision.is_approved)
        self.assertNotIn("PAPER_DAYS_GATE", gate_names(decision.passed_gates))
        self.assertNotIn("INDEPENDENCE_GATE", gate_names(decision.passed_gates))


class TestStageSequencing(GovernanceEngineTestBase):
    """
    Regression tests for the gate that did not exist. The previous engine
    approved every one of these with otherwise-passing artifacts, while the
    documentation claimed sequential gatekeeping was enforced.
    """

    def test_skipping_straight_from_research_to_live_is_rejected(self):
        decision = self.promote(
            PipelineStage.RESEARCH_BACKTEST, PipelineStage.LIVE_PRODUCTION)

        self.assertFalse(decision.is_approved)
        self.assertIn("STAGE_SEQUENCE_GATE", gate_names(decision.failed_gates))
        sequence_failure = next(
            g for g in decision.failed_gates if g.startswith("STAGE_SEQUENCE_GATE"))
        self.assertIn("skips 3 stage(s)", sequence_failure)
        # The three skipped stages must be named, so the record shows what was
        # bypassed rather than only that something was.
        for skipped in ("INDEPENDENT_VALIDATION", "PAPER_TRADING_SHADOW",
                        "STAGING_CANARY"):
            self.assertIn(skipped, sequence_failure)

    def test_skipping_a_single_stage_is_rejected(self):
        decision = self.promote(
            PipelineStage.PAPER_TRADING_SHADOW, PipelineStage.LIVE_PRODUCTION)
        self.assertFalse(decision.is_approved)
        self.assertIn("STAGE_SEQUENCE_GATE", gate_names(decision.failed_gates))

    def test_backward_transition_is_rejected(self):
        decision = self.promote(
            PipelineStage.LIVE_PRODUCTION, PipelineStage.RESEARCH_BACKTEST)
        self.assertFalse(decision.is_approved)
        self.assertIn(
            "not a forward promotion",
            next(g for g in decision.failed_gates
                 if g.startswith("STAGE_SEQUENCE_GATE")))

    def test_same_stage_transition_is_rejected(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.STAGING_CANARY)
        self.assertFalse(decision.is_approved)
        self.assertIn("STAGE_SEQUENCE_GATE", gate_names(decision.failed_gates))

    def test_sequencing_failure_does_not_suppress_other_gate_findings(self):
        """A skipped-stage request must still report why else it would fail."""
        decision = self.promote(
            PipelineStage.RESEARCH_BACKTEST,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(backtest_sharpe=0.2, has_risk_committee_signoff=False),
        )
        self.assertEqual(
            gate_names(decision.failed_gates),
            ["RISK_GOVERNANCE_GATE", "SHARPE_GATE", "STAGE_SEQUENCE_GATE"])


class TestIndependenceGate(GovernanceEngineTestBase):

    def test_author_cannot_validate_their_own_strategy(self):
        """Self-sign-off previously produced a fully approved live promotion."""
        decision = self.promote(
            PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(author_id="quant_01", validator_id="quant_01"),
        )
        self.assertFalse(decision.is_approved)
        self.assertIn("INDEPENDENCE_GATE", gate_names(decision.failed_gates))
        self.assertIn(
            "Self-validation",
            next(g for g in decision.failed_gates
                 if g.startswith("INDEPENDENCE_GATE")))

    def test_whitespace_padding_does_not_defeat_the_identity_comparison(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(author_id="quant_01", validator_id="  quant_01  "),
        )
        self.assertFalse(decision.is_approved)
        self.assertIn("INDEPENDENCE_GATE", gate_names(decision.failed_gates))

    def test_promotion_out_of_independent_validation_requires_a_validator(self):
        """Otherwise the stage named 'independent validation' gates nothing."""
        decision = self.promote(
            PipelineStage.INDEPENDENT_VALIDATION,
            PipelineStage.PAPER_TRADING_SHADOW,
            make_artifacts(validator_id=""),
        )
        self.assertFalse(decision.is_approved)
        self.assertIn("INDEPENDENCE_GATE", gate_names(decision.failed_gates))


class TestReproducibilityGate(GovernanceEngineTestBase):

    def test_non_hexadecimal_commit_id_is_rejected(self):
        """'notahash' is 8 characters and passed the old length-only check."""
        decision = self.promote(
            PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(git_commit_hash="notahash"),
        )
        self.assertFalse(decision.is_approved)
        self.assertIn("REPRODUCIBILITY_GATE", gate_names(decision.failed_gates))

    def test_all_zero_commit_id_is_rejected(self):
        """The placeholder a pipeline emits when it cannot resolve a revision."""
        decision = self.promote(
            PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(git_commit_hash="0000000"),
        )
        self.assertFalse(decision.is_approved)
        self.assertIn("REPRODUCIBILITY_GATE", gate_names(decision.failed_gates))

    def test_whitespace_only_dataset_checksum_is_rejected(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(dataset_checksum="   "),
        )
        self.assertFalse(decision.is_approved)
        self.assertIn("dataset_checksum is blank",
                      " ".join(decision.failed_gates))

    def test_commit_hash_length_boundaries(self):
        cases = [("a" * 6, False), ("a" * 7, True), ("f" * 64, True),
                 ("a" * 65, False)]
        for candidate, should_pass in cases:
            with self.subTest(length=len(candidate)):
                decision = self.promote(
                    PipelineStage.STAGING_CANARY,
                    PipelineStage.LIVE_PRODUCTION,
                    make_artifacts(git_commit_hash=candidate),
                )
                self.assertEqual(decision.is_approved, should_pass)

    def test_uppercase_hex_commit_id_is_accepted(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION,
            make_artifacts(git_commit_hash="A1B2C3D4E5F"),
        )
        self.assertTrue(decision.is_approved)


class TestThresholdBoundaries(GovernanceEngineTestBase):
    """Exactly-at-the-limit must pass; a hair beyond must fail."""

    def test_sharpe_boundary(self):
        for sharpe, expected in [(1.50, True), (1.4999, False)]:
            with self.subTest(sharpe=sharpe):
                decision = self.promote(
                    PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                    make_artifacts(backtest_sharpe=sharpe))
                self.assertEqual(decision.is_approved, expected)

    def test_drawdown_boundary(self):
        for drawdown, expected in [(15.0, True), (15.0001, False)]:
            with self.subTest(drawdown=drawdown):
                decision = self.promote(
                    PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                    make_artifacts(backtest_max_drawdown_pct=drawdown))
                self.assertEqual(decision.is_approved, expected)

    def test_drawdown_rounds_to_the_cap_but_still_breaches(self):
        """15.004% renders as '15.0%' in the message and must still fail."""
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            make_artifacts(backtest_max_drawdown_pct=15.004))
        self.assertFalse(decision.is_approved)
        self.assertIn("DRAWDOWN_GATE", gate_names(decision.failed_gates))

    def test_tracking_error_boundary(self):
        for error, expected in [(5.0, True), (5.0001, False)]:
            with self.subTest(tracking_error=error):
                decision = self.promote(
                    PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                    make_artifacts(shadow_tracking_error_pct=error))
                self.assertEqual(decision.is_approved, expected)

    def test_paper_trading_days_boundary(self):
        for days, expected in [(14, True), (13, False)]:
            with self.subTest(days=days):
                decision = self.promote(
                    PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                    make_artifacts(paper_trading_days=days))
                self.assertEqual(decision.is_approved, expected)


class TestArtifactValidation(GovernanceEngineTestBase):
    """Structurally invalid submissions raise; they are not silently judged."""

    def test_negative_drawdown_is_rejected(self):
        """
        Regression: a -40% drawdown expressed as -40.0 satisfies `<= 15.0` and
        the old engine approved the promotion outright.
        """
        with self.assertRaises(ValueError) as ctx:
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(backtest_max_drawdown_pct=-40.0))
        self.assertIn("positive percentage magnitude", str(ctx.exception))

    def test_drawdown_above_one_hundred_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(backtest_max_drawdown_pct=150.0))

    def test_negative_tracking_error_is_rejected(self):
        """-50.0 satisfies `<= 5.0` and previously passed the shadow gate."""
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(shadow_tracking_error_pct=-50.0))

    def test_non_finite_metrics_are_rejected(self):
        for fieldname, value in [
                ("backtest_sharpe", float("nan")),
                ("backtest_sharpe", float("inf")),
                ("backtest_max_drawdown_pct", float("nan")),
                ("shadow_tracking_error_pct", float("nan")),
        ]:
            with self.subTest(field=fieldname, value=value):
                with self.assertRaises(ValueError):
                    self.promote(
                        PipelineStage.STAGING_CANARY,
                        PipelineStage.LIVE_PRODUCTION,
                        make_artifacts(**{fieldname: value}))

    def test_negative_paper_trading_days_is_rejected(self):
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(paper_trading_days=-999))

    def test_non_integer_paper_trading_days_is_rejected(self):
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(paper_trading_days=21.5))

    def test_truthy_non_boolean_signoff_is_rejected(self):
        """'pending' is truthy and would otherwise grant approval by accident."""
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(has_risk_committee_signoff="pending"))

    def test_blank_author_is_rejected(self):
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(author_id="   "))

    def test_blank_strategy_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                strategy_id="  ")

    def test_non_enum_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_stage_promotion(
                "S", "STAGING_CANARY", PipelineStage.LIVE_PRODUCTION,
                make_artifacts(), decided_at_utc=FIXED_TS)


class TestConstructorValidation(unittest.TestCase):

    def test_rejects_nonsensical_thresholds(self):
        bad_kwargs = [
            {"max_backtest_drawdown_pct": 1000.0},
            {"max_backtest_drawdown_pct": 0.0},
            {"max_backtest_drawdown_pct": -5.0},
            {"max_shadow_tracking_error_pct": -1.0},
            {"min_paper_trading_days": -5},
            {"min_paper_trading_days": 14.5},
            {"min_backtest_sharpe": float("nan")},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    StrategyResearchToProductionGovernanceEngine(**kwargs)

    def test_negative_min_sharpe_is_allowed(self):
        """A deliberately permissive Sharpe floor is a policy choice, not a bug."""
        engine = StrategyResearchToProductionGovernanceEngine(
            min_backtest_sharpe=-1.0)
        self.assertEqual(engine.min_sharpe, -1.0)


class TestAuditHash(GovernanceEngineTestBase):

    def test_hash_is_deterministic_and_reproducible(self):
        """
        Regression: the old hash mixed in an unrecorded `time.time()`, so it
        could never be recomputed by an auditor.
        """
        artifacts = make_artifacts()
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION, artifacts)

        self.assertIsInstance(decision, StagePromotionDecision)
        self.assertEqual(len(decision.audit_trail_hash), 64)

        # The exported hashing function is what an auditor would reach for, so
        # it must reproduce the engine's digest exactly.
        self.assertEqual(
            decision.audit_trail_hash,
            compute_audit_hash(
                decision.strategy_id, decision.current_stage,
                decision.target_stage, artifacts, self.engine.thresholds,
                decision.is_approved, decision.status_code,
                decision.passed_gates, decision.failed_gates,
                decision.decided_at_utc, decision.previous_audit_hash,
                decision.ledger_index,
            ),
        )
        self.assertTrue(
            verify_audit_hash(decision, artifacts, self.engine.thresholds))

        # A second engine, same inputs, same timestamp -> identical digest.
        twin = StrategyResearchToProductionGovernanceEngine()
        twin_decision = twin.evaluate_stage_promotion(
            "STAT_ARB_PROD_01", PipelineStage.STAGING_CANARY,
            PipelineStage.LIVE_PRODUCTION, make_artifacts(),
            decided_at_utc=FIXED_TS)
        self.assertEqual(decision.audit_trail_hash, twin_decision.audit_trail_hash)

    def test_timestamp_is_recorded_on_the_decision(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)
        self.assertEqual(decision.decided_at_utc, FIXED_TS)

    def test_ambiguous_or_malformed_timestamps_are_rejected(self):
        for bad in ["2026-08-28T09:30:00",      # naive: which 09:30?
                    "28/08/2026 09:30",          # not ISO-8601
                    "not-a-timestamp",
                    "   "]:
            with self.subTest(timestamp=bad):
                with self.assertRaises(ValueError):
                    self.promote(
                        PipelineStage.STAGING_CANARY,
                        PipelineStage.LIVE_PRODUCTION,
                        decided_at_utc=bad)

    def test_zulu_suffix_timestamp_is_accepted(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            decided_at_utc="2026-08-28T09:30:00Z")
        self.assertEqual(decision.decided_at_utc, "2026-08-28T09:30:00Z")

    def test_non_utc_offset_is_accepted_and_recorded_verbatim(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            decided_at_utc="2026-08-28T15:00:00+05:30")
        self.assertEqual(decision.decided_at_utc, "2026-08-28T15:00:00+05:30")

    def test_default_timestamp_is_timezone_aware_utc(self):
        engine = StrategyResearchToProductionGovernanceEngine()
        decision = engine.evaluate_stage_promotion(
            "S", PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            make_artifacts())
        self.assertTrue(decision.decided_at_utc.endswith("+00:00"))

    def test_hash_binds_the_quantitative_evidence(self):
        """
        Regression: the old hash covered only strategy id, stage names, the git
        hash and the boolean outcome. Altering the Sharpe ratio that justified
        an approval left the digest unchanged.
        """
        artifacts = make_artifacts()
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION, artifacts)

        for fieldname, tampered in [
                ("backtest_sharpe", 0.1),
                ("shadow_tracking_error_pct", 40.0),
                ("paper_trading_days", 1),
                ("has_risk_committee_signoff", False),
                ("validator_id", "Quant_Researcher_01"),
                ("dataset_checksum", "swapped_dataset"),
        ]:
            with self.subTest(field=fieldname):
                forged = copy.deepcopy(artifacts)
                setattr(forged, fieldname, tampered)
                self.assertFalse(
                    verify_audit_hash(decision, forged, self.engine.thresholds),
                    f"tampering with {fieldname} left the audit hash valid")

    def test_hash_binds_the_thresholds_the_decision_was_made_against(self):
        """A quietly loosened Sharpe floor must not produce the same record."""
        artifacts = make_artifacts()
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION, artifacts)
        loosened = dict(self.engine.thresholds, min_backtest_sharpe=0.1)
        self.assertFalse(verify_audit_hash(decision, artifacts, loosened))

    def test_hash_binds_the_recorded_outcome(self):
        artifacts = make_artifacts()
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION, artifacts)
        flipped = copy.deepcopy(decision)
        flipped.is_approved = False
        self.assertFalse(
            verify_audit_hash(flipped, artifacts, self.engine.thresholds))

    def test_two_distinct_decisions_get_distinct_hashes(self):
        """
        Regression: `time.time()` has ~15ms resolution on Windows, so two
        decisions evaluated in the same tick collided to one digest.
        """
        first = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            strategy_id="STRAT_A")
        second = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            strategy_id="STRAT_B")
        self.assertNotEqual(first.audit_trail_hash, second.audit_trail_hash)

    def test_identical_resubmission_still_gets_a_distinct_hash(self):
        """Chaining distinguishes two genuinely separate submission events."""
        first = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)
        second = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)
        self.assertNotEqual(first.audit_trail_hash, second.audit_trail_hash)
        self.assertEqual(second.previous_audit_hash, first.audit_trail_hash)


class TestAuditLedger(GovernanceEngineTestBase):

    def test_rejections_are_recorded_too(self):
        """A trail containing only approvals cannot show anything was refused."""
        self.promote(PipelineStage.RESEARCH_BACKTEST,
                     PipelineStage.LIVE_PRODUCTION)
        self.assertEqual(len(self.engine.ledger), 1)
        self.assertFalse(self.engine.ledger[0].is_approved)

    def test_ledger_chains_from_genesis_and_verifies(self):
        artifacts_by_index = {}
        for index, (current, target) in enumerate([
                (PipelineStage.RESEARCH_BACKTEST,
                 PipelineStage.INDEPENDENT_VALIDATION),
                (PipelineStage.INDEPENDENT_VALIDATION,
                 PipelineStage.PAPER_TRADING_SHADOW),
                (PipelineStage.PAPER_TRADING_SHADOW,
                 PipelineStage.STAGING_CANARY),
                (PipelineStage.STAGING_CANARY,
                 PipelineStage.LIVE_PRODUCTION)]):
            artifacts = make_artifacts()
            artifacts_by_index[index] = artifacts
            self.promote(current, target, artifacts)

        ledger = self.engine.ledger
        self.assertEqual(len(ledger), 4)
        self.assertEqual(ledger[0].previous_audit_hash, GENESIS_HASH)
        self.assertEqual([e.ledger_index for e in ledger], [0, 1, 2, 3])
        self.assertTrue(self.engine.verify_ledger())
        self.assertTrue(self.engine.verify_ledger(artifacts_by_index))

    def test_editing_a_recorded_entry_is_detected(self):
        artifacts = make_artifacts()
        self.promote(PipelineStage.STAGING_CANARY,
                     PipelineStage.LIVE_PRODUCTION, artifacts)
        self.promote(PipelineStage.PAPER_TRADING_SHADOW,
                     PipelineStage.STAGING_CANARY, make_artifacts())

        # Flip the first decision from rejected/approved without rehashing.
        self.engine.ledger[0].is_approved = False
        self.assertFalse(
            self.engine.verify_ledger({0: artifacts, 1: make_artifacts()}))

    def test_removing_an_entry_breaks_the_chain(self):
        for _ in range(3):
            self.promote(PipelineStage.STAGING_CANARY,
                         PipelineStage.LIVE_PRODUCTION)
        self.assertTrue(self.engine.verify_ledger())

        del self.engine._ledger[1]
        self.assertFalse(self.engine.verify_ledger())

    def test_reordering_entries_breaks_the_chain(self):
        for _ in range(2):
            self.promote(PipelineStage.STAGING_CANARY,
                         PipelineStage.LIVE_PRODUCTION)
        self.engine._ledger.reverse()
        self.assertFalse(self.engine.verify_ledger())

    def test_verify_ledger_reports_incomplete_when_artifacts_are_missing(self):
        self.promote(PipelineStage.STAGING_CANARY,
                     PipelineStage.LIVE_PRODUCTION)
        self.assertFalse(self.engine.verify_ledger({}))

    def test_empty_ledger_verifies(self):
        self.assertTrue(self.engine.verify_ledger())

    def test_ledger_property_is_an_immutable_snapshot(self):
        self.promote(PipelineStage.STAGING_CANARY,
                     PipelineStage.LIVE_PRODUCTION)
        snapshot = self.engine.ledger
        self.assertIsInstance(snapshot, tuple)
        self.promote(PipelineStage.STAGING_CANARY,
                     PipelineStage.LIVE_PRODUCTION)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(len(self.engine.ledger), 2)


class TestRejectionReporting(GovernanceEngineTestBase):

    def test_rejected_promotion_reports_every_breached_gate(self):
        artifacts = make_artifacts(
            shadow_tracking_error_pct=8.5,      # BREACH: 8.5% > 5% cap
            paper_trading_days=7,               # BREACH: 7d < 14d min
            has_risk_committee_signoff=False,   # BREACH: missing sign-off
        )
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION, artifacts)

        self.assertFalse(decision.is_approved)
        self.assertEqual(
            decision.status_code, PromotionStatus.REJECTED_GATES_FAILED.value)
        self.assertEqual(
            gate_names(decision.failed_gates),
            ["PAPER_DAYS_GATE", "RISK_GOVERNANCE_GATE", "SHADOW_TRACKING_GATE"])

    def test_status_code_is_stable_and_matchable(self):
        """
        Regression: the old code emitted 'REJECTED_GATES_FAILED (3)' with the
        count interpolated, so no caller could match on it, and none of the
        three documented status codes was ever produced.
        """
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            make_artifacts(has_risk_committee_signoff=False))
        self.assertEqual(decision.status_code, "REJECTED_GATES_FAILED")
        self.assertEqual(
            decision.status_code, PromotionStatus.REJECTED_GATES_FAILED.value)

    def test_signoff_flag_without_a_named_approver_is_rejected(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
            make_artifacts(validator_id="", has_risk_committee_signoff=True))
        self.assertFalse(decision.is_approved)
        self.assertIn("RISK_GOVERNANCE_GATE", gate_names(decision.failed_gates))

    def test_rejection_logs_a_warning_and_approval_logs_info(self):
        with self.assertLogs(LOGGER_NAME, level=logging.WARNING) as captured:
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION,
                make_artifacts(has_risk_committee_signoff=False))
        self.assertIn("REJECTED_GATES_FAILED", captured.output[0])

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            self.promote(
                PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)
        self.assertIn("APPROVED_FOR_PROMOTION", captured.output[0])

    def test_audit_notes_name_the_stages_the_timestamp_and_the_hash(self):
        decision = self.promote(
            PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)
        for fragment in ("STAGING_CANARY", "LIVE_PRODUCTION", FIXED_TS,
                         decision.audit_trail_hash,
                         PromotionStatus.APPROVED_FOR_PROMOTION.value):
            self.assertIn(fragment, decision.audit_notes)


if __name__ == '__main__':
    unittest.main()
