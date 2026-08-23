# Standards for Database Backup and Point-In-Time Restore Testing

## 1. Definitions (authoritative)

| Term | Definition | Source |
|---|---|---|
| Recovery Point Objective (RPO) | "The point in time to which data must be recovered after an outage." | NIST SP 800-34 Rev. 1, via NIST CSRC Glossary |
| Recovery Time Objective (RTO) | The maximum length of time a system's components may be in the recovery phase before the disruption unacceptably impacts business processes. | NIST SP 800-34 Rev. 1 |

A drill therefore measures the *shortfall* against the recovery point: the width
of the data-loss window between the desired target and the furthest point the
archive can actually reach. It is zero only when the archive covers the target.

- <https://csrc.nist.gov/glossary/term/recovery_point_objective>
- <https://csrc.nist.gov/glossary/term/recovery_time_objective>

## 2. PostgreSQL archive-recovery behaviour modelled by this skill

| Behaviour | Documented wording | Source |
|---|---|---|
| Inclusive recovery target | `recovery_target_inclusive` defaults to `on`: stop "just after the specified recovery target"; transactions with exactly the target commit time **are** included. | PostgreSQL docs, Recovery Target settings |
| Unreachable target is fatal | "If a recovery target is configured but the archive recovery ends before the target is reached, the server will shut down with a fatal error." | PostgreSQL docs, Recovery Target settings |
| Unbroken WAL chain required | "To recover successfully using continuous archiving ... you need a continuous sequence of archived WAL files that extends back at least as far as the start time of your backup." Corrupt WAL halts recovery: "recovery will halt at that point and the server will not start." | PostgreSQL docs, Continuous Archiving and PITR |
| Target must follow the base backup | "The stop point must be after the ending time of the base backup ... You cannot use a base backup to recover to a time when that backup was in progress." | PostgreSQL docs, Continuous Archiving and PITR |
| Test archiving before relying on it | "set up and test your procedure for archiving WAL files *before* you take your first base backup." | PostgreSQL docs, Continuous Archiving and PITR |

- <https://www.postgresql.org/docs/current/runtime-config-wal.html>
- <https://www.postgresql.org/docs/current/continuous-archiving.html>

## 3. Regulatory context — what is actually mandated

Nothing below prescribes a numeric RPO or RTO for a trading database. These are
obligations to *have, review, and test* continuity arrangements covering data
recovery; the numbers are yours to justify.

| Requirement | Scope / jurisdiction | Wording |
|---|---|---|
| FINRA Rule 4370(a), (c)(1), (b) | US FINRA member broker-dealers | Members must "create and maintain a written business continuity plan"; the plan must address "Data back-up and recovery (hard copy and electronic)"; each member "must also conduct an annual review of its business continuity plan". |
| Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6), Art. 14 | EU investment firms engaged in algorithmic trading | Art. 14(1): business continuity arrangements "appropriate to the nature, scale and complexity of its business"; Art. 14(2) covers scenarios including "loss or alteration of critical data and documents"; Art. 14(4): "review and test its business continuity arrangements on an **annual** basis". |

Applicability is entity- and jurisdiction-specific. A non-member proprietary
trading firm is not bound by FINRA 4370; a non-EU firm is not bound by RTS 6.
Confirm scope with compliance before citing either as a control's basis.

- <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4370>
- <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589>

## 4. Internal engineering objectives (defaults in this module)

These are **defaults, not standards**. They are the engine's constructor
defaults and are appropriate for a desk whose intraday order and fill records
are financially material. Set your own from a business impact analysis.

| Objective | Default | Rationale |
|---|---|---|
| Maximum RPO | 60 s | At streaming-archive cadence, more than a minute of lost fills makes broker reconciliation manual. |
| Maximum RTO | 15 min | Aims to restore inside a trading session rather than across one. |
| Drill cadence | Weekly, on an isolated staging instance | Exceeds the annual review floor of FINRA 4370(b) / RTS 6 Art. 14(4); weekly is chosen so an archive that stops advancing is caught within one week, not one year. |
