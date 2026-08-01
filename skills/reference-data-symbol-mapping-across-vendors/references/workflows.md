# Workflows for Reference Data Symbol Mapping Across Vendors

1. **Mapping Table Construction**:
   - Register vendor-specific symbol entries mapping to canonical internal symbols.
2. **Forward Lookup (Vendor → Canonical)**:
   - Resolve vendor name + vendor symbol to canonical symbol.
3. **Reverse Lookup (Canonical → Vendor)**:
   - Resolve canonical symbol + target vendor to vendor-specific identifier.
4. **Ambiguity Detection & Coverage Report**:
   - Flag ambiguous mappings; output coverage report.
