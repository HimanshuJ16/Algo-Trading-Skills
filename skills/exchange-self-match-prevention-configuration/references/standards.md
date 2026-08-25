# Standards for Exchange Self-Match Prevention (SMP / STP)

Primary sources (all consulted 2026-08-24):

- **FIX Trading Community / FIX 5.0 SP2 EP299** — `SelfMatchPreventionID(2362)`, String: "identifies an order or trade that should not be matched to an opposite order or trade if both buy and sell orders for the same asset contain the same SelfMatchPreventionID and submitted by the same firm": https://www.onixs.biz/fix-dictionary/5.0.sp2.ep299/tagnum_2362.html · `SelfMatchPreventionInstruction(2964)`, int, `1` = Cancel aggressive, `2` = Cancel passive, `3` = Cancel aggressive and passive: https://www.onixs.biz/fix-dictionary/5.0.sp2.ep299/tagnum_2964.html · FIX Recommended Practice — Self-Match Prevention v1.0: https://www.fixtrading.org/wp-content/uploads/download-manager-files/FIX-Recommended-Practice-Self-Match-Prevention-v1.0.pdf (listed but not retrievable at time of writing — HTTP 429)
- **CME Group**, "FAQ: Globex Self-Match Prevention Functionality": https://www.cmegroup.com/solutions/market-access/globex/trade-on-globex/faq-self-match.html — tag `7928-SelfMatchPreventionID` on iLink 2, tag `2362` on iLink 3, tag `8000-SelfMatchPreventionInstruction` on both; `O` (Old) cancels the resting order, `N` cancels the aggressing order; **absent tag 8000 defaults to cancelling the resting order(s)**; the SMP ID is an exchange-generated 7-digit number registered through firm administration (FADB): https://www.cmegroup.com/tools-information/webhelp/fadb/Content/self-match.html
- **CME Group**, Market Regulation Advisory Notice **RA1614-5** (16 Nov 2016), Rule 534 "Wash Trades Prohibited", FAQ 17: https://www.cmegroup.com/tools-information/lookups/advisories/market-regulation/ra1614-5.html — SMP is **optional**; it "does not prevent self-matches upon the opening of the market" for orders entered during the pre-open; entering pre-open orders a party knew or should have known would match is itself a Rule 534 violation. Predecessor: RA1308-5: https://www.cmegroup.com/rulebook/files/cme-group-ra1308-5.pdf
- **Commodity Exchange Act §4c(a)(1)–(2)** (7 U.S.C. §6c(a)) — the statutory prohibition on transactions "of the character of, or … commonly known to the trade as, a 'wash sale'". Distinct from **17 CFR §1.38**, which is the *open and competitive execution* requirement: https://www.ecfr.gov/current/title-17/chapter-I/part-1/section-1.38
- **Commission Delegated Regulation (EU) 2016/522**, Annex II Section 1 (indicators of manipulative behaviour under MAR Annex I Section A) — "wash trades": entering into arrangements for the sale or purchase of a financial instrument "where there is no change in beneficial interests or market risk or where beneficial interest or market risk is transferred between parties who are acting in concert or collusion": https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R0522 · **Regulation (EU) No 596/2014 (MAR) Article 12** defines market manipulation: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32014R0596
- **Nasdaq**, Nordic OUCH 5 specification — STP Action `1` cancel passive, `2` cancel aggressive, `3` cancel both, `4` create a transfer transaction: https://www.nasdaq.com/docs/2025/11/13/OUCH5-for-Nasdaq-Nordic-5.01.14.pdf · European Market Services STP factsheet: https://www.nasdaq.com/docs/2024/04/02/0583-Q24_Update%20STP%20factsheet.pdf · Equity Trader Alert 2011-52, "Enhancements to the Self Match Prevention Functionality" — decrement / cancel-oldest / cancel-newest variants, assigned at MPID or port level with an optional Group ID: https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2011-52
- **Coinbase**, Self-Trade Prevention `stp` on order placement — `dc` (decrease and cancel, default), `co` (cancel oldest), `cn` (cancel newest), `cb` (cancel both); `dc` cancels the taker and decrements the maker: https://help.coinbase.com/en/derivatives/risk-management/self-match-prevention

## Wire encoding — verified, per venue

| Venue profile | SMP ID field | Instruction field | Supported instructions and wire values | Source |
|---|---|---|---|---|
| `CME_ILINK2` | tag `7928` | tag `8000` (char) | `CANCEL_RESTING`=`O`, `CANCEL_AGGRESSIVE`=`N` | CME Globex SMP FAQ |
| `CME_ILINK3` | tag `2362` | tag `8000` (char) | `CANCEL_RESTING`=`O`, `CANCEL_AGGRESSIVE`=`N` | CME Globex SMP FAQ |
| `FIX_LATEST` | tag `2362` | tag `2964` (int) | `CANCEL_AGGRESSIVE`=`1`, `CANCEL_RESTING`=`2`, `CANCEL_BOTH`=`3` | FIX 5.0 SP2 EP299 |
| `COINBASE_EXCHANGE` | `profile_id` (account scope) | `stp` | `dc` / `co` / `cn` / `cb` | Coinbase STP docs |
| `NASDAQ_INET` | `group_id` (with MPID) | STP Action | `CANCEL_RESTING`=`1`, `CANCEL_AGGRESSIVE`=`2`, `CANCEL_BOTH`=`3` | Nordic OUCH 5 |

Cross-venue caveats that the table cannot express:

- **Cancel-both is not universal.** Globex offers cancel-resting and cancel-aggressing only. The engine raises rather than downgrading `CANCEL_BOTH`, because a downgrade leaves one side of the intended double-cancel live.
- **Decrement is not one behaviour.** Nasdaq's decrement removes `min(aggressor, resting)` from both sides, cancelling the smaller in full. Coinbase `dc` always cancels the taker in full and decrements the maker. A model chosen for one venue produces wrong quantities at the other, so the engine refuses to simulate decrement unless the profile declares which model applies.
- **Nasdaq's decrement is a port/MPID configuration, not a per-order value.** The shipped `NASDAQ_INET` profile therefore does not expose `DECREMENT_AND_CANCEL`; confirm your port setup and supply your own `SmpVenueProfile` if you need it.
- **The scope key differs.** CME and FIX scope by a client-supplied ID *within one firm*. Coinbase scopes by account/profile with no client-chosen group. Nasdaq scopes by MPID, optionally narrowed by Group ID. An "SMP group" is not a portable concept.

## Regulatory position — what is and is not mandatory

| Claim | Status | Jurisdiction | Source |
|---|---|---|---|
| Wash sales are prohibited | **Mandatory, statutory** | US (CFTC-regulated futures/options) | CEA §4c(a)(1)–(2), 7 U.S.C. §6c(a) |
| A wash trade requires intent — the party "knew or should have known" the orders would negate or strictly limit market risk | Mandatory standard | US, CME Group markets | CME Rule 534 / RA1614-5 |
| Unintentional, incidental self-matching is generally not a Rule 534 violation; recurring self-matching beyond incidental may be | Guidance | US, CME Group markets | CME RA1614-5 |
| Traders who frequently work both sides and self-match beyond an incidental basis are *recommended* to employ minimising functionality | **Advisory, not mandatory** | US, CME Group markets | CME RA1614-5 |
| CME SMP is optional and does not operate on the opening for orders entered during the pre-open | Mandatory limitation of the tool | US, CME Globex | CME RA1614-5, FAQ 17 |
| Wash trades are a listed indicator of manipulative behaviour, defined by absence of change in beneficial interest **or** market risk | **Mandatory** (indicator, non-exhaustive and non-determinative) | EU/EEA | Del. Reg. (EU) 2016/522 Annex II §1; MAR Art. 12 |
| 17 CFR §1.38 requires open and competitive execution | Mandatory — but it is **not** the wash-trade prohibition | US | 17 CFR §1.38 |

The last row is the correction that matters: Regulation 1.38 governs *how* trades must be executed (openly and competitively, absent an approved DCM rule for non-competitive execution). It is adjacent to, and frequently cited alongside, the wash-trade prohibition — but the prohibition itself sits in CEA §4c(a) and in exchange rules such as CME Rule 534.

Because MAR's definition turns on **beneficial interest**, the SMP group must be scoped to commonly-owned accounts, not to a strategy or a trading desk. Two strategies inside one legal entity self-matching still produce a transaction with no change in beneficial interest.

## Engineering standards for this module

| Requirement | Standard | Basis |
|---|---|---|
| Instruction encoding | The value placed on the wire MUST be the venue's own enum (`O`/`N`, `1`/`2`/`3`, `dc`/`co`/`cn`/`cb`), never the internal instruction name. | FIX 2964 is `int`; CME 8000 is `char` |
| Unsupported instruction | An instruction the venue does not list MUST be rejected, never downgraded to the nearest supported one. | Engine requirement |
| Unknown instruction | An unrecognised instruction string MUST raise. Defaulting a typo to `CANCEL_RESTING` silently weakens a market-integrity control. | Engine requirement |
| Blank SMP ID | A blank identifier means SMP is disabled at the venue and MUST be an explicit decision (`require_smp_id=False`), never a missing field. | CME SMP FAQ (SMP applies only when IDs match) |
| Blank-to-blank matching | Two orders with a blank SMP ID MUST NOT be reported as a self-collision. | FIX 2362 definition |
| Sweep completeness | The audit MUST report every own resting order the aggressor reaches, not the first. | CME: the engine cancels the resting order(**s**) at each executable price level |
| Match ordering | Collisions MUST be ordered price-then-time, so the audit trail does not depend on the caller's list order. | Engine requirement (reproducibility) |
| Market orders | An unpriced aggressor MUST be treated as crossing every opposing level. | Matching-engine semantics |
| Enforcement boundary | The module MUST NOT issue cancels. It predicts what the venue will do. | CME SMP FAQ (the platform performs the cancel) |
| Reconciliation | SMP cancels MUST be reconciled against the venue's own reports — on Globex `MsgType=8`, `OrdStatus=4`, `ExecRestatementReason(378)=103` for the resting side. | CME Globex SMP documentation |
| Pre-open | The audit model is continuous-session only; pre-open de-confliction MUST happen upstream. | CME RA1614-5 FAQ 17 |
