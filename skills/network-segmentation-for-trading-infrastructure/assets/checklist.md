# Pre-Flight Checklist — Network Segmentation for Trading Infrastructure

Sign off before a topology carrying order-sending or key-holding hosts goes live,
and again at every recurring review.

## Inventory integrity

- [ ] Subnet list was **exported** from the source of record (Terraform state,
      `aws ec2 describe-subnets`, appliance config), not hand-transcribed.
- [ ] Every subnet carries exactly one of the five tiers: `PUBLIC_DMZ`,
      `DEV_MANAGEMENT`, `STRATEGY_ENGINE`, `TRADING_EXECUTION`, `KEY_CUSTODY`.
- [ ] An untagged or unrecognised subnet **fails the extraction step** — it is
      not defaulted into any tier.
- [ ] No `subnet_id` appears twice.
- [ ] Firewall rules were exported from the same source of record, in the same run.
- [ ] Security-group *references* (`UserIdGroupPairs`) were resolved to subnets,
      not dropped.
- [ ] Port ranges were carried through as `FromPort`/`ToPort` — no range was
      collapsed to its low end.
- [ ] `IpProtocol: "-1"` rules were passed through as `ALL`, not as a single port.
- [ ] If the policy is an ordered ACL (NACL / iptables / Cisco), it was flattened
      to its effective allow set **before** auditing — this engine does not model
      rule precedence.

## Audit execution

- [ ] The audit ran to completion. A `SegmentationInputError` was treated as a
      **failure**, never as a pass.
- [ ] Every finding was printed, not just the first.
- [ ] Decisions branch on `violation_codes`, not on `description` text.

## Zone isolation

- [ ] No direct `ALLOW` from `PUBLIC_DMZ` or `DEV_MANAGEMENT` into
      `TRADING_EXECUTION` or `KEY_CUSTODY`.
- [ ] `KEY_CUSTODY` ingress is restricted to the documented signing requesters.
- [ ] Administrative access reaches production through a bastion or session
      broker on a dedicated administration network — not from a developer subnet
      directly (DORA RTS Art. 13(1)(c)).
- [ ] Corporate VPN termination does **not** hold a route to a broker FIX
      endpoint.
- [ ] Every multi-hop finding was reviewed. For each one accepted, the middle hop
      is a genuine policy enforcement point (authenticates and authorises each
      request), and that reasoning is recorded in code or the change record.
- [ ] `detect_transitive_paths=False` appears nowhere without an adjacent comment
      naming the reviewed PEP and the review date.

## Port and source hygiene

- [ ] Ports 22 and 3389 are not reachable from `PUBLIC_DMZ` (AWS FSBP EC2.13 /
      EC2.14), including via any range that *contains* them.
- [ ] Telnet (23) and FTP (21) are not reachable anywhere in the estate.
- [ ] No `0.0.0.0/0` or `::/0` source reaches a `TRADING_EXECUTION` or
      `KEY_CUSTODY` zone — including any subnet labelled as trusted that happens
      to carry a wildcard CIDR.
- [ ] Every port range into a critical zone is justified by a named service. No
      range was widened "temporarily" and left.
- [ ] Any `DENY` in a security-group export was investigated — AWS security
      groups have no deny, so it indicates a modelling error.

## Cadence and ownership

- [ ] The audit runs in CI against the plan, before apply.
- [ ] The audit also runs on a schedule against **exported live state** — the
      only run that catches a console change made during an incident.
- [ ] Live-state findings were diffed against plan findings; any difference was
      investigated as console drift.
- [ ] Review interval does not exceed **6 months** for systems supporting
      critical or important functions (DORA RTS Art. 13(2), applicable to EU
      financial entities since 17 January 2025).
- [ ] The review has a **named owner** who signed off, not just a green job
      (Art. 13(1)(h)).

## Scope acknowledgement

- [ ] Reviewers understand this audit covers **ingress reachability only** —
      egress, exfiltration, and C2 paths are out of scope.
- [ ] Reviewers understand a `COMPLIANT` result evidences **segmentation**, not
      Zero Trust: per NIST SP 800-207 tenet 2, "Network location alone does not
      imply trust." Authentication and authorisation are audited elsewhere.
- [ ] Where Reg SCI was cited, the entity actually meets the definition of an
      "SCI entity" — it does not apply to an ordinary broker-dealer or a
      proprietary trading firm.

---

**Topology reviewed:** ______________________  **Date:** ____________

**Auditor:** ______________________  **Accepted findings (codes + rationale):** ____________
