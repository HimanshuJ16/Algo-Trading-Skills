# Pre-Flight Checklist

- [ ] Are WAL continuous logs streamed to off-site durable storage?
- [ ] Is PITR restoration tested against a specific target timestamp $T_{\text{target}}$?
- [ ] Are RPO ($\le 60\text{s}$) and RTO ($\le 15\text{m}$) SLAs verified?
- [ ] Is row count and checksum parity validated post-restoration?