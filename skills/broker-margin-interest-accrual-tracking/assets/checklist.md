# Pre-Flight Checklist: Margin & Financing Integration

- [ ] **Tier Schedule Configuration**: Are the broker's rate tiers verified and up-to-date in the simulation environment?
- [ ] **Blended Flag**: Is the tracker correctly set to use blended (progressive) vs. flat tiers depending on the broker?
- [ ] **Day-Count Convention**: Is USD margin configured to use 360 days (Actual/360)?
- [ ] **Borrow Fees Tracking**: Are hard-to-borrow fees properly calculated on gross short market value?
- [ ] **Weekend Multiplier**: Does holding a position from Friday to Monday correctly charge 3 days of financing?
- [ ] **Holiday Handling**: Is a holiday calendar integrated to account for 4-day weekends (e.g. Labor Day)?
- [ ] **Intraday Filter**: Does the system ensure intraday-only leverage (closed before EOD) does not accrue overnight interest?
- [ ] **Monthly Posting**: Are accrued daily charges posted to the cash balance at the end of the month (for long-term compounding)?
