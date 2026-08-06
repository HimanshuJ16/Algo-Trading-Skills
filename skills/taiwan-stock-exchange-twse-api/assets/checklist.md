# TWSE API Integration Audit Checklist

- [x] Validated FINI institutional investor ID on order header.
- [x] Verified 1,000 share lot multiple check for standard orders.
- [x] Implemented dynamic tick size lookup table (NT$0.01 vs NT$0.05).
- [x] Implemented 10% daily price limit validation against prior closing price.
- [x] Enforced locate verification to prevent illegal naked short selling.
- [x] 100% unit test suite pass rate.
