# Standards for KRX (KOSPI / KOSDAQ) Cash Equity Integration

Source of record: the KRX market regulation portal (`regulation.krx.co.kr`),
the KRX English *Guide to Trading in the Korean Stock Market*, and the KRX
member notices on the 25 January 2023 tick size revision as circulated by
Korean brokers. Transcribed here on **25 August 2026**. The trading system is
**EXTURE 3.0**, live since **25 January 2023** (matching and market data;
clearing and settlement followed on 4 October 2023), succeeding EXTURE+.

| Metric | Engineering Standard |
|---|---|
| Base Currency | Korean won (KRW). KRX quotes whole won; the finest tick is KRW 1. |
| Short Code Format | Six characters. The first five are digits; the sixth is a digit **or** a letter. `I`, `O` and `U` are excluded from the letter set. Leading zeros are significant. |
| Tick Size Rules | Order prices MUST be an exact multiple of the tick for the **order price's** band, drawn from the schedule applicable to the instrument's class. Bands are 「이상 ~ 미만」 — the upper bound is **exclusive**. |
| Daily Price Limits | KRX computes an **amount**: base price × pct, with the sub-tick remainder **truncated** (절사) using the tick of the **base price's** band. 상한가 = base + amount; 하한가 = base − amount. Never a percentage deviation test against the order price. |
| Trading Unit | 1 share for stocks, ETFs and ETNs. ELWs trade in units of 10 warrants. There is no board-lot multiple to enforce for equities. |
| Trading Hours | Regular session 09:00–15:30 KST; orders accepted from 08:00. Pre-hours 07:30–09:00; after-hours 15:40–18:00. |
| Settlement | T+2. |
| Scope | These rules govern the KRX auction cash equity market. Derivatives on the KRX derivatives market, and off-auction block/basket trading, are out of scope. |

## Short Codes (단축코드)

KRX issues a 12-character standard code (ISIN-style, e.g. `KR7005930003`) and a
six-character short code (`005930`) for each listed issue. The short code is
what an order carries.

- The sixth character is the **share-class** character. `0` denotes common
  stock. Preferred lines issued before 2013 were assigned `5`, `7`, `9` in
  order; those issued from 2013 onward are assigned letters starting at `K`.
- Listed examples with a letter today: `00781K` (Korea Circuit 2nd preferred),
  `03473K` (SK preferred), `18064K` (Hanjin KAL preferred), `02826K` (Samsung
  C&T preferred). Samsung Electronics' preferred line predates the change and
  remains numeric: `005935`.
- **Code system reform, announced May 2023, effective for codes issued from
  1 January 2024.** KRX widened the code space — issuance capacity rose from
  roughly 50,000 to roughly 165,000 short codes — by mixing letters into the
  short code: the **sixth character of stock short codes**, and the 3rd and
  5th–7th positions for ETNs (ELWs follow the ETN rule; subscription warrants,
  subscription rights and corporate bonds follow the stock rule). `I`, `O` and
  `U` are excluded to avoid confusion with `1`, `0` and `V`. The second
  character is partitioned: `0`–`4` for stocks, `5`–`8` for ETNs.
- **Previously issued codes are not reissued.** All-numeric and alphanumeric
  short codes coexist permanently. A validator that accepts only digits
  rejects issues that trade today.

> The published ETN wording ("3rd and 5th–7th positions") cannot map entirely
> onto a six-character short code, so the ETN/ELW pattern implemented in
> `scripts/` is the deliberately permissive reading: digits in the first two
> positions, digit-or-letter thereafter. Confirm a specific code against the
> KRX standard code system before relying on it in production.

## Tick Size (호가가격단위)

Bands are published as 「1,000원 이상 2,000원 미만」 — "1,000 or above, less
than 2,000". The upper bound is **exclusive**, so a price sitting exactly on a
boundary takes the **coarser** tick of the band above. This is the opposite
convention to the Tokyo Stock Exchange, whose bands are 「以下」 (inclusive) —
do not carry an implementation across from `japan-exchange-group-jpx-api-integration`
without changing the comparison.

### Stocks — in force since 25 January 2023

Applies to KOSPI (유가증권시장), KOSDAQ and KONEX alike, and to K-OTC.

| Price (KRW) | Tick (KRW) | Previously (to 24 Jan 2023) |
|---|---|---|
| below 2,000 | 1 | 1 below 1,000; **5** from 1,000 |
| 2,000 – below 5,000 | 5 | 5 |
| 5,000 – below 20,000 | 10 | 10 below 10,000; **50** from 10,000 |
| 20,000 – below 50,000 | 50 | 50 |
| 50,000 – below 200,000 | 100 | 100 below 100,000; **500** from 100,000 (KOSPI) / 100 (KOSDAQ) |
| 200,000 – below 500,000 | 500 | 500 (KOSPI) / **100** (KOSDAQ) |
| 500,000 and above | 1,000 | 1,000 (KOSPI) / **100** (KOSDAQ) |

The revision was the first since 2010. It cut the maximum tick-to-price ratio
from roughly 0.5% to roughly 0.25%, narrowing three bands (1,000–2,000,
10,000–20,000, 100,000–200,000) and lifting KOSDAQ's 200,000-and-above bands
so both boards now share one schedule. Matching changes were applied to single
stock futures.

**Historical reconstruction.** Quotes before 25 January 2023 must be validated
against the *old* per-board tables, not this one. A backtest that applies
today's schedule to 2022 data will accept prices that never existed on the
book.

### ETFs, ETNs and ELWs

**KRW 5 at every price level**, with no bands. These products were explicitly
excluded from the 2023 revision and retain the flat tick.

## Daily Price Limit (가격제한폭)

KOSPI and KOSDAQ have used **±30%** of the base price since **15 June 2015**,
widened from ±15%. KONEX remains at **±15%**.

**The limit is an amount, computed and truncated before it is applied.** The
KRX regulation portal states it directly: 「가격제한폭은 기준가격에 0.3를
곱하여 산출한 금액으로 하며, 산출된 금액중 기준가격대에 해당하는
호가가격단위 미만의 금액은 절사」 — the limit is the base price multiplied by
0.3, with the portion below the tick unit *of the base price's band*
discarded. 상한가 and 하한가 are then the base price plus and minus that
amount.

KRX's own worked example:

| Step | Value |
|---|---|
| 기준가격 (base price) | KRW 9,940 |
| Tick band for 9,940 | 5,000 – below 20,000 → KRW 10 |
| 9,940 × 0.3 | KRW 2,982 |
| 가격제한폭 after 절사 | **KRW 2,980** |
| 상한가 | KRW 12,920 |
| 하한가 | KRW 6,960 |

Consequences an implementation must respect:

- A `abs(P − base) / base <= 0.30` test is **wrong**. It accepts KRW 12,922
  here (+30.00%), which is outside the published band.
- The band is **symmetric** — truncation is applied once, to the amount, not
  separately to each bound — so both bounds stay inside the nominal
  percentage.
- Both bounds are **inclusive**. An order at exactly 상한가 is the limit-up
  price and is tradeable.
- The bounds are not themselves guaranteed to be tick-aligned when the band
  spans a tick boundary. Validate tick alignment against the *order* price
  independently of band containment.
- For a base price low enough that base × pct falls below one tick, the
  truncated amount is zero and the band collapses to the base price. This is
  what the published formula yields; it is far below any realistic listed
  price and is not a special case in the rules.

**Exempt from the daily price limit**: issues in liquidation trading
(정리매매), subscription warrants and subscription rights (신주인수권증권·
증서), and ELWs. Off-hours single-price sessions and the KONEX market run
their own ranges.

**Base price (기준가격)** is normally the previous session's closing price,
adjusted for corporate actions such as ex-dividend and stock splits. On a
listing day it is set by auction rather than carried forward. The engine takes
it as an input and cannot derive it.

## Not Modelled Here

Volatility Interruption (변동성완화장치) static and dynamic triggers; market-wide
circuit breakers and sidecars; trading halts, suspensions and designation as an
issue subject to caution; the opening and closing call auctions and the
off-hours single-price sessions; the listing-day base-price auction; short-sale
price restrictions and the reporting/disclosure thresholds for net short
positions; foreign investor limits and the investor registration regime;
order-to-trade and participant-level controls; and any credit, margin or
position check. These are enforced by the KRX matching engine and by your
member firm, not by this engine.

## Sources

- KRX Regulation Portal, *코스닥시장 · 매매계약체결방법 · 기준가격/가격제한폭/
  상하한가* (±30% limit, the 절사 rule quoted above, the KRW 9,940 worked
  example, and the exemption for 신주인수권증권·증서):
  <https://regulation.krx.co.kr/contents/RGL/03/03020201/RGL03020201.jsp>
- KRX, *Guide to Trading in the Korean Stock Market* (English; trading hours,
  T+2 settlement, "Daily Price limit 30% of base prices", 1-share trading unit
  for stocks/ETFs/ETNs and 10 warrants for ELWs, and the flat KRW 5 tick for
  ETFs/ETNs/ELWs). **Note:** the tick table in this PDF as retrieved on
  25 August 2026 still shows the *pre-2023* schedule, including the old
  KOSDAQ column — it is cited here for the rules it states correctly, not for
  the tick table:
  <https://global.krx.co.kr/contents/GLB/01/0109/0109000000/guide_to_trading_in_the_korean_stock_market.pdf>
- Samsung Securities, *국내 증권/파생시장 호가가격단위 변경 안내* (KRX member
  notice carrying the before/after tick tables for both boards, effective
  25 January 2023):
  <https://samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=19236>
- Daishin Securities, *한국거래소 제도개편 안내* (same revision; confirms the
  unified seven-band table and 「ETF, ETN, ELW 상품은 현행 호가가격단위(5원)
  유지」): <https://money2.daishin.com/html/Notice/2023/n_07.html>
- Newsis, *한국거래소, 내년부터 호가가격단위 축소* (1 Nov 2022 announcement;
  first revision in 13 years, maximum tick ratio cut from 0.5% to 0.25%):
  <https://mobile.newsis.com/view.html?ar_id=NISX20221101_0002069430>
- eToday, *한국거래소, 新시장시스템 'EXTURE 3.0' 25일 가동* (EXTURE 3.0 live
  25 January 2023; latency 70µs → 50µs, capacity 420m → 940m messages/day;
  clearing phase 4 October 2023):
  <https://www.etoday.co.kr/news/view/2213826>
- Seoul Finance, *거래소, 주권 등 종목코드 체계 개편 — 내년부터 알파벳 혼용*
  (May 2023 KRX announcement: letters in the 6th character of stock short
  codes and the 3rd/5th–7th of ETN codes, effective for codes issued from
  1 January 2024; existing codes unchanged):
  <https://www.seoulfn.com/news/articleView.html?idxno=486580>
- Hankyung, *거래소, 종목코드 체계 개편…코드 중복 방지·발급 여력 확보*
  (same reform; `I`, `O`, `U` excluded; 2nd character partitioned 0–4 stocks /
  5–8 ETNs; capacity 50k → 165k):
  <https://www.hankyung.com/article/2023052334076>
- Shinhan Investment, *주식/파생시장 가격안정화장치 개선(가격제한폭 확대 등)
  안내* (±15% → ±30% on 15 June 2015; 정리매매종목, ELW and 신주인수권증서/
  증권 excluded from the widening):
  <https://open.shinhansec.com/notice/notice_150605_02.html>
- Korea Investment & Securities, *코넥스(KONEX) 거래 주요내용* (KONEX daily
  price limit ±15%):
  <https://file.koreainvestment.com/Storage/customer/guide/regards/konex_notice.html>
