# Workflows for Cross-Jurisdiction Regulatory Conflict Resolution

1. **Jurisdiction Identification**:
   - Extract $\mathcal{J} = \{\text{Entity Jurisdictions}\} \cup \{\text{Execution Venue Jurisdiction}\}$.
2. **Strictest Rule Resolution**:
   - PFOF Status: $\text{PFOF\_Allowed} = \bigwedge_{j \in \mathcal{J}} \text{PFOF\_Allowed}_j$.
   - LEI Status: $\text{LEI\_Mandatory} = \bigvee_{j \in \mathcal{J}} \text{LEI\_Mandatory}_j$.
   - Short Selling: $\max_{j \in \mathcal{J}} (\text{Restriction\_Severity}_j)$.
3. **Order Audit**:
   - Compare proposed order parameters against resolved strict rule set.
4. **Decision Output**:
   - Log compliance decision (`APPROVED` or `REJECTED`) with rule justification.