# Pre-Flight Checklist

- [ ] Are T7 ETI binary header fields (`session_id`, `seq_num`, `msg_type: 10100`) formatted correctly?
- [ ] Is Xetra price-band tick size compliance verified prior to order dispatch?
- [ ] Are MiFID II tags (`account_type` and `mifid_short_code`) populated on all order messages?
- [ ] Is T7 execution report processing configured for async fills?
