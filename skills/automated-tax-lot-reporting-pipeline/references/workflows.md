# Workflows for Automated Tax Lot Reporting

## Overview
This document details recommended workflows for implementing and operating the automated tax lot reporting pipeline in production environments. It covers end-to-end processes from data ingestion to report generation, including operational best practices.

## Standard Workflow

### 1. Data Ingestion and Normalization
**Objective**: Convert raw execution data into standardized TradeRecord format.

**Steps**:
1. **Extract**: Pull execution records from source systems (broker APIs, FIX feeds, internal systems)
2. **Validate**: Check for basic data integrity (required fields, data types)
3. **Normalize**: Map source-specific fields to TradeRecord schema:
   - trade_id → Unique transaction identifier
   - symbol → Instrument ticker/security identifier
   - action → BUY or SELL (case-insensitive mapping)
   - quantity → Number of shares/units (positive decimal)
   - price → Execution price per share/unit (non-negative decimal)
   - timestamp_ms → Execution time in milliseconds since epoch
4. **Enrich**: Add any missing required fields from reference data
5. **Output**: Stream normalized TradeRecord objects to processing pipeline

**Technical Considerations**:
- Use message queues (Kafka, RabbitMQ) for buffering and decoupling
- Implement schema validation (Avro, JSON Schema) for data contracts
- Apply data masking for PII compliance where required
- Maintain dead letter queue for invalid records requiring manual review

### 2. Chronological Ordering and Gap Detection
**Objective**: Ensure trades are processed in strict time order to maintain lot accounting integrity.

**Steps**:
1. **Sort**: Order trades by timestamp_ms (ascending)
2. **Gap Analysis**: Detect missing sequences or duplicate timestamps
3. **Correction**: 
   - For gaps: Flag for investigation (may indicate missing data feeds)
   - For duplicates: Apply deduplication rules based on trade_id or other unique identifiers
   - For out-of-order: Determine if processing can continue with notification or requires reset
4. **Checkpointing**: Save processing position for recovery/restart scenarios

**Technical Considerations**:
- Use streaming processors (Apache Flink, Kafka Streams) for ordered processing
- Implement watermarking for handling late-arriving events
- Maintain state stores for gap detection logic
- Allow configurable tolerance for timestamp duplicates (same-second trades)

### 3. Engine Configuration and Initialization
**Objective**: Prepare the tax lot engine for processing.

**Steps**:
1. **Strategy Selection**: Choose between FIFO (default) and HIFO based on:
   - Tax jurisdiction requirements
   - Client preferences (tax optimization vs simplicity)
   - Account type (taxable vs tax-advantaged)
2. **Engine Instantiation**: Create AutomatedTaxLotReportingPipelineEngine with selected strategy
3. **State Hydration**: If resuming from checkpoint, rebuild open lots from saved state
4. **Pre-flight Checks**: Validate engine readiness (connections, memory, dependencies)
5. **Monitoring Setup**: Configure logging, metrics, and alerting

**Configuration Parameters**:
- matching_strategy: FIFO|HIFO
- validation_strictness: lenient|standard|strict
- precision_mode: decimal|float
- memory_warning_threshold: lot count threshold for alerts
- error_handling: fail_fast|continue_with_logging

### 4. Trade Processing Loop
**Objective**: Process each trade through the tax lot matching engine.

**Steps**:
1. **Input Validation**: Engine validates TradeRecord parameters (raises ValueError on failure)
2. **Buy Trade Handling**:
   - Create new TaxLot with trade details
   - Add to open lots ledger for the symbol
   - Log lot creation for audit trail
3. **Sell Trade Processing**:
   - Query open lots for symbol
   - Sort lots according to selected strategy (FIFO: oldest first; HIFO: highest cost basis first)
   - Iteratively match sell quantity against lots:
     * Calculate quantity to consume from current lot
     * Compute realized PnL using (sell_price - cost_basis) * quantity
     * Create RealizedGainRecord for the match
     * Update lot remaining quantity
     * Remove fully consumed lots from ledger (memory cleanup)
     * Continue until sell quantity is satisfied or no lots remain
   - Handle partial lot consumption (split lot logic)
   - Detect and handle overselling conditions
4. **Output Collection**: Gather RealizedGainRecord objects for reporting
5. **Post-processing Updates**: Update monitoring metrics and internal state

**Error Handling**:
- Validation Errors: Log invalid trade, skip processing, continue with next trade
- Processing Errors (oversell, no lots): Log error, optional notification, continue or halt based on configuration
- System Errors: Implement circuit breaker patterns, fallback to manual processing

### 5. Report Generation and Output
**Objective**: Convert engine output into actionable tax and reporting formats.

**Steps**:
1. **Collect Results**: Gather RealizedGainRecord objects from engine processing
2. **Aggregate**: Group by tax lot or time period as required for reporting
3. **Format Conversion**:
   - **Form 8949**: Map to IRS required columns:
     * Description of property (symbol)
     * Date acquired (lot timestamp → date)
     * Date sold (sell timestamp → date)
     * Proceeds (quantity * sell_price)
     * Cost basis (quantity * cost_basis_price)
     * Gain/loss (quantity * (sell_price - cost_basis_price))
   - **CSV/Excel**: For internal reporting or spreadsheet import
   - **JSON/XML**: For system-to-system integration
   - **PDF**: For formal client statements
4. **Validation**: Verify mathematical consistency (sum of proceeds, cost basis, gain/loss)
5. **Delivery**: Send to appropriate recipients (clients, tax professionals, archival systems)
6. **Reconciliation**: Compare against broker-provided statements where available

### 6. Post-Processing and Maintenance
**Objective**: Ensure ongoing system health and readiness for next cycle.

**Steps**:
1. **State Persistence**: Save open lots ledger for recovery (if not processing in real-time stream)
2. **Metrics Collection**: Update operational counters and performance indicators
3. **Log Rotation**: Manage log file sizes and retention
4. **Resource Cleanup**: Close connections, release temporary resources
5. **Health Verification**: Run self-diagnostics to confirm system integrity
6. **Preparation for Next Cycle**: Reset readiness for subsequent trade batches

## Operational Workflows

### Daily Operations Cycle
1. **Pre-market**: 
   - Verify system health and connectivity
   - Confirm data feeds are active
   - Check for overnight messages or corrections
   - Validate engine configuration
2. **Market Hours**:
   - Process trades in real-time or near real-time
   - Monitor processing latency and error rates
   - Track lot ledger size and memory usage
   - Respond to alerts and anomalies
3. **Post-market**:
   - Finish processing any late-arriving trades
   - Generate end-of-day reports
   - Reconcile with broker statements
   - Archive day's trading data
   - Prepare system for next day

### Error Handling and Recovery
1. **Invalid Trade Data**:
   - Route to dead letter queue for manual review
   - Notify data quality team
   - Continue processing valid trades
   - Track error rates for data source quality metrics
2. **Processing Errors**:
   - Implement retry logic with exponential backoff for transient errors
   - Failover to backup processing systems for persistent errors
   - Manual intervention procedures for uncorrectable errors
   - Maintain audit trail of all errors and resolutions
3. **System Failures**:
   - Automatic failover to hot standby systems
   - State recovery from persistent checkpoints
   - Replay mechanism for missed trades during downtime
   - Post-incident analysis and prevention measures

### Periodic Maintenance
1. **Monthly**:
   - Validate year-to-date tax calculations
   - Prepare preliminary tax estimates
   - Review and update tax strategy elections if applicable
   - Check for regulatory changes affecting lot accounting
2. **Quarterly**:
   - Reconcile with official broker statements
   - Validate against third-party tax software
   - Update system components and dependencies
   - Performance benchmarking and optimization
3. **Annually**:
   - Generate final tax reports (Form 8949, Schedule D)
   - Support tax filing process
   - Archive complete year's data per retention policies
   - Plan for next year's tax strategy considerations

## Specialized Workflows

### Corporate Actions Handling
**Objective**: Process corporate actions that affect lot accounting (splits, dividends, mergers, spinoffs).

**Steps**:
1. **Detection**: Identify corporate action events from market data feeds
2. **Classification**: Determine action type (split, dividend, merger, etc.)
3. **Lot Adjustment**:
   - **Stock Splits**: Adjust quantity and cost basis of existing lots
   - **Reverse Splits**: Consolidate lots according to ratio
   - **Cash Dividends**: No lot adjustment (separate income tracking)
   - **Reinvested Dividends**: Create new BUY lots at reinvestment price
   - **Mergers/Acquisitions**: Map lots to new securities per exchange ratio
   - **Spinoffs**: Allocate cost basis between parent and child per IRS guidelines
4. **Validation**: Verify adjusted lots maintain correct economic substance
5. **Documentation**: Record all adjustments for audit trail

### Wash Sale Integration
**Objective**: Coordinate tax lot reporting with wash sale rule tracking.

**Steps**:
1. **Parallel Processing**: Run wash sale detection alongside lot matching
2. **Loss Identification**: Flag lots with losses that may be subject to wash sale rules
3. **Period Adjustment**: For wash sales, adjust loss disallowance and basis increase
4. **Lot Maintenance**: Ensure washed lots remain in ledger with adjusted basis
5. **Reporting**: Reflect wash sale adjustments in final gain/loss calculations

### Multi-Account Aggregation
**Objective**: Process trades from multiple accounts while maintaining lot separation.

**Steps**:
1. **Account Segregation**: Maintain separate lot ledgers per account
2. **Processing**: Route trades to appropriate account-specific engine instances
3. **Consolidation**: Aggregate results for omnibus reporting where permitted
4. **Isolation**: Ensure no cross-contamination of lots between accounts
5. **Reporting**: Generate both account-specific and consolidated reports

## Implementation Patterns

### Real-time Streaming Architecture
```
[Data Sources] → [Message Queue] → [Stream Processor] → [Tax Lot Engine] → [Results Store] → [Reporting]
                     ↑              ↑                    ↑                  ↑
              [Schema Validation] [Enrichment]     [Monitoring]    [Error Handling]
```

### Batch Processing Architecture
```
[Daily Trade Files] → [Validation] → [Sorting] → [Tax Lot Engine] → [Results] → [Reporting]
                              ↑              ↑                  ↑                  ↑
                     [Gap Detection] [Enrichment] [Checkpointing] [Error Handling]
```

### Hybrid Architecture (Recommended)
```
[Real-time Feed] → [Stream Processor] → [Tax Lot Engine] → [Real-time Dashboard]
                        � ↓                    ↑                  ↑                  ↑
[End-of-day File] ──→ [Batch Processor] ───�┘                  └──→ [Daily Reports] → [Archival]
```

## Performance and Scaling Guidelines

### Throughput Optimization
- **Lot Storage**: Use efficient data structures (TreeMap, skip lists) for sorted lot access
- **Batch Processing**: Process trades in chunks to amortize sorting overhead
- **Pre-sorting**: Maintain lots in sorted order to avoid O(n log n) per transaction
- **Memory Pooling**: Reuse lot objects to reduce garbage collection pressure
- **Parallel Processing**: Process different symbols in parallel (lot ledgers are symbol-independent)

### Latency Reduction
- **In-memory Processing**: Keep hot lots in RAM for immediate access
- **Asynchronous Logging**: Decouple logging from critical path
- **Metric Sampling**: Use statistical sampling for high-frequency metrics
- **Connection Pooling**: Reuse database/network connections
- **CPU Affinity**: Pin processing threads to specific cores for cache efficiency

### Memory Management
- **Lot Eviction**: Remove fully settled lots immediately (already implemented)
- **Aging Policies**: Consider archiving very old settled lots to secondary storage
- **Memory Mapping**: Use memory-mapped files for large ledgers if needed
- **Garbage Collection Tuning**: Optimize GC settings for application profile
- **Monitoring**: Track heap usage, GC frequency, and pause times

## Testing and Validation Workflows

### Unit Testing
- **Input Validation**: Test all parameter validation edge cases
- **Strategy Logic**: Verify FIFO/HIFO sorting correctness
- **PnL Calculation**: Test profit/loss calculations with known values
- **Edge Cases**: Zero quantities, fractional shares, lot splits
- **Error Conditions**: Validate proper exception raising

### Integration Testing
- **End-to-end Flows**: Process known trade sequences and verify outputs
- **Cross-strategy Comparison**: Ensure FIFO and HIFO produce expected different results
- **Memory Leak Detection**: Verify lot cleanup in long-running simulations
- **Error Propagation**: Confirm errors are handled according to configuration
- **State Recovery**: Test checkpoint/restore functionality

### Performance Testing
- **Load Testing**: Simulate high-volume trading scenarios
- **Scalability Testing**: Verify performance with increasing lot counts
- **Latency Benchmarking**: Measure processing time per trade under various loads
- **Memory Profiling**: Identify and resolve memory hotspots
- **Soak Testing**: Run extended duration tests to detect slow leaks

### Compliance Testing
- **Regulatory Validation**: Verify outputs against known tax calculation examples
- **Audit Trail Testing**: Ensure complete traceability from input to output
- **Reporting Accuracy**: Validate Form 8949 mappings and calculations
- **Jurisdiction Specific**: Test for specific country requirements if applicable
- **Third-party Validation**: Compare results with established tax software

## Troubleshooting Guide

### Common Issues and Resolutions

#### Symptom: Processing Lag Increases Over Time
- **Possible Causes**: 
  - Memory leak from uncleared lots
  - Growing ledger size due to processing errors
  - Inefficient sorting algorithm
- **Diagnosis**:
  - Monitor get_total_open_lot_count() trend
  - Check lot age distribution
  - Review processing latency metrics
- **Resolution**:
  - Verify lot removal logic is functioning
  - Check for error conditions preventing lot consumption
  - Consider optimizing lot data structure

#### Symptom: Incorrect PnL Calculations
- **Possible Causes**:
  - Invalid input data (wrong prices, quantities)
  - Timestamp ordering issues
  - Strategy misconfiguration
  - Corporate action not applied
- **Diagnosis**:
  - Validate input trade data against source
  - Check trade chronological order
  - Confirm strategy selection
  - Review recent corporate actions
- **Resolution**:
  - Correct data source issues
  - Implement pre-processing sorting
  - Reconfigure strategy as needed
  - Apply missing corporate action adjustments

#### Symptom: Engine Crashes or Exceptions
- **Possible Causes**:
  - Invalid trade data bypassing validation
  - Numerical overflow/underflow
  - Resource exhaustion (memory, file handles)
  - Concurrent modification exceptions
- **Diagnosis**:
  - Review exception logs and stack traces
  - Check system resource utilization
  - Examine recent trade data for anomalies
  - Review threading/concurrency patterns
- **Resolution**:
  - Strengthen input validation
  - Add numerical bounds checking
  - Increase system resources or optimize usage
  - Review and fix threading issues

#### Symptom: Output Doesn't Match Broker Statements
- **Possible Causes**:
  - Different lot identification methods (FIFO vs specific ID)
  - Missing corporate actions or dividends
  - Timing differences (trade date vs settlement date)
  - Fee/commission handling differences
  - Wash sale rule applications
- **Diagnosis**:
  - Compare lot-level details not just totals
  - Verify chronological alignment
  - Check for corporate action processing
  - Review fee inclusion/exclusion policies
  - Validate wash sale treatment
- **Resolution**:
  - Adjust strategy to match broker method if possible
  - Process missing corporate actions
  - Align on trade vs settlement date conventions
  - Standardize fee handling approach
  - Coordinate wash sale calculations

## Performance Benchmarks

### Typical Processing Rates
- **Low Volume** (<100 trades/sec): <1ms latency per trade
- **Medium Volume** (1,000 trades/sec): 2-5ms latency per trade
- **High Volume** (10,000+ trades/sec): 10-20ms latency per trade (with optimization)
- **Burst Handling**: Capable of processing 100k+ trade bursts with appropriate buffering

### Resource Utilization
- **Memory**: Approximately 1-2KB per open lot (varies with symbol count and lot fragmentation)
- **CPU**: Minimal - primarily limited by I/O and validation overhead
- **Storage**: Negligible for engine itself (state persistence depends on implementation)
- **Network**: Determined by data feed characteristics

### Scaling Characteristics
- **Symbol Parallelism**: Near-linear scaling with number of distinct symbols (independent ledgers)
- **Trade Volume**: Sub-linear scaling due to shared resources (memory, CPU)
- **Lot Depth**: Logarithmic scaling with lots per symbol (efficient sorting data structures)
- **Batch Size**: Optimal batch size typically 100-1000 trades for amortized overhead

## Glossary

- **Tax Lot**: A specific purchase of a security tracked for cost basis and holding period purposes
- **FIFO**: First-In, First-Out - oldest lots sold first
- **HIFO**: Highest-In, First-Out - lots with highest cost basis sold first
- **Realized Gain/Loss**: Profit or loss from selling a security (sell price - cost basis) * quantity
- **Cost Basis**: Original value of an asset for tax purposes (usually purchase price plus fees)
- **Holding Period**: Time between acquisition and disposition of a security
- **Wash Sale**: Sale of security at loss followed by repurchase of substantially identical security within 30 days
- **Specific Identification**: Method of identifying which specific lots are being sold (alternative to FIFO/HIFO)
- **Corporate Action**: Event issued by a corporation that affects its securities (splits, dividends, mergers, etc.)
- **Form 8949**: IRS form used to report capital gains and losses from sales of investments
- **Schedule D**: IRS form used to report overall capital gains and losses
- **Cost Basis Average**: Method where basis is averaged across all lots of a security
- **Lot Splitting**: Dividing a tax lot when only portion is sold
- **Lot Consolidation**: Combining multiple lots (typically after corporate actions)
- **Chronological Processing**: Handling transactions in strict time order
- **State Persistence**: Saving engine state (open lots) for recovery/restart purposes
- **Dead Letter Queue**: Holding area for messages that cannot be processed normally
- **Watermarking**: Technique in stream processing to handle event time and lateness
- **Checkpointing**: Saving processing state at intervals for fault tolerance