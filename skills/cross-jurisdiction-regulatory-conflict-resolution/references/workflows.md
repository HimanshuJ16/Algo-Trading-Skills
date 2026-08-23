# Workflows for Cross-Jurisdiction Regulatory Conflict Resolution

1. **Jurisdiction Identification**:
   - Extract $\mathcal{J} = \{\text{Entity Jurisdiction}\} \cup \{\text{Execution Venue Jurisdiction}\}$.
   - Normalize each code (trim, upper-case) and **sort** the result. Blank or non-string
     codes raise `ValueError`: an order whose applicable regime cannot be identified must
     never reach an APPROVED decision.
2. **Strictest Rule Resolution**:
   - PFOF Status: $\text{PFOF\_Allowed} = \bigwedge_{j \in \mathcal{J}} \text{PFOF\_Allowed}_j$.
   - LEI Status: $\text{LEI\_Mandatory} = \bigvee_{j \in \mathcal{J}} \text{LEI\_Mandatory}_j$.
   - Short Selling: $\max_{j \in \mathcal{J}} (\text{Restriction\_Severity}_j)$ over the
     ordering `NONE(0) < REPORTING(1) < PRICE_TEST(2) < BAN(3)` — ordered by restriction on
     the ability to execute, so a disclosure duty never outranks a price test.
   - Unregistered $j$: substitute the strictest value on every dimension (PFOF blocked,
     LEI mandatory, short selling banned), log a warning, and record the code.
   - $\mathcal{J} = \emptyset$: raise. The identity elements of the three accumulators are
     the *most permissive* rule set, so an empty set would fail open.
3. **Order Audit**:
   - PFOF: reject if `routed_via_pfof` and PFOF is banned in any $j$.
   - Client identification (when LEI is mandatory):
     - legal entity → `lei_tag` must satisfy ISO 17442 (20 upper-case alphanumerics,
       numeric positions 19-20) **and** the ISO/IEC 7064 MOD 97-10 checksum
       ($\text{int}_{36}(\text{LEI}) \bmod 97 = 1$);
     - natural person → a national client identifier is required instead
       (RTS 22 Art. 6 / Annex II, CONCAT fallback). Requiring an LEI here is wrong.
   - Short selling (`is_short` only): `BAN` rejects; `PRICE_TEST` and `REPORTING` emit
     obligations (`SHORT_SELL_PRICE_TEST_APPLIES`, `SHORT_SELL_POSITION_REPORTING_REQUIRED`)
     because neither can be evaluated from the order payload alone.
4. **Decision Output**:
   - Emit `RegulatoryComplianceDecision` (`is_approved`, resolved rule set, `violations`,
     `required_obligations`, `unregistered_jurisdictions`, `applicable_jurisdictions`,
     deterministic `applied_rules_summary`); log REJECTED at ERROR and APPROVED at INFO.
   - Append a defensive copy to `engine.audit_trail`; the accessor returns copies so a
     recorded decision cannot be edited through the object it handed back.
5. **Downstream Handoff** (outside this module):
   - Resolve `required_obligations` before release: GLEIF lookup for LEI issuance/status,
     price-test enforcement against the national best bid, net short position reporting.
