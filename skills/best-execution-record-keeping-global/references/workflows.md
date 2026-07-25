# Best Execution Workflows

## Pre-Trade
1. **Algorithm Selection**: Ensure the chosen algo matches the client's execution objectives.
2. **Benchmark Capture**: Record the prevailing market benchmark (e.g., arrival price) right before order submission.

## Trade Execution
1. **Timestamping**: Record precise UTC timestamps for Order Creation, Order Submission, Venue Acceptance, and Execution.
2. **Fills Processing**: Aggregate fills with accurate price, quantity, and individual timestamps.

## Post-Trade
1. **Slippage Calculation**: Compare the volume-weighted average price (VWAP) of fills against the benchmark.
2. **Regulatory Tag Check**: Validate that all necessary LEIs, trader IDs, and algorithmic IDs are attached to the trade record.
3. **Audit Log Generation**: Generate a cryptographic hash of the entire trade record payload.
4. **Archiving**: Append the record and its hash to the firm's central Compliance WORM (Write Once, Read Many) database.