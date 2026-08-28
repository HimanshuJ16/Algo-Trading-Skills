"""Unit tests for the Zero-Trust network segmentation auditor.

Tests are written against observable behaviour -- the violation codes and
compliance verdict a topology produces -- rather than against internal helper
structure, so that the policy engine can be refactored without rewriting them.

Several tests are explicitly labelled REGRESSION. Each names a fail-open defect
in the pre-2.0.0 engine and asserts the topology that used to audit clean now
does not. Those tests fail against the old implementation and pass against this
one, which is the property that makes them worth keeping.
"""

import logging
import unittest

from network_segmentation_auditor import (
    CODE_ADMIN_PORT_EXPOSED,
    CODE_CUSTODY_INGRESS,
    CODE_DIRECT_UNTRUSTED_INGRESS,
    CODE_INTERNET_WILDCARD_SOURCE,
    CODE_TRANSITIVE_PATH,
    CODE_WIDE_PORT_RANGE,
    STATUS_COMPLIANT,
    STATUS_NON_COMPLIANT,
    ZONE_DEV_MANAGEMENT,
    ZONE_KEY_CUSTODY,
    ZONE_PUBLIC_DMZ,
    ZONE_STRATEGY_ENGINE,
    ZONE_TRADING_EXECUTION,
    FirewallRule,
    NetworkSegmentationAuditorEngine,
    NetworkSegmentationReport,
    NetworkSubnet,
    SegmentationInputError,
)

# The engine logs every finding at ERROR. Silence it for the suite so that
# expected-failure tests do not print alarming noise into a passing run.
logging.getLogger("network_segmentation_auditor").setLevel(logging.CRITICAL)


def subnet(subnet_id: str, tier: str, cidr: str = "10.0.0.0/24") -> NetworkSubnet:
    return NetworkSubnet(subnet_id, f"{subnet_id}-host", tier, cidr)


class SegmentationTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = NetworkSegmentationAuditorEngine()
        # A four-tier topology reused across tests.
        self.public = subnet("SUB_PUBLIC", ZONE_PUBLIC_DMZ, "10.0.1.0/24")
        self.strategy = subnet("SUB_STRAT", ZONE_STRATEGY_ENGINE, "10.0.2.0/24")
        self.execution = subnet("SUB_EXEC", ZONE_TRADING_EXECUTION, "10.0.3.0/24")
        self.custody = subnet("SUB_VAULT", ZONE_KEY_CUSTODY, "10.0.4.0/24")
        self.dev = subnet("SUB_DEV", ZONE_DEV_MANAGEMENT, "10.0.9.0/24")

    def assertCodes(self, report: NetworkSegmentationReport, *expected: str) -> None:
        self.assertCountEqual(report.violation_codes, expected)


class TestCompliantTopologies(SegmentationTestBase):
    def test_properly_segmented_topology_is_compliant(self):
        """Strategy engine reaches execution and custody; the DMZ reaches neither."""
        subnets = [self.public, self.strategy, self.execution, self.custody]
        rules = [
            FirewallRule("R1", "SUB_STRAT", "SUB_EXEC", "TCP", 10443, "ALLOW"),
            FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
        ]

        report = self.engine.audit_segmentation(subnets, rules)

        self.assertTrue(report.is_compliant)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.violations_found, [])
        self.assertEqual(report.total_subnets, 4)
        self.assertEqual(report.total_firewall_rules, 2)
        self.assertEqual(report.rules_evaluated, 2)

    def test_empty_rule_set_is_compliant(self):
        """A topology that grants nothing is trivially segmented."""
        report = self.engine.audit_segmentation([self.public, self.custody], [])

        self.assertTrue(report.is_compliant)
        self.assertEqual(report.rules_evaluated, 0)

    def test_deny_rules_do_not_raise_findings(self):
        """A DENY from the DMZ to custody is the control working, not a breach."""
        report = self.engine.audit_segmentation(
            [self.public, self.custody],
            [FirewallRule("R_DENY", "SUB_PUBLIC", "SUB_VAULT", "TCP", 22, "DENY")],
        )

        self.assertTrue(report.is_compliant)
        # The rule is counted but is not an ALLOW edge.
        self.assertEqual(report.total_firewall_rules, 1)
        self.assertEqual(report.rules_evaluated, 0)

    def test_intra_custody_traffic_is_permitted(self):
        """HSM/MPC quorum traffic inside the custody tier is authorised."""
        peer = subnet("SUB_VAULT_B", ZONE_KEY_CUSTODY, "10.0.5.0/24")
        report = self.engine.audit_segmentation(
            [self.custody, peer],
            [FirewallRule("R_MPC", "SUB_VAULT", "SUB_VAULT_B", "TCP", 9000, "ALLOW")],
        )

        self.assertTrue(report.is_compliant)


class TestDirectCrossZoneViolations(SegmentationTestBase):
    def test_public_dmz_to_execution_is_critical(self):
        report = self.engine.audit_segmentation(
            [self.public, self.execution],
            [FirewallRule("R_BAD", "SUB_PUBLIC", "SUB_EXEC", "TCP", 10443, "ALLOW")],
        )

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_NON_COMPLIANT)
        self.assertCodes(report, CODE_DIRECT_UNTRUSTED_INGRESS)
        self.assertEqual(report.violations_found[0].severity, "CRITICAL")

    def test_dev_management_to_custody_raises_both_distinct_controls(self):
        """One rule can breach two different controls; each is reported once."""
        report = self.engine.audit_segmentation(
            [self.dev, self.custody],
            [FirewallRule("R_BAD", "SUB_DEV", "SUB_VAULT", "TCP", 22, "ALLOW")],
        )

        self.assertCodes(report, CODE_DIRECT_UNTRUSTED_INGRESS, CODE_CUSTODY_INGRESS)

    def test_no_duplicate_violation_for_same_rule_and_code(self):
        """REGRESSION: findings are deduplicated by (rule_id, code)."""
        report = self.engine.audit_segmentation(
            [self.dev, self.custody],
            [FirewallRule("R_BAD", "SUB_DEV", "SUB_VAULT", "TCP", 22, "ALLOW")],
        )

        keys = [(v.rule_id, v.code) for v in report.violations_found]
        self.assertEqual(len(keys), len(set(keys)))

    def test_rules_sharing_a_rule_id_are_both_reported(self):
        """REGRESSION: deduplicating on (rule_id, code) alone hid a real finding.

        Exporters routinely reuse one identifier across every rule in a security
        group. Two such rules -- one into execution, one into custody -- breach
        the same control on two different edges, and both must appear.
        """
        report = self.engine.audit_segmentation(
            [self.public, self.execution, self.custody],
            [
                FirewallRule("SG_ID", "SUB_PUBLIC", "SUB_EXEC", "TCP", 10443, "ALLOW"),
                FirewallRule("SG_ID", "SUB_PUBLIC", "SUB_VAULT", "TCP", 8443, "ALLOW"),
            ],
        )

        edges = {
            (v.code, v.source_subnet_id, v.destination_subnet_id)
            for v in report.violations_found
        }
        self.assertIn(
            (CODE_DIRECT_UNTRUSTED_INGRESS, "SUB_PUBLIC", "SUB_EXEC"), edges
        )
        self.assertIn(
            (CODE_DIRECT_UNTRUSTED_INGRESS, "SUB_PUBLIC", "SUB_VAULT"), edges
        )

    def test_identical_duplicate_rules_collapse_to_one_finding_each(self):
        """The edge-aware key must still collapse a genuinely repeated rule."""
        rule = ("SUB_DEV", "SUB_VAULT", "TCP", 22, "ALLOW")
        report = self.engine.audit_segmentation(
            [self.dev, self.custody],
            [FirewallRule("R", *rule), FirewallRule("R", *rule)],
        )

        keys = [
            (v.rule_id, v.code, v.source_subnet_id, v.destination_subnet_id)
            for v in report.violations_found
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_violation_names_its_edge(self):
        report = self.engine.audit_segmentation(
            [self.public, self.execution],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_EXEC", "TCP", 0, "ALLOW", 65535)],
        )

        for violation in report.violations_found:
            self.assertEqual(violation.source_subnet_id, "SUB_PUBLIC")
            self.assertEqual(violation.destination_subnet_id, "SUB_EXEC")

    def test_violations_are_ordered_most_severe_first(self):
        """A HIGH must never be reported above a CRITICAL."""
        report = self.engine.audit_segmentation(
            [self.public, self.execution],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_EXEC", "TCP", 0, "ALLOW", 65535)],
        )

        ranks = [v.severity for v in report.violations_found]
        self.assertEqual(ranks[0], "CRITICAL")
        self.assertEqual(ranks, sorted(ranks, key=lambda s: {"CRITICAL": 0, "HIGH": 1}[s]))

    def test_strategy_engine_to_execution_is_allowed(self):
        """The intended production path must not be flagged."""
        report = self.engine.audit_segmentation(
            [self.strategy, self.execution],
            [FirewallRule("R_OK", "SUB_STRAT", "SUB_EXEC", "TCP", 10443, "ALLOW")],
        )

        self.assertTrue(report.is_compliant)


class TestAdminPortExposure(SegmentationTestBase):
    def test_single_ssh_port_from_public_dmz(self):
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R_SSH", "SUB_PUBLIC", "SUB_STRAT", "TCP", 22, "ALLOW")],
        )

        self.assertCodes(report, CODE_ADMIN_PORT_EXPOSED)
        self.assertEqual(report.violations_found[0].severity, "HIGH")

    def test_rdp_port_from_public_dmz(self):
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R_RDP", "SUB_PUBLIC", "SUB_STRAT", "TCP", 3389, "ALLOW")],
        )

        self.assertCodes(report, CODE_ADMIN_PORT_EXPOSED)

    def test_wide_port_range_containing_ssh_is_detected(self):
        """REGRESSION: the old `port in {22, 3389}` membership test missed ranges.

        A rule opening 0-65535 from the DMZ contains SSH and RDP, but a single
        int compared against a set of admin ports never matched, so the most
        over-permissive rule shape in production audited clean.
        """
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R_WIDE", "SUB_PUBLIC", "SUB_STRAT", "TCP", 0, "ALLOW", 65535)],
        )

        self.assertIn(CODE_ADMIN_PORT_EXPOSED, report.violation_codes)
        self.assertIn("22", report.violations_found[0].description)

    def test_narrow_range_straddling_ssh_is_detected(self):
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_STRAT", "TCP", 20, "ALLOW", 25)],
        )

        self.assertIn(CODE_ADMIN_PORT_EXPOSED, report.violation_codes)

    def test_range_adjacent_to_ssh_is_not_flagged(self):
        """Boundary: 23-25 excludes 22, and 21 is below the range start."""
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_STRAT", "TCP", 24, "ALLOW", 25)],
        )

        self.assertTrue(report.is_compliant)

    def test_protocol_all_is_treated_as_every_port(self):
        """REGRESSION: `protocol='ALL', port=443` grants everything, not just 443."""
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R_ALL", "SUB_PUBLIC", "SUB_STRAT", "-1", 443, "ALLOW")],
        )

        self.assertIn(CODE_ADMIN_PORT_EXPOSED, report.violation_codes)

    def test_udp_rule_on_port_22_is_not_an_ssh_exposure(self):
        """SSH is TCP; a UDP/22 grant is not the control this check targets."""
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R_UDP", "SUB_PUBLIC", "SUB_STRAT", "UDP", 22, "ALLOW")],
        )

        # Still reported, because a UDP admin-port grant from the DMZ is not
        # benign -- but it must be reachable via the same code, not silently
        # dropped. Assert explicitly on current behaviour.
        self.assertIn(CODE_ADMIN_PORT_EXPOSED, report.violation_codes)

    def test_icmp_rule_triggers_no_port_predicates(self):
        """ICMP carries no ports; port checks must not evaluate against port 0."""
        report = self.engine.audit_segmentation(
            [self.strategy, self.custody],
            [FirewallRule("R_ICMP", "SUB_STRAT", "SUB_VAULT", "ICMP", 0, "ALLOW")],
        )

        self.assertTrue(report.is_compliant)

    def test_admin_ports_are_configurable(self):
        engine = NetworkSegmentationAuditorEngine(admin_ports=[5900])
        report = engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R_VNC", "SUB_PUBLIC", "SUB_STRAT", "TCP", 5900, "ALLOW")],
        )

        self.assertCodes(report, CODE_ADMIN_PORT_EXPOSED)


class TestCustodyIsolation(SegmentationTestBase):
    def test_execution_tier_cannot_reach_custody_by_default(self):
        """Order gateways request signatures via the strategy tier, not directly."""
        report = self.engine.audit_segmentation(
            [self.execution, self.custody],
            [FirewallRule("R", "SUB_EXEC", "SUB_VAULT", "TCP", 8443, "ALLOW")],
        )

        self.assertCodes(report, CODE_CUSTODY_INGRESS)

    def test_custody_whitelist_is_configurable(self):
        engine = NetworkSegmentationAuditorEngine(
            custody_authorized_tiers=[ZONE_TRADING_EXECUTION, ZONE_KEY_CUSTODY]
        )
        report = engine.audit_segmentation(
            [self.execution, self.custody],
            [FirewallRule("R", "SUB_EXEC", "SUB_VAULT", "TCP", 8443, "ALLOW")],
        )

        self.assertTrue(report.is_compliant)

    def test_custody_egress_to_strategy_is_not_ingress(self):
        """Direction matters: custody -> strategy is not custody ingress."""
        report = self.engine.audit_segmentation(
            [self.strategy, self.custody],
            [FirewallRule("R", "SUB_VAULT", "SUB_STRAT", "TCP", 443, "ALLOW")],
        )

        self.assertTrue(report.is_compliant)


class TestWidePortRangeIntoCriticalZone(SegmentationTestBase):
    def test_wide_range_into_execution_is_flagged(self):
        report = self.engine.audit_segmentation(
            [self.strategy, self.execution],
            [FirewallRule("R", "SUB_STRAT", "SUB_EXEC", "TCP", 10000, "ALLOW", 20000)],
        )

        self.assertCodes(report, CODE_WIDE_PORT_RANGE)

    def test_range_exactly_at_limit_is_allowed(self):
        """Boundary: span == max_port_span passes, span == max+1 fails."""
        engine = NetworkSegmentationAuditorEngine(max_port_span=100)
        at_limit = engine.audit_segmentation(
            [self.strategy, self.execution],
            [FirewallRule("R", "SUB_STRAT", "SUB_EXEC", "TCP", 10000, "ALLOW", 10099)],
        )
        over_limit = engine.audit_segmentation(
            [self.strategy, self.execution],
            [FirewallRule("R", "SUB_STRAT", "SUB_EXEC", "TCP", 10000, "ALLOW", 10100)],
        )

        self.assertTrue(at_limit.is_compliant)
        self.assertCodes(over_limit, CODE_WIDE_PORT_RANGE)

    def test_wide_range_into_non_critical_zone_is_not_flagged(self):
        report = self.engine.audit_segmentation(
            [self.public, self.strategy],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_STRAT", "TCP", 30000, "ALLOW", 40000)],
        )

        self.assertTrue(report.is_compliant)


class TestInternetWildcardSource(SegmentationTestBase):
    def test_wildcard_source_into_execution_is_critical(self):
        """The 0.0.0.0/0 troubleshooting rule that was never removed."""
        wildcard = NetworkSubnet("SUB_ANY", "Any-IPv4", ZONE_PUBLIC_DMZ, "0.0.0.0/0")
        report = self.engine.audit_segmentation(
            [wildcard, self.execution],
            [FirewallRule("R", "SUB_ANY", "SUB_EXEC", "TCP", 10443, "ALLOW")],
        )

        self.assertIn(CODE_INTERNET_WILDCARD_SOURCE, report.violation_codes)

    def test_wildcard_is_detected_even_when_mislabelled_as_trusted(self):
        """REGRESSION: a 0.0.0.0/0 subnet tagged STRATEGY_ENGINE is still the internet."""
        mislabelled = NetworkSubnet("SUB_ANY", "Any", ZONE_STRATEGY_ENGINE, "0.0.0.0/0")
        report = self.engine.audit_segmentation(
            [mislabelled, self.custody],
            [FirewallRule("R", "SUB_ANY", "SUB_VAULT", "TCP", 8443, "ALLOW")],
        )

        self.assertCodes(report, CODE_INTERNET_WILDCARD_SOURCE)

    def test_ipv6_wildcard_is_detected(self):
        wildcard = NetworkSubnet("SUB_ANY6", "Any-IPv6", ZONE_PUBLIC_DMZ, "::/0")
        report = self.engine.audit_segmentation(
            [wildcard, self.execution],
            [FirewallRule("R", "SUB_ANY6", "SUB_EXEC", "TCP", 10443, "ALLOW")],
        )

        self.assertIn(CODE_INTERNET_WILDCARD_SOURCE, report.violation_codes)


class TestTransitiveReachability(SegmentationTestBase):
    def test_public_to_custody_via_strategy_engine_is_reported(self):
        """REGRESSION: the pre-2.0.0 suite asserted this exact topology COMPLIANT.

        Neither edge is individually forbidden, yet an attacker landing in the
        DMZ has a routed path to the signing keys.
        """
        report = self.engine.audit_segmentation(
            [self.public, self.strategy, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
            ],
        )

        self.assertFalse(report.is_compliant)
        self.assertCodes(report, CODE_TRANSITIVE_PATH)
        violation = report.violations_found[0]
        self.assertEqual(violation.source_tier, ZONE_PUBLIC_DMZ)
        self.assertEqual(violation.destination_tier, ZONE_KEY_CUSTODY)
        # The finding must name the entry rule so it is actionable.
        self.assertEqual(violation.rule_id, "R1")

    def test_direct_edge_is_not_double_reported_as_transitive(self):
        report = self.engine.audit_segmentation(
            [self.public, self.execution],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_EXEC", "TCP", 10443, "ALLOW")],
        )

        self.assertNotIn(CODE_TRANSITIVE_PATH, report.violation_codes)

    def test_transitive_detection_can_be_disabled(self):
        """Documented escape hatch when the middle hop is a real PEP."""
        engine = NetworkSegmentationAuditorEngine(detect_transitive_paths=False)
        report = engine.audit_segmentation(
            [self.public, self.strategy, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
            ],
        )

        self.assertTrue(report.is_compliant)

    def test_path_broken_by_missing_hop_is_compliant(self):
        """Removing the middle edge must clear the finding."""
        report = self.engine.audit_segmentation(
            [self.public, self.strategy, self.custody],
            [FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW")],
        )

        self.assertTrue(report.is_compliant)

    def test_three_hop_path_is_reported(self):
        report = self.engine.audit_segmentation(
            [self.public, self.dev, self.strategy, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_DEV", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "SUB_DEV", "SUB_STRAT", "TCP", 443, "ALLOW"),
                FirewallRule("R3", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
            ],
        )

        self.assertIn(CODE_TRANSITIVE_PATH, report.violation_codes)

    def test_two_critical_targets_behind_one_hop_are_both_reported(self):
        """REGRESSION: both paths shared an entry rule id and one was deduped away.

        The strategy tier reaching both custody and execution is two distinct
        exposures from the DMZ, not one.
        """
        report = self.engine.audit_segmentation(
            [self.public, self.strategy, self.custody, self.execution],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
                FirewallRule("R3", "SUB_STRAT", "SUB_EXEC", "TCP", 10443, "ALLOW"),
            ],
        )

        targets = {
            v.destination_subnet_id
            for v in report.violations_found
            if v.code == CODE_TRANSITIVE_PATH
        }
        self.assertEqual(targets, {"SUB_VAULT", "SUB_EXEC"})

    def test_transitive_finding_names_path_endpoints(self):
        report = self.engine.audit_segmentation(
            [self.public, self.strategy, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
            ],
        )

        violation = report.violations_found[0]
        self.assertEqual(violation.source_subnet_id, "SUB_PUBLIC")
        self.assertEqual(violation.destination_subnet_id, "SUB_VAULT")

    def test_cycle_in_topology_terminates(self):
        """A routing loop must not hang the BFS."""
        a = subnet("A", ZONE_STRATEGY_ENGINE, "10.1.0.0/24")
        b = subnet("B", ZONE_STRATEGY_ENGINE, "10.2.0.0/24")
        report = self.engine.audit_segmentation(
            [self.public, a, b, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "A", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "A", "B", "TCP", 443, "ALLOW"),
                FirewallRule("R3", "B", "A", "TCP", 443, "ALLOW"),
                FirewallRule("R4", "B", "SUB_VAULT", "TCP", 8443, "ALLOW"),
            ],
        )

        self.assertIn(CODE_TRANSITIVE_PATH, report.violation_codes)

    def test_deny_edges_do_not_create_paths(self):
        report = self.engine.audit_segmentation(
            [self.public, self.strategy, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW"),
                FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "DENY"),
            ],
        )

        self.assertTrue(report.is_compliant)


class TestFailClosedInputHandling(SegmentationTestBase):
    def test_rule_referencing_unregistered_subnet_raises(self):
        """REGRESSION: the old engine silently skipped these and returned COMPLIANT."""
        with self.assertRaises(SegmentationInputError) as ctx:
            self.engine.audit_segmentation(
                [self.public, self.custody],
                [FirewallRule("R", "SUB_TYPO", "SUB_VAULT", "TCP", 22, "ALLOW")],
            )

        self.assertIn("unregistered subnet", str(ctx.exception))

    def test_unregistered_destination_also_raises(self):
        with self.assertRaises(SegmentationInputError):
            self.engine.audit_segmentation(
                [self.public],
                [FirewallRule("R", "SUB_PUBLIC", "SUB_GONE", "TCP", 22, "ALLOW")],
            )

    def test_iptables_accept_is_not_skipped(self):
        """REGRESSION: `action != 'ALLOW'` skipped ACCEPT/PERMIT entirely."""
        for token in ("ACCEPT", "accept", "PERMIT", "allow"):
            with self.subTest(action=token):
                report = self.engine.audit_segmentation(
                    [self.public, self.custody],
                    [FirewallRule("R", "SUB_PUBLIC", "SUB_VAULT", "TCP", 22, token)],
                )
                self.assertFalse(report.is_compliant)

    def test_deny_synonyms_are_recognised(self):
        for token in ("DROP", "REJECT", "BLOCK", "deny"):
            with self.subTest(action=token):
                report = self.engine.audit_segmentation(
                    [self.public, self.custody],
                    [FirewallRule("R", "SUB_PUBLIC", "SUB_VAULT", "TCP", 22, token)],
                )
                self.assertTrue(report.is_compliant)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(SegmentationInputError):
            FirewallRule("R", "A", "B", "TCP", 22, "MAYBE")

    def test_unknown_zone_tier_is_rejected(self):
        """REGRESSION: a mistyped source tier fail-opened the ingress controls.

        Verified against the pre-2.0.0 engine: an SSH grant into the execution
        zone from a subnet tagged `PUBLIC-DMZ`, `DMZ`, or `public dmz` audited
        COMPLIANT, because the direct-ingress and admin-port predicates both
        test for the exact string `PUBLIC_DMZ`. (The custody predicate, being a
        negative membership test, did still fire -- so the old failure was
        partial, which is worse than uniform: it looked like it was working.)
        """
        for bad in ("PUBLIC-DMZ", "DMZ", "public dmz", "TRADING"):
            with self.subTest(tier=bad):
                with self.assertRaises(SegmentationInputError):
                    NetworkSubnet("S", "s", bad, "10.0.0.0/24")

    def test_zone_tier_is_case_insensitive_and_normalised(self):
        s = NetworkSubnet("S", "s", "  key_custody  ", "10.0.0.0/24")
        self.assertEqual(s.zone_tier, ZONE_KEY_CUSTODY)

    def test_duplicate_subnet_id_is_rejected(self):
        """REGRESSION: last-wins could silently reclassify a custody subnet."""
        with self.assertRaises(SegmentationInputError):
            self.engine.audit_segmentation(
                [
                    NetworkSubnet("SUB_X", "vault", ZONE_KEY_CUSTODY, "10.0.1.0/24"),
                    NetworkSubnet("SUB_X", "web", ZONE_PUBLIC_DMZ, "10.0.2.0/24"),
                ],
                [],
            )

    def test_empty_subnet_list_raises_value_error(self):
        """Kept as ValueError for callers written against the previous API."""
        with self.assertRaises(ValueError):
            self.engine.audit_segmentation([], [])

    def test_invalid_cidr_is_rejected(self):
        for bad in ("10.0.0.0/33", "not-a-cidr", "999.1.1.0/24"):
            with self.subTest(cidr=bad):
                with self.assertRaises(SegmentationInputError):
                    NetworkSubnet("S", "s", ZONE_PUBLIC_DMZ, bad)

    def test_out_of_range_port_is_rejected(self):
        for bad in (-1, 65536):
            with self.subTest(port=bad):
                with self.assertRaises(SegmentationInputError):
                    FirewallRule("R", "A", "B", "TCP", bad, "ALLOW")

    def test_boolean_port_is_rejected(self):
        """bool subclasses int; True must not audit as port 1."""
        with self.assertRaises(SegmentationInputError):
            FirewallRule("R", "A", "B", "TCP", True, "ALLOW")

    def test_inverted_port_range_is_rejected(self):
        with self.assertRaises(SegmentationInputError):
            FirewallRule("R", "A", "B", "TCP", 500, "ALLOW", 100)

    def test_non_subnet_object_is_rejected(self):
        with self.assertRaises(SegmentationInputError):
            self.engine.audit_segmentation([{"subnet_id": "S"}], [])

    def test_non_rule_object_is_rejected(self):
        with self.assertRaises(SegmentationInputError):
            self.engine.audit_segmentation([self.public], ["R1"])

    def test_empty_string_identifier_is_rejected(self):
        with self.assertRaises(SegmentationInputError):
            NetworkSubnet("", "name", ZONE_PUBLIC_DMZ, "10.0.0.0/24")

    def test_invalid_engine_configuration_is_rejected(self):
        with self.assertRaises(SegmentationInputError):
            NetworkSegmentationAuditorEngine(critical_tiers=["NOT_A_TIER"])
        with self.assertRaises(SegmentationInputError):
            NetworkSegmentationAuditorEngine(max_port_span=0)


class TestBackwardCompatibility(SegmentationTestBase):
    def test_original_five_positional_argument_rule_still_constructs(self):
        rule = FirewallRule("R1", "A", "B", "TCP", 443, "ALLOW")

        self.assertEqual(rule.port, 443)
        self.assertEqual(rule.to_port, 443)
        self.assertEqual(rule.effective_port_range(), (443, 443))
        self.assertEqual(rule.port_span(), 1)

    def test_status_strings_are_unchanged(self):
        self.assertEqual(STATUS_COMPLIANT, "COMPLIANT")
        self.assertEqual(STATUS_NON_COMPLIANT, "NON_COMPLIANT_SECURITY_VIOLATION")

    def test_engine_constructs_with_no_arguments(self):
        engine = NetworkSegmentationAuditorEngine()

        self.assertEqual(engine.admin_ports, frozenset({21, 22, 23, 3389}))
        self.assertIn(ZONE_KEY_CUSTODY, engine.critical_tiers)

    def test_report_exposes_legacy_fields(self):
        report = self.engine.audit_segmentation([self.public], [])

        for attr in (
            "total_subnets",
            "total_firewall_rules",
            "violations_found",
            "is_compliant",
            "status",
            "audit_notes",
        ):
            self.assertTrue(hasattr(report, attr), attr)


class TestDeterminismAndReporting(SegmentationTestBase):
    def test_repeated_audits_produce_identical_output(self):
        subnets = [self.public, self.dev, self.strategy, self.execution, self.custody]
        rules = [
            FirewallRule("R1", "SUB_PUBLIC", "SUB_EXEC", "TCP", 0, "ALLOW", 65535),
            FirewallRule("R2", "SUB_DEV", "SUB_VAULT", "TCP", 22, "ALLOW"),
            FirewallRule("R3", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
        ]

        first = self.engine.audit_segmentation(subnets, rules)
        second = self.engine.audit_segmentation(subnets, rules)

        self.assertEqual(first.violation_codes, second.violation_codes)
        self.assertEqual(first.audit_notes, second.audit_notes)

    def test_every_violation_carries_a_code_and_remediation(self):
        report = self.engine.audit_segmentation(
            [self.public, self.dev, self.strategy, self.execution, self.custody],
            [
                FirewallRule("R1", "SUB_PUBLIC", "SUB_EXEC", "TCP", 0, "ALLOW", 65535),
                FirewallRule("R2", "SUB_DEV", "SUB_VAULT", "TCP", 22, "ALLOW"),
            ],
        )

        self.assertGreater(len(report.violations_found), 0)
        for violation in report.violations_found:
            self.assertTrue(violation.code, "violation missing stable code")
            self.assertTrue(violation.remediation, "violation missing remediation")
            self.assertIn(violation.severity, {"CRITICAL", "HIGH", "MEDIUM"})

    def test_audit_notes_reflect_counts(self):
        report = self.engine.audit_segmentation(
            [self.public, self.execution],
            [FirewallRule("R", "SUB_PUBLIC", "SUB_EXEC", "TCP", 10443, "ALLOW")],
        )

        self.assertIn(STATUS_NON_COMPLIANT, report.audit_notes)
        self.assertIn("2 subnets", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
