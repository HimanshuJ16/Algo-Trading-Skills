---
name: eu-short-selling-regulation-disclosure-thresholds
description: >-
  Use when a strategy holds net short positions in shares admitted to an EU or EEA venue
  and must know what Regulation 236/2012 requires today: the 0.1% private notification
  threshold and the 0.5% public disclosure threshold. Sovereign debt has its own regime.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: eu-ssr, short-selling-regulation, nca-notification, public-disclosure, naked-short-ban, locate-audit, esma
  brokers_frameworks: "Regulation (EU) No 236/2012; Commission Delegated Regulation (EU) 2022/27; Commission Delegated Regulation (EU) No 918/2012; Commission Implementing Regulation (EU) No 827/2012; ESMA SSR Q&A (ESMA70-145-408); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy, prime-brokerage reporting stack or risk system holds net short positions in shares admitted to trading on an EU/EEA trading venue, and you need to know what **Regulation (EU) No 236/2012** requires today.

It answers two separate questions and keeps them separate:

- **Arts. 5 and 6 — disclosure.** A net short position reaching **0.1%** of issued share capital, and each **0.1%** above that, is privately notified to the relevant competent authority (RCA); at **0.5%** and each 0.1% above, it is also publicly disclosed. The 0.1% notification figure is not the original text — Commission Delegated Regulation (EU) 2022/27 permanently lowered it from 0.2% with effect from **31 January 2022**.
- **Art. 12 — execution.** A short sale of a share may only be entered into where the seller has borrowed it, has an agreement to borrow it, or holds a third-party arrangement confirming the share is located with a reasonable expectation of settlement.

Both surfaces are evidence-backed against the regulation, the Delegated and Implementing Regulations, and ESMA's Q&A. Where the rule depends on something this module cannot know — the RCA's local timezone, that Member State's trading calendar — it says so instead of guessing.

## When NOT to Use

- **For sovereign debt or sovereign CDS.** Arts. 7 and 14 have their own regime: the notification thresholds are set per sovereign issuer by ESMA as absolute amounts, not as a percentage of issued share capital, and positions are duration-adjusted. Nothing here applies to them.
- **As a delta-adjustment calculator.** The Arts. 5/6 position is a *delta-adjusted* measure (Delegated Regulation (EU) No 918/2012 Annex II Part 1) covering cash, derivatives, ETF look-through and ADRs/GDRs. This engine consumes delta-adjusted share equivalents; it does not price options. Feeding it raw share counts while holding options understates the position and misses filings.
- **As the aggregation layer.** Delegated Regulation (EU) No 918/2012 Arts. 12-13 set where the calculation happens — per legal entity, per fund/sub-fund for management entities, and at group level. Run this engine on an already-correctly-aggregated position; running it on one desk's book computes a percentage nobody has to report.
- **Outside the EU/EEA regime.** The UK, Switzerland and other jurisdictions run separate short-selling regimes with their own thresholds, forms and deadlines. The class name says EU for a reason.
- **As the filing transport.** It decides *what* is owed and *by when*. Each NCA has its own portal, form and authentication; nothing here submits anything.
- **Without checking scope first.** Shares whose principal trading venue is in a third country are outside Arts. 5, 6 and 12 entirely (Art. 16). ESMA publishes the exempted-shares list; a US-principal-venue share cross-admitted in Germany generates no EU obligation, and reporting one anyway is a false filing.

## Prerequisites

- Python 3.10+ (`zoneinfo`; stdlib only). On platforms with no system IANA database — Windows in particular — the `tzdata` package must be installed or deadline computation raises with that instruction.
- **Delta-adjusted** long and short share equivalents per issuer (`long_shares_qty`, `short_shares_qty`), aggregated at the correct reporting level.
- **Issued share capital** = total of ordinary *and* preference shares, all classes, irrespective of voting rights (Art. 2(1)(l); ESMA Q&A A6.6). Use the figure the issuer/NCA publishes, not a vendor's free float.
- The **relevant competent authority** and the IANA timezone of its Member State (`nca_timezone`, e.g. `Europe/Helsinki`), plus a `next_trading_day` callable implementing that Member State's trading calendar. Without both, no deadline instant is produced.
- The **last percentage notified** for this issuer (`previously_notified_percentage`), or `None`. Without it the engine cannot distinguish a fresh crossing from a position sitting inside a band it already reported.
- Scope flags: Art. 16 exempted-share status and Art. 17 market-making status (the latter requires 30 calendar days' prior written notice to the home competent authority before it may be relied on).

## Workflow

1. **Scope the instrument before calculating anything.** Check the share against ESMA's exempted-shares list (Art. 16) and your Art. 17 notification status.
   - **Decision point — exemption is checked first, not last.** An exempt share returns `OUT_OF_SSR_SCOPE` with no action. Computing a percentage and filing it "to be safe" puts a position on a public register that the regulation does not place there.
2. **Compute the net short position at the Art. 9(2) relevant time** — midnight at the end of the trading day — as delta-adjusted short minus delta-adjusted long, over issued share capital.
   - **Decision point — intraday peaks are not the reported figure.** The obligation attaches to the end-of-day position; a position that touches 0.6% at 11:00 and closes at 0.3% is a 0.3% notification.
3. **Truncate to two decimal places.** ESMA Q&A A5.6: 0.3199% is reported as 0.31%, by truncation, and the *threshold test runs on the truncated figure*.
   - **Decision point — never round up into a band.** 0.49999% is 0.49% and owes a private notification only. Rounding it to 0.5% publishes a position on the public register that is not required to be there, and files a figure that does not match the holder's books.
4. **Map the truncated figure to a band** (0.10%, 0.20%, 0.30%, …) and compare with the band last notified.
   - **Decision point — a move inside an already-notified band owes nothing** (ESMA Q&A A5.7). 0.30% drifting to 0.3989% is not a new notification.
   - **Decision point — falling below a threshold is itself notifiable.** Dropping from 0.35% to 0.05% requires a notification, and dropping out of the 0.5% regime requires the public register to be updated, not just the NCA.
   - No prior notification on record is treated as "not yet notified" — a position in a band is reported as due. That is the conservative direction, and it is why the field should be populated from your filing history rather than left `None`.
5. **Resolve the deadline in the RCA's local time.** Art. 9(2) requires filing by 15:30 on the following trading day; ESMA Q&A A5.2 confirms that is the local time and trading-day calendar *of the Member State of the relevant competent authority*.
   - **Decision point — "15:30 CET" is not the rule and is wrong for much of the Union.** 15:30 in Helsinki is 12:30 UTC; 15:30 in Berlin is 13:30 UTC. A CET-based scheduler files an hour late to every EET competent authority, and DST transitions move both.
   - Without a configured timezone or trading calendar the engine returns no deadline instant and says which piece is missing. Fail closed and fix the configuration; do not substitute a default.
6. **Gate short sale orders separately** with `evaluate_short_sale_order`. Art. 12 needs a borrow, an agreement to borrow, or a located-and-confirmed arrangement, evidenced in a durable medium (ITS 827/2012 Arts. 5-7).
   - **Decision point — an Art. 12 problem never cancels an Art. 5/6 obligation.** A locate gap blocks the next order; it does not excuse the disclosure owed on the position already held.
   - **Decision point — ETFs and depositary receipts are not shares for Art. 12** (ESMA Q&A A4.6/A4.7), yet they *do* count towards the Arts. 5/6 position. Applying one rule's scope to the other blocks legitimate orders and misses real ones.

> Full procedure: see `references/workflows.md`.
> Standards and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Filing to a "15:30 CET" clock.** The Art. 9(2) cut-off is 15:30 local time in the Member State of the relevant competent authority (ESMA Q&A A5.2, A9.3). For Finland, Greece, Cyprus, Bulgaria, Romania and the Baltics that is an hour earlier than CET — a scheduler pinned to CET files late every single time, and the breach is invisible because the job "succeeded".
- **Rounding the percentage instead of truncating it.** `round(pct, 4)` turns 0.49999% into 0.5% and demands a public disclosure that is not owed; it turns 0.09999% into 0.1% and generates a notification Art. 5(2) does not require. ESMA Q&A A5.6 is explicit: truncate to two decimals.
- **Treating every recalculation as a new notification.** Art. 5(2) triggers on reaching, exceeding or falling below a threshold. A position moving from 0.30% to 0.3989% owes nothing (A5.7). Re-filing on every tick floods the NCA and buries the crossings that matter.
- **Forgetting that a *fall* is notifiable.** Closing a 0.6% position to zero without notifying leaves a stale public disclosure standing on the register against your name.
- **Netting raw share counts while holding options.** The Arts. 5/6 position is delta-adjusted (DR 918/2012 Annex II Part 1). A book that is flat in shares and short 2% delta-adjusted through puts is a 2% net short position and is reportable.
- **Letting a locate gap suppress the disclosure evaluation.** The two regimes are independent. Returning "naked short ban breach" and stopping loses the public disclosure owed on the position you are already carrying — one breach silently becomes two.
- **Applying Art. 12 to the wrong instruments.** ETFs, ADRs and GDRs are not shares for Art. 12 (A4.6/A4.7); derivatives are not share sales at all. All of them still feed Arts. 5/6.
- **Accepting an unevidenced locate.** ITS 827/2012 Art. 7 requires the arrangement, confirmation and instruction in a durable medium, and ESMA has said that pointing at an "easy-to-borrow" list does not by itself satisfy Art. 6 of the ITS. A boolean flag with nothing behind it is not a locate.
- **Assuming the whole EU regime applies to every EU-admitted share.** Art. 16 takes third-country-principal-venue shares out of Arts. 5, 6 and 12 entirely.
- **Reporting off a free-float or vendor share count.** The denominator is total issued share capital across all classes including preference and non-voting shares (A6.6). A wrong denominator moves the whole position across bands.

## Verification

- Instantiate `EuShortSellingRegulationEngine(next_trading_day=next_weekday_excluding_holidays)`. For 100,000,000 issued shares and 600,000 net short (0.60%), expect `reporting_status == "PUBLIC_DISCLOSURE_REQUIRED"`, `disclosure_action == "NOTIFY_NCA_AND_DISCLOSE_PUBLICLY"` and `current_threshold_pct == 0.60`.
- Submit 499,990 shares (0.49999%): expect `net_short_percentage == 0.49` and `PRIVATE_NCA_NOTIFICATION_REQUIRED` — **not** a public disclosure. Submit 99,990 (0.09999%): expect `BELOW_REPORTING_THRESHOLDS` and `NO_ACTION`. Submit ESMA's own example, 319,900 (0.3199%): expect the filed figure `0.31`.
- Submit 312,000 with `previously_notified_percentage=0.30`: expect `NO_ACTION` (still inside the notified band). Submit 50,000 with `previously_notified_percentage=0.35`: expect `NOTIFY_NCA` on the fall below. Submit 450,000 with `previously_notified_percentage=0.55`: expect `NOTIFY_NCA_AND_DISCLOSE_PUBLICLY`, because leaving the 0.5% regime updates the public register.
- Submit 800,000 with `has_valid_locate_agreement=False`: expect `PUBLIC_DISCLOSURE_REQUIRED` **and** `art12_status == "NAKED_SHORT_BAN_BREACH"` **and** `is_short_execution_allowed is False` — the coverage gap must not suppress the disclosure.
- Evaluate the same position with `nca_timezone="Europe/Helsinki"` and `"Europe/Berlin"`: expect the two deadlines to differ by exactly one hour in UTC. Omit `nca_timezone`: expect `notification_deadline_local is None` and basis `RCA_TIMEZONE_NOT_CONFIGURED`, never a CET default.
- Gate a share order with `covering_arrangement="NONE"`: expect blocked with `NO_ART12_COVERING_ARRANGEMENT`. With `COVER_LOCATE_ARRANGEMENT` and no `locate_evidence_reference`: expect `NO_DURABLE_MEDIUM_EVIDENCE`. Same order as an ETF: expect `ART12_NOT_APPLICABLE` and allowed.
- Run `python -m unittest discover -s skills/eu-short-selling-regulation-disclosure-thresholds/scripts` (71 tests) and confirm a 100% pass rate.

## Related Skills

- `eu-market-abuse-regulation-mar-surveillance`
- `us-reg-sho-short-sale-locate-requirements`
- `short-selling-borrow-cost-and-availability-modeling`
- `mifid-ii-algo-trading-compliance-eu`
- `record-retention-periods-by-jurisdiction`
