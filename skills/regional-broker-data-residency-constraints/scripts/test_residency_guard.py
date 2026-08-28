"""
Unit tests for regional-broker-data-residency-constraints skill.
"""
import os
import unittest
from unittest import mock

from residency_guard import (
    BrokerDeploymentConstraintEngine,
    BrokerDeploymentConstraintError,
    DeploymentProfile,
    EGRESS_DYNAMIC,
    EGRESS_STATIC_DEDICATED,
    EGRESS_STATIC_SHARED,
    EGRESS_UNKNOWN,
    ROLE_CLIENT,
    ROLE_REGULATED_ENTITY,
    SEVERITY_ADVISORY,
    SEVERITY_BLOCKING,
    STATUS_BLOCKED,
    STATUS_COMPLIANT,
    STATUS_REVIEW_REQUIRED,
)


class TestOrderAccessConstraints(unittest.TestCase):

    def setUp(self):
        self.engine = BrokerDeploymentConstraintEngine()

    def test_indian_broker_with_dedicated_static_ip_in_mumbai_is_compliant(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)
        self.assertTrue(decision.is_deployable)
        self.assertEqual(decision.region_jurisdiction, "IN")

    def test_dynamic_egress_ip_blocks_order_deployment(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_DYNAMIC,
        ))
        self.assertEqual(decision.status, STATUS_BLOCKED)
        self.assertFalse(decision.is_deployable)
        codes = {f.code for f in decision.findings_by_severity(SEVERITY_BLOCKING)}
        self.assertIn("DYNAMIC_EGRESS_IP", codes)

    def test_shared_static_ip_escalates_rather_than_approves(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="upstox",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_STATIC_SHARED,
        ))
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)
        self.assertIn("SHARED_STATIC_EGRESS_IP", {f.code for f in decision.findings})

    def test_unknown_egress_posture_is_review_not_approval(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_UNKNOWN,
        ))
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)
        self.assertIn("EGRESS_IP_UNKNOWN", {f.code for f in decision.findings})

    def test_read_only_deployment_is_exempt_from_static_ip_requirement(self):
        # The SEBI static-IP requirement attaches to order requests only; market
        # data and portfolio endpoints stay reachable from any address.
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_DYNAMIC,
            places_orders=False,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)
        self.assertIn(
            "STATIC_IP_NOT_REQUIRED_FOR_DATA",
            {f.code for f in decision.findings_by_severity(SEVERITY_ADVISORY)},
        )

    def test_us_broker_has_no_static_ip_constraint(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="alpaca",
            cloud_region="us-east-1",
            egress_ip_type=EGRESS_DYNAMIC,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)


class TestResidencyPosture(unittest.TestCase):
    """
    Regression tests for the pre-2.0 behaviour, which asserted a SEBI/GDPR/SEC
    hosting-region mandate that none of those regimes contains. Hosting a
    client's own algo outside the broker's jurisdiction is not a violation.
    """

    def setUp(self):
        self.engine = BrokerDeploymentConstraintEngine()

    def test_indian_broker_hosted_in_us_region_is_not_a_violation(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="us-east-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)
        self.assertEqual(decision.region_jurisdiction, "US")
        severities = {f.severity for f in decision.findings}
        self.assertNotIn(SEVERITY_BLOCKING, severities)
        # ... but it is flagged as outside the latency-preferred set.
        self.assertIn(
            "REGION_NOT_LATENCY_PREFERRED",
            {f.code for f in decision.findings_by_severity(SEVERITY_ADVISORY)},
        )

    def test_eu_broker_hosted_in_us_region_is_not_a_gdpr_violation(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="degiro",
            cloud_region="us-east-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)

    def test_client_role_records_the_no_mandate_position_as_advisory(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
            deployer_role=ROLE_CLIENT,
        ))
        note = next(f for f in decision.findings if f.code == "NO_CLIENT_RESIDENCY_MANDATE")
        self.assertEqual(note.severity, SEVERITY_ADVISORY)
        self.assertIn("abeyance", note.message)

    def test_regulated_deployer_escalates_to_review(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
            deployer_role=ROLE_REGULATED_ENTITY,
        ))
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)
        self.assertIn("REGULATED_DEPLOYER_REVIEW", {f.code for f in decision.findings})


class TestFailClosedBehaviour(unittest.TestCase):

    def setUp(self):
        self.engine = BrokerDeploymentConstraintEngine()

    def test_unregistered_broker_is_review_not_silent_approval(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="mock_broker_xyz",
            cloud_region="us-east-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)
        self.assertFalse(decision.is_deployable)
        self.assertIn("BROKER_NOT_REGISTERED", {f.code for f in decision.findings})

    def test_missing_region_is_review_not_approval(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region=None,
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)
        self.assertIn("REGION_UNRESOLVED", {f.code for f in decision.findings})

    def test_blank_region_string_is_treated_as_unresolved(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="   ",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertIsNone(decision.cloud_region)
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)

    def test_unmapped_region_is_review_not_approval(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="zerodha",
            cloud_region="xx-nowhere-9",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_REVIEW_REQUIRED)
        self.assertIn("REGION_UNMAPPED", {f.code for f in decision.findings})

    def test_london_region_is_uk_not_eu(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="degiro",
            cloud_region="eu-west-2",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.region_jurisdiction, "UK")

    def test_assert_deployable_raises_on_blocked_deployment(self):
        with self.assertRaises(BrokerDeploymentConstraintError) as ctx:
            self.engine.assert_deployable(DeploymentProfile(
                broker="zerodha",
                cloud_region="ap-south-1",
                egress_ip_type=EGRESS_DYNAMIC,
            ))
        self.assertIn("DYNAMIC_EGRESS_IP", str(ctx.exception))

    def test_assert_deployable_returns_decision_when_compliant(self):
        decision = self.engine.assert_deployable(DeploymentProfile(
            broker="zerodha",
            cloud_region="ap-south-1",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)


class TestInputHandling(unittest.TestCase):

    def setUp(self):
        self.engine = BrokerDeploymentConstraintEngine()

    def test_broker_and_region_are_case_and_whitespace_insensitive(self):
        decision = self.engine.evaluate(DeploymentProfile(
            broker="  ZeRoDhA ",
            cloud_region=" AP-SOUTH-1 ",
            egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.assertEqual(decision.status, STATUS_COMPLIANT)
        self.assertEqual(decision.cloud_region, "ap-south-1")

    def test_empty_broker_name_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate(DeploymentProfile(broker="   "))

    def test_invalid_egress_type_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate(DeploymentProfile(
                broker="zerodha", cloud_region="ap-south-1", egress_ip_type="whatever",
            ))

    def test_invalid_role_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate(DeploymentProfile(
                broker="zerodha",
                cloud_region="ap-south-1",
                egress_ip_type=EGRESS_STATIC_DEDICATED,
                deployer_role="ADMIN",
            ))


class TestRegionProbeAndAudit(unittest.TestCase):

    def setUp(self):
        self.engine = BrokerDeploymentConstraintEngine()

    def test_probe_returns_none_when_no_region_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(BrokerDeploymentConstraintEngine.probe_current_region())

    def test_probe_prefers_aws_and_normalizes_case(self):
        with mock.patch.dict(os.environ, {"AWS_REGION": " AP-SOUTH-1 "}, clear=True):
            self.assertEqual(
                BrokerDeploymentConstraintEngine.probe_current_region(), ("AWS", "ap-south-1")
            )

    def test_probe_falls_back_to_gcp_then_custom(self):
        with mock.patch.dict(os.environ, {"GCP_REGION": "asia-south1"}, clear=True):
            self.assertEqual(
                BrokerDeploymentConstraintEngine.probe_current_region(), ("GCP", "asia-south1")
            )
        with mock.patch.dict(os.environ, {"TRADING_HOST_REGION": "us-west-2"}, clear=True):
            self.assertEqual(
                BrokerDeploymentConstraintEngine.probe_current_region(), ("CUSTOM", "us-west-2")
            )

    def test_audit_trail_records_each_decision_and_returns_a_copy(self):
        self.engine.evaluate(DeploymentProfile(
            broker="zerodha", cloud_region="ap-south-1", egress_ip_type=EGRESS_STATIC_DEDICATED,
        ))
        self.engine.evaluate(DeploymentProfile(
            broker="zerodha", cloud_region="ap-south-1", egress_ip_type=EGRESS_DYNAMIC,
        ))
        trail = self.engine.audit_trail
        self.assertEqual([d.status for d in trail], [STATUS_COMPLIANT, STATUS_BLOCKED])
        trail.clear()
        self.assertEqual(len(self.engine.audit_trail), 2)


if __name__ == "__main__":
    unittest.main()
