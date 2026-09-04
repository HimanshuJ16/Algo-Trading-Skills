---
name: network-segmentation-for-trading-infrastructure
description: >-
  Use when auditing whether an attacker landing in the least-trusted subnet can reach an
  order gateway or a signing host; checks declared subnets and firewall rules for direct
  and multi-hop paths.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment-ops, network-segmentation, zero-trust, firewall-auditing, key-custody, aws-vpc, dora-rts
  brokers_frameworks: "NIST SP 800-207 (Zero Trust Architecture); AWS VPC Security Groups & Network ACLs; AWS Foundational Security Best Practices; Commission Delegated Regulation (EU) 2024/1774 (DORA RTS)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when you have a network topology — cloud VPC, co-location
VLANs, or on-premise firewall zones — carrying hosts that can send orders or
sign transactions, and you need to know whether an attacker who lands in the
least-trusted part of it can reach those hosts.

The failure it prevents is specific and well documented. NIST SP 800-207 puts it
plainly: "Perimeter-based network security has also been shown to be
insufficient since once attackers breach the perimeter, further lateral movement
is unhindered." In a trading estate the assets at the end of that lateral path
are an order gateway with live broker credentials and an HSM holding signing
keys. A flat network means one compromised CI runner is one hop from both.

Use it in three places: at design review before a topology is built, as a CI
gate on the Terraform plan that changes security groups, and on a recurring
schedule against the *live* exported rule set — for EU financial entities the
last one is not optional (see Prerequisites).

## When NOT to Use

- **You want your Zero-Trust posture assessed.** This engine implements the
  *micro-segmentation deployment approach* of NIST SP 800-207 §3.1.2, which is
  one way to build a ZTA, not ZTA itself. NIST says that approach "requires an
  identity governance program (IGP) to fully function," and tenet 2 of the same
  document states that "Network location alone does not imply trust." Passing
  this audit means your segments are laid out defensibly. It says nothing about
  whether any request was authenticated.
- **Your policy is an ordered, first-match-wins ACL.** This engine evaluates
  rules as an unordered set. That is exactly right for AWS security groups,
  where "the rules from each security group are aggregated to form a single set
  of rules." It is wrong for AWS network ACLs, iptables chains, and Cisco ACLs,
  where "If the traffic matches a rule, the rule is applied and we do not
  evaluate any additional rules" — a low-numbered DENY can shadow an ALLOW that
  this engine will still report. Model shadowing before feeding those in.
- **You need enforcement rather than detection.** This is a Python function in
  your pipeline; anyone with console access can add a security group around it.
  Enforce with SCPs, AWS Config rules, or admission control, and use this to
  fail fast and explain why.
- **You want the live account audited.** The engine makes no AWS API call. It
  audits the inventory you hand it, and a security group that exists in the
  account but not in your export is invisible to it.
- **You are auditing identity or credential scope.** Who may call the signer,
  with which key, is `api-key-least-privilege-audit-tool` and
  `multi-party-computation-mpc-custody-solutions`. This skill only decides who
  can reach it over the network.

## Prerequisites

- **Subnet inventory** with a zone tier per subnet, exported from the source of
  record rather than hand-transcribed. Tiers are a closed set: `PUBLIC_DMZ`,
  `DEV_MANAGEMENT`, `STRATEGY_ENGINE`, `TRADING_EXECUTION`, `KEY_CUSTODY`. Any
  other string is rejected at construction — see Pitfalls for why.
- **The firewall / security-group rule set**, likewise exported —
  `aws ec2 describe-security-groups`, Terraform state, or the appliance's config
  dump. DORA's RTS on ICT risk management requires "the documentation of all of
  the financial entity's network connections and data flows" (Art. 13(1)(b));
  an undocumented flow cannot be audited by anything, including this.
- **A review cadence.** For EU financial entities, Art. 13(2) of the same RTS is
  explicit: "For ICT systems that support critical or important functions,
  financial entities shall verify the adequacy of the existing firewall rules
  and connection filters **at least every 6 months**." An order-routing gateway
  is squarely such a system. DORA has applied since 17 January 2025.
- Python 3.10+. Standard library only — no dependencies.

## Workflow

1. **Classify every subnet into exactly one tier, and let bad tags fail loudly.**
   - `NetworkSubnet` rejects any `zone_tier` outside the closed set. This is
     deliberate and it is the single most important input control: in the
     previous version a subnet tagged `PUBLIC-DMZ` instead of `PUBLIC_DMZ`
     matched no policy predicate, so an SSH grant from it into the execution
     zone audited `COMPLIANT`. A tier the engine does not recognise is an
     error, never a silently trusted zone.
   - Duplicate `subnet_id` values are rejected too. Last-wins could reclassify a
     custody subnet as a DMZ one, or the reverse, with no warning.

2. **Express port ranges as ranges, not as a representative port.**
   - `FirewallRule` carries `port` and `to_port`, mirroring EC2's
     `FromPort`/`ToPort`: AWS documents "a single port number (for example,
     `22`), or range of port numbers (for example, `7000-8000`)". `to_port`
     defaults to `port`, so single-port rules need no change.
   - Admin-port checks test **range intersection**, not membership. A rule
     opening `0-65535` from the DMZ contains SSH and RDP and is reported as
     such — the previous `port in {22, 3389}` test never matched it, which meant
     the most over-permissive rule shape in production was the one that evaded
     detection.
   - `protocol="ALL"` (or EC2's `-1`) is treated as covering every port
     regardless of the declared range, because that is what it grants.

3. **Give the action in whatever dialect your firewall uses — but a real one.**
   - `ALLOW`/`ACCEPT`/`PERMIT` and `DENY`/`DROP`/`REJECT`/`BLOCK` are all
     understood. An unrecognised token raises rather than being skipped:
     previously anything that was not the literal string `ALLOW` was silently
     ignored, so an iptables rule spelled `ACCEPT` passed through the audit
     unexamined.
   - Note that AWS security groups have no deny at all — "You can specify allow
     rules, but not deny rules." A `DENY` in a security-group export is a
     modelling error worth chasing down.

4. **Treat an unresolvable rule as a failure, not a skip.**
   - A rule naming a subnet that was not registered raises
     `SegmentationInputError`. The old behaviour was to `continue` past it,
     which meant a topology with one mistyped subnet id returned `COMPLIANT`
     having never examined the rule in question.
   - Catch this as a **failed** gate. An unevaluable topology is not a compliant
     topology.

5. **Read the multi-hop findings, not just the direct ones.**
   - `TRANSITIVE_PATH_FROM_UNTRUSTED_TO_CRITICAL_ZONE` reports paths of two or
     more hops from `PUBLIC_DMZ`/`DEV_MANAGEMENT` into `TRADING_EXECUTION`/
     `KEY_CUSTODY`. DMZ → strategy engine → custody breaches no single rule, yet
     it is a routed path from the internet edge to the signing keys.
   - This is a **routing** claim, not an authentication one. If the middle hop
     genuinely is a policy enforcement point that authenticates every request,
     the path may be fine — that is why the finding is `HIGH` and its
     remediation says "prove this hop is a PEP." If you have established that,
     construct with `detect_transitive_paths=False` and record *why*.

6. **Branch on `report.violation_codes`, never on description text.**
   - `report.is_compliant` is the gate. `report.violations_found` is ordered
     most-severe first, each finding carrying a stable `code`, a `severity`, and
     a `remediation`. Print all of them — a gate that reveals one finding per
     run burns a change window per fix.
   - `description` wording may change between versions; `code` will not.

7. **Re-run against the live export on a schedule, not just in CI.**
   - CI audits the plan. Only an audit of exported live state catches the
     console change made during an incident at 02:00 and never reverted. Wire
     the six-month floor from Art. 13(2) as an outer bound, not a target.

> Full step-by-step procedure and CI wiring: see `references/workflows.md`.
> Control-by-control sources: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A mistyped zone tier that silently disables the checks.** `PUBLIC-DMZ`,
  `DMZ`, and `public dmz` all used to audit clean against the direct-ingress and
  admin-port controls. Worse, the custody control *did* still fire on them, so
  the tool looked like it was working. Tiers are now a closed set for exactly
  this reason.
- **Auditing a single port when the rule opens a range.** `0-65535` contains
  SSH. A membership test against `{22, 3389}` does not see it. This is the
  detection bypass most likely to matter in a real account.
- **Hiding a wide grant behind `protocol: ALL` and a tidy port number.**
  `protocol="ALL", port=443` grants all 65,536 ports; the `443` is decorative.
- **The `0.0.0.0/0` rule added to unblock a deploy and never removed.** AWS
  warns that with `0.0.0.0/0` "this enables anyone to access your instances from
  any IP address using the specified protocol." The audit flags a wildcard
  source into a critical tier *even when the subnet is labelled as trusted* —
  the CIDR outranks the tag.
- **Believing tier separation means the tiers cannot reach each other.** Direct
  edges are the easy case. Check the transitive findings before concluding the
  custody zone is isolated.
- **Reading `COMPLIANT` from an audit that raised on half your rules.** Treat
  `SegmentationInputError` as a failure. It means the topology was not audited,
  not that it passed.
- **Assuming a DENY somewhere makes the ALLOW safe.** True for ordered ACLs,
  false for security groups, and this engine models the unordered case. If your
  policy is order-sensitive, resolve shadowing before the audit, not after.
- **Putting the corporate VPN one hop from the FIX gateway.** DORA RTS
  Art. 13(1)(c) calls for "the use of a separate and dedicated network for the
  administration of ICT assets" — administrative access belongs behind a bastion
  or session broker, which is why `DEV_MANAGEMENT` counts as untrusted here.
- **Treating this audit as evidence of Zero Trust.** It is evidence of
  segmentation. Tenet 2 of NIST SP 800-207: "Network location alone does not
  imply trust."

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/network-segmentation-for-trading-infrastructure/scripts`
  — all tests must pass.
- Audit a properly segmented topology (strategy engine reaching execution and
  custody; DMZ reaching neither) and confirm `status == "COMPLIANT"` with
  `violations_found` empty.
- Add `FirewallRule("R", "<dmz>", "<vault>", "TCP", 22, "ALLOW")` and confirm
  **both** `DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE` and
  `CUSTODY_INGRESS_FROM_UNAUTHORIZED_TIER` appear from the one rule, each once.
- Replace it with a `0-65535` range from the DMZ and confirm
  `ADMIN_PORT_REACHABLE_FROM_PUBLIC_DMZ` fires and the description names port 22
  — this is the case a membership test misses.
- Build DMZ → strategy → custody with two individually-legal rules and confirm
  `TRANSITIVE_PATH_FROM_UNTRUSTED_TO_CRITICAL_ZONE`, then confirm
  `detect_transitive_paths=False` restores `COMPLIANT`.
- Point a rule at an unregistered subnet id and confirm `SegmentationInputError`
  is raised rather than a `COMPLIANT` report returned.
- Tag a `0.0.0.0/0` subnet as `STRATEGY_ENGINE`, point it at custody, and confirm
  `INTERNET_WILDCARD_SOURCE_INTO_CRITICAL_ZONE` still fires.
- Against your real estate: export live security groups, audit them, then
  compare the finding count to the audit of your Terraform plan. A difference is
  console drift, and it is the thing this skill exists to surface.

## Related Skills

- `multi-party-computation-mpc-custody-solutions`
- `api-key-least-privilege-audit-tool`
- `centralized-secrets-management-vault-integration`
- `infrastructure-as-code-for-trading-hosts`
- `configuration-drift-detection-across-environments`
- `singapore-mas-notice-on-cyber-hygiene-for-trading-systems`
- `log-aggregation-and-centralized-observability`
