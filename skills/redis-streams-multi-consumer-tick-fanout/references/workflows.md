# Workflows for Redis Streams Multi-Consumer Tick Fanout

1. **Stream & Consumer Group Setup**:
   - Create stream and register consumer groups via XGROUP CREATE.
2. **Tick Publishing (XADD)**:
   - Publish tick data with MAXLEN cap for memory management.
3. **Fanout Consumption (XREADGROUP)**:
   - Each consumer group reads all ticks independently.
4. **Acknowledgment & Recovery (XACK/XCLAIM)**:
   - Acknowledge processed ticks; reclaim stale entries from crashed workers.
