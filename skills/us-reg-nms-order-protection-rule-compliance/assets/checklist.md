# SEC Regulation NMS Rule 611 Surveillance Checklist

Sign-off gate for a trade-through surveillance pipeline. Every item traces to
rule text or to the SEC Division of Trading and Markets Reg NMS FAQ; cites are
in `references/standards.md`.

## Scope and session

- [ ] **NMS stocks only.** Options, futures, fixed income and FX are excluded
      from the evaluated population, not merely expected to pass.
- [ ] **Regular trading hours gate applied before anything else.** 09:30–16:00
      **Eastern**, converted with a real tz database (`tzdata` installed where
      the OS ships none). Rule 600(b)(105) confines trade-throughs to RTH.
- [ ] **Pre- and post-market prints are classified `NOT_SUBJECT_RULE_611`**, not
      as violations and not as exceptions (FAQ 7.01).
- [ ] **Eastern-time conversion verified across a DST boundary** — a January and
      a July execution at the same UTC time must not both be inside the session.

## Quote data

- [ ] **Firm-specific quotation data with firm receipt timestamps** is the data
      of record (FAQ 6.01), not SIP timestamps.
- [ ] **As-of selection implemented**: quotes stamped after the execution are
      discarded, and only each venue's latest quote at or before it is used.
- [ ] **Symbol filter enforced.** Quotes for another NMS stock never enter the
      NBBO; a symbol mismatch raises rather than silently producing a book.
- [ ] **Manual quotations excluded.** Rule 611 protects automated quotations
      only (Rule 600(b)(6), (54), (81)); the automated/manual flag comes from
      the venue, not from an assumption.
- [ ] **Top of book only.** One protected bid and one protected offer per venue.
      Depth-of-book levels are never treated as protected quotations.
- [ ] **A one-sided or empty protected book raises**, and is not silently
      treated as a zero price.

## Trade-through detection

- [ ] **Price test, not side test.** `price < protected bid` and
      `price > protected offer` are both applied to purchases *and* sales.
      Verified with a BUY below the protected bid and a SELL above the protected
      offer.
- [ ] **NaN, infinite, zero and negative prices raise**, and never resolve to
      "no violation" through failed comparisons.
- [ ] **Naive and timezone-aware timestamps interoperate** without a
      `TypeError` in the overnight batch.
- [ ] **Severity measured against the traded-through protected quotation**, not
      against the NBBO midpoint.

## Statutory exceptions

- [ ] **Exceptions applied only to a transaction that was a trade-through.** An
      ISO-marked or benchmark-marked print that never traded through the market
      is recorded as compliant, not exempt.
- [ ] **(b)(4) crossed market handled**, and a *locked* market (NBB == NBO)
      correctly excluded from it.
- [ ] **(b)(3) single-priced opening/reopening/closing transactions** are
      classified as such rather than flooding the queue.
- [ ] **(b)(5)/(6) ISO**: the router's Rule 611(c) obligation is tested against
      Rule 600(b)(47)(ii) wherever the ISO limit price and simultaneous routes
      are captured. Superior is measured against the **limit price**; a buy
      sweeps offers, a sell sweeps bids; Self-Help venues may be omitted
      (FAQ 4.09).
- [ ] **An ISO with insufficient routes is escalated separately** as a routing
      failure, not filed as an execution violation.
- [ ] **(b)(7) benchmark claims carry a recorded benchmark reference**, and a
      bare flag is marked unsubstantiated. FAQ 3.08/3.16 require documentation
      of the externally observable circumstances.
- [ ] **(b)(8) flickering quote is per-venue, strictly prior, equal-or-inferior.**
      Verified against a live quote stream: a large trade-through with every
      venue quoting at the execution instant must still be reported.
- [ ] **(b)(9) stopped orders**: the (iii) underwater condition is tested —
      a stopped **buy** below the national best bid, a stopped **sell** above the
      national best offer — and customer agreement is recorded, not assumed.
- [ ] **Rule 611(d) Commission exemptions** (qualified contingent trades,
      sub-penny, error correction, print protection, non-convertible preferred)
      are handled as separate orders, not modelled as Rule 611(b) paragraphs.

## Self-Help (Rule 611(b)(1), FAQ 4.07)

- [ ] **Declarations stored as `(declared_at, revoked_at)` intervals** and
      evaluated at the execution timestamp, so a tape replay is reproducible.
- [ ] **Element 1 — notice.** Notice to the bypassed trading centre is sent
      immediately upon use, reaches a responsive contact, and its dispatch is
      recorded on the declaration.
- [ ] **A mechanism exists to RECEIVE self-help notices** from other trading
      centres, monitored in real time (FAQ 2.03).
- [ ] **Element 2 — systems assessment.** Self-Help is not elected where the
      firm has reason to believe the fault is its own systems or links.
- [ ] **Element 3 — objective parameters** are documented, including what
      *terminates* the exception. The FAQ's benchmark is repeated failure of the
      destination to answer an IOC within one second, **after adjusting for
      order transmission time** — not a single slow round trip.
- [ ] **`EXEMPT_SELF_HELP` is distinguished from `COMPLIANT`** in the audit
      record, and the per-venue rate is reviewed.

## Records, clocks and CAT

- [ ] **Business Clocks within 50 ms of NIST** for Industry Members (100 µs for
      Participants; 1 s for Manual Order Event clocks), with synchronisation
      logs retained **five years**.
- [ ] **Timestamps reported in milliseconds or finer**, at the finest increment
      the firm's own systems capture, **truncated** at nanoseconds — never
      rounded.
- [ ] **Time of execution recorded per FAQ 3.02** — final agreement on stock,
      price and size — documented simultaneously and not retrospectively
      alterable. Not the trade report time.
- [ ] **CAT-reportable records retained under SEA Rule 17a-4(b)**: three years,
      first two in an accessible place. The six-year figure is Rule 17a-4(a)
      blotters and ledgers and does not apply here.
- [ ] **Every audit record carries the protected NBB/NBO, the as-of instant, the
      contributing venues and the Self-Help venues** — on exempt outcomes as
      well as violations.

## Rule 611(a)(2) periodic review

- [ ] **Review periods selected and retained** with enough firm-specific
      quotation data to demonstrate their reasonableness (FAQ 6.03).
- [ ] **Same periods screened against Network/SIP data**, with each disagreement
      explainable as a false positive from firm-specific evidence (FAQ 6.04).
- [ ] **Exception *rates* reviewed, not only violations.** A rising share of
      unverified ISO, unsubstantiated benchmark or single-venue Self-Help
      reliance is a deficiency even at zero violations.
- [ ] **Deficiencies remedied promptly and the remediation recorded** —
      Rule 611(a)(2)'s operative requirement.
- [ ] **Rule status tracked.** Release No. 34-105655 (File No. S7-2026-20)
      proposes rescinding Rule 611; as of 2 September 2026 no final rule has
      been adopted and the obligation stands. Controls are not removed ahead of
      a final rule.
