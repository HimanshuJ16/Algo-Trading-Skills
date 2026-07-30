# Standards for Database Backup and Point-In-Time Restore Testing

| Metric | Engineering Standard |
|---|---|
| Maximum Target RPO | Recovery Point Objective MUST NOT exceed 60 seconds of data loss ($\text{RPO} \le 60\text{s}$). |
| Maximum Target RTO | Recovery Time Objective MUST NOT exceed 15 minutes ($\text{RTO} \le 15\text{m}$). |
| Automated PITR Drill | Automated PITR restoration drills MUST be executed weekly on isolated staging environments. |