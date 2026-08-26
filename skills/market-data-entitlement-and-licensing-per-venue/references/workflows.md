# Workflows for Per-Venue Market Data Entitlement Governance

This is the implementation-time reference. `SKILL.md` carries the summary; the
evidence for each rule is in `standards.md`.

## 0. Build the entitlement inventory before writing any code

The gate can only enforce what someone has encoded. Start from the paperwork, not
from the feed handler:

1. List every executed market data agreement, Order Form and schedule.
2. For each, record the **venue as licensed**, not the venue as your config names
   it. CME Group's DCMs (CME, CBOT, NYMEX, COMEX) are four entitlements. LSE's
   segments (UK, International, ETF/ETP, AIM) are declared separately.
3. For each venue, record the deepest licensed product tier and map it onto the
   `L1`/`L2`/`L3` ladder. Where a venue names its tiers differently
   (TotalView, Level 2, MBO), write the mapping down next to the entitlement — the
   ladder is an abstraction and the mapping is the part that can be wrong.
4. For each venue, record which non-display activity categories are licensed.
   Empty is a legitimate and common answer: display-only.
5. For each venue, record the term expiry. If you genuinely do not track it there,
   leave it `None` and accept the logged warning — do not invent a placeholder.

A duplicate entitlement for one venue is rejected at evaluation time rather than
resolved last-one-wins, because the record that loses is as likely to be the
narrower one, which would fail open.

## 1. Classify the subscriber

Professional is the default. Only ask the Non-Professional question when the
account holder is a natural person.

```
account_holder_type == ORGANISATION            -> PROFESSIONAL (no exceptions)
is_securities_professional                     -> PROFESSIONAL
automated / non-display consumption            -> PROFESSIONAL tier entitlement
otherwise, if verified by the distributor      -> NON_PROFESSIONAL
```

A Non-Professional declaration is only as good as its last verification. Record
`classification_attested_on` when the distributor verifies status, and re-verify
on at least the semi-annual cadence CTA requires for retired and inactive
professionals. The engine refuses an unverified, future-dated, or stale
declaration rather than letting it decay silently into a false statement.

Over-declaring Professional is not a compliance defect. It costs the firm money,
which is the failure direction you want.

## 2. Classify the consumption

Two questions, both of which must be answered before the stream opens:

- **Display or non-display?** Non-display is automated access by a machine without
  a natural person reading a display. A human watching a chart the algo also reads
  does not make the algo's consumption display use — and non-display remains
  fee-liable whether the process runs on a desktop, in a datacenter or in the
  cloud.
- **Which non-display activity?** Trading as principal, facilitating client
  business, and operating a trading platform are separately licensed. If your
  request pipeline cannot say which one applies, that is a gap in the pipeline,
  not a reason to default — the engine denies rather than guessing.

An unrecognised `usage_type` is a denial. This is the check most worth keeping:
a gate that compares against one literal and does nothing in the `else` branch
will approve `NON_DISPLAY`, `NONDISPLAY_ALGO` and `algo` as display use.

## 3. Evaluate, in the documented order

```
identity -> usage recognised -> classification integrity -> classification age
         -> venue -> term -> depth -> non-display activity
```

The order determines which status an auditor sees when a request breaches several
rules at once. Identity comes first because a mismatch means the whole evaluation
was against the wrong entitlement set. Classification precedes venue because
misclassification is a firm-wide error that every venue prices retroactively.
Venue and term precede depth and activity because an unlicensed or lapsed venue
makes the finer questions moot.

Pass `as_of_date` explicitly in batch runs, replays and tests. Defaulting to
today's date makes a decision that cannot be reproduced later, which is exactly
what an auditor will ask you to do.

## 4. Wire it in at the right place

The gate belongs at stream-open, upstream of the vendor's permissioning system —
before a subscription request reaches a fee-liable feed. Placing it after the feed
is already open turns it into a reporting tool rather than a control.

Because it reserves nothing and counts nothing, it is safe to call concurrently.
That also means it cannot tell you how many entitlements you are consuming; see
§6.

## 5. Persist every decision

Return values are the audit evidence. Persist every `EntitlementAuditReport`,
denials included, with the normalised inputs it carries (`data_level`,
`non_display_category`, `subscriber_classification`, `evaluated_on`). Retention
should cover the audit look-back period — three years under the Nasdaq Global Data
Agreement. The engine holds no durable record of its own.

## 6. What still has to happen outside this skill

- **Reportable-unit counting and declaration.** Nasdaq's non-display unit of count
  is the greater of the number of Subscribers that can modify the application in
  real time or the number of Devices that receive and benefit from the
  Information. That comes from your infrastructure inventory. CME requires
  Applications to be declared and approved, and reported in the month they are
  added or removed.
- **Feed-level permissioning.** LSEG DACS and Bloomberg EMRS still need to be
  configured correctly.
- **Contract scope** (licensed use cases, redistribution, seat caps) —
  `data-vendor-contractual-usage-restriction-tracking`.
- **Real-time vs delayed tiering** — `real-time-vs-delayed-data-entitlement-handling`.
- **Periodic reconciliation.** Compare the encoded entitlement inventory against
  the executed Order Forms on a schedule. The most dangerous state is an inventory
  that was accurate when written and has since drifted.
