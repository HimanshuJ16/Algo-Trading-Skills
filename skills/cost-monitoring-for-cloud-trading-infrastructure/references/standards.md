# Standards for Cloud Cost Monitoring

| Metric | Engineering Standard |
|---|---|
| Z-Score Threshold | Cost spikes exceeding $Z \ge 3.0$ (with > 30% mean increase) MUST trigger immediate FinOps alerts. |
| Flat-Baseline Materiality | On a flat baseline ($\sigma \approx 0$) the Z-score is denominated in dollars; escalation MUST additionally require a relative-change floor (`flat_baseline_min_pct_change`, default 1%) so scale alone cannot generate pages. |
| Tagging Compliance | 100% of cloud resources MUST be tagged with `Environment`, `Service`, and `StrategyID`; baselines MUST be scoped to a single (service, environment) pair. |
| Unit Cost Primacy | Spend evaluation MUST track unit economics (Cost per trade — scale ×10,000 for a per-10k-trades view) alongside raw dollar totals. Zero trades with positive spend MUST report an unbounded unit cost, never \$0.00. |

## Cross-AZ Egress Pricing — verified claim

The "hidden tax" cited in `SKILL.md` is AWS inter-Availability-Zone data
transfer, billed **per direction**, so one GB moved between AZs in the same
Region bills at both ends.

| Claim | Source |
|---|---|
| "Data transferred 'in' to and 'out' from Amazon EC2, Amazon RDS, Amazon Redshift, Amazon DynamoDB Accelerator (DAX), and Amazon ElastiCache instances, Elastic Network Interfaces or VPC Peering connections across Availability Zones in the same AWS Region is charged at \$0.01/GB in each direction." | AWS EC2 On-Demand Pricing / AWS Networking & Content Delivery Blog — https://aws.amazon.com/blogs/networking-and-content-delivery/optimizing-data-transfer-costs-when-using-aws-network-load-balancer/ |
| Same \$0.01/GB-each-direction rate stated on a first-party service pricing page ("traffic forwarded between brokers across availability zones in the same region") | Amazon MQ Pricing — https://aws.amazon.com/amazon-mq/pricing/ |

> **Scope note.** The rate above is the standard commercial-Region rate as
> published by AWS and verified in August 2026. Per-service exceptions exist
> (traffic within a single AZ between the listed services is free), GovCloud and
> China Regions price separately, and GCP/Azure inter-zone rates differ. Confirm
> against your own provider's current pricing page before hard-coding any rate.