# Pre-Flight Checklist — Japan FSA High-Speed Trading

## Scope: does the FIEA art. 2(41) definition actually bite?

- [ ] Are trading decisions made automatically by an electronic data processing system?
- [ ] Is the destination venue on the **current** designated list under 定義府令 art. 26(1)? (Re-verified against the FSA notice, not a stale snapshot?)
- [ ] Is the order server in, adjacent to, or proximate to the venue's matching-engine facility?
- [ ] Is a contention-avoidance mechanism in place for the transmission (e.g. an exclusive virtual server contract)?
- [ ] Has every classification decision been made **without** reference to a latency threshold? (There is none in the FIEA.)
- [ ] Where several parties sit in the order chain, has art. 2(41) been applied separately to each party's own act?

## Registration route

- [ ] Is the entity a registered financial instruments business operator or registered financial institution?
  - [ ] If **yes**: has the FIEA art. 29-2(1)(vii) notification covering high-speed trading been filed? (No HST registration number will exist — do not demand one.)
  - [ ] If **no**: is the entity registered under FIEA art. 66-50?
- [ ] Is the registration number recorded in the form `関東財務局長（高速）第N号` and verified against the FSA register?
- [ ] Is the registration confirmed still live (not revoked, not under a business suspension order)?
- [ ] Stated capital ≥ JPY 10,000,000 and net assets non-negative (FIEA art. 66-53(5)(b), (7))?

## Foreign entities

- [ ] Has a representative or agent in Japan been appointed (FIEA art. 66-53(5)(c), (6)(b))?
- [ ] Can that person respond substantively to a regulatory report demand rather than merely relay it (Guidelines III-3-1-3(1)(i)(g))?

## System and risk controls (Guidelines III-2-1-2)

- [ ] Is a kill switch armed and **tested**, able to cancel anomalous orders already transmitted to the market?
- [ ] Are **both** a hard and a soft pre-trade limit configured, calibrated to the firm's characteristics and scale?
- [ ] Has the limit been calibrated and documented by the firm rather than left at a code default? (The FSA prescribes no yen figure.)
- [ ] Is continuous monitoring for anomalous orders in place?
- [ ] Has load testing confirmed sufficient processing capacity under assumed data-volume increases?

## Per-order exchange obligations

- [ ] Does every in-scope order carry the exchange's high-speed trading indicator (TSE Business Regulations art. 14(1)(7))?
- [ ] Is the trading strategy type indicated on **each** entrustment (TSE Brokerage Agreement Standards art. 6(5))?
- [ ] Is that type one of market-making / arbitrage / directional / other?
- [ ] Is it among the strategies recorded in the 業務方法書, and has the 業務方法書 been updated for any new strategy?

## Records and filings

- [ ] Do order slips record the venue-notified timestamp and order acceptance number (金商業等府令 art. 338(6))?
- [ ] Can the **content of the program** that generated each order be confirmed from the records (art. 338(7)(i))?
- [ ] Are the books organised so entries are readily searchable (art. 338(7)(ii))?
- [ ] Are business reports (FIEA art. 66-59) and commencement/discontinuance notifications (arts. 66-60, 66-61) current?

## Audit trail integrity

- [ ] Does the compliance report list **all** breaches, not just the first one hit?
- [ ] Does every affirmative flag on the report correspond to a check that actually ran?
- [ ] Are conservatively-resolved unknowns surfaced as warnings rather than silently assumed?
