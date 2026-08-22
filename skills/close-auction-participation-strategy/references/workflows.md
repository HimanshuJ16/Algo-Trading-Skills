# Workflows for Closing Auction Participation

1. **Imbalance feed connection**:
   - Nasdaq: subscribe to TotalView-ITCH and consume the NOII message (type `I`).
     Keep only messages with Cross Type `C` (Closing Cross); the same message
     type also carries opening, halt/IPO and Extended Trading Close crosses.
   - NYSE: consume the closing imbalance publication (paired quantity, total
     imbalance quantity, closing-only interest price, continuous book clearing
     price), disseminated every second from 15:50 when changed.

2. **Normalization**:
   - Build a `NoiiMessage` with a **timezone-aware** timestamp. ITCH timestamps
     are nanoseconds since midnight *Eastern* — attach `America/New_York`
     explicitly at the parser boundary, never later.
   - Map NYSE fields: closing-only interest price → `far_price`, continuous book
     clearing price → `near_price`.

3. **Metrics**:
   - `imbalance_ratio = imbalance_shares / (paired_shares + imbalance_shares)`.
   - Track drift of the indicative clearing price across successive messages; a
     ratio that is large but shrinking is a different trade from one that is
     growing into the cross.

4. **Time-based decision engine** (times are US/Eastern):
   - 15:50–15:55 (Nasdaq): imbalance size and direction are known, but there is
     **no** indicative clearing price yet. Use this window to pre-size, not to
     price. On NYSE this window already carries clearing prices, but entry is
     restricted to the contra side of a published Significant Imbalance.
   - 15:55–15:58 (Nasdaq): Near/Far prices arrive every second and LOC entry is
     still open. This is the actionable window for a Nasdaq contra-side LOC,
     with the late-LOC re-pricing caveat.
   - After the venue cutoff: no new entry. After 15:50: no cancel or modify on
     either venue.

5. **Sizing and pricing**:
   - `qty = floor(min(max_participation_pct × imbalance_shares,
     max_auction_volume_pct × (paired_shares + imbalance_shares), target_qty))`.
   - `limit = basis_price ∓ price_concession_bps`, rounded away from the
     aggressive side to `tick_size` (sell limits up, buy limits down).
   - Refuse to price when the chosen basis price is non-positive or not yet
     disseminated for the venue.

6. **Submission**:
   - Evaluate with the *intended submission time*, not the message timestamp,
     and keep a latency buffer ahead of the cutoff.
   - Submit as LOC; the helper emits LOC parameters only. Record the decision
     reason for every message, including the rejections — the rejection trail is
     what makes a missed close explainable afterwards.

7. **Post-cross processing**:
   - Reconcile fills after the 16:00 cross against the official closing price,
     and treat unfilled LOC quantity as opportunity cost carried into the next
     session rather than as a completed execution.
