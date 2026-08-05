# Workflows for Sanctions Screening for Counterparties and Instruments

1. **Subject Ingestion**:
   - Ingest counterparty LEI / name / country or instrument ISIN / issuer country.
2. **OFAC 50% Rule & Embargo Check**:
   - Check if $\ge 50\%$ owned by sanctioned entity or located in embargoed country.
3. **Database Screening & Fuzzy Matching**:
   - Run exact LEI/ISIN matching and Levenshtein fuzzy name matching ($\ge 85\%$).
4. **Audit Report & Order Gate**:
   - Return compliance report; block order routing or onboarding if hits detected.