# Pre-Flight Checklist — PSE (Philippine Stock Exchange) Order Entry

## Reference Price & Instrument

- [ ] Is the **Reference Price** (previous close / LACP) sourced from reference data, and validated as finite and strictly positive before use?
- [ ] Is the market segment (`PHP` / `DDS`) taken from reference data rather than inferred from the price?
- [ ] Is the Reference Price on the PSE price lattice — and is an off-lattice value logged rather than silently bucketed?

## Board Lot & Tick Size

- [ ] Are the board lot and tick size derived from the **Reference Price**, never from the order price or the last traded price?
- [ ] Is it understood that they are fixed for the **whole trading day** and do not change when the stock trades through a band boundary?
- [ ] Are band `From`/`To` bounds treated as **inclusive** on both sides?
- [ ] Is the peso table the full 15-band Article IV Section 8 schedule (1,000,000 shares down to 5)?
- [ ] Do dollar-denominated securities use the **DDS** table rather than the peso one?
- [ ] Is tick alignment tested with exact `Decimal` modulo rather than `round(price * 10000) % round(tick * 10000)`?

## Static Threshold

- [ ] Is the band **+50% / −30%** — asymmetric — rather than a symmetric ±50%?
- [ ] Is a replay of a session on or before **23 March 2020** switched back to the symmetric ±50% figure?
- [ ] Is the ceiling rounded **down** and the floor rounded **up** onto the Reference Price's tick?
- [ ] Are both bounds treated as **inclusive**, so an order at exactly the ceiling is accepted?
- [ ] Is the band computed in `Decimal`, so a Reference Price of PHP 0.30 yields a ceiling of exactly PHP 0.45?

## Dynamic Threshold

- [ ] Is the dynamic threshold checked at all, or is the validator static-only?
- [ ] Does the percentage come from the **current PSE circular** for that security's cluster (20% / 15% / 10% for A / B / C) rather than a guess?
- [ ] Is the band measured against the **last traded price**, not the Reference Price?
- [ ] Does a half-supplied payload (price without percentage, or vice versa) **raise** rather than silently skip the check?

## Order Payload

- [ ] Is `side` validated against `BUY`/`SELL` rather than merely upper-cased?
- [ ] Is `quantity` a strictly positive integer, with `bool` explicitly rejected?
- [ ] Is the quantity a whole multiple of the board lot — no odd lots on the main board?
- [ ] Are NaN and infinite prices rejected rather than silently failing every comparison?

## Report & Error Handling

- [ ] Does the report carry the applied lot, the tick, the Reference Price and **both** band bounds, so a rejection can be repriced?
- [ ] Is malformed input **raised**, never returned as a status a caller could mistake for an exchange rejection?

## Scope & Change Management

- [ ] Is the board lot schedule injectable, ready for the proposed **One Lot One Share** structure (CN-2025-0046) and the Nasdaq Eqlipse Trading migration — without forking the module?
- [ ] Is it understood that halts, suspensions, the market-wide circuit breaker, session mechanics, the Odd Lot Market, commissions and taxes are **not** modelled here, and that the PSE matching engine remains authoritative?
