# Standards for Disaster Recovery Runbook for Full Region Outage

## 1. Vendor behaviour the interlocks are built on

| Behaviour | Documented wording | Source |
|---|---|---|
| Write fencing is best-effort | "Because fencing writes is a best-effort attempt, it's possible that writes might be momentarily accepted in the old primary Region, causing split-brain issues." Aurora emits one RDS event when writes were stopped and a different one when the process timed out. | Aurora Global Database — switchover/failover |
| Failover accepts data loss; switchover does not | Failover "RPO ... is typically a non-zero value measured in seconds"; switchover "synchronizes secondary DB clusters with the primary before making any other changes, RPO is 0". The CLI requires `--allow-data-loss` to "Explicitly make this a failover operation instead of a switchover". | Aurora Global Database — switchover/failover |
| Take applications offline before failing over | "To prevent writes from being sent to the primary cluster of Aurora Global Database, take applications offline." Also: choose the secondary "with the least replication lag". | Aurora Global Database — switchover/failover |
| Aurora RTO/RPO magnitudes | "For Aurora Global Database, RTO can be in the order of minutes"; "RPO is typically measured in seconds". | Aurora Global Database — BCDR planning |
| `rds.global_db_rpo` has a 20-second floor | "Valid values for `rds.global_db_rpo` range from 20 seconds to 2,147,483,647 seconds". In a two-Region global database AWS recommends leaving it at the default, since enforcement pauses transactions on the primary. | Aurora Global Database — managing RPOs |
| Low DNS TTLs for failover records | "Setting a TTL of 60 or 120 seconds is a common choice for this scenario." For the Aurora global writer endpoint AWS suggests reducing DNS cache TTL "to a low value such as 5 seconds" to reduce split-brain likelihood. | ARC routing control best practices; Aurora Global Database |
| DNS movement does not drain existing connections | "Clients with pre-existing open connections might continue to make requests against the impaired location until the clients reconnect." ALB HTTP client keepalive "default ... 3600 seconds, or 1 hour". | ARC routing control best practices |
| Use the data plane, not the console | "ARC offers extreme reliability with the API in the data plane to fail over traffic. We recommend using the API instead of changing routing control states in the AWS Management Console." Each ARC cluster "is a data plane of endpoints in five AWS Regions"; retry across all five. | ARC routing control / best practices |
| Keep DR credentials reachable offline | Create IAM long-lived credentials "specifically for DR tasks, and keep the credentials securely in an on-premises physical safe or a virtual vault"; bookmark the five cluster endpoints because "you might not be able to access some API operations" during a failure. | ARC routing control best practices |
| Cancel on Disconnect has documented holes | COD cancels resting futures and options orders for a disconnected registered iLink session, but "does not include GTC (Good Till Cancel) and GTD (Good Till Date) orders" and "is not invoked for a graceful disconnect". | CME Group — Cancel on Disconnect |

- <https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html>
- <https://docs.aws.amazon.com/r53recovery/latest/dg/routing-control.html>
- <https://docs.aws.amazon.com/r53recovery/latest/dg/route53-arc-best-practices.regional.html>
- <https://www.cmegroup.com/confluence/display/EPICSANDBOX/Cancel+on+Disconnect>

Venue behaviour differs. CME is cited because its COD semantics are publicly
documented; confirm the equivalent for every venue you route to rather than
generalising from this one.

## 2. Regulatory context — what is actually mandated, and to whom

No regulator prescribes a 300-second RTO for a trading firm's own
infrastructure. The obligations below are to *have, review, and test* continuity
arrangements; the numbers are yours to justify.

| Requirement | Scope / jurisdiction | Wording |
|---|---|---|
| Regulation SCI, 17 CFR 242.1001(a)(2)(v) | **SCI entities only** — SCI self-regulatory organizations, SCI alternative trading systems, plan processors, exempt clearing agencies. Not ordinary broker-dealers or proprietary trading firms. | Policies and procedures must include "Business continuity and disaster recovery plans that include maintaining backup and recovery capabilities sufficiently resilient and geographically diverse and that are reasonably designed to achieve next business day resumption of trading and two-hour resumption of critical SCI systems following a wide-scale disruption". |
| FINRA Rule 4370(a), (c)(1), (b) | US FINRA member broker-dealers | Members must "create and maintain a written business continuity plan"; it must address "Data back-up and recovery (hard copy and electronic)"; each member "must also conduct an annual review". |
| MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589), Art. 14 | EU investment firms engaged in algorithmic trading | Arrangements "appropriate to the nature, scale and complexity of its business" (14(1)), covering scenarios including "loss or alteration of critical data and documents" (14(2)); firms must "review and test its business continuity arrangements on an **annual** basis" (14(4)). |

Note the gap in magnitude: even the strictest of these — Reg SCI's two-hour
critical-system resumption — is 24× the 300-second target this module defaults
to, and it does not apply to most firms. Treat 300s as an internally chosen
ambition, never as a compliance floor you are meeting.

- <https://www.law.cornell.edu/cfr/text/17/242.1001>
- <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4370>
- <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589>

## 3. Internal engineering objectives (defaults in this module)

**Defaults, not standards.** Derive your own from a business impact analysis.

| Objective | Default | Rationale |
|---|---|---|
| RTO (incl. DNS TTL) | 300 s | Aims to restore execution within a single volatility episode. Achievable only with TTLs well below the budget and no manual console steps. |
| RPO (replication lag at promotion) | 15 s | Bounds unreplicated fills and balance updates. Monitorable via `AuroraGlobalDBRPOLag`; note it is below the 20 s floor of `rds.global_db_rpo`, so it cannot be *enforced* by that parameter. |
| Cancellation before resumption | Mandatory interlock | Not a timing target: resuming against an unconfirmed book risks duplicate live exposure in two regions. |
| Drill cadence | Quarterly, plus after any topology change | AWS: "test this after you set up ARC ... and continue to test periodically". Exceeds the annual floor in FINRA 4370(b) / RTS 6 Art. 14(4). |
