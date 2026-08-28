# Standards — risk-control-bypass-audit-logging

## Scope and jurisdiction

Override-logging obligations attach to **regulated entities**, not to everyone who
runs an algorithm. Two regimes are relevant, and neither is universal:

| Regime | Who is in scope | The override provision |
|---|---|---|
| **US** — SEA Rule 15c3-5 | Broker-dealers with market access to an exchange or ATS | Rule text + Division of Trading and Markets FAQ No. 18 |
| **EU / UK** — RTS 6 (Commission Delegated Regulation (EU) 2017/589, assimilated in the UK) | Investment firms engaged in algorithmic trading | Article 15(6) |

A proprietary trader, a fund's internal system, or an individual running an
algorithm is in scope of neither. Using this engine is then good operational
hygiene, not compliance evidence — say so rather than implying otherwise.

Nothing here is legal advice. Whether a firm is in scope, and for how long records
must be kept, are questions for counsel.

## EU / UK — RTS 6 Article 15(6): what an override actually requires

This is the single most important provision for this skill, and it is materially
stricter than "check the authoriser against an allowlist":

> "An investment firm shall have procedures and arrangements in place for dealing
> with orders which have been blocked by the investment firm's pre-trade controls
> but which the investment firm nevertheless wishes to submit. Such procedures and
> arrangements shall be applied in relation to a specific trade on a temporary
> basis and in exceptional circumstances. They shall be subject to verification by
> the risk management function and authorisation by a designated individual of the
> investment firm."

Four distinct requirements, each mapped to a field or option in the engine:

| Requirement | Modelled as |
|---|---|
| "in relation to a specific trade" | `strategy_id`, `instrument` — scope the record, do not log a blanket override |
| "on a temporary basis" | `expires_at_iso`; `require_expiry_for_critical=True` flags an open-ended critical bypass |
| "verification by the risk management function" | `risk_function_verifier`; `require_risk_function_verification=True` |
| "authorisation by a designated individual" | `authorized_by` checked against `authorized_principals` |

Two *distinct* actors are contemplated — a verifier and an authoriser. Article 1(c)
supplies the reason:

> "a separation of tasks and responsibilities of trading desks on the one hand and
> supporting functions, including risk control and compliance functions, on the
> other, to ensure that unauthorised trading activity cannot be concealed."

Hence the self-authorisation flag: a requester who is also the authoriser fails
Article 1(c) even when they sit on the allowlist.

Related provisions: Article 15(1) mandates price collars, maximum order values,
maximum order volumes and maximum message limits — the four control types seeded
into `HIGH_SEVERITY_CONTROLS`. Article 12(1) requires kill functionality, the
ability to "cancel immediately, as an emergency measure, any or all of its
unexecuted orders".

Source: [Commission Delegated Regulation (EU) 2017/589 (RTS 6)](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589).

The UK applies the same text as assimilated law, supplemented by FCA Handbook
**MAR 7A**. The FCA's [multi-firm review of algorithmic trading controls
(21 August 2025)](https://www.fca.org.uk/publications/multi-firm-reviews/algorithmic-trading-controls-high-level-observations)
reviewed principal trading firms against RTS 6; it "creates no new requirements"
and does not add an override-specific rule, so do not cite it as one.

## US — SEA Rule 15c3-5 and the FAQ that covers overrides

Rule 15c3-5(b) requires a broker-dealer with market access to "establish, document,
and maintain a system of risk management controls and supervisory procedures
reasonably designed to manage the financial, regulatory, and other risks" of that
activity. Rule 15c3-5(d)(1) requires those controls to be "under the direct and
exclusive control of the broker or dealer". Rule 15c3-5(e)(1) requires an annual
documented review, and (e)(2) an annual CEO certification.

The rule text does not itself say "log every override". The override case is
addressed in staff guidance, **Division of Trading and Markets FAQ No. 18**:

> "If a threshold is reached, and as a result subsequent orders are rejected, the
> broker-dealer may evaluate whether it is appropriate to increase the relevant
> threshold, and, if appropriate, do so in accordance with supervisory procedures.
> The reasons for such modifications should be documented and retained as part of
> the broker-dealer's books and records."

Note the register: staff FAQs are guidance, not rules. FAQ No. 16 separately
indicates that workarounds which defeat pre-trade limits — malformed orders,
"chase and cancel" behaviour — would not satisfy the rule.

Sources:
[17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5) ·
[SEC Division of Trading and Markets, Responses to FAQs Concerning Risk Management Controls for Brokers or Dealers with Market Access](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0) ·
[Adopting Release 34-63241 (2010)](https://www.federalregister.gov/documents/2010/11/15/2010-28303/risk-management-controls-for-brokers-or-dealers-with-market-access).

## Immutability — what SEA Rule 17a-4(f) actually requires

"Tamper-proof" is not a regulatory term; Rule 17a-4(f) is specific. Since the
amendments **effective 3 January 2023** (broker-dealer compliance date 3 May 2023),
an electronic recordkeeping system must satisfy **one** of two alternatives:

| Alternative | Requirement |
|---|---|
| WORM | "Preserve the records exclusively in a non-rewriteable, non-erasable format" |
| Audit-trail | "Preserve a record for the duration of its applicable retention period in a manner that maintains a complete time-stamped audit trail that includes: All modifications to and deletions of the record or any part thereof; The date and time of actions that create, modify, or delete the record; If applicable, the identity of the individual creating, modifying, or deleting the record" |

The system must also verify automatically the completeness and accuracy of the
storage processes, provide backup, and be covered by an undertaking from a
designated executive officer permitting prompt SEC access.

**What this means for the engine.** A SHA-256 chain held in one process satisfies
neither alternative on its own. It makes edits *detectable* to a holder of an
earlier chain head; it does not make them impossible, and an attacker who can
rewrite the chain can recompute every hash after the edit. The engine records the
identity of the authorising individual and the create time (`recorded_at_iso`), so
its entries carry the *content* the audit-trail alternative wants — but the
preservation guarantee has to come from the storage layer. Persist entries and
publish `chain_head_hash` to append-only storage.

Sources:
[17 CFR 240.17a-4](https://www.law.cornell.edu/cfr/text/17/240.17a-4) ·
[SEC, Electronic Recordkeeping Requirements for Broker-Dealers, Security-Based Swap Dealers, and Major Security-Based Swap Participants (Release 34-96034, 2022)](https://www.federalregister.gov/documents/2022/11/03/2022-22670/electronic-recordkeeping-requirements-for-broker-dealers-security-based-swap-dealers-and-major).

## Retention — do not copy a number without checking scope

| Regime | Period | Caveat |
|---|---|---|
| US broker-dealer | SEA Rule 17a-4(b)(1): "not less than three years, the first two years in an easily accessible place"; 17a-4(e)(7) for compliance and procedures manuals, "until three years after the termination of the use of the manual" | 15c3-5(e)(1) points the review documentation at these paragraphs |
| EU / UK investment firm | Generally five years, extendable to seven at a competent authority's request — MiFID II Article 16(6)–(7) and Delegated Regulation (EU) 2017/565 Article 72 | Confirm with counsel; the exact article applicable to a given record type varies |

A common error is to cite **RTS 6 Article 28(3)** ("The records referred to in
paragraphs 1 and 2 shall be kept for five years from the date of the submission of
an order to a trading venue or to another investment firm for execution") as the
retention period for override records. Article 28 governs the *order* records of a
firm using a high-frequency algorithmic trading technique. It is not the override
record's retention rule.

## Why SOX is not the frame

Earlier versions of this skill cited "SOX Audit Trail Requirements". Sarbanes-Oxley
§302/§404 concern internal control over financial reporting at **issuers**, and
§802 (18 U.S.C. §1519) criminalises altering or destroying records to obstruct a
federal investigation. Neither is a source of risk-control-override logging
requirements for a trading system, and a firm that is not an issuer is not brought
into scope by running a trading algorithm. The operative sources are the ones
above.

## Engineering standards applied by this skill

| Property | Standard | Basis |
|---|---|---|
| Chain integrity | Every entry commits to its predecessor via SHA-256; `verify_integrity()` detects edits, deletions and reordering | Engineering; supports 17a-4(f) audit-trail content |
| Verdict stability | Severity and suspicion are computed once at log time, covered by the hash, and read — never re-derived — by the report | Engineering; a record carrying two verdicts for one event is not evidence |
| Idempotency | Identical resubmission of an `event_id` is a no-op; conflicting resubmission raises | Engineering; a retried write must not inflate the counts a regulator reads |
| Timestamp discipline | Timezone-aware ISO-8601 required; event time and record time stored separately; forward-dating flagged | Engineering; 17a-4(f) audit-trail alternative requires create/modify times |
| Justification length | `min_justification_chars`, default 5 | **Engineering default with no regulatory basis** — it catches empty fields, nothing more |
| Two-person control | Self-authorisation flagged; risk-function verification available as an opt-in | RTS 6 Articles 15(6) and 1(c) |
| Report cadence | Review flagged entries on a defined cadence, by someone who did not authorise them | Firm policy; no cited rule prescribes a daily cadence |
