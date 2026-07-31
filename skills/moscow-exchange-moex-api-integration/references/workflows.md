# Workflows for Moscow Exchange (MOEX) Integration

1. **Board & Instrument Validation**:
   - Verify instrument `secid` matches board (`TQBR`, `CETS`, `RFUD`).
2. **Tick Step & Price Collar Audit**:
   - Align price to tick step and audit against MOEX price collars.
3. **FIX / TWIME Payload Formatting**:
   - Construct FIX/TWIME order payload with `BoardID` and `MISX` exchange tags.
4. **Audit Report Generation**:
   - Output structured MOEX order report.
