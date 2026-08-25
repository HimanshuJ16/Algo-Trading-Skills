# Workflows for the MiFIR Article 5 Volume Cap Mechanism

## 0. Inputs and their provenance

- `EsmaSuspensionRegister` — parsed from the **ESMA-published** Volume Cap suspension file (Excel on the ESMA register; results also published as XML/SVCRES). Record the publication date with it; the age check is not optional. An empty suspension list from a real file is a valid, meaningful state.
- `rolling_12m_union_rpw_volume_eur` — Union volume executed under the **Art. 4(1)(a) reference price waiver** only. Not total dark volume, not LIS volume, not negotiated volume. Since January 2026 ESMA derives its own figure from NCA transaction reports; yours will differ, which is exactly why it is labelled an estimate.
- `rolling_12m_total_eu_volume_eur` — total trading volume in that instrument in the Union over the same 12 months. Must be > 0 and ≥ the RPW volume; the engine rejects an inverted pair rather than emitting a share above 100 %.
- `lis_threshold_eur` — the instrument's large-in-scale threshold, from ESMA's transparency calculations or `rts1_lis_threshold_eur(adt, instrument_class)`. Required for a `LIS` order; there is no default and the engine will not invent one.
- `as_of` — the trading date. It selects the regime: on or after 29 September 2025 the single 7 % cap applies (Art. 5(8)); before it, the repealed 4 %/8 % DVC, for backtests only.

## 1. Register load and staleness check

1. Parse the current quarter's suspension file; keep `published_on`.
2. Reject a file published after `as_of` — a future-dated register cannot be applied to today's order (the engine raises).
3. If `as_of - published_on > max_register_age_days` (default 100, from the quarterly cadence in Art. 5(4)), mark `REGISTER_STALE`.
4. `REGISTER_NOT_SUPPLIED` and `REGISTER_STALE` both mean *unknown*, and both block the capped waiver. This is the single most important behaviour in the module: the alternative — treating an unreadable or stale file as "nothing suspended" — fails open, silently, and looks identical to a healthy day in the logs.

## 2. Waiver resolution

1. `RPW` (Art. 4(1)(a)) → subject to the cap.
2. `NTW` (Art. 4(1)(b)) → **not** subject to the cap under the current regime. Only in `LEGACY_DVC` mode is it capped, because the pre-2024 Art. 5(1) also covered Art. 4(1)(b)(i), the negotiated trade waiver for liquid instruments. Legacy mode treats every `NTW` order as capped, which is conservative: the old cap reached only *liquid* instruments, and instrument liquidity is not modelled here.
3. `LIS` (Art. 4(1)(c)) → compare `order_val_eur` against the instrument's threshold, inclusive at the boundary. At or above it, the order sits outside Article 5. **Below it, the LIS claim is rejected**: the order is re-labelled `RPW` and re-evaluated against the cap. Size does not confer the exemption — the waiver does, and the waiver is only available at or above the threshold.
4. `OMF` (Art. 4(1)(d)) → not subject to the cap.

Anything else is rejected at construction. A free-text waiver string that silently falls through to "dark allowed" is how an unmapped venue code becomes a compliance breach.

## 3. Share, headroom and the estimate

$$\text{Union RPW Share \%} = \frac{u \times 100}{t}, \qquad \text{Headroom}_{\text{EUR}} = \frac{c \times t}{100} - u$$

where $u$ is Union RPW volume, $t$ total EU volume and $c$ the cap in per cent.

- Multiply before dividing. $(u/t) \times 100$ rounds twice and evaluates $70{,}000{,}000 / 1{,}000{,}000{,}000 \times 100$ to $7.000000000000001$ — an instrument sitting exactly on the cap, reported as through it.
- Test the breach by cross multiplication, $u \times 100 > c \times t$, strictly greater (Art. 5(1) says "exceeds"). Never on a rounded share: at 2 dp, 6.996 % becomes 7.00 % and a compliant name is suspended by arithmetic.
- Headroom is the operationally useful number: how much more RPW volume the name can absorb this window. Negative means the cap is already through, and the next ESMA publication is likely to suspend it — which is the point of computing an estimate at all.

## 4. Routing decision

Block and route `LIT_VENUE` when the cap applies **and** any of:

1. the register reports an active suspension covering `as_of` (inclusive of both endpoint dates);
2. the official status is unknown (`REGISTER_NOT_SUPPLIED`, `REGISTER_STALE`);
3. `block_rpw_on_estimated_breach` is on (default) and the internal estimate shows a breach.

Otherwise route `DARK_RPW`. Limb 3 is pre-emptive and deliberately conservative — it stops trading dark into a name that is already through the cap and awaiting ESMA's next quarterly file. Set `block_rpw_on_estimated_breach=False` where ESMA's file must be the sole authority and you accept dark execution up to the moment the suspension publishes.

Waivers outside Article 5 return `DARK_LIS_EXEMPT`, `DARK_NTW` or `DARK_OMF` without consulting the register at all — its status for them is `NOT_APPLICABLE`, not "not suspended".

## 5. Audit trail

`VolumeCapAuditReport` keeps `internal_estimate_status` and `official_register_status` in **separate** fields, alongside `regime`, `cap_pct`, the unrounded shares, `rpw_headroom_eur`, `effective_waiver_type` and `suspension_end_date`. Collapsing the two statuses into one flag destroys the only evidence of which authority drove the block — and the answer to a regulator's question is "ESMA's file, published on this date", not "our model".

Persist the report per order. `effective_waiver_type` differing from the submitted `intended_waiver_type` is the record that a LIS claim was rejected, and is worth alerting on: a router repeatedly claiming LIS below the threshold is misconfigured, not unlucky.

## 6. Operating cadence

1. Refresh the suspension file each quarter, within two working days of ESMA's publication (Art. 5(1) gives venues that long to act on it).
2. Diff each new file against the last: newly suspended ISINs need their RPW flow rerouted before the start date, and expiring suspensions release names back to dark on the day after the end date.
3. Alert on register age well before the staleness guard trips — the guard is a backstop that degrades execution quality, not a monitoring strategy.
4. Re-check the threshold periodically: Art. 5(10) has ESMA assessing the 7 % figure annually from 29 September 2027, with the Commission able to change it by delegated act. `cap_pct` is a constructor parameter for that reason.
