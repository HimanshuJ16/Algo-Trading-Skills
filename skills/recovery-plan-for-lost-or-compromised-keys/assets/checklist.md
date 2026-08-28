# Pre-Flight Checklist

## Backup integrity (key-loss path)
- [ ] Is the backup method one of Shamir SSS / HSM seed / mnemonic — and written down as such?
- [ ] For Shamir: are verified shards $\ge$ threshold + the surplus your policy requires (default 1)?
- [ ] Are "verified" shards ones actually read in this drill cycle, not merely believed to exist?
- [ ] Is the threshold at least 2, so no single shard is a full key?
- [ ] Is `max_shards_at_single_location` strictly **below** the threshold, so no one location can reconstruct the key alone?
- [ ] Is backup material held in at least 2 geographically distinct locations (CCSS 1.03.3.2)?

## Sweep readiness (key-compromise path)
- [ ] Is an emergency sweep destination pre-configured, before any incident?
- [ ] Is that destination's key material **independent** of the key being protected — different seed, different device, different keyholders?
- [ ] Has a real test transaction confirmed to it, on the right chain and the right address?

## Incident response substance
- [ ] Is there a documented inventory of all cryptographic keys (NIST SP 800-57 Pt.1 R5 §5.5.2(d))?
- [ ] Are at least 2 incident response contacts named, so notification and recovery do not depend on one person?
- [ ] Is there an out-of-band channel to reach them that does not rely on the compromised system?

## Drill
- [ ] Has a full recovery drill ever been conducted — and is `last_drill_date` an actual date rather than a sentinel?
- [ ] Was it within the policy window (default 90 days; CCSS Level III floor is annual)?
- [ ] Did the drill compare the reconstructed address against the recorded one, not just "the shards combined"?

## Before trusting the report
- [ ] Was `as_of_date` passed explicitly, so the audit is reproducible?
- [ ] Are all `plan_id`s unique, and does the batch cover every wallet whose keys you hold?
- [ ] Are the configured thresholds recorded as *your* policy, not cited as a regulatory requirement?
