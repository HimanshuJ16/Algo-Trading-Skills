---
name: regional-broker-data-residency-constraints
description: >-
  Use when choosing where to run a process that connects to a broker in a specific
  national market. Separates enforced controls such as SEBI static-IP whitelisting from
  hosting-region mandates that do not actually bind the deploying entity.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: data-residency, cloud-region, static-ip, sebi-algo-circular, gdpr, sec-17a-4, broker-compliance, aws, gcp
  brokers_frameworks: "SEBI Circular CIR/2025/0000013 (retail algo / static IP); Zerodha Kite Connect; Upstox Developer API; EU GDPR Chapter V / MiFID II / DORA; SEC Rules 17a-4 & 17a-7; AWS Regions; GCP Regions"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when choosing where to run an algorithmic trading process that connects to a broker in a specific national market, and you need to know which of the constraints on that choice are real. It answers two separable questions: **will the broker accept order flow from this deployment's network egress**, and **does any residency rule actually bind the entity doing the deploying**. The first has a hard, in-force answer for Indian brokers — SEBI's 4 Feb 2025 retail-algo circular requires API order traffic to originate from a static IP whitelisted with the broker, phased in to all stock brokers by 1 Apr 2026. The second, for a client trading its own account, is usually "no": SEBI CSCRF's data-localisation standard PR.DS.S2 has been in abeyance since 31 Dec 2024, GDPR contains no localisation requirement, and SEC Rule 17a-4 imposes no hosting-region mandate.

## When NOT to Use

- **As a storage-residency decision.** Where trade records may *rest* is a different question with different regimes attached — use `data-localization-requirements-for-trade-records`. For egress and PII minimization use `cross-border-data-transfer-restrictions-for-trade-data`.
- **As the compliance analysis for a regulated entity.** If the deployer is itself a broker, a SEBI regulated entity, an EU financial entity under DORA, or a registered broker-dealer, the obligations are contract- and document-dependent (outsourcing terms, ICT third-party register, recordkeeping location undertakings). The engine returns `REVIEW_REQUIRED` for those roles by design; it is a prompt for compliance, not a verdict.
- **As a substitute for the broker's own configuration.** Registering a static IP is an action taken in the broker's developer console (Zerodha: developer-account level, up to two IPs, one change per calendar week; Upstox: one primary plus one secondary, once per calendar week). This engine checks posture; it does not register anything.

## Prerequisites

- The deployment's **egress** IP posture, not just its region: `STATIC_DEDICATED`, `STATIC_SHARED` (e.g. a NAT gateway shared with unrelated workloads), `DYNAMIC`, or `UNKNOWN`.
- Whether the process **places orders** or is read-only — the static-IP requirement attaches to order requests only.
- The **deployer role**: `CLIENT` (trading its own account through the broker), `REGULATED_ENTITY`, or `RE_VENDOR`.
- The cloud region string (`AWS_REGION`, `GCP_REGION`, or `TRADING_HOST_REGION`); `probe_current_region()` reads these and returns `None` rather than defaulting.

## Workflow

1. **Resolve the deployment**, don't assume it. `probe_current_region()` returns `None` when no region variable is set; an unresolved region yields `REVIEW_REQUIRED`, never an approval. Region names do not identify jurisdictions — `eu-west-2` is London and `eu-central-2` is Zurich, both outside the EEA — so the region is resolved through an explicit map and an unmapped region also escalates.
2. **Check the enforced access control first.** For a broker with `requires_static_order_ip`, an order-placing deployment on a `DYNAMIC` egress address is `BLOCKED`: the broker will reject the order requests regardless of what any residency analysis says. A `STATIC_SHARED` address is `REVIEW_REQUIRED`, not approved — Zerodha permits sharing a registered IP only with immediate family, and sharing beyond that risks suspension of the API key. `UNKNOWN` egress is likewise `REVIEW_REQUIRED`.
3. **Apply the read-only carve-out deliberately.** With `places_orders=False`, the static-IP requirement does not attach: market data, WebSocket, order book and position endpoints remain reachable from any address. Do not gate a data-only backfill job on an order-path control.
4. **Assess residency by deployer role, not by broker jurisdiction.** `CLIENT` against the encoded brokers produces an advisory recording that no in-force mandate binds client-side hosting. `REGULATED_ENTITY` and `RE_VENDOR` produce `REVIEW_REQUIRED` with the jurisdiction's live obligations named — that escalation is the correct output, not a gap.
5. **Keep latency preference out of the compliance verdict.** Hosting a Zerodha bot in `us-east-1` is an `ADVISORY` finding about round-trip latency to the exchange, not a violation. Reporting it as a violation is how an agent learns a rule that does not exist.
6. **Gate on the decision, not on a boolean.** `assert_deployable()` raises `BrokerDeploymentConstraintError` unless the status is `COMPLIANT`; every non-compliant status carries `is_deployable=False`, so a caller that gates on that flag fails closed. Decisions are appended to `engine.audit_trail` as frozen records.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Asserting a SEBI cloud-region mandate that is not in force.** Blocking `us-east-1` for a Zerodha bot as a "SEBI data localisation violation" is regulatory misinformation: CSCRF PR.DS.S2 is in abeyance, the RBI localisation circular covers payment system data rather than client-side algo hosting, and Zerodha has stated the registered order IP need not be India-based. Fabricating the rule also hides the real one — the static IP.
- **Treating GDPR as a localisation law.** GDPR regulates the cross-border *transfer* (Chapter V: adequacy, SCCs, derogations); it mandates no EU hosting region. MiFID II Art. 16(6) requires records to be retained and made available to the competent authority, not stored in the EU.
- **Reading SEC Rule 17a-4 as a residency rule.** It sets retention and prompt-production duties. The US-location duty lives in Rule 17a-7 and addresses *non-resident registered broker-dealers* — with a written-undertaking exception — not their clients.
- **Confusing "static" with "yours".** A shared NAT gateway address is static and still non-compliant in substance: the whitelisted IP is meant to identify one client's order flow. Two unrelated traders behind one address defeat the traceability the rule exists for.
- **Forgetting that serverless and autoscaling change the egress address.** Lambda, Cloud Run, and autoscaled instances without a NAT gateway or reserved address will silently rotate the source IP; orders start rejecting on the first scale event, not at deploy time.
- **Burning the IP-change budget.** Both Zerodha and Upstox limit static-IP changes to once per calendar week. A blue/green cutover that moves the egress address is therefore a scheduled, rate-limited operation — plan the secondary IP slot before the migration, not during the incident.
- **Fail-open defaults.** The pre-2.0 version of this skill returned "compliant" for any unregistered broker and fell back to `ap-south-1` when no region variable was set — two ways an unconfigured process passed the gate. Unknown must mean unresolved.
- **Assuming an official API exists.** DEGIRO publishes no official public trading API; a deployment plan built on an unofficial client carries risks this engine does not model. See `degiro-unofficial-api-risk-assessment`.

## Verification

- Instantiate `BrokerDeploymentConstraintEngine`. Evaluate `DeploymentProfile(broker="zerodha", cloud_region="ap-south-1", egress_ip_type=EGRESS_STATIC_DEDICATED)` and verify `status="COMPLIANT"`.
- Change `egress_ip_type` to `EGRESS_DYNAMIC` and verify `status="BLOCKED"` with a `DYNAMIC_EGRESS_IP` finding; set `places_orders=False` on the same profile and verify it returns to `COMPLIANT`.
- Evaluate the same broker in `us-east-1` with a dedicated static IP and verify `status="COMPLIANT"` with only a `REGION_NOT_LATENCY_PREFERRED` advisory — no blocking finding.
- Evaluate an unregistered broker, a `None` region, a blank region string, and `"xx-nowhere-9"` and verify all four return `REVIEW_REQUIRED` with `is_deployable=False`.
- Evaluate `broker="degiro", cloud_region="eu-west-2"` and verify `region_jurisdiction="UK"`, not `"EU"`.
- Evaluate with `deployer_role=ROLE_REGULATED_ENTITY` and verify `REVIEW_REQUIRED` with a `REGULATED_DEPLOYER_REVIEW` finding.
- With no region environment variables set, verify `probe_current_region()` returns `None`.
- Run `python -m unittest discover -s skills/regional-broker-data-residency-constraints/scripts`.

## Related Skills

- `data-localization-requirements-for-trade-records`
- `cross-border-data-transfer-restrictions-for-trade-data`
- `india-sebi-algo-trading-tagging-requirements`
- `degiro-unofficial-api-risk-assessment`
