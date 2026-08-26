# Pre-Flight Checklist — JPX / TSE arrowhead4.0

- [ ] Does the securities code validator accept letters in positions 2 and 4
      (`130A`, `9A76`) and not just four digits?
- [ ] Is the correct tick table selected **per issue** (`TOPIX500` /
      `ETF_SINGLE_UNIT` / `OTHER`), from TSE's published applicability notices?
- [ ] Are tick bands treated as **inclusive** at the upper bound (JPY 5,000
      takes the finer tick on the `OTHER` table)?
- [ ] Is tick alignment checked in decimal arithmetic, so JPY 0.1 and JPY 0.5
      increments are exact?
- [ ] Is the trading unit taken from the instrument (100 for domestic stocks,
      1 or 10 for ETFs/ETNs/REITs) rather than hard-coded to 100?
- [ ] Is the daily price limit read from the **absolute-yen** schedule, and not
      modelled as a percentage of the previous close?
- [ ] Are price-limit bands treated as **exclusive** at the upper bound (base
      JPY 100 takes ±JPY 50), and the resulting price bounds as inclusive?
- [ ] Is the base price (基準値段) validated as finite and strictly positive
      before it anchors a band?
- [ ] Is there a path to supply a **broadened** daily price limit for issues
      TSE has published as expanded?
- [ ] Is it documented that halts, special quotes, auction phases, short-sale
      restrictions and ToSTNeT are **not** covered by this pre-trade filter?
- [ ] Is there a plan for the 1 March 2027 move to STR-based tick sizing?
