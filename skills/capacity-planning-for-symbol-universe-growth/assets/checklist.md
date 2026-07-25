# Pre-Flight Checklist

- [ ] Have you modeled using *peak* microburst tick rates rather than daily averages?
- [ ] Is TCP/IP header overhead included in the `bytes_per_msg` calculation?
- [ ] Have you verified that your broker/exchange allows subscribing to this many symbols on a single WebSocket/FIX connection? (If not, you must model multiple connections).
- [ ] Is the resulting CPU core count strictly dedicated (pinned) rather than shared with OS processes?