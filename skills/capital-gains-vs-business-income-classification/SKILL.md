---
name: capital-gains-vs-business-income-classification
description: >-
  Use when sorting a year of closed trades into the tax buckets one authority actually
  recognises, under Indian s.43(5) and s.2(42A), US IRC s.1222, s.475(f) and s.1256, or
  the Canadian tests. The categories differ by jurisdiction, so there is no generic
  mode.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: tax, capital-gains, business-income, speculative, classification
  brokers_frameworks: Generic Post-Trade
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

> **Engineering guidance, not tax advice.** This skill encodes how to build and verify the
> bookkeeping and the checks around it; it does not determine anyone's tax position. Confirm
> the treatment with a qualified tax professional in the relevant jurisdiction before relying
> on its output for a filing.

## When to Use

Use this skill when sorting a year's closed trades into the tax buckets a *particular* tax authority recognises — for end-of-year reporting, or for modelling net post-tax PnL in a backtest. The classification is jurisdiction-specific down to the category names, so the engine takes `Jurisdiction` as a required argument:

| | India | United States | Canada |
|---|---|---|---|
| Business income split by speculation? | **Yes** — speculative vs non-speculative (s.43(5)) | No such concept | No such concept |
| Capital gains split by holding period? | Yes — 12 months listed, 24 months otherwise (s.2(42A)) | Yes — more than one year (IRC s.1222) | **No** — holding period is irrelevant |
| What makes trading income business income? | Shares held as stock-in-trade (CBDT Circular 6/2016); non-delivery settlement is always speculative business | An IRC s.475(f) mark-to-market election | Income account under the IT-479R factors, absent an ITA s.39(4) election |

## When NOT to Use

- **For a jurisdiction not listed above.** There is no generic mode and no default. Porting India's speculative/non-speculative split to a US or Canadian return invents categories that do not exist on those forms.
- **To compute tax payable.** The engine classifies only. It applies no rates, no s.112A ₹1.25 lakh exemption, no s.111A rate, no Canadian inclusion rate, no US bracket.
- **To net losses across buckets.** Bucket-level set-off rules are separate and asymmetric — an Indian speculative business loss can only be set off against speculative business income (s.73), which the aggregate output does not enforce.
- **As the s.1256 engine.** US s.1256 contracts are flagged and routed out to `section-1256-contract-tax-treatment-us-futures`; the 60/40 split is not computed here.
- **To decide whether the taxpayer *is* a trader.** Trader-versus-investor status, the s.475(f) election, the s.39(4) election and the stock-in-trade position are all filing positions the taxpayer takes with an adviser. They are inputs (`TaxElections`), never inferences the engine draws from trade frequency.

## Prerequisites

- Closed round-trip trades with acquisition and disposal timestamps. Prefer timezone-aware timestamps: the session *date* decides intraday classification, and a US session closing at 16:00 ET falls on the next UTC date.
- Asset class tags, plus whether the instrument is listed on a recognised exchange (`is_listed`).
- For India, a delivery flag (`settled_without_delivery`) per trade. The statutory test in s.43(5) is settlement without actual delivery, not the calendar.
- The taxpayer's elections for the year, as a `TaxElections` object.

## Workflow

1. **Fix the Jurisdiction First**: Construct `TaxClassificationEngine(Jurisdiction.INDIA | UNITED_STATES | CANADA, elections)`. There is no neutral default, because the output categories differ per jurisdiction. `aggregate_pnl` returns only the buckets that exist in that jurisdiction, so a caller cannot read a zero out of a bucket its tax code does not have.
2. **Supply Elections, Never Infer Them**: Populate `TaxElections`. A high trade count does not by itself make a US trader's gains ordinary — only a timely s.475(f) election does. Note that s.39(4) is unavailable to traders and dealers under s.39(5) and cannot be rescinded once made.
3. **Normalise Timestamps**: The engine converts aware timestamps to the exchange-local session timezone before taking dates, and rejects a trade whose open and close differ in timezone awareness rather than comparing them and producing a `TypeError` deep in the call stack.
4. **Classify**: `explain_trade()` returns the category *and* the rationale naming the provision applied — keep the rationale in the ledger, because it is what makes the classification auditable a year later.
5. **Apply the Delivery Test (India)**: If `settled_without_delivery` is not supplied, the engine falls back to a same-session-date proxy and logs a warning. Treat that warning as a data-quality defect to fix, not as noise: the proxy misclassifies delivery-based same-day trades and BTST positions.
6. **Route the Buckets**: Send each category to its own return line and its own set-off pool. Deduct infrastructure and data costs only against business-income buckets.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming the Categories Travel**: "Speculative business income" is a creature of India's s.43(5). A US return has no such line; an intraday US equity round trip is an ordinary short-term capital gain absent a s.475(f) election. Canada has neither the speculative split *nor* a long-term/short-term split.
- **Counting 365 Days Instead of 12 Months**: Both India (s.2(42A): "not more than twelve months") and the US (IRS Topic 409: "more than one year") use calendar periods and a *strict* threshold. A position bought 1 Jan 2024 and sold 31 Dec 2024 is 365 days but is still short-term; and one sold on the 1 Jan 2025 anniversary is *also* still short-term. A `days >= 365` test gets both wrong.
- **Treating F&O as Business Income Everywhere**: India's s.43(5) proviso (d) carve-out only reaches *eligible* derivative transactions on a *recognised stock exchange* — an OTC derivative stays speculative. In the US the same contract is likely a s.1256 contract with a 60/40 split, and in Canada IT-346R lets a speculator report futures on capital account if done consistently.
- **Deriving Session Dates from UTC**: Taking `.date()` off a UTC timestamp turns a single US or Canadian session into a two-day hold, flipping an intraday trade into an overnight one.
- **Switching Basis Between Years**: Every election here carries a consistency obligation — CBDT Circular 6/2016, IT-346R, and the irrevocable s.39(4) election alike. Flipping treatment year to year is what invites the assessment.
- **Summing a Tax Ledger in Floats**: Binary float drift lands in a filed figure. The engine accumulates in `Decimal` and converts incoming floats via `str()`.
- **Deducting Expenses Against Capital Gains**: Server, data-feed and execution costs are deductible against business income, not against a capital gain.
- **Ignoring Wash Sale / Superficial Loss Rules**: Neither is applied here — see `wash-sale-rule-tracking-us`.

## Verification

- Classify one trade bought 1 Jan 2024 and sold 1 Jan 2025 under `Jurisdiction.UNITED_STATES`: it must be `SHORT_TERM_CAPITAL_GAINS`, not long-term. Move the disposal to 2 Jan 2025 and confirm it flips to `LONG_TERM_CAPITAL_GAINS`.
- Classify the same intraday equity trade under all three jurisdictions and confirm three different answers: `SPECULATIVE_BUSINESS` (India), `SHORT_TERM_CAPITAL_GAINS` (US), `BUSINESS_INCOME` (Canada, absent a s.39(4) election).
- Classify a seven-year Canadian equity hold and confirm no `LONG_TERM_CAPITAL_GAINS` is ever produced.
- Run `python -m unittest discover -s skills/capital-gains-vs-business-income-classification/scripts` and confirm all tests pass.

## Related Skills

- `section-1256-contract-tax-treatment-us-futures`
- `mark-to-market-election-for-active-traders-us`
- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
- `multi-jurisdiction-tax-residency-implications`
- `best-execution-record-keeping-global`
