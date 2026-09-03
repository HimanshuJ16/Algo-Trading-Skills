---
name: interactive-brokers-global-multi-exchange-routing
description: >-
  Use when code builds an Interactive Brokers contract and must choose between
  SmartRouting and a direct venue across IBKR global markets, resolving symbol, security
  type, currency and exchange ambiguity. Unnecessary once you hold a conId.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: broker-integration, ibkr, tws-api, ib-insync, smart-routing
  brokers_frameworks: "Interactive Brokers TWS API; ib_insync; IB Gateway"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when code builds an IBKR `Contract` and picks a destination for it — across US,
European, Hong Kong or other IBKR markets. IBKR's own boilerplate put its reach at "over 170
markets" as of 1 July 2026, and a single symbol string can match several of them, so the
question this skill answers is *which* contract you actually addressed and *where* the order
will go.

Two fields carry that weight:

- **`exchange`** is the destination. Either `SMART` (IBKR's SmartRouting, which evaluates
  price, transaction cost and add/remove-liquidity fees across venues and can split an order)
  or a direct venue code such as `ISLAND`, `DTB` or `SEHK`.
- **`primaryExchange`** is *not* a destination. It names the contract's native listing venue
  and exists to break ties: "For smart routed contracts, used to define contract in case of
  ambiguity."

The helper in `scripts/` is a **pre-flight screen**, not an oracle. It catches the parameter
mistakes that produce error 200 ("No security definition has been found for the request") or
an ambiguity error, plus order-field mistakes IBKR rejects at entry. It cannot tell you a
contract exists.

## When NOT to Use

- **You already hold a `conId`.** `Contract.conId` is "the unique IB contract identifier".
  Submitting on a conId removes symbol ambiguity entirely — none of the symbol heuristics
  here add anything.
- **You need to know whether a venue is actually available for a contract.** That is
  `ContractDetails.validExchanges` ("Valid exchange fields when placing an order for this
  contract") and `ContractDetails.aggGroup == -1` (contract cannot be smart-routed). No local
  table substitutes for the lookup.
- **You are comparing execution quality across brokers or venues.** This skill validates
  addressing, not outcomes — see `smart-order-routing-across-venues` and
  `post-trade-execution-quality-scorecard`.
- **You are on IBKR's Client Portal Web API.** Different contract-resolution endpoints and a
  different identifier flow; the TWS API field semantics here do not transfer.
- **You need connectivity, session or process management.** That is
  `ibkr-tws-gateway-headless-launch`.

## Prerequisites

- A running IB Gateway or TWS with API access enabled, and an `ibapi` / `ib_insync` client
  able to call `reqContractDetails`. Validation without that call is half a workflow.
- Market-data and trading permissions for the destination market — a permission failure looks
  nothing like a malformed contract, and this skill will not catch it.
- The order payload: `symbol`, `sec_type`, `currency`, `exchange`, optional
  `primary_exchange`, a local `routing_mode`, plus `action`, `order_type`, `quantity` and
  `lmt_price`.

## Workflow

1. **Screen the payload locally with `IbkrGlobalRoutingEngine.audit_and_route_order`.**
   It rejects only on a positive contradiction of documented IBKR behaviour and returns
   everything else as `warnings`, because rejecting a valid order is as much a production
   failure as accepting an invalid one. Read `report.warnings` even on
   `IBKR_ROUTING_VALIDATED` — that is where "SMART with no listing hint, currency unchecked"
   lives.

2. **Keep `routing_mode` consistent with `exchange`, and know what it is not.**
   `SMART_BEST_EXECUTION` / `SMART_MAX_REBATE` require `exchange='SMART'`; `DIRECT_EXCHANGE`
   requires a venue code. The engine rejects the contradiction rather than letting a config
   that *reads* as direct-routed be silently smart-routed. `SMART_MAX_REBATE` is a local
   label only: rebate-seeking routing of non-marketable orders is an **account/TWS
   election** under the Cost Plus commission structure, not an order field, so the engine
   flags it and emits nothing on the wire.

3. **Set `primaryExchange` on stocks when the symbol is ambiguous — and nowhere else.**
   IBKR calls it "good practice to include for all stocks" but does not require it; its own
   shipped `USStockAtSmart` sample smart-routes `IBKR`/`USD` with no `primaryExchange` at
   all. So a missing hint on a smart-routed stock is a **warning**, not a rejection, and a
   missing hint on an option, future or forex pair is neither. If the venue name contains a
   period, pass only the part before it (`ENEXT`, not `ENEXT.BE`). `primaryExchange='SMART'`
   is always wrong.

4. **Do not reformat symbols to match a market-data vendor's display convention.**
   HKEX publishes zero-padded display codes (`00700`), but IBKR's shipped SEHK contract
   sample uses `symbol = "1"` for the security listed under HKEX code 00001 — the plain
   code, unpadded. The engine therefore validates the shape and returns the symbol
   **unchanged**; a zero-padded input passes with a warning rather than being rewritten in
   either direction. Confirm the exact string with `reqContractDetails`.

5. **Treat currency as a property of the *line*, not of the region.**
   Venue-currency rules break on real instruments: HKEX runs the HKD-RMB Dual Counter Model,
   so an SEHK line can be CNH; IBKR's Stock Connect venues (`SEHKNTL`, `SEHKSZSE`) carry
   6-digit mainland codes quoted in CNH; Eurex lists CHF-denominated SMI products alongside
   its EUR book. For `secType='CASH'` the rule is inverted entirely — `symbol` is the base
   currency and `currency` the quote currency (`EUR`/`GBP` on `IDEALPRO`), so no region rule
   applies. When the destination is `SMART`, the currency is checked against the
   `primaryExchange` listing venue; with no hint, it is not checked at all and says so.

6. **Resolve with `reqContractDetails`, then submit on the returned `conId`.**
   Reconcile `exchange` against `validExchanges`, check `aggGroup != -1` before assuming
   `SMART` is available, and confirm the currency IBKR reports. A local pass is a
   precondition; this call is the gate. `report.requires_contract_details_check` is always
   `True` for exactly this reason.

> Full step-by-step procedure: see `references/workflows.md`.
> Venue table, field semantics and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Zero-padding Hong Kong codes to five digits.** A vendor feed showing `00700` does not
  mean IBKR wants `00700`. IBKR's own SEHK sample is `symbol = "1"`. Padding a symbol IBKR
  lists unpadded converts a resolvable contract into error 200 — and the failure surfaces at
  order entry, not at import.
- **Hard-coding "SEHK means HKD" or "Eurex means EUR".** Both are false for real, routable
  lines (RMB dual counters; CHF SMI products). A currency rule that rejects a valid
  instrument costs you the trade just as surely as one that accepts an invalid one.
- **Applying an equity symbol rule by currency.** Keying off `currency == 'HKD'` alone drags
  HKEX derivatives into it and rejects `HSI` on `HKFE` for not being a numeric stock code.
  Scope symbol-format rules to security type *and* venue.
- **Requiring `primaryExchange` on everything smart-routed.** It is a stock-ambiguity
  tie-breaker. Demanding it on a smart-routed option or future rejects contracts IBKR
  accepts.
- **Validating currency only on direct venues.** The `SMART` path is the common path. A
  validator that skips it passes `currency='EUR'` with `primaryExchange='NASDAQ'` — the
  exact mistake it was written to catch.
- **Letting `routing_mode` and `exchange` disagree.** A config labelled `DIRECT_EXCHANGE`
  that still carries `exchange='SMART'` is smart-routed. The label is documentation; only
  `Contract.exchange` reaches IBKR.
- **Expecting an order-level "maximise rebate" flag.** There isn't one. Rebate-seeking
  routing of non-marketable orders is elected at the account/TWS level under Cost Plus, and
  IBKR is explicit that best execution stays the priority, so not all trades earn rebates.
- **Skipping order-field validation because "IBKR will reject it anyway".** A `LMT` order
  with no limit price, a negative quantity or `action='LONG'` costs a round trip and an
  unexplained rejection in the middle of a live session. `lmtPrice` is documented as used for
  limit, stop-limit and relative orders and zero otherwise.
- **Typing quantity as `int`.** TWS API v10 types `totalQuantity` as `Decimal`; an int-only
  payload cannot express a fractional-share or forex size at all.
- **Treating a local pass as a routable contract.** It means "no known-bad parameter found".
  Only `reqContractDetails` knows whether the contract exists and where it may go.

## Verification

- Run the unit suite: `python -m unittest discover -s skills/interactive-brokers-global-multi-exchange-routing/scripts` — all tests must pass.
- Route `symbol='700'`, `secType='STK'`, `currency='HKD'`, `exchange='SEHK'`,
  `routing_mode='DIRECT_EXCHANGE'` and confirm `report.symbol == '700'` — the audit must not
  rewrite it.
- Route `currency='EUR'` with `exchange='SMART'`, `primaryExchange='NASDAQ'` and confirm
  `REJECTED_CURRENCY_MISMATCH`; the same contract with `exchange='ISLAND'` must reject too.
- Route a `FUT` on `DTB` in `CHF` and an `STK` on `SEHK` in `CNH`, and confirm both are
  `IBKR_ROUTING_VALIDATED` — venue-currency rules must not reject real instruments.
- Route a smart-routed `OPT` with no `primaryExchange` and confirm `IBKR_ROUTING_VALIDATED`;
  route a smart-routed `STK` with no `primaryExchange` and confirm it also validates, but
  carries a warning.
- Route `routing_mode='DIRECT_EXCHANGE'` with `exchange='SMART'` and confirm
  `REJECTED_ROUTING_MODE_CONFLICT`.
- Route an unknown venue code and confirm it validates with a warning rather than rejecting.
- Route `quantity=0`, `lmt_price=None` on a `LMT`, and `action='LONG'` and confirm each gives
  `REJECTED_INVALID_ORDER_PARAMS`.
- Against a live Gateway, call `reqContractDetails` for every contract you validated and
  confirm `validExchanges` contains your `exchange`, that `aggGroup != -1` if you routed
  `SMART`, and that the reported currency matches.

## Related Skills

- `ibkr-tws-gateway-headless-launch`
- `broker-agnostic-adapter-interface`
- `smart-order-routing-across-venues`
- `broker-order-type-capability-matrix`
- `reference-data-symbol-mapping-across-vendors`
- `multi-currency-pnl-and-fx-conversion`
