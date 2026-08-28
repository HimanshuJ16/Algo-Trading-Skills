# Pre-Flight Checklist

## Deployment facts resolved (no defaults, no guesses)

- [ ] Cloud region read from an environment variable that is actually set — not a fallback default.
- [ ] Region resolved to a jurisdiction through an explicit map, not the region-name prefix (`eu-west-2` = London, `eu-central-2` = Zurich).
- [ ] Egress IP posture classified: dedicated static, shared static, dynamic, or unknown.
- [ ] Recorded whether the process places orders or is read-only.
- [ ] Deployer role recorded: client, regulated entity, or vendor to a regulated entity.

## Broker access control (the constraint that is actually enforced)

- [ ] For SEBI-regulated brokers placing orders: a static IP is registered with the broker for the API key in use.
- [ ] The registered address is dedicated to this client's order flow, not a NAT gateway shared with unrelated workloads.
- [ ] Serverless / autoscaled components are behind a NAT gateway or reserved address so the source IP cannot rotate on a scale event.
- [ ] Secondary static-IP slot reserved before any planned region or egress migration (changes are limited to one per calendar week).
- [ ] Read-only jobs are not gated on the order-path static-IP control.

## Residency position (documented, not assumed)

- [ ] No hosting-region "violation" is asserted without naming an in-force instrument that binds *this* deployer.
- [ ] If the deployer is a regulated entity or its vendor: outsourcing / ICT third-party contract terms, location clauses, recordkeeping-location duties and notification obligations reviewed with compliance.
- [ ] SEBI CSCRF PR.DS.S2 abeyance status re-checked (abeyance is not repeal).
- [ ] Storage residency for trade records assessed separately (`data-localization-requirements-for-trade-records`).

## Gate behaviour

- [ ] Deployment gate fails closed: unknown broker, unresolved region, and unknown egress posture all block promotion.
- [ ] Gate runs at startup and on configuration change, not only at first deploy.
- [ ] Latency-preference findings are reported as advisory and never as compliance violations.
- [ ] Decisions with their findings and citations are retained in the audit trail.
