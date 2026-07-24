# Deep Workflow Reference — multi-strategy-capital-allocation-limits

## Full Procedure

1. **Register strategies** with maximum allocation percentages.
2. **Validate** total allocations + cash reserve ≤ 100%.
3. **Update exposures** on every fill/mark-to-market cycle.
4. **Pre-trade check**: Verify projected exposure ≤ allocation cap before order placement.

## Production Implementation Reference

- Code: `scripts/capital_allocator.py` (`MultiStrategyCapitalAllocator`).
- Tests: `scripts/test_capital_allocator.py`.
