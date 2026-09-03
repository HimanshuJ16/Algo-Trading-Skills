# Standards for Audit Logging

Regulatory applicability is jurisdiction- and entity-dependent. Encode only what
applies to your firm, keep compliance decisions auditable, and do not describe a
voluntarily adopted control as a mandated one.

| Standard | Scope / applicability | What it actually says | How this engine relates |
|---|---|---|---|
| **FINRA Rule 3110** (Supervision) | FINRA member broker-dealers | Written supervisory procedures under 3110(b)(1). A record of the names of designated supervisory personnel and the dates of designation, preserved "for a period of not less than three years, the first two years in an easily accessible place" (3110(b)(6)(B)). Internal inspection reports kept on file "for a minimum of three years" (3110(c)(2)). | Every record carries an authenticated `user_id` and a `justification`; rejected attempts are retained alongside approved ones. The engine does not itself satisfy 3110 — it produces evidence a supervisory procedure can rely on. |
| **FINRA Regulatory Notice 15-09** (March 2015) | Firms engaging in algorithmic trading strategies | **Guidance, not a rule.** "Suggested effective practices" that "complement, rather than supplant, obligations firms have under existing or future rules". Includes "a development and change management process that tracks the development of new trading code or material changes to existing code", with "a review of test results and a set of approval protocols", and "archiving code versions in a retrievable manner". | `ConfigChangeRecord` captures `old_value`/`new_value`, principal, timestamp and sequence for forensic reconstruction. Note the Notice does **not** require documenting the *reason* for a change — that obligation comes from 15c3-5 below, and only for some firms. |
| **SEA Rule 15c3-5** + Division of Trading and Markets **FAQ No. 18** | US broker-dealers with market access | Where a credit/capital threshold is reached and orders are blocked, the firm may modify the threshold in accordance with supervisory procedures, and "the reasons for any such modification should be appropriately documented and retained as part of the broker-dealer's books and records". | This is the authority for the mandatory `justification` field on risk-control parameter changes. It is US- and market-access-specific; it does not generalise to every firm or every parameter. |
| **SEC Rule 17a-4** (as amended 2022) | SEC-registered broker-dealers and SBS entities | Release 34-96034, effective 3 January 2023, compliance date 3 May 2023 for broker-dealers. An electronic recordkeeping system may satisfy 17a-4(f) by **either** preserving records in WORM format **or** by an audit-trail arrangement that permits recreation of an original record if it is modified or deleted, recording the date and time of each creation, modification or deletion and the identity of the person who performed it. | The emitted JSON is forwarded to whichever the firm has elected. The engine's per-record principal and UTC timestamp are the identity and time elements such an arrangement needs; the hash chain is not a substitute for either option. |
| **SEC Regulation SCI** (Rules 1000–1007) | "SCI entities" only: SROs, certain ATSs, plan processors, certain exempt clearing agencies | Resiliency, systems integrity and forensic reconstruction for "SCI systems", with recordkeeping under Rules 1005–1007. The March 2023 proposal to extend the definition to certain large broker-dealers and SBSDRs (88 FR 23146) was **formally withdrawn** by the Commission on 12 June 2025 (effective 17 June 2025). | High-precision UTC timestamps, strict `old_value`→`new_value` mapping, sequence numbers and hash chaining are SCI-style controls. A firm outside the SCI perimeter may adopt them voluntarily but must not call it SCI compliance. |
| **NIST SP 800-92** §3.1 (2006; Rev. 1 remains an initial public draft as of this writing) | Advisory best practice | "Log file integrity checking involves calculating a message digest for each file and storing the message digest securely… The original message digests should be protected from alteration through FIPS-approved encryption algorithms, storage on read-only media, or other suitable means." Federal agencies must use SHA rather than MD5, and should prefer SHA-256. | Per-record SHA-256 `record_hash` chained via `prev_hash`; canonical sorted-key JSON; UTC ISO-8601 timestamps. `chain_head_hash` is the digest that must be stored where the trading host cannot alter it. |

## Integrity model

Tamper *detection* is provided in-process by the hash chain; tamper *prevention* is
provided by the downstream WORM or 17a-4(f) audit-trail recordkeeping system. The two
layers are complementary, and neither substitutes for the other.

What `verify_chain` detects:

- in-place modification of any field of any record;
- deletion of a record from the middle of the chain, and any other sequence gap;
- reordering of records;
- an edit whose author recomputed that record's own `record_hash` — the following
  record's `prev_hash` no longer matches.

What it does **not** detect, and why more hashing does not fix it:

- **Truncation of the newest records.** Dropping the last N records leaves a chain that
  verifies cleanly. Only a chain head held outside the trading system exposes the loss.
  This is the specific reason SP 800-92 requires the digest to be stored protected.
- **A wholesale recomputation.** An attacker with write access to the entire log and the
  hashing code can rebuild a consistent chain. Detection assumes the emitting process
  and the archive are not both under the attacker's control.
- **A falsified principal.** The engine records the `user_id` it is given. If the caller
  accepts an unverified identity, the chain faithfully preserves a lie.

## Sources

- FINRA Rule 3110 — https://www.finra.org/rules-guidance/rulebooks/finra-rules/3110
- FINRA Regulatory Notice 15-09 — https://www.finra.org/rules-guidance/notices/15-09
- SEC Division of Trading and Markets, Responses to FAQs Concerning Risk Management
  Controls for Brokers or Dealers with Market Access (FAQ No. 18) —
  https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0
- SEC, Amendments to Electronic Recordkeeping Requirements for Broker-Dealers (Release
  34-96034) —
  https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers
- SEC, Regulation SCI proposal (88 FR 23146, 14 April 2023) —
  https://www.federalregister.gov/documents/2023/04/14/2023-05775/regulation-systems-compliance-and-integrity
- SEC, Withdrawal of Proposed Regulatory Actions (12 June 2025) —
  https://www.federalregister.gov/documents/2025/06/17/2025-11110/withdrawal-of-proposed-regulatory-actions
- NIST SP 800-92, Guide to Computer Security Log Management —
  https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-92.pdf
