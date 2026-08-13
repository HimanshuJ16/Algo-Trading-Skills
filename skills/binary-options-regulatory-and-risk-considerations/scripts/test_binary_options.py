import unittest
from datetime import datetime, timedelta, timezone

from binary_options import (
    BinaryOptionsManager,
    ClientCategory,
    ComplianceEngine,
    Jurisdiction,
    RegulatoryException,
    RiskEngine,
    RiskException,
    TradeContext,
    VenueStatus,
)

# Fixed evaluation instant so every rule that depends on time-to-maturity is
# deterministic rather than dependent on when the suite happens to run.
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def ctx(**overrides):
    """Builds a trade that is lawful and within limits unless overridden."""
    params = dict(
        asset_id="T1",
        underlying="AAPL",
        notional=50_000.0,
        strike=150.0,
        expiry=NOW + timedelta(days=40),
        jurisdiction=Jurisdiction.US_CFTC,
        client_type="PROFESSIONAL",
        venue="DCM_A",
        venue_status=VenueStatus.REGISTERED,
        is_natural_person=False,
    )
    params.update(overrides)
    return TradeContext(**params)


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.compliance_engine = ComplianceEngine()
        self.risk_engine = RiskEngine(max_notional_per_trade=100000.0)
        self.manager = BinaryOptionsManager(self.compliance_engine, self.risk_engine)


class TestTradeContextValidation(BaseCase):
    def test_client_category_is_case_insensitive(self):
        """Regression: the original engine compared client_type == "RETAIL" exactly,
        so "Retail" or "retail" silently cleared the EU/UK retail prohibition."""
        for value in ("RETAIL", "retail", "  Retail  ", ClientCategory.RETAIL):
            with self.subTest(value=value):
                self.assertIs(
                    ctx(client_type=value).client_category, ClientCategory.RETAIL
                )

    def test_unknown_client_category_rejected(self):
        for value in ("RETAILL", "", None, 1, "CONSUMER"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ctx(client_type=value)

    def test_case_variant_retail_is_still_banned_end_to_end(self):
        """The fail-open, exercised through the public entry point."""
        result = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.UK_FCA, client_type="Retail"), now=NOW
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_UK_RETAIL_PROHIBITED")

    def test_nan_and_nonpositive_notional_rejected(self):
        """Regression: NaN > limit is False, so a NaN notional passed the risk cap."""
        for value in (float("nan"), float("inf"), 0.0, -1.0, "50000", None, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ctx(notional=value)

    def test_nan_and_nonpositive_strike_rejected(self):
        for value in (float("nan"), 0.0, -150.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ctx(strike=value)

    def test_naive_expiry_rejected(self):
        """Term to maturity is legally material (MI 91-102), so an ambiguous naive
        timestamp is rejected rather than assumed to be UTC."""
        with self.assertRaises(ValueError):
            ctx(expiry=datetime(2026, 9, 1))

    def test_non_datetime_expiry_rejected(self):
        with self.assertRaises(ValueError):
            ctx(expiry="2026-09-01")

    def test_bad_enum_fields_rejected(self):
        with self.assertRaises(ValueError):
            ctx(jurisdiction="US_CFTC")
        with self.assertRaises(ValueError):
            ctx(venue_status="REGISTERED")
        with self.assertRaises(ValueError):
            ctx(asset_id="   ")

    def test_term_to_maturity(self):
        self.assertEqual(
            ctx(expiry=NOW + timedelta(days=10)).term_to_maturity(NOW), timedelta(days=10)
        )

    def test_mutating_client_type_after_construction_is_re_evaluated(self):
        """TradeContext is mutable, so the category cached at construction must not be
        trusted at evaluation time — otherwise a post-construction downgrade to RETAIL
        would be evaluated as PROFESSIONAL."""
        context = ctx(jurisdiction=Jurisdiction.UK_FCA, client_type="PROFESSIONAL")
        self.assertEqual(self.manager.process_order(context, now=NOW)["status"], "APPROVED")
        context.client_type = "RETAIL"
        self.assertEqual(
            self.manager.process_order(context, now=NOW)["reason_code"],
            "REG_UK_RETAIL_PROHIBITED",
        )

    def test_naive_now_rejected(self):
        """A naive `now` would otherwise raise TypeError deep inside a rule."""
        for call in (
            lambda: self.manager.process_order(ctx(), now=datetime(2026, 8, 13, 12)),
            lambda: self.compliance_engine.validate_trade(ctx(), now=datetime(2026, 8, 13, 12)),
            lambda: self.risk_engine.check_limits(ctx(), now=datetime(2026, 8, 13, 12)),
            lambda: self.risk_engine.current_exposure(now=datetime(2026, 8, 13, 12)),
        ):
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()


class TestVenueChecks(BaseCase):
    def test_unknown_venue_status_denied(self):
        """Regression: the original engine approved any US_CFTC trade regardless of
        venue, so an offshore unregistered platform passed."""
        result = self.manager.process_order(
            ctx(venue_status=VenueStatus.UNKNOWN), now=NOW
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_VENUE_STATUS_UNKNOWN")

    def test_unregistered_venue_denied(self):
        result = self.manager.process_order(
            ctx(venue_status=VenueStatus.UNREGISTERED), now=NOW
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_VENUE_NOT_REGISTERED")

    def test_venue_gate_applies_outside_the_us(self):
        """The venue check is not US-only: a UK professional on an unverified venue is
        the same offshore-platform exposure and must not be approved."""
        for jurisdiction in (
            Jurisdiction.UK_FCA,
            Jurisdiction.AU_ASIC,
            Jurisdiction.EU_ESMA,
            Jurisdiction.CA_CSA,
        ):
            for status, code in (
                (VenueStatus.UNKNOWN, "REG_VENUE_STATUS_UNKNOWN"),
                (VenueStatus.UNREGISTERED, "REG_VENUE_NOT_REGISTERED"),
            ):
                with self.subTest(jurisdiction=jurisdiction, status=status):
                    result = self.manager.process_order(
                        ctx(jurisdiction=jurisdiction, venue_status=status), now=NOW
                    )
                    self.assertEqual(result["reason_code"], code)

    def test_registered_venue_us_professional_approved(self):
        result = self.manager.process_order(ctx(), now=NOW)
        self.assertEqual(result["status"], "APPROVED")

    def test_unregulated_jurisdiction_prohibited(self):
        result = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.UNREGULATED, venue_status=VenueStatus.REGISTERED),
            now=NOW,
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_UNREGULATED_VENUE")


class TestJurisdictionRules(BaseCase):
    def test_uk_retail_banned_professional_allowed(self):
        """FCA PS19/11 / COBS 22.4 bans retail only; professionals are out of scope."""
        banned = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.UK_FCA, client_type="RETAIL"), now=NOW
        )
        self.assertEqual(banned["reason_code"], "REG_UK_RETAIL_PROHIBITED")
        self.assertIn("COBS 22.4", banned["citation"])

        allowed = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.UK_FCA, client_type="PROFESSIONAL"), now=NOW
        )
        self.assertEqual(allowed["status"], "APPROVED")

    def test_eu_retail_banned(self):
        result = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.EU_ESMA, client_type="RETAIL"), now=NOW
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_EU_RETAIL_PROHIBITED")

    def test_eu_professional_is_unresolved_not_approved(self):
        """ESMA's EU-wide measure expired 1 July 2019 and binding measures are now
        national, so EU_ESMA alone cannot authorise a trade."""
        result = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.EU_ESMA, client_type="PROFESSIONAL"), now=NOW
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_JURISDICTION_UNRESOLVED")

    def test_au_retail_banned_professional_allowed(self):
        banned = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.AU_ASIC, client_type="RETAIL"), now=NOW
        )
        self.assertEqual(banned["reason_code"], "REG_AU_RETAIL_PROHIBITED")
        allowed = self.manager.process_order(
            ctx(jurisdiction=Jurisdiction.AU_ASIC, client_type="PROFESSIONAL"), now=NOW
        )
        self.assertEqual(allowed["status"], "APPROVED")

    def test_canada_short_term_banned_for_individual_despite_professional_status(self):
        """MI 91-102 applies to individuals *including* accredited investors. The
        original retail/professional switch would have approved this."""
        result = self.manager.process_order(
            ctx(
                jurisdiction=Jurisdiction.CA_CSA,
                client_type="PROFESSIONAL",
                is_natural_person=True,
                expiry=NOW + timedelta(days=29),
            ),
            now=NOW,
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertEqual(result["reason_code"], "REG_CA_SHORT_TERM_PROHIBITED")

    def test_canada_30_day_boundary(self):
        """Prohibition is 'less than 30 days': exactly 30 days is outside it."""
        common = dict(
            jurisdiction=Jurisdiction.CA_CSA,
            client_type="RETAIL",
            is_natural_person=True,
        )
        at_30 = self.manager.process_order(
            ctx(expiry=NOW + timedelta(days=30), **common), now=NOW
        )
        self.assertEqual(at_30["status"], "APPROVED")

        just_under = self.manager.process_order(
            ctx(expiry=NOW + timedelta(days=30) - timedelta(seconds=1), **common), now=NOW
        )
        self.assertEqual(just_under["reason_code"], "REG_CA_SHORT_TERM_PROHIBITED")

    def test_canada_entity_short_term_allowed(self):
        result = self.manager.process_order(
            ctx(
                jurisdiction=Jurisdiction.CA_CSA,
                client_type="PROFESSIONAL",
                is_natural_person=False,
                expiry=NOW + timedelta(days=5),
            ),
            now=NOW,
        )
        self.assertEqual(result["status"], "APPROVED")

    def test_canada_unknown_person_type_fails_closed(self):
        result = self.manager.process_order(
            ctx(
                jurisdiction=Jurisdiction.CA_CSA,
                is_natural_person=None,
                expiry=NOW + timedelta(days=5),
            ),
            now=NOW,
        )
        self.assertEqual(result["reason_code"], "REG_CLIENT_TYPE_UNRESOLVED")

    def test_expired_option_rejected(self):
        result = self.manager.process_order(
            ctx(expiry=NOW - timedelta(seconds=1)), now=NOW
        )
        self.assertEqual(result["reason_code"], "REG_ALREADY_EXPIRED")

    def test_jurisdiction_without_rule_denies_by_default(self):
        engine = ComplianceEngine(jurisdiction_rules={})
        with self.assertRaises(RegulatoryException) as cm:
            engine.validate_trade(ctx(), now=NOW)
        self.assertEqual(cm.exception.reason_code, "REG_NO_RULE_CONFIGURED")

    def test_every_default_jurisdiction_has_a_rule(self):
        engine = ComplianceEngine()
        for jurisdiction in Jurisdiction:
            with self.subTest(jurisdiction=jurisdiction):
                self.assertIn(jurisdiction, engine.jurisdiction_rules)


class TestRulesetStaleness(BaseCase):
    def test_stale_ruleset_warns(self):
        engine = ComplianceEngine(
            ruleset_last_verified=NOW - timedelta(days=400),
            ruleset_max_age=timedelta(days=180),
        )
        with self.assertLogs("binary_options", level="WARNING") as logs:
            engine.validate_trade(ctx(), now=NOW)
        self.assertTrue(any("re-verify" in line for line in logs.output))

    def test_fresh_ruleset_does_not_warn(self):
        engine = ComplianceEngine(ruleset_last_verified=NOW - timedelta(days=1))
        with self.assertNoLogs("binary_options", level="WARNING"):
            engine.validate_trade(ctx(), now=NOW)


class TestRiskLimits(BaseCase):
    def test_per_trade_notional_limit(self):
        result = self.manager.process_order(ctx(notional=200_000.0), now=NOW)
        self.assertEqual(result["status"], "REJECTED_RISK")
        self.assertEqual(result["reason_code"], "RISK_PER_TRADE_NOTIONAL")
        self.assertIn("exceeds maximum per trade limit", result["message"])

    def test_per_trade_notional_boundary(self):
        at_limit = self.manager.process_order(ctx(notional=100_000.0), now=NOW)
        self.assertEqual(at_limit["status"], "APPROVED")

    def test_aggregate_notional_limit_accumulates(self):
        engine = RiskEngine(max_notional_per_trade=100_000.0, max_aggregate_notional=150_000.0)
        manager = BinaryOptionsManager(ComplianceEngine(), engine)
        first = manager.process_order(ctx(asset_id="A", notional=100_000.0), now=NOW)
        self.assertEqual(first["status"], "APPROVED")
        second = manager.process_order(ctx(asset_id="B", notional=100_000.0), now=NOW)
        self.assertEqual(second["reason_code"], "RISK_AGGREGATE_NOTIONAL")

    def test_resubmitting_same_asset_id_does_not_double_count(self):
        """Idempotency: a retried submission replaces its own exposure."""
        engine = RiskEngine(max_notional_per_trade=100_000.0, max_aggregate_notional=150_000.0)
        manager = BinaryOptionsManager(ComplianceEngine(), engine)
        for _ in range(3):
            result = manager.process_order(ctx(asset_id="A", notional=100_000.0), now=NOW)
            self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(engine.current_exposure(NOW)["aggregate_notional"], 100_000.0)

    def test_register_false_leaves_book_untouched(self):
        engine = RiskEngine(max_notional_per_trade=100_000.0, max_aggregate_notional=150_000.0)
        manager = BinaryOptionsManager(ComplianceEngine(), engine)
        manager.process_order(ctx(asset_id="A", notional=100_000.0), now=NOW, register=False)
        self.assertEqual(engine.current_exposure(NOW)["aggregate_notional"], 0.0)

    def test_rejected_trades_are_not_booked(self):
        engine = RiskEngine(max_notional_per_trade=100_000.0, max_aggregate_notional=150_000.0)
        manager = BinaryOptionsManager(ComplianceEngine(), engine)
        manager.process_order(
            ctx(asset_id="A", venue_status=VenueStatus.UNREGISTERED), now=NOW
        )
        self.assertEqual(engine.current_exposure(NOW)["aggregate_notional"], 0.0)

    def test_pin_risk_exposure_limit_applies_only_near_expiry(self):
        """Regression: max_pin_risk_exposure was accepted by __init__ and never read,
        so this limit could not be breached at all."""
        engine = RiskEngine(
            max_notional_per_trade=100_000.0,
            max_pin_risk_exposure=150_000.0,
            pin_window=timedelta(hours=24),
        )
        manager = BinaryOptionsManager(ComplianceEngine(), engine)
        near = dict(expiry=NOW + timedelta(hours=12), notional=100_000.0)

        self.assertEqual(
            manager.process_order(ctx(asset_id="A", **near), now=NOW)["status"], "APPROVED"
        )
        breach = manager.process_order(ctx(asset_id="B", **near), now=NOW)
        self.assertEqual(breach["reason_code"], "RISK_PIN_EXPOSURE")

        # Same aggregate notional, but outside the pin window: permitted.
        far = manager.process_order(
            ctx(asset_id="C", expiry=NOW + timedelta(days=10), notional=100_000.0), now=NOW
        )
        self.assertEqual(far["status"], "APPROVED")

    def test_pin_window_boundary_is_inclusive(self):
        engine = RiskEngine(max_pin_risk_exposure=1.0, pin_window=timedelta(hours=24))
        with self.assertRaises(RiskException):
            engine.check_limits(ctx(notional=2.0, expiry=NOW + timedelta(hours=24)), now=NOW)
        self.assertTrue(
            engine.check_limits(
                ctx(notional=2.0, expiry=NOW + timedelta(hours=24, seconds=1)), now=NOW
            )
        )

    def test_expired_positions_drop_out_of_exposure(self):
        engine = RiskEngine(max_notional_per_trade=100_000.0)
        engine.register_trade(ctx(asset_id="A", expiry=NOW + timedelta(days=1)))
        self.assertEqual(engine.current_exposure(NOW)["aggregate_notional"], 50_000.0)
        later = NOW + timedelta(days=2)
        self.assertEqual(engine.current_exposure(later)["aggregate_notional"], 0.0)

    def test_release_trade(self):
        engine = RiskEngine()
        engine.register_trade(ctx(asset_id="A"))
        self.assertTrue(engine.release_trade("A"))
        self.assertFalse(engine.release_trade("A"))
        self.assertEqual(engine.current_exposure(NOW)["aggregate_notional"], 0.0)

    def test_invalid_risk_limits_rejected(self):
        for kwargs in (
            {"max_notional_per_trade": 0},
            {"max_notional_per_trade": float("nan")},
            {"max_pin_risk_exposure": -1.0},
            {"max_aggregate_notional": 0.0},
            {"pin_window": timedelta(0)},
            {"pin_window": 24},
        ):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                with self.assertRaises(ValueError):
                    RiskEngine(**kwargs)


class TestDecisionRecordAndAudit(BaseCase):
    def test_compliance_precedes_risk(self):
        """A trade that breaches both must report the regulatory rejection: the two
        have different escalation paths."""
        result = self.manager.process_order(
            ctx(venue_status=VenueStatus.UNREGISTERED, notional=999_999.0), now=NOW
        )
        self.assertEqual(result["status"], "REJECTED_REGULATORY")

    def test_decision_record_shape(self):
        for context in (ctx(), ctx(venue_status=VenueStatus.UNREGISTERED), ctx(notional=1e9)):
            with self.subTest(context=context.asset_id):
                result = self.manager.process_order(context, now=NOW)
                for key in ("status", "message", "reason_code", "citation", "asset_id"):
                    self.assertIn(key, result)

    def test_rejections_are_logged_for_audit(self):
        with self.assertLogs("binary_options", level="WARNING") as logs:
            self.manager.process_order(
                ctx(venue_status=VenueStatus.UNREGISTERED), now=NOW
            )
        self.assertTrue(any("REG_VENUE_NOT_REGISTERED" in line for line in logs.output))

    def test_approvals_are_logged_for_audit(self):
        with self.assertLogs("binary_options", level="INFO") as logs:
            self.manager.process_order(ctx(), now=NOW)
        self.assertTrue(any("approved" in line for line in logs.output))

    def test_denials_carry_a_citation(self):
        for context in (
            ctx(jurisdiction=Jurisdiction.UK_FCA, client_type="RETAIL"),
            ctx(jurisdiction=Jurisdiction.AU_ASIC, client_type="RETAIL"),
            ctx(
                jurisdiction=Jurisdiction.CA_CSA,
                is_natural_person=True,
                expiry=NOW + timedelta(days=5),
            ),
            ctx(jurisdiction=Jurisdiction.UNREGULATED),
        ):
            with self.subTest(jurisdiction=context.jurisdiction):
                result = self.manager.process_order(context, now=NOW)
                self.assertEqual(result["status"], "REJECTED_REGULATORY")
                self.assertTrue(result["citation"], "denial must cite its source")


if __name__ == "__main__":
    unittest.main()
