import unittest
from datetime import date, datetime

from eu_benchmark_regulation_for_strategies_referencing_indices import (
    ARTICLE_29_1B_REPLACEMENT_MONTHS,
    BMR_AMENDMENT_APPLICATION_DATE,
    BenchmarkSpec,
    BmrConfigurationError,
    CATEGORY_COMMODITY_ANNEX_II,
    CATEGORY_CRITICAL,
    CATEGORY_EU_CLIMATE,
    CATEGORY_OUT_OF_SCOPE,
    CATEGORY_SIGNIFICANT,
    ENTITY_INVESTMENT_FIRM,
    ENTITY_NON_SUPERVISED,
    ENTITY_UCITS,
    EXEMPTION_CENTRAL_BANK,
    EuBmrComplianceEngine,
    FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
    FINDING_ADDITION_PROHIBITED_WARNING_NOTICE,
    FINDING_CENTRAL_BANK_PLAN_ADVISORY,
    FINDING_EXISTING_USE_REPLACEMENT_OVERDUE,
    FINDING_EXISTING_USE_REPLACEMENT_REQUIRED,
    FINDING_MISSING_FALLBACK_PROVISIONS,
    FINDING_MISSING_WRITTEN_PLAN,
    FINDING_NO_ALTERNATIVE_DESIGNATED,
    FINDING_OUT_OF_SCOPE_NO_USER_OBLIGATIONS,
    FINDING_REGISTER_CHECK_PREDATES_AMENDMENT,
    FINDING_REGISTER_CHECK_STALE,
    FINDING_STATUTORY_REPLACEMENT_RELIED_ON,
    FINDING_WARNING_NOTICE_DEROGATION_ACTIVE,
    SEVERITY_VIOLATION,
    STATUS_BMR_ACTION_REQUIRED,
    STATUS_BMR_COMPLIANT,
    STATUS_BMR_VIOLATION,
    STATUS_OUT_OF_SCOPE_ARTICLE_2_2_EXEMPTION,
    STATUS_OUT_OF_SCOPE_BENCHMARK,
    STATUS_OUT_OF_SCOPE_NOT_A_BMR_USE,
    STATUS_OUT_OF_SCOPE_NOT_SUPERVISED_ENTITY,
    StrategyBenchmarkUsage,
    USE_MEASURING_FUND_PERFORMANCE,
    USE_NOT_A_BMR_USE,
    _add_months,
)

# All assessments use a fixed date after the 1 January 2026 application of
# Regulation (EU) 2025/914 so results are reproducible.
TODAY = date(2026, 8, 24)
RECENT_CHECK = date(2026, 8, 20)


def _codes(report):
    return {f.code for f in report.findings}


def _fully_compliant_usage(**overrides):
    """A UCITS tracking an index with every Article 28(2) limb satisfied."""
    kwargs = dict(
        strategy_id="STRAT_EU_EQUITY_01",
        strategy_name="EU Index Tracking Sub-Fund",
        referenced_benchmark_id="BM_STOXX50",
        entity_type=ENTITY_UCITS,
        use_type=USE_MEASURING_FUND_PERFORMANCE,
        is_new_reference=True,
        has_written_fallback_plan=True,
        designates_alternative_benchmark=True,
        fallback_reflected_in_contractual_terms=True,
    )
    kwargs.update(overrides)
    return StrategyBenchmarkUsage(**kwargs)


class BmrEngineTestBase(unittest.TestCase):

    def setUp(self):
        self.engine = EuBmrComplianceEngine()

        # EURO STOXX 50 — a significant benchmark from 1 Jan 2026 per the
        # administrator's own published classification.
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_STOXX50",
            benchmark_name="EURO STOXX 50",
            administrator_name="STOXX Ltd",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=RECENT_CHECK,
            fallback_benchmark_name="STOXX Europe 600",
        ))

        # A small proprietary index: out of scope from 1 Jan 2026.
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_CUSTOM_ALPHA",
            benchmark_name="Custom Prop Alpha Index",
            administrator_name="Unlicensed Vendor",
            category=CATEGORY_OUT_OF_SCOPE,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
        ))

        # A CTB whose third-country administrator is not on the register.
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_CTB_UNREG",
            benchmark_name="Global Climate Transition Benchmark",
            administrator_name="Third-Country Index Co",
            category=CATEGORY_EU_CLIMATE,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
            fallback_benchmark_name="STOXX Europe 600 CTB",
        ))

        # €STR: provided by the ECB, exempt under Article 2(2)(a).
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_ESTR",
            benchmark_name="Euro short-term rate (€STR)",
            administrator_name="European Central Bank",
            category=CATEGORY_OUT_OF_SCOPE,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
            article_2_2_exemption=EXEMPTION_CENTRAL_BANK,
        ))


class TestScopeGates(BmrEngineTestBase):
    """Scope is decided before any register or plan test."""

    def test_non_supervised_entity_has_no_bmr_user_obligations(self):
        usage = _fully_compliant_usage(
            entity_type=ENTITY_NON_SUPERVISED,
            has_written_fallback_plan=False,
            designates_alternative_benchmark=False,
            fallback_reflected_in_contractual_terms=False,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status,
                         STATUS_OUT_OF_SCOPE_NOT_SUPERVISED_ENTITY)
        self.assertFalse(report.in_scope)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.findings, [])

    def test_activity_outside_article_3_1_7_is_not_use(self):
        """Trading an index future on own book is not 'use of a benchmark'."""
        usage = _fully_compliant_usage(
            entity_type=ENTITY_INVESTMENT_FIRM,
            use_type=USE_NOT_A_BMR_USE,
            has_written_fallback_plan=False,
            designates_alternative_benchmark=False,
            fallback_reflected_in_contractual_terms=False,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status,
                         STATUS_OUT_OF_SCOPE_NOT_A_BMR_USE)
        self.assertFalse(report.in_scope)
        self.assertEqual(report.findings, [])

    def test_out_of_scope_benchmark_is_not_a_register_violation(self):
        """Regression: the pre-2026 engine flagged this as UNAUTHORIZED."""
        usage = _fully_compliant_usage(
            referenced_benchmark_id="BM_CUSTOM_ALPHA",
            has_written_fallback_plan=False,
            designates_alternative_benchmark=False,
            fallback_reflected_in_contractual_terms=False,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_OUT_OF_SCOPE_BENCHMARK)
        self.assertFalse(report.in_scope)
        self.assertTrue(report.is_compliant)
        self.assertEqual(_codes(report), {FINDING_OUT_OF_SCOPE_NO_USER_OBLIGATIONS})

    @staticmethod
    def _legacy_engine():
        """Same out-of-scope index, but with a register check dated pre-2026."""
        engine = EuBmrComplianceEngine()
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_CUSTOM_ALPHA",
            benchmark_name="Custom Prop Alpha Index",
            administrator_name="Unlicensed Vendor",
            category=CATEGORY_OUT_OF_SCOPE,
            administrator_on_esma_register=False,
            register_status_verified_on=date(2025, 6, 20),
            fallback_benchmark_name="STOXX Europe 600",
        ))
        return engine

    def test_same_benchmark_was_in_scope_before_the_amendment_applied(self):
        """Historical audits must use the regime in force on their own date."""
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_CUSTOM_ALPHA")
        report = self._legacy_engine().audit_strategy_bmr_compliance(
            usage, date(2025, 6, 30))

        self.assertTrue(report.in_scope)
        self.assertEqual(report.compliance_status, STATUS_BMR_VIOLATION)
        self.assertIn(FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                      _codes(report))

    def test_amendment_boundary_is_inclusive_of_1_january_2026(self):
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_CUSTOM_ALPHA")
        engine = EuBmrComplianceEngine(register_check_max_age_days=100000)
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_CUSTOM_ALPHA",
            benchmark_name="Custom Prop Alpha Index",
            administrator_name="Unlicensed Vendor",
            category=CATEGORY_OUT_OF_SCOPE,
            administrator_on_esma_register=False,
            register_status_verified_on=date(2025, 12, 30),
            fallback_benchmark_name="STOXX Europe 600",
        ))

        day_before = engine.audit_strategy_bmr_compliance(
            usage, date(2025, 12, 31))
        first_day = engine.audit_strategy_bmr_compliance(
            usage, BMR_AMENDMENT_APPLICATION_DATE)

        self.assertTrue(day_before.in_scope)
        self.assertFalse(first_day.in_scope)

    def test_central_bank_benchmark_is_exempt_but_plans_still_advised(self):
        usage = _fully_compliant_usage(
            referenced_benchmark_id="BM_ESTR",
            has_written_fallback_plan=False,
            designates_alternative_benchmark=False,
            fallback_reflected_in_contractual_terms=False,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status,
                         STATUS_OUT_OF_SCOPE_ARTICLE_2_2_EXEMPTION)
        self.assertEqual(_codes(report), {FINDING_CENTRAL_BANK_PLAN_ADVISORY})
        # Advisory only — the exemption means nothing is breached.
        self.assertTrue(report.is_compliant)

    def test_central_bank_benchmark_with_a_plan_raises_no_advisory(self):
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_ESTR")
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status,
                         STATUS_OUT_OF_SCOPE_ARTICLE_2_2_EXEMPTION)
        self.assertEqual(report.findings, [])


class TestArticle29Register(BmrEngineTestBase):

    def test_significant_benchmark_on_register_is_compliant(self):
        report = self.engine.audit_strategy_bmr_compliance(
            _fully_compliant_usage(), TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertTrue(report.in_scope)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.findings, [])
        self.assertIsNone(report.replacement_deadline)

    def test_unregistered_administrator_blocks_a_new_ctb_reference(self):
        usage = _fully_compliant_usage(
            strategy_id="STRAT_CLIMATE_01",
            referenced_benchmark_id="BM_CTB_UNREG",
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_VIOLATION)
        self.assertIn(FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                      _codes(report))

    def test_unregistered_administrator_does_not_block_an_existing_reference(self):
        """Article 29(1) bars ADDING a reference, not continuing to hold one."""
        usage = _fully_compliant_usage(
            strategy_id="STRAT_CLIMATE_02",
            referenced_benchmark_id="BM_CTB_UNREG",
            is_new_reference=False,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertNotIn(FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                         _codes(report))

    def test_significant_benchmark_without_notice_has_no_register_gate(self):
        """A significant benchmark is barred only under a public notice."""
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_SIG_UNREG",
            benchmark_name="Widely Used Third-Country Index",
            administrator_name="Pending Recognition Co",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
            fallback_benchmark_name="STOXX Europe 600",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_SIG_UNREG")
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertNotIn(FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                         _codes(report))

    def test_critical_benchmark_register_gate_applies(self):
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_EURIBOR",
            benchmark_name="EURIBOR 3M",
            administrator_name="EMMI",
            category=CATEGORY_CRITICAL,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
            fallback_benchmark_name="€STR compounded plus spread adjustment",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_EURIBOR")
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertIn(FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                      _codes(report))

    def test_annex_ii_commodity_register_gate_applies(self):
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_COMMODITY",
            benchmark_name="Contributed Freight Rate Assessment",
            administrator_name="Assessment Vendor",
            category=CATEGORY_COMMODITY_ANNEX_II,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
            fallback_benchmark_name="Exchange-settled freight index",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_COMMODITY")
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertIn(FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                      _codes(report))

    def test_statutory_replacement_permits_an_otherwise_barred_reference(self):
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_EONIA",
            benchmark_name="EONIA",
            administrator_name="EMMI",
            category=CATEGORY_CRITICAL,
            administrator_on_esma_register=False,
            register_status_verified_on=RECENT_CHECK,
            designated_statutory_replacement="€STR plus 8.5 basis points",
            fallback_benchmark_name="€STR plus 8.5 basis points",
        ))
        usage = _fully_compliant_usage(
            referenced_benchmark_id="BM_EONIA",
            relies_on_designated_statutory_replacement=True,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertEqual(_codes(report), {FINDING_STATUTORY_REPLACEMENT_RELIED_ON})

    def test_claiming_a_replacement_that_does_not_exist_raises(self):
        usage = _fully_compliant_usage(
            relies_on_designated_statutory_replacement=True)
        with self.assertRaises(BmrConfigurationError):
            self.engine.audit_strategy_bmr_compliance(usage, TODAY)


class TestArticle29WarningNotice(unittest.TestCase):

    NOTICE_DATE = date(2026, 3, 15)

    def setUp(self):
        self.engine = EuBmrComplianceEngine()
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_NOTICED",
            benchmark_name="Non-Compliant Significant Index",
            administrator_name="Struggling Administrator",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=date(2026, 3, 10),
            warning_notice_published_on=self.NOTICE_DATE,
            fallback_benchmark_name="STOXX Europe 600",
        ))

    def _usage(self, **overrides):
        return _fully_compliant_usage(
            referenced_benchmark_id="BM_NOTICED", **overrides)

    def test_new_reference_under_a_public_notice_is_prohibited(self):
        report = self.engine.audit_strategy_bmr_compliance(
            self._usage(), date(2026, 4, 1))

        self.assertEqual(report.compliance_status, STATUS_BMR_VIOLATION)
        self.assertIn(FINDING_ADDITION_PROHIBITED_WARNING_NOTICE,
                      _codes(report))

    def test_notice_is_not_in_force_before_its_publication_date(self):
        report = self.engine.audit_strategy_bmr_compliance(
            self._usage(), date(2026, 3, 14))

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertIsNone(report.replacement_deadline)

    def test_existing_reference_must_be_replaced_within_six_months(self):
        report = self.engine.audit_strategy_bmr_compliance(
            self._usage(is_new_reference=False), date(2026, 4, 1))

        self.assertEqual(report.compliance_status, STATUS_BMR_ACTION_REQUIRED)
        self.assertEqual(_codes(report),
                         {FINDING_EXISTING_USE_REPLACEMENT_REQUIRED})
        self.assertEqual(report.replacement_deadline, date(2026, 9, 15))

    def test_six_month_deadline_boundary_is_inclusive(self):
        on_deadline = self.engine.audit_strategy_bmr_compliance(
            self._usage(is_new_reference=False), date(2026, 9, 15))
        day_after = self.engine.audit_strategy_bmr_compliance(
            self._usage(is_new_reference=False), date(2026, 9, 16))

        self.assertEqual(on_deadline.compliance_status,
                         STATUS_BMR_ACTION_REQUIRED)
        self.assertEqual(day_after.compliance_status, STATUS_BMR_VIOLATION)
        self.assertIn(FINDING_EXISTING_USE_REPLACEMENT_OVERDUE,
                      _codes(day_after))

    def test_published_reasoned_statement_satisfies_the_alternative_limb(self):
        report = self.engine.audit_strategy_bmr_compliance(
            self._usage(is_new_reference=False,
                        replacement_statement_published=True),
            date(2027, 1, 1))

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertEqual(_codes(report),
                         {FINDING_EXISTING_USE_REPLACEMENT_REQUIRED,
                          FINDING_REGISTER_CHECK_STALE})

    def test_active_derogation_permits_a_new_reference(self):
        engine = EuBmrComplianceEngine()
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_DEROG",
            benchmark_name="Derogated Significant Index",
            administrator_name="Struggling Administrator",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=date(2026, 3, 20),
            warning_notice_published_on=self.NOTICE_DATE,
            warning_notice_derogation_until=date(2026, 4, 15),
            fallback_benchmark_name="STOXX Europe 600",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_DEROG")

        during = engine.audit_strategy_bmr_compliance(usage, date(2026, 4, 15))
        after = engine.audit_strategy_bmr_compliance(usage, date(2026, 4, 16))

        self.assertEqual(during.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertIn(FINDING_WARNING_NOTICE_DEROGATION_ACTIVE, _codes(during))
        self.assertEqual(after.compliance_status, STATUS_BMR_VIOLATION)
        self.assertIn(FINDING_ADDITION_PROHIBITED_WARNING_NOTICE, _codes(after))


class TestArticle28Limbs(BmrEngineTestBase):

    def test_missing_written_plan_is_a_violation(self):
        usage = _fully_compliant_usage(has_written_fallback_plan=False)
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_VIOLATION)
        self.assertEqual(_codes(report), {FINDING_MISSING_WRITTEN_PLAN})

    def test_plan_not_reflected_in_contractual_fallbacks_is_a_violation(self):
        usage = _fully_compliant_usage(
            fallback_reflected_in_contractual_terms=False)
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_VIOLATION)
        self.assertEqual(_codes(report), {FINDING_MISSING_FALLBACK_PROVISIONS})

    def test_no_nominated_alternative_is_advisory_not_a_violation(self):
        """Article 28(2) nominates alternatives 'where feasible and appropriate'."""
        usage = _fully_compliant_usage(designates_alternative_benchmark=False)
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertEqual(_codes(report), {FINDING_NO_ALTERNATIVE_DESIGNATED})

    def test_alternative_claimed_but_none_recorded_on_the_spec_is_advisory(self):
        engine = EuBmrComplianceEngine()
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_NO_FALLBACK",
            benchmark_name="Significant Index Without Fallback",
            administrator_name="STOXX Ltd",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=RECENT_CHECK,
        ))
        usage = _fully_compliant_usage(
            referenced_benchmark_id="BM_NO_FALLBACK")
        report = engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(_codes(report), {FINDING_NO_ALTERNATIVE_DESIGNATED})

    def test_all_failing_limbs_are_reported_not_just_the_first(self):
        """Regression: the pre-2.0 engine returned only the first violation."""
        usage = _fully_compliant_usage(
            referenced_benchmark_id="BM_CTB_UNREG",
            has_written_fallback_plan=True,
            designates_alternative_benchmark=False,
            fallback_reflected_in_contractual_terms=False,
        )
        report = self.engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(
            _codes(report),
            {FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
             FINDING_NO_ALTERNATIVE_DESIGNATED,
             FINDING_MISSING_FALLBACK_PROVISIONS})
        self.assertEqual(
            sum(1 for f in report.findings if f.severity == SEVERITY_VIOLATION), 2)


class TestRegisterCurrency(BmrEngineTestBase):

    def test_stale_register_check_is_advisory(self):
        engine = EuBmrComplianceEngine(register_check_max_age_days=30)
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_STALE",
            benchmark_name="Stale-Checked Index",
            administrator_name="STOXX Ltd",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=date(2026, 6, 1),
            fallback_benchmark_name="STOXX Europe 600",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_STALE")
        report = engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertEqual(report.compliance_status, STATUS_BMR_COMPLIANT)
        self.assertIn(FINDING_REGISTER_CHECK_STALE, _codes(report))

    def test_staleness_boundary_is_exclusive(self):
        """Exactly at the window is not yet stale; one day past it is."""
        for age_days, expected_stale in ((30, False), (31, True)):
            with self.subTest(age_days=age_days):
                engine = EuBmrComplianceEngine(register_check_max_age_days=30)
                engine.register_benchmark(BenchmarkSpec(
                    benchmark_id="BM_AGE",
                    benchmark_name="Age Boundary Index",
                    administrator_name="STOXX Ltd",
                    category=CATEGORY_SIGNIFICANT,
                    administrator_on_esma_register=True,
                    register_status_verified_on=date.fromordinal(
                        TODAY.toordinal() - age_days),
                    fallback_benchmark_name="STOXX Europe 600",
                ))
                report = engine.audit_strategy_bmr_compliance(
                    _fully_compliant_usage(referenced_benchmark_id="BM_AGE"),
                    TODAY)
                self.assertEqual(
                    FINDING_REGISTER_CHECK_STALE in _codes(report),
                    expected_stale)

    def test_register_check_predating_the_amendment_is_flagged(self):
        engine = EuBmrComplianceEngine(register_check_max_age_days=100000)
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_OLD_CHECK",
            benchmark_name="Index Checked Under The Old Register",
            administrator_name="STOXX Ltd",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=date(2025, 11, 1),
            fallback_benchmark_name="STOXX Europe 600",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_OLD_CHECK")
        report = engine.audit_strategy_bmr_compliance(usage, TODAY)

        self.assertIn(FINDING_REGISTER_CHECK_PREDATES_AMENDMENT, _codes(report))

    def test_future_dated_register_check_raises(self):
        engine = EuBmrComplianceEngine()
        engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_FUTURE",
            benchmark_name="Future-Checked Index",
            administrator_name="STOXX Ltd",
            category=CATEGORY_SIGNIFICANT,
            administrator_on_esma_register=True,
            register_status_verified_on=date(2026, 12, 1),
            fallback_benchmark_name="STOXX Europe 600",
        ))
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_FUTURE")
        with self.assertRaises(BmrConfigurationError):
            engine.audit_strategy_bmr_compliance(usage, TODAY)


class TestInputValidation(BmrEngineTestBase):

    def test_unknown_benchmark_id_raises_rather_than_reporting_a_violation(self):
        """Regression: the pre-2.0 engine returned UNAUTHORIZED for a typo."""
        usage = _fully_compliant_usage(referenced_benchmark_id="BM_STOXX_50")
        with self.assertRaises(BmrConfigurationError):
            self.engine.audit_strategy_bmr_compliance(usage, TODAY)

    def test_unrecognised_category_raises(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="BM_BAD_CATEGORY",
                benchmark_name="Mis-Cased Category",
                administrator_name="Vendor",
                category="Significant",
                administrator_on_esma_register=True,
                register_status_verified_on=RECENT_CHECK,
            ))

    def test_unrecognised_exemption_raises(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="BM_BAD_EXEMPTION",
                benchmark_name="Bad Exemption",
                administrator_name="Vendor",
                category=CATEGORY_OUT_OF_SCOPE,
                administrator_on_esma_register=False,
                register_status_verified_on=RECENT_CHECK,
                article_2_2_exemption="CENTRAL_BANKS",
            ))

    def test_duplicate_benchmark_id_raises(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="BM_STOXX50",
                benchmark_name="EURO STOXX 50 (duplicate entry)",
                administrator_name="STOXX Ltd",
                category=CATEGORY_OUT_OF_SCOPE,
                administrator_on_esma_register=False,
                register_status_verified_on=RECENT_CHECK,
            ))

    def test_warning_notice_on_a_non_significant_benchmark_raises(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="BM_NOTICE_ON_CRITICAL",
                benchmark_name="Critical With Notice",
                administrator_name="EMMI",
                category=CATEGORY_CRITICAL,
                administrator_on_esma_register=True,
                register_status_verified_on=RECENT_CHECK,
                warning_notice_published_on=date(2026, 3, 15),
            ))

    def test_derogation_without_a_notice_raises(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="BM_ORPHAN_DEROGATION",
                benchmark_name="Derogation Without Notice",
                administrator_name="Vendor",
                category=CATEGORY_SIGNIFICANT,
                administrator_on_esma_register=True,
                register_status_verified_on=RECENT_CHECK,
                warning_notice_derogation_until=date(2026, 6, 1),
            ))

    def test_empty_identifiers_raise(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="   ",
                benchmark_name="Blank Id",
                administrator_name="Vendor",
                category=CATEGORY_OUT_OF_SCOPE,
                administrator_on_esma_register=False,
                register_status_verified_on=RECENT_CHECK,
            ))

    def test_unrecognised_entity_type_raises(self):
        usage = _fully_compliant_usage(entity_type="HEDGE_FUND")
        with self.assertRaises(BmrConfigurationError):
            self.engine.audit_strategy_bmr_compliance(usage, TODAY)

    def test_unrecognised_use_type_raises(self):
        usage = _fully_compliant_usage(use_type="INDEX_ARBITRAGE")
        with self.assertRaises(BmrConfigurationError):
            self.engine.audit_strategy_bmr_compliance(usage, TODAY)

    def test_negative_register_check_window_raises(self):
        with self.assertRaises(BmrConfigurationError):
            EuBmrComplianceEngine(register_check_max_age_days=-1)

    def test_datetime_assessment_date_raises_rather_than_comparing(self):
        """datetime subclasses date; comparing it to a date raises TypeError."""
        usage = _fully_compliant_usage()
        with self.assertRaises(BmrConfigurationError):
            self.engine.audit_strategy_bmr_compliance(
                usage, datetime(2026, 8, 24, 13, 30))

    def test_datetime_register_check_raises_at_registration(self):
        with self.assertRaises(BmrConfigurationError):
            self.engine.register_benchmark(BenchmarkSpec(
                benchmark_id="BM_DATETIME",
                benchmark_name="Datetime-Stamped Index",
                administrator_name="STOXX Ltd",
                category=CATEGORY_SIGNIFICANT,
                administrator_on_esma_register=True,
                register_status_verified_on=datetime(2026, 8, 20, 9, 0),
            ))


class TestAddMonths(unittest.TestCase):
    """Independently derived expectations for the Article 29(1b) clock."""

    def test_month_end_clamping(self):
        self.assertEqual(_add_months(date(2026, 3, 31), 6), date(2026, 9, 30))

    def test_year_rollover(self):
        self.assertEqual(_add_months(date(2026, 10, 15), 6), date(2027, 4, 15))

    def test_leap_year_february(self):
        self.assertEqual(_add_months(date(2027, 8, 29), 6), date(2028, 2, 29))
        self.assertEqual(_add_months(date(2026, 8, 29), 6), date(2027, 2, 28))

    def test_configured_window_matches_the_regulation(self):
        self.assertEqual(ARTICLE_29_1B_REPLACEMENT_MONTHS, 6)


if __name__ == '__main__':
    unittest.main()
