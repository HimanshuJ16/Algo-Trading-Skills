# Standards for Network Segmentation of Trading Infrastructure

Every control the audit engine enforces, with the source it rests on. Where a
threshold is a repository convention rather than a published requirement, that is
stated rather than implied.

## Control-to-source map

| Control (violation code) | Standard / source | What it actually says |
|---|---|---|
| `DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE` | NIST SP 800-207 §2.1 | "Perimeter-based network security has also been shown to be insufficient since once attackers breach the perimeter, further lateral movement is unhindered." |
| `DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE` | CDR (EU) 2024/1774 Art. 13(1)(a) | Requires "the segregation and segmentation of ICT systems and networks taking into account: (i) the criticality or importance of the function those ICT systems and networks support". |
| `DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE` (DEV_MANAGEMENT as untrusted) | CDR (EU) 2024/1774 Art. 13(1)(c) | Requires "the use of a separate and dedicated network for the administration of ICT assets" — administrative access is mediated, not direct. |
| `ADMIN_PORT_REACHABLE_FROM_PUBLIC_DMZ` | AWS Foundational Security Best Practices, EC2.13 / EC2.14 | EC2.13 checks that security groups do not allow ingress from `0.0.0.0/0` or `::/0` to **port 22**; EC2.14 the same for **port 3389**. |
| `ADMIN_PORT_REACHABLE_FROM_PUBLIC_DMZ` | AWS VPC User Guide, *security group best practices* | "When you add inbound rules for ports 22 (SSH) or 3389 (RDP) … authorize only specific IP address ranges. If you specify 0.0.0.0/0 … this enables anyone to access your instances from any IP address using the specified protocol." |
| `CUSTODY_INGRESS_FROM_UNAUTHORIZED_TIER` | 17 CFR 240.15c3-5(c)(2)(iii) | Requires controls reasonably designed to "Restrict access to trading systems and technology that provide market access to persons and accounts pre-approved and authorized by the broker or dealer." |
| `CUSTODY_INGRESS_FROM_UNAUTHORIZED_TIER` | CDR (EU) 2024/1774 Art. 21(a) | "the assignment of access rights to ICT assets based on need-to-know, need-to-use and least privilege principles, including for remote and emergency access". |
| `WIDE_PORT_RANGE_INTO_CRITICAL_ZONE` | AWS VPC User Guide, *security group best practices* | "Do not open large port ranges. Ensure that access through each port is restricted to the sources or destinations that require it." **AWS does not define "large"** — see *Thresholds* below. |
| `INTERNET_WILDCARD_SOURCE_INTO_CRITICAL_ZONE` | AWS VPC User Guide (same clause as EC2.13/14 above) | A `0.0.0.0/0` source "enables anyone to access your instances from any IP address". |
| `TRANSITIVE_PATH_FROM_UNTRUSTED_TO_CRITICAL_ZONE` | NIST SP 800-207 App. B.4.5 | "The tenets of ZTA aim to reduce the exposure of resources to attackers and minimize or prevent lateral movement within an enterprise should a host asset be compromised." |
| `TRANSITIVE_PATH_FROM_UNTRUSTED_TO_CRITICAL_ZONE` | NIST SP 800-207 §2.1 | "unauthorized lateral movement within the environment has been one of the biggest challenges for federal agencies." |
| Fail-closed on unresolvable rules | CDR (EU) 2024/1774 Art. 13(1)(b) | Requires "the documentation of all of the financial entity's network connections and data flows" — an undocumented flow cannot be audited. |

## The Zero-Trust claim, stated precisely

This engine implements the **micro-segmentation deployment approach** described
in NIST SP 800-207 §3.1.2: "placing individual or groups of resources on a unique
network segment protected by a gateway security component."

That is one approach to building a ZTA. It is not ZTA. The same section is
explicit that the approach "requires an identity governance program (IGP) to
fully function", and tenet 2 (§2.1) states:

> "All communication is secured regardless of network location. **Network
> location alone does not imply trust.** Access requests from assets located on
> enterprise-owned network infrastructure (e.g., inside a legacy network
> perimeter) must meet the same security requirements as access requests and
> communication from any other nonenterprise-owned network."

A topology that passes this audit has a defensible segment layout. Nothing in
that result speaks to whether any request across those segments was
authenticated or authorised.

§3.1.2 carries one further caveat worth heeding: "It is possible to implement
some features of a micro-segmented enterprise by using less advanced gateway
devices and even stateless firewalls, but the administration cost and difficulty
to quickly adapt to changes make this a very poor choice."

## Firewall evaluation semantics — why the model matters

The engine evaluates rules as an **unordered set**. Whether that is correct
depends entirely on what produced the rules.

| Mechanism | Allow/deny | Ordering | Stateful | Unordered-set model valid? |
|---|---|---|---|---|
| AWS security group | Allow only — "You can specify allow rules, but not deny rules." | None; "the rules from each security group are aggregated to form a single set of rules" | Yes | **Yes** |
| AWS network ACL | Both | Rule number 1–32766; "If the traffic matches a rule, the rule is applied and we do not evaluate any additional rules" | No ("NACLs are *stateless*") | **No** — resolve shadowing first |
| iptables / nftables | Both (`ACCEPT`/`DROP`) | First match wins within a chain | Optional (conntrack) | **No** |
| Cisco ACL | Both (`permit`/`deny`) | First match wins | Depends | **No** |

Two consequences:

1. A `DENY` in an export claiming to be AWS **security groups** is a modelling
   error — the mechanism has no deny. Chase it down before trusting the audit.
2. For an ordered ACL, this engine may report an `ALLOW` that a lower-numbered
   `DENY` already shadows. Flatten the effective policy before auditing, or
   accept the finding as a prompt to verify the shadowing rather than as proof
   of a breach.

## Review cadence — mandatory for EU financial entities

Commission Delegated Regulation (EU) 2024/1774 (the DORA RTS on ICT risk
management), Article 13(2):

> "For ICT systems that support critical or important functions, financial
> entities shall verify the adequacy of the existing firewall rules and
> connection filters at least every 6 months."

Article 13(1)(h) additionally requires identifying "roles, responsibilities and
procedures for specifying, implementing, approving, modifying and reviewing
firewall rules and connection filters" — the review needs a named owner, not
just a cron entry.

**Applicability:** DORA (Regulation (EU) 2022/2554) has applied since
**17 January 2025** to EU financial entities, which includes investment firms.
The six-month floor is a legal minimum for in-scope entities, not a
best-practice suggestion, and not a target to aim at.

## US applicability — read the scope before citing

- **SEC Rule 15c3-5** (the Market Access Rule) applies to a broker-dealer with
  market access. Paragraph (c)(2)(iii) requires restricting "access to trading
  systems and technology that provide market access to persons and accounts
  pre-approved and authorized by the broker or dealer", and (d)(1) requires the
  risk management controls to be "under the direct and exclusive control of the
  broker or dealer". Both bear directly on isolating the execution and
  risk-control tiers. **This rule does not apply to a firm trading its own
  capital through a broker without market access of its own.**

- **Regulation SCI** (17 CFR 242.1000–1001) is frequently miscited in this
  context. It applies only to an **"SCI entity"**, defined as "An SCI
  self-regulatory organization, SCI alternative trading system, plan processor,
  exempt clearing agency subject to ARP, or SCI competing consolidator." A
  proprietary trading firm or ordinary broker-dealer is **not** an SCI entity.
  Do not cite Reg SCI as a segmentation requirement for such a firm.

  Where it *does* apply, its framing is directly useful. Rule 1001(a)(1) requires
  policies ensuring SCI systems "and, for purposes of security standards,
  **indirect SCI systems**, have levels of capacity, integrity, resiliency,
  availability, and security" adequate to "promote the maintenance of fair and
  orderly markets" — and an indirect SCI system is defined as one "that, if
  breached, would be reasonably likely to pose a security threat to SCI systems."
  That is precisely the argument for tiering a developer-management network away
  from an execution network: the dev network's security posture is in scope
  *because* a breach there threatens the trading systems.

## Thresholds that are conventions, not requirements

- **`max_port_span` (default 100).** AWS says "Do not open large port ranges"
  without defining "large", and no regulator publishes a number. The default is
  a repository convention chosen to catch obviously-wide grants into execution
  and custody zones. Tune it to your own standard and record the choice.
- **`custody_authorized_tiers` (default `{STRATEGY_ENGINE, KEY_CUSTODY}`).** A
  design assumption — signing requests originate in the strategy tier, and
  intra-tier traffic covers HSM/MPC quorum. If your signers are reached from
  elsewhere, or are fully isolated, configure it explicitly.
- **The four administrative ports (21, 22, 23, 3389).** SSH (22) and RDP (3389)
  are named by AWS FSBP EC2.13/EC2.14. Telnet (23) and FTP (21) are included on
  the general ground that they carry credentials in cleartext; no cited standard
  names them for this specific control.

## Sources

- NIST SP 800-207, *Zero Trust Architecture* (August 2020) —
  https://doi.org/10.6028/NIST.SP.800-207
- Amazon VPC User Guide, *Control traffic to your AWS resources using security
  groups* and *Security group rules* —
  https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html
- Amazon VPC User Guide, *Control subnet traffic with network access control
  lists* —
  https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html
- AWS Security Hub, *Foundational Security Best Practices* — EC2 controls
  EC2.13, EC2.14 —
  https://docs.aws.amazon.com/securityhub/latest/userguide/ec2-controls.html
- Commission Delegated Regulation (EU) 2024/1774, Arts. 13 and 21 —
  https://eur-lex.europa.eu/eli/reg_del/2024/1774/oj/eng
- Regulation (EU) 2022/2554 (DORA) —
  https://eur-lex.europa.eu/eli/reg/2022/2554/oj/eng
- 17 CFR 240.15c3-5, *Risk management controls for brokers or dealers with
  market access*
- 17 CFR 242.1000–1001, *Regulation SCI* — definitions and Rule 1001(a)
