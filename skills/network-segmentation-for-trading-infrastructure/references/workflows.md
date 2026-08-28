# Workflows for Network Segmentation of Trading Infrastructure

Deep procedure for building the inventory, running the audit, and wiring it into
CI and the recurring review. See `standards.md` for the source behind each
control.

## 1. Build the subnet inventory from the source of record

Never hand-transcribe. The audit is a claim about the topology you feed it, so a
transcription gap is a silent hole in the result.

```bash
# AWS: subnets with their zone tag
aws ec2 describe-subnets \
  --query 'Subnets[].{id:SubnetId,cidr:CidrBlock,tier:Tags[?Key==`ZoneTier`]|[0].Value,name:Tags[?Key==`Name`]|[0].Value}' \
  --output json > subnets.json
```

Tag discipline is the prerequisite for everything downstream. A subnet with no
`ZoneTier` tag must fail the pipeline at extraction time — do not default it to a
trusted tier, and do not default it to `PUBLIC_DMZ` either, which produces
plausible-looking findings on the wrong subnet.

Map each subnet to exactly one of the five tiers:

| Tier | What belongs there |
|---|---|
| `PUBLIC_DMZ` | Internet-facing load balancers, public web/API frontends, NAT egress |
| `DEV_MANAGEMENT` | Jump hosts, bastions, CI runners, corporate VPN termination |
| `STRATEGY_ENGINE` | Signal generation, research hosts, model serving, backtest workers |
| `TRADING_EXECUTION` | Order gateways, FIX sessions, broker connectivity, risk-check layer |
| `KEY_CUSTODY` | HSMs, MPC signer nodes, anything holding private key material |

If a host does not fit exactly one tier, that is a finding about the host, not a
reason to invent a tier. Split it.

## 2. Extract the effective rule set

```bash
# AWS security groups -> flat rule rows
aws ec2 describe-security-groups \
  --query 'SecurityGroups[].{sg:GroupId,rules:IpPermissions}' \
  --output json > sgs.json
```

Three transformations matter when converting these into `FirewallRule` objects:

- **`FromPort`/`ToPort` map to `port`/`to_port`.** Do not collapse a range to its
  low end. Collapsing `0-65535` to `0` is exactly the bug the engine was fixed to
  catch.
- **`IpProtocol: "-1"` means all protocols and all ports.** Pass `"-1"` through;
  the engine treats it as covering `0-65535` regardless of any declared range.
- **Security-group *references* (`UserIdGroupPairs`) are not CIDRs.** Resolve the
  referenced group to the subnets its instances sit in before building an edge,
  or the topology graph will be missing exactly the internal paths the
  transitive check exists to find.

For network ACLs, iptables, or Cisco ACLs, flatten the ordered policy into its
*effective* allow set first — see the semantics table in `standards.md`. This
engine does not model rule precedence.

## 3. Run the audit

```python
from network_segmentation_auditor import (
    NetworkSegmentationAuditorEngine, NetworkSubnet, FirewallRule,
    SegmentationInputError,
)

engine = NetworkSegmentationAuditorEngine()

try:
    report = engine.audit_segmentation(subnets, rules)
except SegmentationInputError as exc:
    # Unevaluable topology. This is a FAILURE, not a pass.
    raise SystemExit(f"Segmentation audit could not run: {exc}")

if not report.is_compliant:
    for v in report.violations_found:          # already most-severe first
        print(f"[{v.severity}] {v.code} ({v.rule_id})")
        print(f"    {v.description}")
        print(f"    -> {v.remediation}")
    raise SystemExit(1)
```

Print every finding. A gate that surfaces one violation per run costs a change
window per fix.

## 4. Triage the findings

| Code | Severity | First question to ask |
|---|---|---|
| `DIRECT_UNTRUSTED_INGRESS_TO_CRITICAL_ZONE` | CRITICAL | Why does this path exist at all? Route it via a bastion. |
| `CUSTODY_INGRESS_FROM_UNAUTHORIZED_TIER` | CRITICAL | Which service needs this? If legitimate, widen `custody_authorized_tiers` deliberately and record why. |
| `INTERNET_WILDCARD_SOURCE_INTO_CRITICAL_ZONE` | CRITICAL | Almost always a troubleshooting rule left behind. Delete it. |
| `ADMIN_PORT_REACHABLE_FROM_PUBLIC_DMZ` | HIGH | Narrow the range, or move admin access to the dedicated admin network. |
| `WIDE_PORT_RANGE_INTO_CRITICAL_ZONE` | HIGH | Which ports does the service actually need? Usually one or two. |
| `TRANSITIVE_PATH_FROM_UNTRUSTED_TO_CRITICAL_ZONE` | HIGH | Is the middle hop a real policy enforcement point, or does it just forward? |

The transitive finding is the one most likely to be a *considered* accept. A DMZ
web tier calling a strategy API that calls a signer is a normal three-tier
design — provided the strategy API authenticates and authorises each request
rather than proxying it. If you have established that, disable the check
explicitly and leave the reasoning in the code:

```python
# The strategy API is a PEP: mTLS + per-request authz, reviewed 2026-08-27.
engine = NetworkSegmentationAuditorEngine(detect_transitive_paths=False)
```

Do not silence it by deleting the subnet from the inventory.

## 5. Wire it into CI

Run against the **plan**, before apply:

```yaml
- name: Segmentation audit
  run: |
    terraform show -json tfplan > plan.json
    python tools/topology_from_plan.py plan.json > topology.json
    python scripts/audit_topology.py topology.json   # exits non-zero on findings
```

The plan catches what the change introduces. It does not catch what is already
there, and it does not catch console changes.

## 6. Run it against live state on a schedule

This is the run that catches the security group someone widened at 02:00 during
an incident and never reverted.

```yaml
on:
  schedule:
    - cron: "0 6 * * 1"   # weekly; the regulatory floor is 6 months
```

For EU financial entities, Art. 13(2) of the DORA RTS requires verifying the
adequacy of firewall rules and connection filters **at least every 6 months** for
ICT systems supporting critical or important functions. Treat six months as the
outer bound you must never exceed, not the cadence to run at. Art. 13(1)(h)
requires a named owner for the review — record who signed off, not just that the
job passed.

A useful signal beyond pass/fail: diff the live audit against the plan audit. Any
difference is console drift.

## 7. Remediate in the right order

1. `INTERNET_WILDCARD_SOURCE_*` — delete the rule. Almost never legitimate.
2. `CUSTODY_INGRESS_*` — smallest blast radius per fix, highest value.
3. `DIRECT_UNTRUSTED_INGRESS_*` — usually needs a bastion introduced first.
4. `WIDE_PORT_RANGE_*` / `ADMIN_PORT_*` — narrow ranges; needs the service owner
   to confirm which ports are genuinely used.
5. `TRANSITIVE_PATH_*` — architectural. Either prove the middle hop is a PEP, or
   break the path.

Re-run after each change. Fixing one rule can reveal a transitive path that was
previously masked by a direct finding on the same pair.

## 8. What this workflow does not cover

- **Egress.** The engine audits who can reach a zone, not what a compromised host
  in that zone can reach outbound. Exfiltration and C2 paths need their own
  egress policy review.
- **Host-level controls.** Segment isolation says nothing about what runs on the
  host. See `immutable-infrastructure-for-trading-bots`.
- **Identity.** See `api-key-least-privilege-audit-tool` and
  `multi-party-computation-mpc-custody-solutions`. Per NIST SP 800-207 tenet 2,
  "Network location alone does not imply trust" — segmentation is a
  precondition for a ZTA, not a substitute for authentication.
