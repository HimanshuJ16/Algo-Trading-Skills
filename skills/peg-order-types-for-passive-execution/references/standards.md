# Standards for Peg Order Types for Passive Execution

Jurisdiction: **United States, NMS equities.** Every rule below is US-specific.
Peg semantics on non-US venues, in futures and in crypto differ in reference
price, increment and offset sign; none of this transfers unverified.

Status note: verified August 2026.

## Peg reference prices

| Peg type | Reference | FIX 4.4 `ExecInst(18)` | FIX 5.0 `PegPriceType(1094)` |
|---|---|---|---|
| Primary | Inside quotation on the **same** side (buy at the bid, sell at the offer) | `R` | `5` |
| Midpoint | Midpoint of the inside bid and inside offer | `M` | `2` |
| Market | Inside quotation on the **opposite** side | `P` | `4` |

Pegged orders are carried as `OrdType(40) = P` (Pegged).

Nasdaq Equity 4, Rule 4703(d) defines the same three references: "Primary
Pegging means Pegging with reference to the Inside Quotation on the same side of
the market", Market Pegging "with reference to the Inside Quotation on the
opposite side", Midpoint Pegging "with reference to the midpoint between the
Inside Bid and the Inside Offer". Pegging is available only during Market Hours.

## Offset sign convention

| Convention | Meaning of a positive offset | Used by |
|---|---|---|
| Side-relative, aggressive-positive | Moves a BUY **up** and a SELL **down** | This module; Nasdaq Rule 4703(d) "aggressive" / "passive" Offset Amount |
| Signed, side-independent | Added to the peg price regardless of side | FIX `PegOffsetValue(211)` |

Nasdaq's worked example: a buy with Primary Pegging against an `$11.00` Inside
Bid prices at `$11.02` with an aggressive `$0.02` offset and at `$10.95` with a
passive `$0.05` offset. Translating a report into a FIX `PegInstructions` block
therefore requires **negating the offset for SELL orders**.

`PegLimitType(837) = 1` ("strict limit") is the FIX equivalent of this module's
`limit_cap`: a hard bound rather than an "or better" indication.

## Tick lattice (Rule 612)

| Price of the NMS stock | Minimum pricing increment |
|---|---|
| `>= $1.00` | `$0.01` |
| `< $1.00` | `$0.0001` |

Rule 612 prohibits displaying, ranking or **accepting** an order or quotation
priced in a finer increment. It does not prohibit sub-penny **executions**
arising from a midpoint, a volume-weighted algorithm or price improvement, so
long as the execution does not stem from an impermissibly priced sub-penny order
or quote. Nasdaq states that an order with Midpoint Pegging "is not displayed and
may be executed in sub-pennies if necessary to obtain a midpoint price" — which
is why this module allows a **non-displayed** Midpoint peg onto the half-tick
lattice and forces a displayed one back onto the full tick.

**Pending change, not yet in effect.** The September 2024 amendments to Rule 612
add a second `$0.005` increment for "tick-constrained" NMS stocks priced at or
above `$1.00`, assigned twice a year from the Time Weighted Average Quoted
Spread. The original compliance date of 3 November 2025 was extended by
exemptive order to the first business day of November 2026, and extended again
on 11 June 2026 to the first business day of **November 2027**. Until then the
penny/`$0.0001` lattice above governs. When it takes effect, the tick becomes a
per-security, semi-annually reassigned attribute — set `tick_size` per
instrument from the assignment, never from a global default.

## Rounding direction

`PegRoundDirection(838)`: `1` = more aggressive (buy rounds up, sell rounds
down); `2` = more passive (buy rounds down, sell rounds up). Passive rounding can
never carry a price through a ceiling on a buy or a floor on a sell; aggressive
rounding can, so any bound must be re-applied on the lattice afterwards.

## Protective bounds

| Bound | Direction | Rule / source |
|---|---|---|
| Passivity | BUY ceiling `< NBO`, SELL floor `> NBB` | Not a rule — the economic premise of passive execution; a marketable peg pays the taker fee or is rejected post-only |
| LULD band | BUY ceiling at the upper band, SELL floor at the lower band | Nasdaq LULD FAQ: "Pegged orders will be repriced to the band price if their pegging instruction would otherwise have resulted in the order being priced at a level more aggressive than the price band. If priced passively outside the bands they will not be price slid, but will not be executable until the peg obtains a current price that is within the LULD bands." |
| Rule 201 floor | SELL floor at one minimum increment above the NBB, short sales only, while the price test is active | Regulation SHO Rule 201; Nasdaq reprices a restricted short sale "to one minimum allowable price increment above the national best bid" |
| Limit cap | BUY ceiling, SELL floor | Caller instruction; FIX `PegLimitType(837)=1` |

On a given side every one of these is a bound in the same direction, so they
cannot conflict: the tightest binds and a clamp always makes the order less
aggressive.

## Locked, crossed and halted markets

A locked NBBO (`bid == ask`) is legal; a midpoint peg there resolves to the
locking price. A crossed consolidated quote (`bid > ask`) is treated here as a
market-data integrity fault and suspends pegging — venue handling of crossed
books varies by order type and display status, so no single behaviour should be
assumed. Nasdaq cancels orders with Midpoint Pegging when a trading halt is
declared and rejects Midpoint Pegging orders entered during a halt.

## Sources

- FIX 5.0 SP2 `PegPriceType(1094)`, `PegOffsetValue(211)`, `PegLimitType(837)`; FIX 4.4 `ExecInst(18)`, `OrdType(40)`, `PegRoundDirection(838)` — https://www.onixs.biz/fix-dictionary/5.0.sp2/tagnum_1094.html, https://www.onixs.biz/fix-dictionary/4.4/tagnum_838.html, https://www.onixs.biz/fix-dictionary/4.4/tagnum_18.html
- Nasdaq Equity 4, Rule 4703(d) (Pegging), as set out in Nasdaq rule filings with the SEC — https://www.sec.gov/files/rules/sro/nasdaq/2016/34-77454-ex5.pdf
- SEC Regulation NMS Rule 612 (Minimum Pricing Increment), 17 CFR 242.612
- SEC, *Regulation NMS: Minimum Pricing Increments, Access Fees, and Transparency of Better Priced Orders*, Release 34-101070 (18 September 2024) — https://www.sec.gov/files/34-101070-fact-sheet.pdf
- SEC exemptive order extending Rule 612 / 610(c) compliance, Release 34-104172 (31 October 2025) — https://www.sec.gov/files/rules/exorders/2025/34-104172.pdf; further extension to November 2027, Release 34-105656 (11 June 2026) — https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf
- SEC Regulation SHO Rule 201, and Division of Trading and Markets FAQs — https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions-7
- Nasdaq, *North American Markets Limit Up-Limit Down FAQ* — https://www.nasdaqtrader.com/content/MarketRegulation/LULD_FAQ.pdf
