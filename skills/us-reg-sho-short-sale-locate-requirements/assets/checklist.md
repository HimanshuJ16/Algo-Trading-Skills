# SEC Regulation SHO Pre-Trade Gate Checklist

## Rule 200(g) — order marking
- [ ] Every outbound equity sell order carries exactly one of `LONG`, `SHORT`, `SHORT_EXEMPT`.
- [ ] The `LONG` determination is made upstream from net long position and settlement-date
      deliverability (242.200(a)-(f)) — the gate validates the marking, it does not derive it.
- [ ] Every `SHORT_EXEMPT` order names its 242.201(c) or 242.201(d)(1)-(7) basis, and the basis
      is recorded on the decision.
- [ ] No order is marked `SHORT_EXEMPT` on a bona fide market making rationale — that is a
      Rule 203(b)(2)(iii) locate exception, not a Rule 201 price-test exception.
- [ ] Written policies exist for 242.201(c) reliance, preventing an order being incorrectly
      identified as priced above the national best bid, with regular surveillance of them.

## Rule 203(b)(1) — locate
- [ ] Automated ingestion of ETB and HTB locate grants with identifier, symbol, quantity, and
      validity window.
- [ ] Every `SHORT` **and** `SHORT_EXEMPT` order is gated on a valid locate — the short exempt
      marking gives no relief from Rule 203.
- [ ] Locate identity, symbol match, expiry, and remaining capacity are all checked, not just
      the presence of an ID.
- [ ] Capacity is **reserved** on approval and **released** on cancel, venue rejection, or
      session drop. Reservation leaks are alerted on.
- [ ] A duplicate `locate_id` grant is refused rather than overwriting consumed capacity.
- [ ] Locate reuse after an intraday buy-to-cover, if permitted at all, is gated on SEC Reg SHO
      FAQ 4.4 conditions and blocked for threshold and hard-to-borrow securities.
- [ ] Documentation of locate compliance is retained under 242.203(b)(1)(iii).

## Rule 201 — short sale price test
- [ ] Restriction state is driven by the **SIP Reg SHO price test indicator**, not a locally
      computed decline. The listing market makes the determination (242.201(b)(3)).
- [ ] A local 10% decline check runs alongside as a feed-health signal and **escalates** on
      disagreement; it never relaxes the gate.
- [ ] `SHORT` orders are rejected at a price at or below the current national best bid while
      the restriction is in force, with the comparison biased toward rejection at the epsilon.
- [ ] A missing, zero, negative, NaN, or infinite national best bid causes a **rejection**, not
      a pass — a data outage must not become a silent bypass.
- [ ] The restriction is only cleared on an authoritative signal. It is never lifted on a
      guessed trading calendar (Rule 201(b)(1)(ii): remainder of the day *and* the following day).
- [ ] The price test is applied only to covered securities (NMS stocks, 242.600(b)(65)).

## Order path integrity
- [ ] Re-validating an `order_id` reserves capacity exactly once; a retry after a timeout
      cannot double-spend a locate.
- [ ] A reused `order_id` carrying different terms (quantity, price, symbol, marking, locate,
      exempt basis) is rejected rather than silently re-decided.
- [ ] A retry carrying a fresher NBBO tick is recognised as the *same* order and returns the
      original decision — market data is not part of the duplicate fingerprint, so a moved
      national best bid must not turn a legitimate retry into a "different terms" rejection.
- [ ] Structurally invalid orders (non-positive quantity, non-finite price, unknown marking)
      are rejected and logged, not passed and not raised as exceptions on the order path.
- [ ] Concurrency is handled: locate reservation is a read-modify-write, so calls are
      serialised per engine instance or sharded by symbol.

## Rule 204 and clearing (outside this gate)
- [ ] Fails-to-deliver are closed out on the 242.204 deadlines measured from **settlement date**
      — generally the settlement day following settlement date; the third consecutive settlement
      day for long sales and bona fide market making; the thirty-fifth consecutive calendar day
      after trade date for deemed-ownership sales.
- [ ] Deadlines are computed against the **T+1** settlement cycle in force since 28 May 2024.
- [ ] The 242.204(b) pre-borrow "penalty box" is enforced against new short sale orders in a
      security with an unresolved fail.
- [ ] Threshold securities with a fail-to-deliver position for 13 consecutive settlement days
      are closed out immediately (242.203(b)(3)).

## Audit and retention
- [ ] Every gate decision — approvals and rejections — is persisted with a timezone-aware
      timestamp, marking, locate status, exempt basis, and reserved quantity.
- [ ] Restriction activations record their source (SIP indicator vs manual) so an examiner can
      distinguish them.
- [ ] Retention periods are set from SEA Rule 17a-4 and FINRA Rule 4511 — 4511(b) requires at
      least six years where no other period is specified, while several 17a-4 categories are
      three years. Confirm the category per artifact with compliance rather than applying one
      number to everything. Reg SHO itself prescribes no retention period.

## Sign-off
- [ ] The skill's reference suite passes from the repository root:
      `python -m unittest discover -s skills/us-reg-sho-short-sale-locate-requirements/scripts`
- [ ] Each box above is either ticked or has a named owner and a date against it.
