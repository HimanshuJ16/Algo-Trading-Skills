# Standards for Prop vs. Client Flow Conflicts

## Jurisdiction and scope

FINRA Rule 5320 is a **US** rule binding on **FINRA members**, and covers **NMS stocks and
OTC Equity Securities** only. It does not apply to options, futures or fixed income, and it
has no extraterritorial reach. Everything in the table below is US-specific; the EU/UK
position is set out separately at the end.

## FINRA Rule 5320 — Prohibition Against Trading Ahead of Customer Orders ("Manning")

| Element | Requirement | Source |
|---|---|---|
| General prohibition | A member holding an unexecuted customer order must not trade that security on the same side for its own account **at a price that would satisfy the customer order**, unless it immediately thereafter executes the customer order up to size at the same or better price. | Rule 5320(a) |
| Direction of the test | A customer **BUY** limit is satisfied by a proprietary purchase **at or below** the limit. A customer **SELL** limit is satisfied by a proprietary sale **at or above** the limit. | Rule 5320(a) |
| Large orders and institutional accounts | Available for accounts meeting the Rule 4512(c) "institutional account" definition, **or** for orders of 10,000 shares or more *unless such orders are less than $100,000 in value* — i.e. size **and** value must both be met. Conditional on clear and comprehensive written disclosure at account opening **and annually thereafter**, plus a meaningful opportunity for the customer to opt **in** to Rule 5320 protection for all or part of the order (negative consent). | Rule 5320.01 |
| No-knowledge exception | Available where the member implements and utilises an effective system of internal controls (e.g. information barriers) preventing one trading unit from obtaining knowledge of customer orders held by a separate unit. For **NMS stocks** this extends to any trading unit including the market-making desk. For **OTC Equity Securities** it is available only to a **non-market-making** trading unit — it does **not** extend to the market-making desk. | Rule 5320.02 |
| Riskless principal | The obligation does not apply where the member is facilitating the customer order on a riskless principal basis, subject to the notice's contemporaneous-reporting conditions. Not implemented by this skill's engine. | Rule 5320.03 |
| Intermarket sweep orders | Exception where the member routes an ISO before receiving the customer order, on the stated conditions. Not implemented by this skill's engine. | Rule 5320.04 |
| Odd lots and bona fide errors | The obligation does not attach to a customer order for less than one round lot, or to a documented bona fide error transaction. | Rule 5320.05 |
| Minimum price improvement | A proprietary trade must improve on the held customer limit by at least the stated increment to avoid the obligation. | Rule 5320.06 |
| Order handling procedures | Members must have written methodologies governing execution and priority of customer orders, consistently applied and periodically reviewed. Organisational, not automatable by this engine alone. | Rule 5320.07 |
| Outside normal market hours | The protections apply where customer and member agree to process the order outside 9:30 a.m.–4:00 p.m. ET. | Rule 5320.08 |

### Rule 5320.06 minimum price improvement increments

| Customer limit price | Minimum improvement |
|---|---|
| $1.00 and above | $0.01 for NMS stocks; for OTC Equity Securities, the lesser of $0.01 or one-half of the current inside spread |
| $0.01 to under $1.00 | Lesser of $0.01 or one-half the inside spread |
| $0.001 to under $0.01 | Lesser of $0.001 or one-half the inside spread |
| $0.0001 to under $0.001 | Lesser of $0.0001 or one-half the inside spread |
| $0.00001 to under $0.0001 | Lesser of $0.00001 or one-half the inside spread |
| Under $0.00001 | Lesser of $0.000001 or one-half the inside spread |

The engine falls back to the tier increment alone when no inside spread is supplied. That is
the larger, stricter threshold; an unknown spread must not widen the set of permitted
proprietary trades.

### FINRA Rule 4512(c) "institutional account"

A bank, savings and loan association, insurance company or registered investment company; an
investment adviser registered with the SEC under Investment Advisers Act s.203 or with a
state securities commission; or any other person — natural person, corporation, partnership,
trust or otherwise — with total assets of at least **$50 million**.

## Order capacity tagging

| Field | Status |
|---|---|
| `OrderCapacity(528)` + `OrderRestrictions(529)` | Current FIX fields for principal/agency capacity, from FIX 4.3 onward. Populate on outbound messages. |
| `Rule80A(47)` | **Deprecated as of FIX 4.3**; renamed from Rule80A to OrderCapacity in 4.2 because the term is US-specific. Support only for legacy 4.2 sessions. |

## EU / UK — a different mechanism, not a translation of Manning

There is no MiFID II equivalent of the 10,000-share/$100,000 negative-consent carve-out. The
on-point obligations are:

- **Commission Delegated Regulation (EU) 2017/565, Art. 67** — an investment firm shall not
  misuse information relating to pending client orders and shall take all reasonable steps to
  prevent such misuse by its relevant persons. Dealing on own account in the instrument a
  pending client order relates to, or in related instruments, on the strength of that
  information is a misuse; legitimate market making or dutiful order execution is not, in
  itself, misuse.
- **MiFID II (Directive 2014/65/EU) Art. 28** — client order handling: prompt, fair and
  expeditious execution of client orders relative to other client orders and to the firm's own
  trading interests, in time order of reception for otherwise comparable orders.
- **MiFID II Art. 23** — identification, prevention and management of conflicts of interest,
  including conflicts between the firm's own account and its clients.
- **UK: FCA COBS 11.3.5A R** — a firm must not misuse information relating to pending client
  orders and must take all reasonable steps to prevent misuse by any of its relevant persons.

Note that MiFID II Art. 27 is the *best execution* obligation, not the trading-ahead
provision — citing it as authority for a Manning-style control is a category error.

## Sources

- FINRA Rule 5320, *Prohibition Against Trading Ahead of Customer Orders* — https://www.finra.org/rules-guidance/rulebooks/finra-rules/5320
- FINRA Regulatory Notice 11-24, *Trading Ahead of Customer Orders* — https://www.finra.org/rules-guidance/notices/11-24
- FINRA Regulatory Notice 09-15, *Trading Ahead of Customer Orders* — https://www.finra.org/rules-guidance/notices/09-15
- FINRA Rule 4512, *Customer Account Information* (institutional account definition, 4512(c)) — https://www.finra.org/rules-guidance/rulebooks/finra-rules/4512
- Commission Delegated Regulation (EU) 2017/565, Art. 67 *General principles* — https://eur-lex.europa.eu/eli/reg_del/2017/565/oj/eng
- Directive 2014/65/EU (MiFID II), Arts. 23, 27, 28 — https://eur-lex.europa.eu/eli/dir/2014/65/oj/eng
- FCA Handbook, COBS 11.3 *Client order handling* — https://www.handbook.fca.org.uk/handbook/COBS/11/3.html
- FIX Dictionary, `OrderCapacity(528)` — https://www.onixs.biz/fix-dictionary/latest/tagnum_528.html
- FIX Dictionary, `Rule80A(47)` (deprecated in FIX 4.3) — https://www.onixs.biz/fix-dictionary/4.2/tagnum_47.html
