# Pre-Flight Checklist

- [ ] Are raw vendor timestamps parsed into 64-bit nanosecond UTC epoch integers using exact integer/`Decimal` arithmetic, with no `float` scaling step anywhere in the path?
- [ ] Is every normalized value range-checked against the signed int64 nanosecond bounds before it reaches an int64 column?
- [ ] Is the raw timestamp stored alongside the normalized nanosecond timestamp, and is the ISO audit string rendered at full 9-digit fidelity rather than truncated to milliseconds?
- [ ] Are ISO-8601 fractional seconds parsed as integers (not via `datetime`, whose resolution stops at microseconds), and do sub-nanosecond digits raise instead of rounding?
- [ ] Does every ISO-8601 timestamp carry an explicit UTC offset, and is any naive input logged where it is assumed to be UTC?
- [ ] Is the precision tier derived from the digits actually delivered rather than from the schema's column type?
- [ ] Are coarse timestamps flagged against a `required_precision_tier` chosen from the applicable obligation (RTS 25 / CAT figures in `references/standards.md`), rather than zero-padded into a nanosecond schema?
- [ ] Are out-of-order arrivals ($\Delta t < 0$) detected over the **arrival** sequence against a running maximum, not by inspecting the sorted output?
- [ ] Are `tick_id` values unique within a batch, and are duplicates rejected rather than silently overwriting arrival order?
- [ ] Is cross-vendor skew computed only between records sharing an event key (exchange sequence number / venue trade id), reported **signed**, and never described as clock drift without confirming both vendors' timestamping points?
