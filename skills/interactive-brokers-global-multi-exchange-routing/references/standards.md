# Broker & Framework Coverage — interactive-brokers-global-multi-exchange-routing

## Contract fields this skill validates

| Field | TWS API meaning | Validation applied here |
|---|---|---|
| `symbol` | "The underlying's asset symbol." | Shape-checked per secType and venue; **never rewritten**. |
| `secType` | `STK`, `OPT`, `FUT`, `IND`, `FOP`, `CASH`, `BAG`, `WAR`, `BOND`, `CMDTY`, `NEWS`, `FUND` | Must be one of the documented values (`CFD`/`CRYPTO` also accepted). |
| `currency` | "The underlying's currency." | Must be a 3-letter ISO-4217-style code; checked against the venue's documented currency set when the venue is known. |
| `exchange` | "The destination exchange." | Must be present; either `SMART` or a venue code. Authority is `ContractDetails.validExchanges`. |
| `primaryExchange` | "The contract's primary exchange. For smart routed contracts, used to define contract in case of ambiguity. Should be defined as native exchange of contract. For exchanges which contain a period in name, will only be part of exchange name prior to period, i.e. ENEXT for ENEXT.BE." | Advisory for stocks; period-trimmed; `SMART` rejected. |
| `conId` | "The unique IB contract identifier." | Not validated — obtaining one via `reqContractDetails` is the exit condition of the workflow. |

## Order fields this skill validates

| Field | TWS API meaning | Validation applied here |
|---|---|---|
| `action` | "Generally available values are BUY and SELL. Additionally, SSHORT and SLONG are available in some institutional-accounts only." | Must be one of the four; `SSHORT`/`SLONG` warned as institutional-only. |
| `totalQuantity` | Typed `decimal` in TWS API v10 (the V9→V10 migration moved size fields from int/double to Decimal). | Must be finite and strictly positive; fractional sizes warned. |
| `orderType` | Free-form order type string. | Empty rejected; unrecognised warned, not rejected. |
| `lmtPrice` | "The LIMIT price. Used for limit, stop-limit and relative orders. In all other cases specify zero." | Required, finite and positive for limit-bearing types; warned when set on others. |

## Routing destinations

`Contract.exchange` is either `SMART` or a direct venue code. **IBKR exposes no per-order
"routing mode" field.** The `routing_mode` in this skill's payload is a local label used to
catch configurations whose stated intent contradicts `Contract.exchange`.

Rebate-seeking behaviour is an **account/TWS-level election**, not an order field: under the
Cost Plus commission structure clients may elect to have non-marketable US stock orders
routed to the exchange with the highest rebate (or the listing exchange, or the
highest-volume venue by add/remove economics), and for options to the exchange offering the
highest rebate. IBKR states best execution at the best possible price remains the priority
and that not all trades will receive rebates.

## Venue registry (starting reference — not authoritative)

`VENUE_REGISTRY` in `scripts/` is a *screen*, not a source of truth. A venue absent from it
produces a warning, never a rejection. Currency sets are the currencies a venue is documented
to quote, not a promise that a given contract trades in all of them.

| IBKR code | Region | Documented currencies | Note |
|---|---|---|---|
| `ISLAND` | US | USD | NASDAQ direct-route destination; the `primaryExchange` value in IBKR's shipped ambiguity sample. |
| `NASDAQ`, `NYSE`, `ARCA`, `AMEX`, `BATS`, `IEX`, `MEMX` | US | USD | `ARCA` is the `primaryExchange` in the current Basic Contracts SPY example. |
| `IBKRATS` | US | USD | IBKR's own ATS; non-marketable orders only. |
| `IBIS`, `IBIS2` | EU | EUR | Xetra. Confirm which segment code applies via `validExchanges`. |
| `FWB`, `SBF`, `AEB`, `BVME` | EU | EUR | Frankfurt floor, Euronext Paris, Euronext Amsterdam, Borsa Italiana. |
| `LSE` | UK | GBP, USD, EUR | GBP domestically, plus USD/EUR lines for depositary receipts and ETFs. |
| `DTB` | EU | EUR, CHF | Eurex. Legacy code used throughout IBKR's shipped contract samples. |
| `EUREX` | EU | EUR, CHF | Eurex. Code used in the current Basic Contracts examples. |
| `SEHK` | HK | HKD, CNH | HKEX runs the HKD-RMB Dual Counter Model, so an SEHK line is not necessarily HKD. |
| `SEHKNTL`, `SEHKSZSE` | HK | CNH | Stock Connect. 6-digit mainland codes (e.g. `603737`, `002436`). |
| `HKFE` | HK | HKD, CNH, USD | HKEX derivatives; alphabetic product symbols (`HSI`), not numeric stock codes. |
| `IDEALPRO` | Global | unconstrained | Forex: `symbol` is the base currency, `currency` the quote currency. |

## Symbol conventions that matter

| Market | Convention | Consequence |
|---|---|---|
| SEHK equities | HKEX allocates 1- to 5-digit numeric codes and publishes them zero-padded for display. **IBKR's shipped SEHK contract sample uses `symbol = "1"`** for the security listed under HKEX code 00001. | Do not pad to match a display or vendor convention; confirm the exact IBKR string with `reqContractDetails`. |
| Stock Connect (`SEHKNTL`, `SEHKSZSE`) | 6-digit mainland codes, quoted in CNH. | A 5-digit HK code is wrong for these venues, and so is HKD. |
| HKEX derivatives (`HKFE`) | Alphabetic product symbols (`HSI`, `MHI`). | Numeric-code rules must be scoped by secType and venue, not by currency. |
| Forex (`CASH`) | `symbol` = base currency, `currency` = quote currency, `exchange='IDEALPRO'`. | Region/currency matching is meaningless; base must differ from quote. |

## Relevant TWS API message codes

| Code | Meaning |
|---|---|
| 200 | "No security definition has been found for the request. The specified contract does not match any in IB's database, usually because of an incorrect or missing parameter." Also carries "The contract description specified for &lt;Symbol&gt; is ambiguous. Ambiguity may occur when the contract definition provided is not unique." |
| 201 | "Order rejected - Reason:" — order-entry rejection, including bad order fields. |
| 321 | "Server error when validating an API client request." |

## Sources

| Claim | Source |
|---|---|
| `primaryExchange` is for smart-routed stock ambiguity; "Should be defined as native exchange of contract"; period-in-name rule (ENEXT for ENEXT.BE); `secType` value list; `conId` is "the unique IB contract identifier" | TWS API — Contract Class Reference, https://interactivebrokers.github.io/tws-api/classIBApi_1_1Contract.html |
| "For certain smart-routed stock contracts that have the same symbol, currency and exchange, you would also need to specify the primary exchange attribute to uniquely define the contract… good practice to include for all stocks"; SPY/`ARCA` example; `EUREX` used for Eurex products; forex example `EUR`/`CASH`/`GBP`/`IDEALPRO` | TWS API — Basic Contracts, https://interactivebrokers.github.io/tws-api/basic_contracts.html |
| `validExchanges` = "Valid exchange fields when placing an order for this contract"; contracts that cannot be smart-routed have `aggGroup = -1`; `reqContractDetails` may match multiple contracts, each returned individually | TWS API — ContractDetails Class Reference, https://interactivebrokers.github.io/tws-api/classIBApi_1_1ContractDetails.html and Requesting Contract Details, https://interactivebrokers.github.io/tws-api/contract_details.html |
| `action` BUY/SELL plus institutional-only SSHORT/SLONG; `totalQuantity` typed `decimal`; `lmtPrice` "Used for limit, stop-limit and relative orders. In all other cases specify zero." | TWS API — Order Class Reference, https://interactivebrokers.github.io/tws-api/classIBApi_1_1Order.html |
| Error 200 text and the ambiguity message; error 201; error 321 | TWS API — Message Codes, https://interactivebrokers.github.io/tws-api/message_codes.html |
| SEHK contract sample `symbol = "1"`, `secType = "STK"`, `currency = "HKD"`, `exchange = "SEHK"`; `USStockAtSmart` (`IBKR`/`USD`/`SMART`) and `EuropeanStock` (`SIE`/`EUR`/`SMART`) carry no `primaryExchange`; `primaryExchange = "ISLAND"` in the ambiguity sample; `DTB` used for Eurex index/option/future samples; `EurGbpFx` forex sample | TWS API shipped contract samples (IBJts `ContractSamples`), e.g. https://github.com/bianster/tws-api/blob/master/IBJts/samples/Cpp/TestCppClient/ContractSamples.cpp |
| Stock Connect contracts carry 6-digit mainland symbols and CNH currency on `SEHKNTL` / `SEHKSZSE` | `reqMatchingSymbols` output in the ib_insync contract-details notebook, https://github.com/erdewit/ib_insync/blob/master/notebooks/contract_details.ipynb |
| HKEX operates an HKD-RMB Dual Counter Model | HKEX — Securities Trading Mechanism, https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en |
| HKEX stock codes are 1–5 numeric digits, allocated by ballot and recycled after delisting | HKEX — Stock Code Balloting Scheme, https://www.hkex.com.hk/Services/Trading/Securities/Overview/Stock-Code-Balloting-Scheme?sc_lang=en |
| Eurex lists CHF-denominated SMI products alongside its EUR book | Eurex — SMI® Futures, https://www.eurex.com/ex-en/markets/idx/country/six/SMI-Futures-952762 |
| SmartRouting weighs transaction costs and add/remove-liquidity fees; Cost Plus clients may elect highest-rebate / listing-exchange / highest-volume routing for non-marketable stock orders, and highest-rebate routing for options; best execution remains the priority and not all trades receive rebates | IBKR — IB SmartRouting, https://www.interactivebrokers.com/en/trading/smart-routing.php (site returns HTTP 403 to automated fetches; read in a browser) |
| "over 170 markets in numerous countries and currencies" | Interactive Brokers Group brokerage-metrics press release, 1 July 2026 (company boilerplate) |
| V9 → V10 migration moved size fields (including `totalQuantity`) from int/double to `Decimal` | TWS API Changelog, https://www.interactivebrokers.com/campus/ibkr-api-page/tws-api-changelog-2/ |

**Unverified at time of writing.** IBKR's own product-listing and SmartRouting pages return
HTTP 403 to automated requests, so the per-venue product and currency listings could not be
fetched programmatically. That is precisely why `VENUE_REGISTRY` is a screen that *warns* on
unknown venues rather than a table that rejects against them, and why the workflow terminates
at `reqContractDetails` rather than at a local pass. The exact IBKR symbol string for any
given Hong Kong listing is likewise a lookup, not a formula — the sample above establishes
that IBKR uses unpadded codes, not that every code is short.

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

No jurisdiction-specific regulatory requirement is asserted by this skill. Venue selection
intersects with best-execution obligations that differ by jurisdiction and client
classification (US Reg NMS order protection, EU/UK MiFID II execution-factor and reporting
duties), but nothing here determines compliance with any of them — those live in
`us-reg-nms-order-protection-rule-compliance`, `mifid-ii-algo-trading-compliance-eu` and
`best-execution-record-keeping-global`. Treat this skill as contract-addressing correctness
only.
