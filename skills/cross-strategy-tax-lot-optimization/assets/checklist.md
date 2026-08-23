# Pre-Flight Checklist

## Scope

- [ ] Confirmed all sub-strategies sit inside a **single US tax entity** (the module's core assumption).
- [ ] Confirmed no IRC § 475(f) mark-to-market election is in force for these securities — if it is, the wash-sale screen does not apply.

## Inventory

- [ ] Open tax lots carry `lot_id`, `strategy_id`, `symbol`, ISO `acquisition_date`, `days_held`, `cost_basis_per_share`, and `quantity`.
- [ ] `sale_date` is supplied to `optimize_sell_order`, so the long-term test uses calendar arithmetic rather than the `days_held > 365` proxy.

## Netting

- [ ] Offsetting sub-strategy orders are netted **before** lot selection.
- [ ] Only the net residual — not the gross sell quantity — is routed externally and fed to `optimize_sell_order`.

## Lot selection

- [ ] Selection method is chosen explicitly; no code path relies on a fallback for an unrecognized method string.
- [ ] For any non-FIFO method, an adequate identification (or a standing instruction) reaches the broker no later than the earlier of settlement date or the Rule 15c6-1 settlement time — **T+1** for most US equities.
- [ ] Broker confirmations are retained as evidence of the identification.

## Wash sale

- [ ] Replacement purchases are registered across **all** sub-strategies, with signed offsets covering both the 30 days before and the 30 days after the loss sale.
- [ ] Replacement `quantity` is supplied where known, so the disallowance is quantity-limited under § 1091(b) rather than reported as a conservative upper bound.
- [ ] The § 1091(d) basis adjustment and § 1223(3) holding-period tacking are applied downstream in `wash-sale-rule-tracking-us` — this module reports the disallowance only.
- [ ] Accounts outside this module (IRAs, spouse accounts, other brokers) are screened separately; § 1091 applies per taxpayer across every account.

## Reporting

- [ ] Filing figures are re-derived in a decimal ledger; the `float` values here are for lot selection, not for Form 8949 / 1099-B.
- [ ] Internal realized-gain ledger is reconciled against the broker 1099-B to confirm the identification was honoured.
