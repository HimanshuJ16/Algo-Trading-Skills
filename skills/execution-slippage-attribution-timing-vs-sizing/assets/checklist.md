# Pre-Flight Checklist — Execution Slippage Attribution

## Input integrity
- [ ] Are decision price, arrival price, and average execution price all recorded, finite, and $> 0$?
- [ ] Are all three prices on the same quotation basis and currency? (The engine cannot detect a mismatch.)
- [ ] Is `side` exactly `'BUY'` or `'SELL'` — with anything else rejected rather than defaulted to SELL?
- [ ] Is `average_exec_price` quantity-weighted across all fills, not a simple average?
- [ ] Are `order_qty` and `filled_qty` populated, with $0 < Q_{\text{filled}} \le Q_{\text{order}}$?
- [ ] Are all three timestamps timezone-aware ISO-8601 and ordered `decision <= arrival <= completion`?

## Decomposition
- [ ] Are all three components normalised on $P_{\text{decision}}$ (not $P_{\text{arrival}}$ for the sizing leg)?
- [ ] Is the identity $\text{IS}_{\text{total}} = \text{IS}_{\text{timing}} + \text{IS}_{\text{sizing}}$ **verified in full precision**, not asserted in a comment?
- [ ] Is the reported total the directly computed total rather than the sum of the rounded components?
- [ ] Are all components reported in bps to 2 decimal places?

## Fill and scope
- [ ] If `is_partial_fill` is true, has the opportunity cost on the unfilled residual been obtained separately before quoting a headline cost?
- [ ] Have explicit fees (commissions, exchange fees, taxes) been accounted for elsewhere — this engine does not ingest them?
- [ ] Is `executed_is_contribution_bps` (not the per-share figure) used when rolling up to portfolio-level IS?

## Classification
- [ ] Are components ranked by **cost-signed** magnitude, so a favourable leg is never named the slippage driver?
- [ ] Does an exact tie between two material components report `BOTH_DRIVERS_MATERIAL` rather than `ZERO_SLIPPAGE`?
- [ ] Is `secondary_driver_material` checked, so a second material leg is not left unaddressed?
- [ ] Are contribution shares normalised on gross cost and within $[-100\%, +100\%]$?
- [ ] Is the materiality threshold set from this desk's own cost distribution, and recorded on the report?

## Acting on the output
- [ ] Has the timing figure been read alongside `delay_seconds` before attributing it to latency rather than market drift?
- [ ] Is the recommendation being treated as a human triage hint — not wired into live algo parameters?
- [ ] Is any parameter change based on an aggregate across a statistically meaningful sample, not a single trade?
