# Workflows for Crypto Transaction Tax Lot Tracking

Jurisdiction: US federal. See `references/standards.md` for the sourced rules.

1. **Lot Acquisition Ingestion**:
   - Record asset, quantity, acquisition timestamp, USD unit cost basis (including acquisition-side transaction costs), and **the wallet or account holding the lot**.
   - Do not store a `days_held` figure — the holding period depends on the disposal date and is computed at disposal time.
2. **Choose the Matching Method Before the Disposal**:
   - `FIFO` — the default; applies absent an adequate identification and requires no election.
   - `HIFO` / `LIFO` — specific identification. Record the identification on your books and records (or a standing order) no later than the date and time of the disposal, and pass it as `identification_reference`. Without it the engine raises.
3. **Swap / Disposal Processing**:
   - Determine USD FMV of the asset or cash received $\implies$ gross proceeds.
   - Net out transaction costs: $\text{Proceeds}_{\text{net}} = \text{Proceeds}_{\text{gross}} - \text{TxCost}_{\text{usd}}$. The cost belongs to the asset disposed of — do not also capitalize it into the received asset's basis.
   - Gas paid in crypto is a separate disposition of that token at its FMV; process it as its own disposal.
4. **Candidate Lot Selection**:
   - Candidates are lots of that asset **in the named wallet**, acquired **on or before** the disposal timestamp. A lot acquired after the disposal is not matchable.
   - If the wallet's open quantity is short, the disposal is rejected with nothing consumed — investigate a missing acquisition, a transfer booked into the wrong wallet, or a double-counted disposal.
5. **Matching and Realization**:
   - Plan the full match across ranked lots, then commit. Realized PnL = net proceeds − cost basis, with net proceeds allocated across matched lots pro rata by quantity.
6. **Form 8949 Output**:
   - Emit one `CryptoLotMatch` row per matched lot: acquisition date, disposal date, quantity, proceeds, basis, gain/loss, term.
   - Term is long-term only if held **more than one year** — counted from the day after acquisition through the day of disposal, by calendar anniversary rather than a 365-day count.
   - A disposal spanning both terms splits across Part I (short-term) and Part II (long-term); report the rows, not the aggregate.
