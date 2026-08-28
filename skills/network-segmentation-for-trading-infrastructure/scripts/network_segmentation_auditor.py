"""Zero-Trust segmentation audit for trading-infrastructure firewall policy.

This module audits a *declared* network topology -- subnets tagged with a
security zone tier, plus the firewall / security-group rules between them --
and reports whether that policy, as written, still permits an untrusted zone to
reach an order-sending or key-holding host.

Scope and honesty boundary
--------------------------
This engine performs **no** network, cloud-API, or packet-level operation. It
never calls ``DescribeSecurityGroups``, never probes a port, and never observes
real traffic. It reads the topology you hand it and reasons about that
description. Two consequences follow, and both are load-bearing:

1. **The audit is only as true as the inventory.** If a security group exists in
   the account but not in the list passed here, no finding can reference it.
   Export the rule set from the source of record (Terraform state, ``aws ec2
   describe-security-groups``, the firewall's own config dump) rather than
   hand-transcribing it. DORA's RTS on ICT risk management requires "the
   documentation of all of the financial entity's network connections and data
   flows" (Art. 13(1)(b)) precisely because an undocumented flow is unauditable.

2. **Rules are evaluated as an unordered set, not an ordered ACL.** That model
   is exactly right for AWS security groups, where "the rules from each security
   group are aggregated to form a single set of rules that are used to determine
   whether to allow access", and where "You can specify allow rules, but not deny
   rules." It is **wrong** for anything first-match-wins -- AWS network ACLs,
   iptables chains, Cisco ACLs -- where rules carry a number and "If the traffic
   matches a rule, the rule is applied and we do not evaluate any additional
   rules." Feeding an ordered ACL in here can raise a finding on an ALLOW that a
   lower-numbered DENY already shadows. See ``references/standards.md``.

What "Zero-Trust" does and does not mean here
---------------------------------------------
This engine implements the *micro-segmentation* deployment approach of NIST
SP 800-207 (§3.1.2): resources grouped onto network segments behind a gateway
acting as a policy enforcement point. That is one approach to a ZTA, not ZTA
itself. NIST is explicit that the approach "requires an identity governance
program (IGP) to fully function", and tenet 2 of the same document states that
"Network location alone does not imply trust." A topology that passes this audit
has a defensible segment layout; it has not thereby authenticated anything.
Passing here is necessary but not sufficient.

See ``references/standards.md`` for the source behind every control.
"""

import ipaddress
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. Every finding is always present on the returned report, so a caller
# with no handlers configured still gets the full result programmatically.
logger.addHandler(logging.NullHandler())

__all__ = [
    "SegmentationInputError",
    "NetworkSubnet",
    "FirewallRule",
    "SecurityViolation",
    "NetworkSegmentationReport",
    "NetworkSegmentationAuditorEngine",
    "ZONE_PUBLIC_DMZ",
    "ZONE_TRADING_EXECUTION",
    "ZONE_STRATEGY_ENGINE",
    "ZONE_KEY_CUSTODY",
    "ZONE_DEV_MANAGEMENT",
    "VALID_ZONE_TIERS",
    "STATUS_COMPLIANT",
    "STATUS_NON_COMPLIANT",
]

# --------------------------------------------------------------------------
# Zone tiers
# --------------------------------------------------------------------------
# Ordered roughly by trust, least to most. These are the only values accepted
# for `NetworkSubnet.zone_tier`. Free-text tiers are rejected rather than
# normalised away: a subnet tagged "DMZ" or "PUBLIC-DMZ" would match none of the
# policy predicates below and would therefore audit clean, which is the most
# dangerous possible failure for a control of this kind.
ZONE_PUBLIC_DMZ = "PUBLIC_DMZ"                # Internet-facing ingress, load balancers, public web
ZONE_DEV_MANAGEMENT = "DEV_MANAGEMENT"        # Jump hosts, CI runners, corporate/admin access
ZONE_STRATEGY_ENGINE = "STRATEGY_ENGINE"      # Signal generation, research, model serving
ZONE_TRADING_EXECUTION = "TRADING_EXECUTION"  # Order gateways, FIX sessions, broker connectivity
ZONE_KEY_CUSTODY = "KEY_CUSTODY"              # HSM / MPC signers, private key material

VALID_ZONE_TIERS: FrozenSet[str] = frozenset({
    ZONE_PUBLIC_DMZ,
    ZONE_DEV_MANAGEMENT,
    ZONE_STRATEGY_ENGINE,
    ZONE_TRADING_EXECUTION,
    ZONE_KEY_CUSTODY,
})

# Zones whose compromise directly costs money or keys. Reaching one of these
# from an untrusted zone is the failure this skill exists to detect.
_DEFAULT_CRITICAL_TIERS: FrozenSet[str] = frozenset({
    ZONE_TRADING_EXECUTION,
    ZONE_KEY_CUSTODY,
})

# Zones that must never originate traffic directly into a critical tier.
# DEV_MANAGEMENT is included deliberately: DORA RTS Art. 13(1)(c) calls for "the
# use of a separate and dedicated network for the administration of ICT assets",
# which is a mandate for a mediated admin path (bastion / session broker), not a
# licence for a developer workstation subnet to hold a direct route to an order
# gateway.
_DEFAULT_UNTRUSTED_TIERS: FrozenSet[str] = frozenset({
    ZONE_PUBLIC_DMZ,
    ZONE_DEV_MANAGEMENT,
})

# --------------------------------------------------------------------------
# Statuses, violation codes, severities
# --------------------------------------------------------------------------
STATUS_COMPLIANT = "COMPLIANT"
STATUS_NON_COMPLIANT = "NON_COMPLIANT_SECURITY_VIOLATION"

CODE_DIRECT_UNTRUSTED_INGRESS = "DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE"
CODE_ADMIN_PORT_EXPOSED = "ADMIN_PORT_REACHABLE_FROM_PUBLIC_DMZ"
CODE_CUSTODY_INGRESS = "CUSTODY_INGRESS_FROM_UNAUTHORIZED_TIER"
CODE_WIDE_PORT_RANGE = "WIDE_PORT_RANGE_INTO_CRITICAL_ZONE"
CODE_INTERNET_WILDCARD_SOURCE = "INTERNET_WILDCARD_SOURCE_INTO_CRITICAL_ZONE"
CODE_TRANSITIVE_PATH = "TRANSITIVE_PATH_FROM_UNTRUSTED_TO_CRITICAL_ZONE"

_SEVERITY_RANK: Dict[str, int] = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}

# Administrative and legacy cleartext ports. SSH and RDP are named directly by
# the AWS Foundational Security Best Practices controls EC2.13 and EC2.14, which
# check that security groups do not allow ingress from 0.0.0.0/0 to port 22 and
# port 3389 respectively. Telnet (23) and FTP (21) are included because they
# carry credentials in cleartext and have no place on a trading network at all.
# All four are TCP services, so the check does not fire on a UDP-only rule.
_DEFAULT_ADMIN_PORTS: FrozenSet[int] = frozenset({22, 3389, 23, 21})

# AWS advises "Do not open large port ranges" but does not define "large". This
# default is therefore a repository convention, not a regulatory threshold --
# tune `max_port_span` to your own standard. It exists so that a rule spanning
# thousands of ports into an execution or custody zone is reported as the
# over-permissive grant it is, even when no single named admin port is in range.
_DEFAULT_MAX_PORT_SPAN = 100

_MIN_PORT = 0
_MAX_PORT = 65535

# Protocol tokens. ICMP carries no port numbers, so port-based predicates are
# skipped for it rather than evaluated against a meaningless field.
_PROTO_TCP = "TCP"
_PROTO_UDP = "UDP"
_PROTO_ICMP = "ICMP"
_PROTO_ALL = "ALL"
# "-1" is how the EC2 API spells "all protocols" in IpProtocol.
_PROTOCOL_ALIASES: Dict[str, str] = {
    "TCP": _PROTO_TCP, "6": _PROTO_TCP,
    "UDP": _PROTO_UDP, "17": _PROTO_UDP,
    "ICMP": _PROTO_ICMP, "1": _PROTO_ICMP,
    "ALL": _PROTO_ALL, "ANY": _PROTO_ALL, "-1": _PROTO_ALL, "*": _PROTO_ALL,
}
_PORTLESS_PROTOCOLS: FrozenSet[str] = frozenset({_PROTO_ICMP})

# Action vocabulary. Firewall dialects disagree: AWS security groups are
# allow-only, network ACLs and iptables use allow/deny and ACCEPT/DROP, Cisco
# ACLs use permit/deny. An unrecognised token is rejected rather than skipped --
# silently ignoring a rule spelled "ACCEPT" would let the single most dangerous
# rule in a policy pass through the audit unexamined.
_ACTION_ALLOW = "ALLOW"
_ACTION_DENY = "DENY"
_ACTION_ALIASES: Dict[str, str] = {
    "ALLOW": _ACTION_ALLOW, "ACCEPT": _ACTION_ALLOW, "PERMIT": _ACTION_ALLOW,
    "DENY": _ACTION_DENY, "DROP": _ACTION_DENY, "REJECT": _ACTION_DENY,
    "BLOCK": _ACTION_DENY,
}

_INTERNET_WILDCARD_CIDRS: FrozenSet[str] = frozenset({"0.0.0.0/0", "::/0"})


class SegmentationInputError(ValueError):
    """Raised when a topology is structurally unusable.

    Distinct from a *violation*: a violation is a well-formed topology that
    fails policy; this is a topology the auditor cannot evaluate at all. A gate
    must never treat the two the same -- an unevaluable topology is not a
    compliant one.

    Subclasses ``ValueError`` so that callers written against the previous
    version of this module, which raised a bare ``ValueError`` on an empty
    subnet list, keep working unchanged.
    """


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------
def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise SegmentationInputError(
            f"{field_name} must be a str, got {type(value).__name__!r}"
        )
    stripped = value.strip()
    if not stripped:
        raise SegmentationInputError(f"{field_name} must be a non-empty string")
    return stripped


def _require_port(value: object, field_name: str) -> int:
    # bool is excluded explicitly: it is a subclass of int, and `True` would
    # otherwise silently audit as port 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SegmentationInputError(
            f"{field_name} must be an int, got {type(value).__name__!r}"
        )
    if not _MIN_PORT <= value <= _MAX_PORT:
        raise SegmentationInputError(
            f"{field_name} must be within {_MIN_PORT}-{_MAX_PORT}, got {value}"
        )
    return value


def _normalise_zone_tier(value: object, field_name: str) -> str:
    tier = _require_str(value, field_name).upper()
    if tier not in VALID_ZONE_TIERS:
        raise SegmentationInputError(
            f"{field_name} must be one of {sorted(VALID_ZONE_TIERS)}, got {tier!r}. "
            "An unrecognised tier would match no policy predicate and audit clean."
        )
    return tier


def _normalise_action(value: object, field_name: str) -> str:
    token = _require_str(value, field_name).upper()
    action = _ACTION_ALIASES.get(token)
    if action is None:
        raise SegmentationInputError(
            f"{field_name} must be one of {sorted(set(_ACTION_ALIASES))}, got {token!r}. "
            "Unrecognised actions are rejected, never skipped."
        )
    return action


def _normalise_protocol(value: object, field_name: str) -> str:
    token = _require_str(value, field_name).upper()
    protocol = _PROTOCOL_ALIASES.get(token)
    if protocol is None:
        raise SegmentationInputError(
            f"{field_name} must be one of {sorted(set(_PROTOCOL_ALIASES))}, got {token!r}"
        )
    return protocol


def _validate_cidr(value: object, field_name: str) -> str:
    cidr = _require_str(value, field_name)
    try:
        # strict=False so an inventory that records a host address with a prefix
        # length (10.0.1.5/24) is accepted rather than rejected; the CIDR is used
        # for wildcard detection, not for routing decisions.
        ipaddress.ip_network(cidr, strict=False)
    except (ValueError, TypeError) as exc:
        raise SegmentationInputError(
            f"{field_name} must be a valid CIDR block, got {cidr!r}: {exc}"
        ) from exc
    return cidr


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class NetworkSubnet:
    """A subnet and the security zone tier it has been assigned to.

    Args:
        subnet_id: Stable identifier, unique within the audited topology
            (e.g. an AWS ``subnet-`` id or a VLAN name).
        subnet_name: Human-readable label, used in finding text.
        zone_tier: One of ``VALID_ZONE_TIERS``. Case-insensitive on input and
            normalised to upper case; any other value raises.
        cidr_block: The subnet's address range. ``0.0.0.0/0`` or ``::/0`` marks
            the source as internet-equivalent regardless of declared tier.

    Raises:
        SegmentationInputError: On any malformed or unrecognised field.
    """

    subnet_id: str
    subnet_name: str
    zone_tier: str
    cidr_block: str

    def __post_init__(self) -> None:
        self.subnet_id = _require_str(self.subnet_id, "subnet_id")
        self.subnet_name = _require_str(self.subnet_name, "subnet_name")
        self.zone_tier = _normalise_zone_tier(self.zone_tier, "zone_tier")
        self.cidr_block = _validate_cidr(self.cidr_block, "cidr_block")

    @property
    def is_internet_wildcard(self) -> bool:
        """True when the subnet's CIDR is the whole internet."""
        return self.cidr_block.strip() in _INTERNET_WILDCARD_CIDRS


@dataclass
class FirewallRule:
    """One firewall / security-group rule between two registered subnets.

    ``port`` and ``to_port`` mirror the EC2 ``FromPort``/``ToPort`` pair: AWS
    documents that you may specify "a single port number (for example, ``22``),
    or range of port numbers (for example, ``7000-8000``)". ``to_port`` defaults
    to ``port``, so single-port rules keep the original five-positional-argument
    construction working unchanged.

    Args:
        rule_id: Stable identifier for the rule, used in findings.
        source_subnet_id: Origin subnet id. Must be registered in the audit.
        destination_subnet_id: Destination subnet id. Must be registered.
        protocol: ``TCP`` / ``UDP`` / ``ICMP`` / ``ALL``. EC2 numeric forms
            (``6``, ``17``, ``1``, ``-1``) are accepted. ``ALL`` is treated as
            covering every port, since that is what it grants.
        port: Low end of the port range (``FromPort``).
        action: ``ALLOW`` / ``DENY`` and their dialect synonyms (``ACCEPT``,
            ``PERMIT``, ``DROP``, ``REJECT``, ``BLOCK``).
        to_port: High end of the port range (``ToPort``). Defaults to ``port``.

    Raises:
        SegmentationInputError: On malformed fields, an unrecognised action or
            protocol, or an inverted port range.
    """

    rule_id: str
    source_subnet_id: str
    destination_subnet_id: str
    protocol: str
    port: int
    action: str
    to_port: Optional[int] = None

    def __post_init__(self) -> None:
        self.rule_id = _require_str(self.rule_id, "rule_id")
        self.source_subnet_id = _require_str(self.source_subnet_id, "source_subnet_id")
        self.destination_subnet_id = _require_str(
            self.destination_subnet_id, "destination_subnet_id"
        )
        self.protocol = _normalise_protocol(self.protocol, "protocol")
        self.port = _require_port(self.port, "port")
        self.action = _normalise_action(self.action, "action")
        if self.to_port is None:
            self.to_port = self.port
        else:
            self.to_port = _require_port(self.to_port, "to_port")
        if self.to_port < self.port:
            raise SegmentationInputError(
                f"to_port ({self.to_port}) must be >= port ({self.port}) for rule "
                f"{self.rule_id!r}"
            )

    @property
    def is_allow(self) -> bool:
        return self.action == _ACTION_ALLOW

    def effective_port_range(self) -> Tuple[int, int]:
        """The port range this rule actually grants.

        A protocol of ``ALL`` grants every port irrespective of the declared
        range, so it is reported as ``(0, 65535)``. Evaluating the declared
        range instead would let ``protocol="ALL", port=443`` hide an
        unrestricted grant behind a reassuring-looking port number.
        """
        if self.protocol == _PROTO_ALL:
            return (_MIN_PORT, _MAX_PORT)
        return (self.port, int(self.to_port))

    def covers_port(self, target_port: int) -> bool:
        """Whether this rule's effective range contains ``target_port``."""
        if self.protocol in _PORTLESS_PROTOCOLS:
            return False
        low, high = self.effective_port_range()
        return low <= target_port <= high

    def port_span(self) -> int:
        """Number of ports the rule grants (1 for a single-port rule)."""
        if self.protocol in _PORTLESS_PROTOCOLS:
            return 0
        low, high = self.effective_port_range()
        return high - low + 1

    def port_description(self) -> str:
        low, high = self.effective_port_range()
        if self.protocol in _PORTLESS_PROTOCOLS:
            return self.protocol
        span = f"{low}" if low == high else f"{low}-{high}"
        return f"{self.protocol}/{span}"


@dataclass
class SecurityViolation:
    """A single segmentation policy breach.

    ``code`` is the stable, machine-branchable identifier; ``description`` is
    human-facing text whose wording may change between versions. Alert and gate
    on ``code``, never on substrings of ``description``.

    ``source_subnet_id``/``destination_subnet_id`` name the exact edge, which
    ``rule_id`` alone does not: exporters routinely reuse one identifier across
    every rule in a security group, so a finding identified only by rule id
    cannot be located in the topology.

    For a transitive-path finding the two subnet ids are the *ends of the path*
    (untrusted origin, critical target), not the endpoints of a single rule.
    """

    rule_id: str
    severity: str
    source_tier: str
    destination_tier: str
    description: str
    code: str = ""
    remediation: str = ""
    source_subnet_id: str = ""
    destination_subnet_id: str = ""


@dataclass
class NetworkSegmentationReport:
    """Result of one segmentation audit.

    ``violations_found`` is ordered most-severe first. ``status`` is
    ``COMPLIANT`` only when the list is empty -- there is no partial pass.
    """

    total_subnets: int
    total_firewall_rules: int
    violations_found: List[SecurityViolation]
    is_compliant: bool
    status: str
    audit_notes: str
    violation_codes: List[str] = field(default_factory=list)
    rules_evaluated: int = 0


class NetworkSegmentationAuditorEngine:
    """Audits a declared network topology against Zero-Trust segmentation policy.

    The engine is stateless across calls and holds no global state; policy is
    fixed at construction so that a caller can tighten or relax it explicitly
    rather than by editing module constants.

    Args:
        admin_ports: Ports treated as administrative/legacy-cleartext and
            therefore never reachable from ``PUBLIC_DMZ``. Defaults to
            ``{21, 22, 23, 3389}``.
        critical_tiers: Tiers whose compromise is directly costly. Defaults to
            ``{TRADING_EXECUTION, KEY_CUSTODY}``.
        untrusted_tiers: Tiers that must not originate traffic into a critical
            tier. Defaults to ``{PUBLIC_DMZ, DEV_MANAGEMENT}``.
        custody_authorized_tiers: The only tiers permitted to originate ingress
            into ``KEY_CUSTODY``. Defaults to ``{STRATEGY_ENGINE, KEY_CUSTODY}``
            -- i.e. the signing requesters and intra-tier HSM/MPC quorum
            traffic. Narrow this further if your signers are isolated.
        max_port_span: Port-range width above which a grant into a critical tier
            is reported as over-permissive. A repository convention, not a
            regulatory number.
        detect_transitive_paths: When True (default), also report multi-hop
            paths from an untrusted tier into a critical tier.

    Raises:
        SegmentationInputError: If any configured tier is not a valid zone tier.
    """

    # Retained as a class attribute for backwards compatibility with callers
    # that read or override it. Instance policy lives in `self.admin_ports`.
    DISALLOWED_ADMIN_PORTS: FrozenSet[int] = _DEFAULT_ADMIN_PORTS

    def __init__(
        self,
        admin_ports: Optional[Iterable[int]] = None,
        critical_tiers: Optional[Iterable[str]] = None,
        untrusted_tiers: Optional[Iterable[str]] = None,
        custody_authorized_tiers: Optional[Iterable[str]] = None,
        max_port_span: int = _DEFAULT_MAX_PORT_SPAN,
        detect_transitive_paths: bool = True,
    ) -> None:
        self.admin_ports: FrozenSet[int] = frozenset(
            _require_port(p, "admin_ports entry")
            for p in (admin_ports if admin_ports is not None else self.DISALLOWED_ADMIN_PORTS)
        )
        self.critical_tiers = self._validate_tier_set(
            critical_tiers, _DEFAULT_CRITICAL_TIERS, "critical_tiers"
        )
        self.untrusted_tiers = self._validate_tier_set(
            untrusted_tiers, _DEFAULT_UNTRUSTED_TIERS, "untrusted_tiers"
        )
        self.custody_authorized_tiers = self._validate_tier_set(
            custody_authorized_tiers,
            frozenset({ZONE_STRATEGY_ENGINE, ZONE_KEY_CUSTODY}),
            "custody_authorized_tiers",
        )
        if isinstance(max_port_span, bool) or not isinstance(max_port_span, int):
            raise SegmentationInputError("max_port_span must be an int")
        if max_port_span < 1:
            raise SegmentationInputError("max_port_span must be >= 1")
        self.max_port_span = max_port_span
        self.detect_transitive_paths = bool(detect_transitive_paths)

    @staticmethod
    def _validate_tier_set(
        supplied: Optional[Iterable[str]], default: FrozenSet[str], field_name: str
    ) -> FrozenSet[str]:
        if supplied is None:
            return default
        return frozenset(_normalise_zone_tier(t, f"{field_name} entry") for t in supplied)

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------
    def audit_segmentation(
        self, subnets: Sequence[NetworkSubnet], rules: Sequence[FirewallRule]
    ) -> NetworkSegmentationReport:
        """Audit a topology against the Zero-Trust cross-zone policy matrix.

        Args:
            subnets: Every subnet in the audited scope. Must be non-empty and
                free of duplicate ``subnet_id`` values.
            rules: Firewall / security-group rules between those subnets. May be
                empty -- a topology with no rules is trivially segmented.

        Returns:
            A ``NetworkSegmentationReport`` whose ``violations_found`` is ordered
            most-severe first and deduplicated by ``(rule_id, code)``.

        Raises:
            SegmentationInputError: If the topology is unevaluable -- empty or
                duplicated subnets, or a rule whose endpoints are not registered
                subnets. Callers must treat this as a **failure**, never as a
                pass: an unevaluable topology is not a compliant one.
        """
        subnet_map = self._build_subnet_map(subnets)
        rule_list = self._validate_rules(rules, subnet_map)

        violations: List[SecurityViolation] = []
        allow_rules = [r for r in rule_list if r.is_allow]

        for rule in allow_rules:
            src = subnet_map[rule.source_subnet_id]
            dst = subnet_map[rule.destination_subnet_id]
            violations.extend(self._evaluate_rule(rule, src, dst))

        if self.detect_transitive_paths:
            violations.extend(self._detect_transitive_paths(subnet_map, allow_rules))

        violations = self._finalise(violations)

        is_compliant = not violations
        status = STATUS_COMPLIANT if is_compliant else STATUS_NON_COMPLIANT
        notes = (
            f"NETWORK SEGMENTATION AUDIT [{status}]: Audited {len(subnet_map)} subnets and "
            f"{len(rule_list)} firewall rules ({len(allow_rules)} ALLOW). "
            f"Found {len(violations)} security violations."
        )
        if is_compliant:
            logger.info(notes)
        else:
            logger.error(notes)

        return NetworkSegmentationReport(
            total_subnets=len(subnet_map),
            total_firewall_rules=len(rule_list),
            violations_found=violations,
            is_compliant=is_compliant,
            status=status,
            audit_notes=notes,
            violation_codes=[v.code for v in violations],
            rules_evaluated=len(allow_rules),
        )

    # ----------------------------------------------------------------------
    # Input handling
    # ----------------------------------------------------------------------
    @staticmethod
    def _build_subnet_map(
        subnets: Sequence[NetworkSubnet],
    ) -> Dict[str, NetworkSubnet]:
        if not isinstance(subnets, (list, tuple)):
            raise SegmentationInputError(
                f"subnets must be a list or tuple, got {type(subnets).__name__!r}"
            )
        if not subnets:
            raise SegmentationInputError("Subnets list cannot be empty.")

        subnet_map: Dict[str, NetworkSubnet] = {}
        for index, subnet in enumerate(subnets):
            if not isinstance(subnet, NetworkSubnet):
                raise SegmentationInputError(
                    f"subnets[{index}] must be a NetworkSubnet, "
                    f"got {type(subnet).__name__!r}"
                )
            if subnet.subnet_id in subnet_map:
                # Last-wins would silently discard one tier assignment and could
                # reclassify a custody subnet as a DMZ one, or the reverse.
                raise SegmentationInputError(
                    f"Duplicate subnet_id {subnet.subnet_id!r}: each subnet must be "
                    "registered exactly once."
                )
            subnet_map[subnet.subnet_id] = subnet
        return subnet_map

    @staticmethod
    def _validate_rules(
        rules: Sequence[FirewallRule], subnet_map: Dict[str, NetworkSubnet]
    ) -> List[FirewallRule]:
        if not isinstance(rules, (list, tuple)):
            raise SegmentationInputError(
                f"rules must be a list or tuple, got {type(rules).__name__!r}"
            )
        validated: List[FirewallRule] = []
        for index, rule in enumerate(rules):
            if not isinstance(rule, FirewallRule):
                raise SegmentationInputError(
                    f"rules[{index}] must be a FirewallRule, "
                    f"got {type(rule).__name__!r}"
                )
            # Fail closed. The previous behaviour was to `continue` past a rule
            # whose endpoints were not registered, which meant a topology
            # referencing a mistyped or unexported subnet id audited COMPLIANT
            # without that rule ever being examined.
            missing = [
                (name, value)
                for name, value in (
                    ("source_subnet_id", rule.source_subnet_id),
                    ("destination_subnet_id", rule.destination_subnet_id),
                )
                if value not in subnet_map
            ]
            if missing:
                detail = ", ".join(f"{name}={value!r}" for name, value in missing)
                raise SegmentationInputError(
                    f"Rule {rule.rule_id!r} references unregistered subnet(s): {detail}. "
                    "Every rule endpoint must be a registered subnet -- an unresolvable "
                    "rule cannot be audited and must not be silently skipped."
                )
            validated.append(rule)
        return validated

    # ----------------------------------------------------------------------
    # Per-rule policy predicates
    # ----------------------------------------------------------------------
    def _evaluate_rule(
        self, rule: FirewallRule, src: NetworkSubnet, dst: NetworkSubnet
    ) -> List[SecurityViolation]:
        found: List[SecurityViolation] = []
        src_tier, dst_tier = src.zone_tier, dst.zone_tier
        ports = rule.port_description()

        def add(code: str, severity: str, description: str, remediation: str) -> None:
            logger.error("SEGMENTATION VIOLATION [%s] %s: %s", rule.rule_id, code, description)
            found.append(
                SecurityViolation(
                    rule_id=rule.rule_id,
                    severity=severity,
                    source_tier=src_tier,
                    destination_tier=dst_tier,
                    description=description,
                    code=code,
                    remediation=remediation,
                    source_subnet_id=src.subnet_id,
                    destination_subnet_id=dst.subnet_id,
                )
            )

        # Violation 1 -- direct ingress from an untrusted tier into a critical one.
        if src_tier in self.untrusted_tiers and dst_tier in self.critical_tiers:
            add(
                CODE_DIRECT_UNTRUSTED_INGRESS,
                "CRITICAL",
                f"Direct traffic allowed from '{src_tier}' ({src.subnet_name}) to "
                f"'{dst_tier}' ({dst.subnet_name}) on {ports}.",
                f"Remove rule {rule.rule_id}. Route this flow through a mediated hop "
                "(bastion or session broker) in a dedicated administration network "
                "(DORA RTS Art. 13(1)(c)).",
            )

        # Violation 2 -- an administrative or cleartext port reachable from the DMZ.
        # Evaluated against the rule's whole effective range, so a 0-65535 grant
        # is caught where an `in {22, 3389}` membership test would miss it.
        if src_tier == ZONE_PUBLIC_DMZ:
            exposed = sorted(p for p in self.admin_ports if rule.covers_port(p))
            if exposed:
                add(
                    CODE_ADMIN_PORT_EXPOSED,
                    "HIGH",
                    f"Administrative port(s) {exposed} reachable from '{ZONE_PUBLIC_DMZ}' "
                    f"({src.subnet_name}) to '{dst.subnet_name}' via {ports}.",
                    f"Remove rule {rule.rule_id} or narrow its range. AWS FSBP controls "
                    "EC2.13/EC2.14 require that ports 22 and 3389 are not reachable from "
                    "0.0.0.0/0; administrative access belongs on a dedicated admin network.",
                )

        # Violation 3 -- custody ingress from a tier outside the signing whitelist.
        if dst_tier == ZONE_KEY_CUSTODY and src_tier not in self.custody_authorized_tiers:
            add(
                CODE_CUSTODY_INGRESS,
                "CRITICAL",
                f"Key custody zone ({dst.subnet_name}) accepts ingress from "
                f"non-whitelisted tier '{src_tier}' ({src.subnet_name}) via {ports}.",
                f"Remove rule {rule.rule_id}. Key custody ingress must be restricted to "
                f"{sorted(self.custody_authorized_tiers)}.",
            )

        # Violation 4 -- an over-wide port range into a critical tier.
        if dst_tier in self.critical_tiers and rule.port_span() > self.max_port_span:
            add(
                CODE_WIDE_PORT_RANGE,
                "HIGH",
                f"Over-permissive grant into '{dst_tier}' ({dst.subnet_name}): {ports} "
                f"spans {rule.port_span()} ports (limit {self.max_port_span}).",
                f"Narrow rule {rule.rule_id} to the specific ports the service needs. "
                "AWS guidance: 'Do not open large port ranges.'",
            )

        # Violation 5 -- an internet-wildcard source reaching a critical tier.
        # Catches the 0.0.0.0/0 troubleshooting rule that was never removed,
        # including the case where it has been mislabelled into a trusted tier.
        if dst_tier in self.critical_tiers and src.is_internet_wildcard:
            add(
                CODE_INTERNET_WILDCARD_SOURCE,
                "CRITICAL",
                f"Source subnet {src.subnet_name} is the internet wildcard "
                f"{src.cidr_block} and reaches '{dst_tier}' ({dst.subnet_name}) via "
                f"{ports}, regardless of its declared tier '{src_tier}'.",
                f"Remove rule {rule.rule_id} and replace the wildcard source with the "
                "specific prefix or security group that requires access.",
            )

        return found

    # ----------------------------------------------------------------------
    # Multi-hop reachability
    # ----------------------------------------------------------------------
    def _detect_transitive_paths(
        self, subnet_map: Dict[str, NetworkSubnet], allow_rules: Sequence[FirewallRule]
    ) -> List[SecurityViolation]:
        """Report untrusted -> critical paths of two or more hops.

        A per-rule audit passes a topology where PUBLIC_DMZ reaches
        STRATEGY_ENGINE and STRATEGY_ENGINE reaches KEY_CUSTODY, because neither
        edge is individually forbidden -- yet an attacker who lands in the DMZ
        has a routed path to the signers. NIST SP 800-207 frames this directly:
        the tenets of ZTA "aim to reduce the exposure of resources to attackers
        and minimize or prevent lateral movement within an enterprise should a
        host asset be compromised."

        Single-hop paths are excluded because ``_evaluate_rule`` already reports
        them as ``DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE``.

        Reachability here is a *routing* claim about the declared policy. It does
        not model application-layer authentication on the intermediate hop, which
        may well be the real control; treat a finding as "prove this hop is a
        policy enforcement point", not automatically as a misconfiguration.
        """
        adjacency: Dict[str, List[FirewallRule]] = {}
        for rule in allow_rules:
            adjacency.setdefault(rule.source_subnet_id, []).append(rule)

        found: List[SecurityViolation] = []
        reported: Set[Tuple[str, str]] = set()

        origins = [
            s for s in subnet_map.values()
            if s.zone_tier in self.untrusted_tiers and s.zone_tier not in self.critical_tiers
        ]

        for origin in origins:
            # BFS over ALLOW edges, carrying the path so a finding can name it.
            queue: Deque[Tuple[str, Tuple[str, ...], Optional[str]]] = deque(
                [(origin.subnet_id, (origin.subnet_id,), None)]
            )
            visited: Set[str] = {origin.subnet_id}

            while queue:
                current_id, path, first_rule_id = queue.popleft()
                for rule in adjacency.get(current_id, ()):
                    next_id = rule.destination_subnet_id
                    if next_id in visited:
                        continue
                    next_subnet = subnet_map[next_id]
                    next_path = path + (next_id,)
                    entry_rule_id = first_rule_id or rule.rule_id

                    if next_subnet.zone_tier in self.critical_tiers:
                        # len(next_path) == 2 is a direct edge, already reported.
                        key = (origin.subnet_id, next_id)
                        if len(next_path) > 2 and key not in reported:
                            reported.add(key)
                            found.append(
                                self._transitive_violation(
                                    origin, next_subnet, next_path, subnet_map, entry_rule_id
                                )
                            )
                        # Do not expand past a critical zone: onward hops are no
                        # longer "untrusted origin reaches protected asset".
                        visited.add(next_id)
                        continue

                    visited.add(next_id)
                    queue.append((next_id, next_path, entry_rule_id))

        return found

    def _transitive_violation(
        self,
        origin: NetworkSubnet,
        target: NetworkSubnet,
        path: Tuple[str, ...],
        subnet_map: Dict[str, NetworkSubnet],
        entry_rule_id: str,
    ) -> SecurityViolation:
        readable = " -> ".join(
            f"{subnet_map[node].subnet_name} [{subnet_map[node].zone_tier}]" for node in path
        )
        description = (
            f"Multi-hop path from '{origin.zone_tier}' ({origin.subnet_name}) to "
            f"'{target.zone_tier}' ({target.subnet_name}) across {len(path) - 1} hops: "
            f"{readable}. No single rule on this path is individually forbidden."
        )
        logger.error(
            "SEGMENTATION VIOLATION [%s] %s: %s",
            entry_rule_id, CODE_TRANSITIVE_PATH, description,
        )
        return SecurityViolation(
            rule_id=entry_rule_id,
            severity="HIGH",
            source_tier=origin.zone_tier,
            destination_tier=target.zone_tier,
            description=description,
            code=CODE_TRANSITIVE_PATH,
            source_subnet_id=origin.subnet_id,
            destination_subnet_id=target.subnet_id,
            remediation=(
                "Confirm the intermediate hop is a policy enforcement point that "
                "authenticates and authorises each request (NIST SP 800-207 §3.1.2). "
                f"If it merely forwards, break the path -- starting with rule {entry_rule_id}."
            ),
        )

    # ----------------------------------------------------------------------
    # Result assembly
    # ----------------------------------------------------------------------
    @staticmethod
    def _finalise(violations: List[SecurityViolation]) -> List[SecurityViolation]:
        """Deduplicate by (rule_id, code, edge) and order most-severe first.

        A single rule can breach more than one distinct control and should
        report each once; it should not report the *same* control twice.

        The edge is part of the key, not just the rule id. Exporters commonly
        reuse one identifier across every rule in a security group, and keying
        on ``(rule_id, code)`` alone would then discard a real finding: two
        rules sharing an id, one reaching the execution zone and one reaching
        custody, breach the same control on two different edges and must both
        be reported.

        Ordering is stable within a severity so output is deterministic.
        """
        seen: Set[Tuple[str, str, str, str]] = set()
        unique: List[SecurityViolation] = []
        for violation in violations:
            key = (
                violation.rule_id,
                violation.code,
                violation.source_subnet_id,
                violation.destination_subnet_id,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(violation)
        return sorted(unique, key=lambda v: _SEVERITY_RANK.get(v.severity, 99))
