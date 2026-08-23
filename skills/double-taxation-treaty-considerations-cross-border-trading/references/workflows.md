# Workflows for Double Taxation Treaty Considerations

1. **Register rates**:
   - Register a `DttTreatySpec` per (residence, source, income type) triple.
     Dividends, interest and royalties fall under separate treaty articles and
     carry separate rates; one spec covers one income class.
   - Register source-country statutory rates for income with no treaty coverage.
   - Rates are decimal fractions in [0.0, 1.0]. A treaty rate above the
     statutory rate is rejected as a data-entry inversion.

2. **Resolve the applicable rate**:
   - Treaty + valid documentation -> treaty rate (`TREATY_APPLIED`).
   - Treaty + missing documentation -> statutory rate (`DOCUMENTATION_MISSING`).
   - No treaty + registered statutory rate -> statutory rate
     (`STATUTORY_NO_TREATY`).
   - Nothing registered -> `REVIEW_REQUIRED`, all amounts `None`. No rate is
     borrowed from another income class and no default is assumed.

3. **Compute withholding**:
   - Withheld = gross x applied rate, in exact decimal arithmetic, rounded half
     up to cents.
   - Saving = statutory withholding - withheld, floored at zero.

4. **Split recoverable from unrecoverable tax**:
   - Where a treaty rate was available but not claimed, cap the creditable
     amount at the treaty rate: the excess is a noncompulsory payment and is
     generally not creditable.
   - Apply the residence-country ceiling: eligible FTC = min(creditable,
     limitation). Supply `ftc_limitation_usd` where the real limitation has been
     computed; the rate-times-gross path is an approximation.
   - Report the residual as `non_creditable_wht_usd` - tax that will not be
     recovered by credit.

5. **Compliance follow-up**:
   - `DOCUMENTATION_MISSING` -> file the required form AND open a source-country
     refund claim for the over-withheld amount; the credit will not recover it.
   - `REVIEW_REQUIRED` -> register the missing rate or escalate to tax counsel
     before booking a tax position.
