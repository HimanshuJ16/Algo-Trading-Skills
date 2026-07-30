# Workflows for Daylight Saving Time Transition Handling

1. **IANA Timezone Parsing**:
   - Parse local market open/close times using IANA timezone strings (`America/New_York`, `Europe/London`).
2. **UTC Conversion**:
   - Convert local open/close times for date $D$ to UTC datetime and 64-bit nanosecond epochs.
3. **Cross-Border Desynchronization Detection**:
   - Compare US vs EU vs Asian UTC session overlaps to detect 2-week DST shift windows.
4. **Schedule Recalibration**:
   - Dynamically adjust cron jobs and strategy execution timers.
