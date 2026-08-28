# Standards — recovery-plan-for-lost-or-compromised-keys

## Scope and status

Key custody for a trading operation is governed by *security standards and
frameworks*, not by a market regulator's rulebook. Nothing below is a trading
rule, and nothing below prescribes a recovery-drill cadence. Where this skill
uses a number, it is an engineering default and is labelled as one.

## NIST SP 800-57 Part 1 Rev. 5 — the compromise-recovery plan

Section 5.5.2 ("Protective Measures") lists creating a compromise-recovery plan
among the measures that minimise the consequences of key compromise, and states:

> "A compromise-recovery plan **shall** be documented and easily accessible."

The publication says the plan *should contain*:

| SP 800-57 item | Modelled as |
|---|---|
| (a) personnel to notify, and what the notification contains (including scope of compromise) | `incident_response_contacts` |
| (b) personnel to perform the recovery actions | `incident_response_contacts` |
| (c) the method for obtaining a new key (re-keying) | `sweep_wallet_configured`, `sweep_wallet_independently_keyed` |
| (d) an inventory of all cryptographic keys (location of all keys in a system) | `key_inventory_documented` |
| (e) education of all appropriate personnel on the procedures | `last_drill_date` |
| (f) identification of all personnel needed to support the procedures | `incident_response_contacts` |
| (g) policies requiring key-revocation checking | **not modelled** — no general revocation infrastructure exists for blockchain keys; the sweep is the revocation |
| (h) monitoring of re-keying operations across all affected keys | depends on (d); the engine checks the inventory prerequisite only |

The default `min_incident_response_contacts=2` follows from (a), (b) and (f)
naming distinct roles: a plan whose notification and recovery both depend on one
reachable person has a single point of failure at the worst possible moment. The
specific number 2 is this skill's choice, not NIST's.

Source: [NIST SP 800-57 Part 1 Rev. 5, §5.5.2](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf)
(May 2020).

## CCSS v9 — crypto-specific backup and key compromise requirements

The CryptoCurrency Security Standard, maintained by the CryptoCurrency
Certification Consortium (C4), is the closest thing to a crypto-native control
standard for this domain. It is a certification scheme with three levels, not law.

| Aspect | Level | Requirement | Modelled as |
|---|---|---|---|
| 1.03.2 Key Material Backup(s) | I / II | A backup of operational key material exists (L1); a backup exists for *all* key material in the wallet (L2) | `backup_method`, `verified_shards_available` |
| 1.03.3 Environmental Protection for Backup(s) | I / II | Backups protected against environmental risks (L1); stored in geographically separate location(s) (L2) | `distinct_backup_locations` |
| 1.03.4 Backup(s) Have Access Control | I | Access controls prevent unauthorised parties reading the backup | **not modelled** — a physical/logical control outside this audit |
| 2.04.1 Key Compromise Policy Existence | I / II | An inventory of all key material exists; KCP and procedures are documented; inventory reviewed **at least annually** (L2) | `key_inventory_documented` |
| 2.04.2 KCP Training and Rehearsals | III | The policy is **tested at least annually**, with frequency increased as the threat model requires | `last_drill_date`, `max_days_since_drill` |

Note what 2.04.2.1 does **not** say: it sets an annual floor, not a 90-day one.

Source: [C4 — CCSS Details v9.0](https://cryptoconsortium.org/cryptocurrency-security-standard-documentation/ccss-details-v9/).

## NIST CSF 2.0 and SP 800-53 — where a drill cadence comes from

- **NIST CSF 2.0** (February 2024) organises this work under the **RECOVER**
  function: `RC.RP` (Incident Recovery Plan Execution — "restoration activities
  are performed to ensure operational availability of systems and services
  affected by cybersecurity incidents") and `RC.CO` (Incident Recovery
  Communication). CSF is outcome-based; it prescribes no test frequency.
- **NIST SP 800-53 Rev. 5 CP-4** (Contingency Plan Testing) requires testing "at
  an organization-defined frequency" using organization-defined tests. The
  frequency is a parameter you supply, not one the control fixes.

So: no external source in scope mandates 90 days. CCSS Level III's annual test is
the only explicit floor, and the engine's default is deliberately stricter than it.

Sources: [NIST CSF 2.0 — Recover](https://csf.tools/reference/nist-cybersecurity-framework/v2-0/rc/),
[NIST SP 800-53 Rev. 5 CP-4](https://csf.tools/reference/nist-sp-800-53/r5/cp/cp-4/).

## Shamir shard arithmetic — what the checks actually rest on

Shamir's Secret Sharing splits a secret into `n` shards with threshold `t`: any
`t` shards reconstruct the secret exactly, and any `t-1` shards reveal *nothing*
about it. Three consequences drive this engine, and none of them requires a
citation beyond the scheme's definition:

1. **Availability**: fewer than `t` verified shards means the key is already
   unrecoverable. Exactly `t` means it is recoverable until any one shard is lost.
   The surplus requirement (`min_shamir_surplus_shards`) buys tolerance for that.
2. **Confidentiality**: any party holding `t` or more shards can reconstruct the
   key unilaterally. So `max_shards_at_single_location >= t` collapses the scheme
   — both as a theft risk (that party needs no one else) and as an availability
   risk (losing that one site destroys the quorum).
3. **Threshold floor**: `t = 1` means every shard is a full key copy. The default
   `min_shamir_threshold=2` rejects it.

## Engineering defaults (not requirements)

| Parameter | Default | Basis |
|---|---|---|
| `max_days_since_drill` | 90 | Stricter than CCSS L3's annual floor; internal policy |
| `min_shamir_surplus_shards` | 1 | Tolerate the loss of one shard between drills |
| `min_shamir_threshold` | 2 | Rejects 1-of-N, where each shard is a full key |
| `min_incident_response_contacts` | 2 | SP 800-57 §5.5.2 (a)/(b)/(f) name distinct roles |
| `min_distinct_backup_locations` | 2 | CCSS 1.03.3.2 geographic separation |

## Category

`crypto-custody-security` — see the top-level `mappings/` directory for how this
category rolls up across the full skill library.
