# Standards for Japan FSA HST Compliance

Source of record: the **Financial Instruments and Exchange Act** (金融商品取引法, Act
No. 25 of 1948, "FIEA") as amended by the Act partially amending the FIEA
(promulgated 24 May 2017), the high-speed trading registration regime of which
took effect **1 April 2018**; the **Order for Enforcement of the FIEA** (Cabinet
Order No. 321 of 1965); the **Cabinet Office Order on Definitions under Article 2
of the FIEA** (Ordinance of the Ministry of Finance No. 14 of 1993, "定義府令");
the **Cabinet Office Order on Financial Instruments Business** (Cabinet Office
Ordinance No. 52 of 2007, "金商業等府令"); and the FSA **Guidelines for Supervision
of High-Speed Traders** (高速取引行為者向けの監督指針), a supplementary volume to
the Comprehensive Guidelines for Supervision of Financial Instruments Business
Operators. Verified 25 August 2026.

| Metric | Engineering Standard |
|---|---|
| Jurisdiction | Japan only. The FIEA definition is narrower and structurally different from MiFID II's "high-frequency algorithmic trading technique"; do not port these tests. |
| Definition | FIEA art. 2(41). Conjunctive: automated decision-making **and** transmission to a designated venue by a prescribed time-shortening method. **No latency threshold and no frequency element.** |
| Registration | FIEA art. 66-50. A person other than a registered high-speed trader may not engage in high-speed trading. |
| Notification route | A registered financial instruments business operator or registered financial institution does **not** register as an HST; it notifies under FIEA art. 29-2(1)(vii). |
| Registering authority | In practice the Director-General of the **Kanto Local Finance Bureau** (関東財務局長) for all registrants. An overseas applicant with no business office in Japan files there. |
| Registration number | Format `関東財務局長（高速）第N号`. A single running series. |
| Minimum capital | JPY 10,000,000 (FIEA art. 66-53(5)(b), Enforcement Order art. 18-4-9). |
| Minimum net assets | Zero — i.e. must not be balance-sheet insolvent (FIEA art. 66-53(7), Enforcement Order art. 18-4-10). |
| Japan representative | Foreign corporations and non-resident individuals must appoint a representative or agent in Japan; failure is a registration refusal ground (FIEA art. 66-53(5)(c), (6)(b)). |
| System management | FIEA art. 66-55 with 金商業等府令 art. 336. Hard and soft limits, anomaly monitoring, load testing and a kill switch are the supervisory expectations under Guidelines III-2-1-2. |
| Pre-trade value limit | **Firm-set.** The FSA requires limits calibrated to the trader's characteristics and scale; it publishes **no yen figure**. Any number in code is a house parameter. |
| Books and records | FIEA art. 66-58 with 金商業等府令 art. 338. |
| Filing language | Statutory filings may be prepared **in English** with no Japanese translation required (金商業等府令 arts. 2, 326(2)–(3)). |
| Supervision / inspection | FSA supervises (registration revocation, business improvement orders); the SESC inspects. |

## The FIEA Article 2(41) Definition

An act is high-speed trading when the decision to perform it "is made
automatically by an electronic data processing system" (電子情報処理組織により自動
的に行われ) **and** the transmission of the information necessary to carry out the
resulting securities trade or market derivatives transaction, to a financial
instruments exchange or other person specified by Cabinet Office Order, uses a
method specified by Cabinet Office Order "for shortening the time normally
required for that transmission".

The acts covered are (i) securities trading or market derivatives transactions,
(ii) the entrustment of (i), and (iii) acts specified by Cabinet Order as
equivalent — Enforcement Order art. 1-22 adds investment management (including
giving instructions) that involves performing (i), and entering into an OTC
derivative such as a total return swap with a person performing (i) so as to
cause them to do so.

Note what is absent: **speed of execution is not a criterion, and neither is
order frequency.** The concept keys off the *transmission arrangement*, not off
any measured latency. The FIEA also reserves a power to carve acts out of the
definition by Cabinet Order where investor protection is not prejudiced, but no
such carve-out was made.

### The transmission destination — 定義府令 art. 26(1)

Only transmission to a **designated** financial instruments exchange or
authorised PTS operator counts. The designating instrument is the FSA notice
"高速取引行為となる情報の伝達先を指定する件" (FSA Notice No. 50 of 2017), which
originally designated seven: Tokyo Stock Exchange, Osaka Exchange, Nagoya Stock
Exchange, Fukuoka Stock Exchange, Sapporo Stock Exchange, and the PTS operators
SBI Japannext and Chi-X Japan (now trading as Cboe Japan).

**This notice is amended over time.** Osaka Digital Exchange was added by the
amendment published 26 June 2026. Treat the designated set as dated
configuration, never as a constant: `DEFAULT_DESIGNATED_VENUES` in the script is
a snapshot as at 2026-06-26 and is overridable through the engine constructor.

### The transmission method — 定義府令 art. 26(2)

Two requirements, **both** of which must be met:

1. The facility housing the order server is located at the place where the
   designated venue installs its matching engine, **including places adjacent or
   proximate to it** — i.e. co-location or proximity hosting.
2. A mechanism is in place to prevent the transmission in question from
   contending with other transmissions. The FSA Guidelines III-3-1-2 give a
   contract for exclusive use of a virtual server with the executing broker as
   an example.

### Attribution where several parties are in the chain

Where an executing broker, a foreign securities firm and an SPC each touch the
order, the art. 2(41) test is applied **separately to each party's own act**.
The FSA's responses to the December 2017 public comments record that a trading
participant merely relaying (取次ぐ) an HST's entrusted order to the exchange is
not itself performing high-speed trading (comments 31–34), while a domestic
financial instruments business operator that performs high-speed trading for an
overseas affiliate's proprietary account under a discretionary investment
contract is the party whose act qualifies (comment 37).

## Registration and Ongoing Obligations

Application under FIEA art. 66-51 attaches a pledge of no refusal grounds, the
**業務方法書** (business method statement), the articles of incorporation and
certificate of registered matters, and further documents under 金商業等府令
art. 329.

The 業務方法書 must set out (金商業等府令 art. 328):

| Item | Content |
|---|---|
| (i)–(iii) | Basic operating principles, method of business execution, division of duties |
| (iv) | **For each trading strategy, an outline of it** — the strategy type, the venues used, the types of securities, and the executing brokers used (Guidelines III-3-1-1(2)(i)) |
| (v)–(vi) | Names and titles of the compliance officer and the business management officer |
| (vii) | Outline of the trading system, its installation location and maintenance method |
| (viii) | Measures to manage the trading system adequately |

Trading strategy types, per Guidelines III-3-1-1(2)(i) and used in the FSA's own
statistics: **market-making, arbitrage, directional, other**.

Books and records under FIEA art. 66-58 with 金商業等府令 art. 338 are unusually
specific: order slips must record the **timestamp and order acceptance number
notified by the venue** (para. 6); records must be created so that **the content
of the program used to generate the order can be confirmed** (para. 7(i)); and
books must be organised so entries are **readily searchable** (para. 7(ii)).
Storing only the strategy label is not sufficient.

Business reports follow art. 66-59; commencement and discontinuance
notifications follow arts. 66-60 and 66-61.

## Exchange-Level Obligations (TSE)

The Tokyo Stock Exchange amended its rules alongside the regime:

| Rule | Requirement |
|---|---|
| Business Regulations art. 14(1)(7) | Where a quote/order relates to high-speed trading as defined in FIEA art. 2(41), that fact must be indicated. |
| Brokerage Agreement Standards art. 6(5) | A customer entrusting high-speed trading orders must indicate to the trading participant, **on each occasion**, the type of trading strategy as separately prescribed by the exchange. |

The FSA's quarterly *Trends in High-Speed Trading* has reported orders placed
from co-location servers **without** the high-speed trading identification flag
set, at a low single-digit percentage of orders and trading value — i.e. this is
a live compliance gap, not a theoretical one.

## Supervisory Expectations — Guidelines III-2-1-2

Section III-2-1-2 (異常動作等の防止等の管理態勢) asks whether the trading system
adopts arrangements preventing "orders unintended by the high-speed trader or
otherwise liable to cause disruption to the financial instruments market"
(異常注文). It looks for:

- **hard limits and soft limits** embedded in the trading system, suited to the
  trader's characteristics and scale, with continuous monitoring;
- a **kill switch** — expressly, "a function to cancel anomalous orders already
  transmitted to the market" (市場に伝達された異常注文をキャンセルする機能（いわゆる
  キルスイッチ）);
- **load testing** confirming the system has sufficient processing capacity under
  assumed increases in data volume.

These are supervisory guidance implementing the statutory system-management
obligation in FIEA art. 66-55, not free-standing statutory rules.

## Consequences of Trading Unregistered

The operative gate is the broker's, not the trader's: **FIEA art. 38(viii)**
makes it a prohibited act for a financial instruments business operator to accept
an entrustment of high-speed trading from a person not registered for it.
金商業等府令 art. 116-4 extends the prohibition to accepting entrustment from an
HST subject to a business suspension order, or one whose adequate
trading-system management measures cannot be confirmed. In practice an
unregistered participant loses access to the flow rather than being quietly
tolerated.

The FSA may revoke a registration or order business improvement; the SESC may
inspect. Enforcement has so far been sparse: as at the 23 July 2026 register
there had been a single published administrative action against a high-speed
trader — the cancellation of the registration of Serenity Capital Management LLC
(関東財務局長（高速）第48号) on 7 February 2025 under FIEA art. 66-63(3), on the
ground that the location of its business office could not be confirmed. That is
a deregistration for unverifiable presence, not a conduct sanction, and it should
not be cited as evidence of an aggressive enforcement posture.

Unregistered high-speed trading is also addressed by the FIEA's penal provisions.
The specific penal article and item were **not verified** during this audit, so
no figure is asserted here; confirm against the current statute before relying on
any penalty amount.

## Scale of the Regime

| Measure | Value | As of |
|---|---|---|
| Registered high-speed traders | 53 (numbers 1–90 issued; gaps are withdrawals) | 23 July 2026 |
| Registered high-speed traders | 52 | 31 December 2023 |
| HST share of TSE trading value | ~35% | Oct–Dec 2023 |
| HST trading value by strategy | Directional ~50%, Other ~20%, Market-making ~15%, Arbitrage ~10% | Oct–Dec 2023 |
| HST orders placed via co-location | ~75% of total orders | Oct–Dec 2023 |
| HST share of total orders (new/cancel/amend) | ~60–90% | Oct–Dec 2023 |

Almost every registrant is a foreign corporation, which is why the Japan
representative requirement is the norm rather than the exception in practice.

## Sources

- FIEA and subordinate legislation — Japanese Law Translation, <https://www.japaneselawtranslation.go.jp/en/laws/view/4405>
- FSA, registration information for high-speed traders (English) — <https://www.fsa.go.jp/en/regulated/hst/>
- FSA, register of high-speed traders (高速取引行為者) — <https://www.fsa.go.jp/menkyo/menkyoj/kousoku.pdf>
- FSA, Guidelines for Supervision of High-Speed Traders, section III — <https://www.fsa.go.jp/common/law/guide/hft/03.html>
- FSA, *Trends in High-Speed Trading*, March 2024 — <https://www.fsa.go.jp/en/regulated/trends_hst/20240329/HFT.pdf>
- FSA, partial amendment of the notice designating transmission destinations, 26 June 2026 — <https://www.fsa.go.jp/news/r7/shouken/20260626/20260626.html>
- Kanto Local Finance Bureau, administrative action against Serenity Capital Management LLC, 7 February 2025 — <https://www.fsa.go.jp/news/r6/hst/20250207.html>
- Anderson Mōri & Tomotsune, *高速取引行為に係る登録制の導入に関する政令・内閣府令等の改正について*, February 2018 — <https://www.amt-law.com/asset/pdf/bulletins2_pdf/180214.pdf>
- Oh-Ebashi LPC & Partners, *High-Speed Trading Regulations in Japan*, Spring 2023 — <https://www.ohebashi.com/jp/newsletter/NL_en_2023spring-Otawa.pdf>
