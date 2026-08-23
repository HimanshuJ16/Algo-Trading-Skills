# Pre-Flight Checklist

## Dividend schedule

- [ ] Are dividend amounts the **gross** declared figures, with withholding carried separately in `withholding_tax_pct`?
- [ ] Is the **ex-date** supplied wherever it differs from the payment date, rather than defaulting to it?
- [ ] Are special/extraordinary dividends tagged `is_special=True` so they stay out of the index accrual?
- [ ] Is the withholding rate for every event inside $[0, 1)$?

## Accrual window

- [ ] Are dividends with an ex-date after expiry ($t^{ex} > T$) excluded?
- [ ] Are **already-ex** dividends excluded, so a stale feed cannot manufacture a false arbitrage signal?
- [ ] For a dividend future, is `accrual_start_years` set to the contract's accrual start rather than left at 0?
- [ ] Has `excluded_dividend_ids` been inspected to confirm the window behaved as intended?

## Pricing

- [ ] Is $\text{PV}(D)$ computed **net** of withholding and discounted from the **payment** date?
- [ ] Is the dividend-futures fair value computed on **gross ordinary** dividends, per Eurex FEXD and CME SDA settlement rules?
- [ ] Do the two forward identities, $(S_0 - \text{PV}(D))e^{rT}$ and $S_0 e^{rT} - \text{FV}(D)$, agree?
- [ ] Is the theoretical forward positive, and are all report `warnings` empty?

## Arbitrage signal

- [ ] Does the spread **strictly exceed** the round-trip cost threshold?
- [ ] Is `reverse_arbitrage_cost_threshold_usd` set explicitly to reflect stock-borrow cost, recall risk, and gross manufactured dividends on the short leg?
- [ ] Is `estimated_net_profit_usd` (not the gross figure) used for the go/no-go decision?
- [ ] Has the venue contract multiplier (Eurex FEXD EUR 100/pt; CME SDA USD 250/pt) been applied before sizing?

## Input hygiene

- [ ] Are non-finite (NaN/Inf) inputs rejected rather than caught and ignored?
- [ ] Are spot $> 0$, maturity $> 0$, and dividend amounts $\ge 0$ enforced?
- [ ] Do the spot, market forward, and dividend amounts share one currency and quotation basis?
