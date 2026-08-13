# Checklist for ASIC DRT Compliance

- [ ] Confirm LEI is 20 uppercase alphanumeric characters and passes the ISO/IEC 7064 MOD 97-10 checksum.
- [ ] Confirm UTI is 20-52 uppercase alphanumeric characters (ISO 23897).
- [ ] Confirm UPI is 12 characters with the "QZ" prefix and the ISO 4914 consonant+digit alphabet.
- [ ] Confirm the T+2 deadline is computed in **business days** (excluding weekends and a supplied Sydney holiday set).
- [ ] Confirm T+4 is applied where `requires_linking_identifier=True` (Item 92 linking identifier required).
- [ ] Confirm trades reported exactly on the deadline are not flagged late (boundary: `reporting_date == deadline`).
- [ ] Confirm the engine correctly flags submissions after the deadline.
- [ ] Run test suite: `python -m unittest discover -s skills/australia-asic-drt-obligations/scripts`.

## Sign-off
- Compliance Officer: ___________________________
- Date: ___________________________
