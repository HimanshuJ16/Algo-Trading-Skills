# Pre-Flight Checklist

- [ ] Is the $M$-of-$N$ threshold scheme properly configured ($2 \le M \le N$)?
- [ ] Are all $N$ shard ids distinct and within $[1, N]$ (no duplicated shard placed twice)?
- [ ] Has it been verified that no single country holds $\ge M$ shards?
- [ ] Has it been verified that no single legal jurisdiction holds $\ge M$ shards, including regimes that reach vaults in more than one country?
- [ ] Has it been verified that no single custodian provider holds $\ge M$ shards, counting subsidiaries of one parent as one provider?
- [ ] Has it been verified that no single country, jurisdiction or provider holds $> (N - M)$ shards, so that losing it cannot leave fewer than $M$ shards reachable?
- [ ] Is the redundancy reserve $(N - M)$ at least `min_redundancy_gap` (default 2)?
- [ ] Is each facility's certification matched to the threat it must resist (EN 1143-1 burglary, EN 1047-1 fire, ISO 27001 / SOC 2 Type II operator controls)?
- [ ] Are emergency recovery SLAs and multi-person authorization quorums documented?
- [ ] Has a shard-recovery rehearsal confirmed that $M$ shards can actually be retrieved within the recovery SLA?
