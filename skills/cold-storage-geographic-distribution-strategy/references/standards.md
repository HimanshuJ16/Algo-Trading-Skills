# Standards for Cold Storage Distribution

These are internal engineering standards enforced by this module, not legal mandates.
No regulator surveyed prescribes a geographic distribution for key shards; jurisdictional
custody rules constrain *who* may hold customer assets and how they are segregated, not
*where* shards physically sit.

| Metric | Engineering Standard |
|---|---|
| Confidentiality Threshold | No single country, legal jurisdiction or provider MUST hold $\ge M$ shards in an $M$-of-$N$ scheme, since that group could reconstruct the key alone. |
| Availability Threshold | No single country, legal jurisdiction or provider MUST hold $> (N - M)$ shards, since losing that group alone would leave fewer than $M$ shards and permanently destroy access. |
| Redundancy Reserve | The threshold gap $(N - M)$ SHOULD be $\ge 2$ (module default `min_redundancy_gap=2`) so ordinary shard loss is survivable. This is a configurable engineering default, not a published figure. |
| Distinct Shards | All $N$ placements MUST carry distinct shard ids in $[1, N]$. Duplicated shards increase exposure without increasing reconstruction margin. |
| Physical Security | Facility certification MUST be evidenced, and matched to the threat: EN 1143-1 for burglary resistance of safes/strongrooms, EN 1047-1 for fire protection of data media (no burglary resistance), ISO/IEC 27001 or SOC 2 Type II for the operator's information-security controls. The module's `is_iso_27001` flag records only the last of these. |

## Sources

| Claim | Source | Verified |
|---|---|---|
| Any $T$ shares reconstruct the secret; fewer than $T$ leak no information about it - so $M$ shards must remain reachable and fewer than $M$ must ever be concentrated. | SLIP-0039, *Shamir's Secret-Sharing for Mnemonic Codes*, SatoshiLabs — https://github.com/satoshilabs/slips/blob/master/slip-0039.md | 2026-08-21 |
| EN 1047-1 specifies fire-resistance testing for data rooms and data containers protecting data media; it defines no burglary resistance. EN 1143-1 is the separate standard classifying safes, strongroom doors and strongrooms by burglary resistance. | CEN EN 1143-1:2019 / EN 1047-1 catalogue entries — https://standards.iteh.ai/catalog/standards/cen/e0d83b62-dda5-4567-b624-aec302fee092/en-1143-1-2019 | 2026-08-21 |
| Key backup and recovery are core key-management functions requiring explicit protection of keying material. | NIST SP 800-57 Part 1 Rev. 5, *Recommendation for Key Management: Part 1 – General* (May 2020, Final) — https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final | 2026-08-21 |
| Virtual-currency custody rules govern segregation, sub-custody approval and disclosure; they do not mandate key sharding or storage geography. A sub-custodian must be licensed by the Department or subject at home to a substantially similar regime — which is why the module groups by legal `jurisdiction` and not only by country. | NYDFS 23 NYCRR 200.9 and Industry Letter, *Updated Guidance on Custodial Structures for Customer Protection in the Event of Insolvency* (30 Sep 2025) — https://www.dfs.ny.gov/industry-guidance/industry-letters/il20250930-updated-guidance-custodial-structures | 2026-08-21 |

Not verified, therefore not asserted: any minimum number of jurisdictions, any required
Shannon-entropy level, and any insurance or bonding threshold for shard-holding facilities.
