# Workflows for KRX (KOSPI / KOSDAQ) Integration

The order of these steps matters. Class selection gates both the code pattern
and the tick schedule, and the base price must be validated before it is used
as a divisor or a band anchor.

## 1. Resolve the Security Class

`STOCK` or `ETF_ETN`. This is a property of the instrument, taken from your
reference data — it is **not** derivable from the price or the code. It selects
the tick schedule (banded vs. flat KRW 5) and the short-code pattern.

Getting this wrong is silent: an ETF validated against the stock schedule
simply rejects legal prices, and nothing in the payload reveals the mistake.

## 2. Short Code Audit (단축코드)

1. Strip whitespace, upper-case.
2. Require six characters: five digits, then a digit or a letter from the
   23-letter set excluding `I`, `O` and `U`.
3. Reject anything else — including short input. Zero-padding is opt-in
   (`allow_zero_pad=True`) and belongs at the boundary where you *know* the
   source stripped leading zeros, not inside the validator. `"5"` pads to
   `"000005"`, a different listed instrument.

Do **not** use `isdigit()`. It rejects `00781K`, `03473K`, `18064K` and
`02826K`, which trade today, and every stock code issued from 1 January 2024.

## 3. Tick Size Audit (호가가격단위)

1. Select the band from the **order price**, using 「이상 ~ 미만」 semantics —
   the upper bound is **exclusive**, so KRW 2,000 takes the KRW 5 tick, not
   KRW 1.
2. `STOCK` schedule (since 25 January 2023): 1 / 5 / 10 / 50 / 100 / 500 /
   1,000 KRW at the 2,000 / 5,000 / 20,000 / 50,000 / 200,000 / 500,000
   boundaries. `ETF_ETN`: flat KRW 5.
3. Test alignment with exact decimal arithmetic. A float tolerance in tick
   units accepts off-grid prices at the coarse end of the schedule.

For any historical replay before 25 January 2023, load the per-board *old*
tables instead — see `standards.md`.

## 4. Daily Price Limit Audit (가격제한폭)

1. Validate the base price (기준가격) is finite and strictly positive **before
   using it**. A zero base price raises `ZeroDivisionError` in a percentage
   formulation; a NaN base price makes every comparison return `False` and
   disguises a data fault as an exchange rejection.
2. If the instrument is exempt — 정리매매, 신주인수권증권·증서, ELW — skip the
   band. The tick check still applies.
3. Otherwise compute the **amount**, not a deviation:
   - $\Delta P_{base} = $ tick of the **base price's** band.
   - $A = \operatorname{trunc}\!\left(P_{base} \times \tfrac{pct}{100},\ \Delta P_{base}\right)$ — discard the sub-tick remainder (절사).
   - 상한가 $= P_{base} + A$, 하한가 $= P_{base} - A$, both **inclusive**.
4. $pct$ = 30 for KOSPI/KOSDAQ, 15 for KONEX.

Worked example from the KRX regulation portal: base KRW 9,940 → KRW 10 tick →
9,940 × 0.3 = 2,982 → truncated to **2,980** → band KRW 6,960 – KRW 12,920.

## 5. Audit Report Generation

Emit the applied tick size, the limit amount, and **both band bounds**. A
rejection that reports only "outside the limit" forces the caller to
re-derive the band; a rejection that reports the band can be repriced to
상한가 or 하한가 directly.

Malformed input — bad code, unknown side or class, non-positive quantity,
non-positive or non-finite price or base price — is **raised**, never folded
into a status. A caller bug must be distinguishable from an exchange-rule
rejection.
