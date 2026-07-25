# Standards: Borrow Cost Modeling

| Concept | Standard / Best Practice | Rationale |
|---------|--------------------------|-----------|
| **GC Rate** | Usually 25 to 50 bps annually (0.25% - 0.50%) | Baseline rate for highly liquid, easy-to-borrow stocks. |
| **HTB Threshold** | Utilization > 80% | Once utilization is high, borrow costs spike non-linearly. |
| **HTB Max Rate** | Can exceed 100% annually | Hard to borrow stocks (meme stocks, heavily shorted small caps) have extreme borrow costs that quickly erase alpha. |
| **Availability** | Hard cutoff at 100% utilization | You cannot short shares that don't exist to be borrowed. |
