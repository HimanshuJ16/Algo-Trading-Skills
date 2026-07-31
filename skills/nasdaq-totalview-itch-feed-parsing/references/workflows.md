# Workflows for Nasdaq TotalView-ITCH Feed Parsing

1. **Header & Type Unpacking**:
   - Inspect 1-byte message type prefix ('A', 'E', 'X', 'D').
2. **Binary Struct Unpacking**:
   - Unpack binary fields using big-endian format `>` and divide price by $10,000.0$.
3. **L3 State Update**:
   - Update `orders_by_ref` map for Add, Execute, Cancel, and Delete events.
4. **Audit Report Generation**:
   - Output structured ITCH 5.0 parsing report.
