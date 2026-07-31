# Workflows for NYSE Arca Integrated Feed Handling

1. **Header Unpacking**:
   - Unpack XDP Packet Header (16 bytes) and Message Header (4 bytes) using little-endian `<`.
2. **Payload Parsing**:
   - Unpack Add Order (100), Modify Order (101), Delete Order (102), and Execution (103) payloads.
3. **L3 State Maintenance**:
   - Update `active_orders` map by OrderID and adjust share volume.
4. **Audit Report Generation**:
   - Output structured NYSE feed report.
