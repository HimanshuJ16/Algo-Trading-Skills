import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class NetworkSubnet:
    subnet_id: str
    subnet_name: str
    zone_tier: str                       # 'PUBLIC_DMZ', 'TRADING_EXECUTION', 'STRATEGY_ENGINE', 'KEY_CUSTODY', 'DEV_MANAGEMENT'
    cidr_block: str

@dataclass
class FirewallRule:
    rule_id: str
    source_subnet_id: str
    destination_subnet_id: str
    protocol: str                        # 'TCP', 'UDP', 'ALL'
    port: int                            # 22 (SSH), 80 (HTTP), 443 (HTTPS), 10443 (FIX), etc.
    action: str                          # 'ALLOW' or 'DENY'

@dataclass
class SecurityViolation:
    rule_id: str
    severity: str                        # 'CRITICAL', 'HIGH', 'MEDIUM'
    source_tier: str
    destination_tier: str
    description: str

@dataclass
class NetworkSegmentationReport:
    total_subnets: int
    total_firewall_rules: int
    violations_found: List[SecurityViolation]
    is_compliant: bool
    status: str                          # 'COMPLIANT', 'NON_COMPLIANT_SECURITY_VIOLATION'
    audit_notes: str

class NetworkSegmentationAuditorEngine:
    """
    Network segmentation auditor enforcing Zero-Trust security zone isolation,
    detecting illegal cross-zone traffic flows (Public/Dev to Execution/Custody), and auditing firewall rules.
    """
    DISALLOWED_ADMIN_PORTS = {22, 3389, 23, 21} # SSH, RDP, Telnet, FTP

    def __init__(self):
        pass

    def audit_segmentation(
        self, subnets: List[NetworkSubnet], rules: List[FirewallRule]
    ) -> NetworkSegmentationReport:
        """
        Audits firewall rules against Zero-Trust cross-zone security policy matrix.
        """
        if not subnets:
            raise ValueError("Subnets list cannot be empty.")

        subnet_map: Dict[str, NetworkSubnet] = {s.subnet_id: s for s in subnets}
        violations: List[SecurityViolation] = []

        for r in rules:
            if r.action.upper() != "ALLOW":
                continue

            src_sub = subnet_map.get(r.source_subnet_id)
            dst_sub = subnet_map.get(r.destination_subnet_id)

            if not src_sub or not dst_sub:
                continue

            src_tier = src_sub.zone_tier.upper()
            dst_tier = dst_sub.zone_tier.upper()

            # Rule Violation 1: Direct ALLOW from PUBLIC_DMZ or DEV_MANAGEMENT into TRADING_EXECUTION or KEY_CUSTODY
            if src_tier in ("PUBLIC_DMZ", "DEV_MANAGEMENT") and dst_tier in ("TRADING_EXECUTION", "KEY_CUSTODY"):
                viol_desc = f"CRITICAL BREACH: Direct traffic allowed from '{src_tier}' ({src_sub.subnet_name}) to '{dst_tier}' ({dst_sub.subnet_name}) on port {r.port}."
                logger.error(f"SECURITY VIOLATION [{r.rule_id}]: {viol_desc}")
                violations.append(SecurityViolation(
                    rule_id=r.rule_id,
                    severity="CRITICAL",
                    source_tier=src_tier,
                    destination_tier=dst_tier,
                    description=viol_desc
                ))

            # Rule Violation 2: Administrative port (SSH 22, RDP 3389) exposed to PUBLIC_DMZ
            if src_tier == "PUBLIC_DMZ" and r.port in self.DISALLOWED_ADMIN_PORTS:
                viol_desc = f"HIGH RISK: Admin port {r.port} exposed from '{src_tier}' ({src_sub.subnet_name}) to '{dst_sub.subnet_name}'."
                logger.error(f"SECURITY VIOLATION [{r.rule_id}]: {viol_desc}")
                violations.append(SecurityViolation(
                    rule_id=r.rule_id,
                    severity="HIGH",
                    source_tier=src_tier,
                    destination_tier=dst_tier,
                    description=viol_desc
                ))

            # Rule Violation 3: KEY_CUSTODY accessible from non-whitelisted tier (only STRATEGY_ENGINE or HSM ALLOWED)
            if dst_tier == "KEY_CUSTODY" and src_tier not in ("STRATEGY_ENGINE", "KEY_CUSTODY"):
                viol_desc = f"CRITICAL BREACH: Key Custody vault accessed from non-whitelisted tier '{src_tier}'."
                logger.error(f"SECURITY VIOLATION [{r.rule_id}]: {viol_desc}")
                violations.append(SecurityViolation(
                    rule_id=r.rule_id,
                    severity="CRITICAL",
                    source_tier=src_tier,
                    destination_tier=dst_tier,
                    description=viol_desc
                ))

        is_compliant = len(violations) == 0
        status = "COMPLIANT" if is_compliant else "NON_COMPLIANT_SECURITY_VIOLATION"

        notes = (
            f"NETWORK SEGMENTATION AUDIT [{status}]: Audited {len(subnets)} subnets and {len(rules)} firewall rules. "
            f"Found {len(violations)} security violations."
        )
        logger.info(notes)

        return NetworkSegmentationReport(
            total_subnets=len(subnets),
            total_firewall_rules=len(rules),
            violations_found=violations,
            is_compliant=is_compliant,
            status=status,
            audit_notes=notes
        )
