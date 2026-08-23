# Pre-Flight Checklist

- [ ] Are crypto tax lots recorded with acquisition timestamp, quantity, USD cost basis, **and the wallet/account holding them**?
- [ ] Is lot inventory scoped per wallet, so a disposal never consumes basis sitting in another wallet?
- [ ] Are crypto-to-crypto swaps recorded as dispositions at USD Fair Market Value?
- [ ] Is crypto spent on gas processed as its own disposition of that token, not just netted as an expense?
- [ ] Are transaction costs subtracted from proceeds **and not also** capitalized into the received asset's basis?
- [ ] Is the matching method FIFO unless a specific identification was made no later than the date and time of the disposal?
- [ ] For every HIFO/LIFO disposal, is the `identification_reference` a real contemporaneous record (books-and-records entry or standing order), not a post-hoc reconstruction?
- [ ] Does every disposal carry a disposal timestamp, so the holding period can be computed?
- [ ] Is long-term classified as **more than one year** by calendar anniversary — not `days_held > 365`, which misclassifies across leap years?
- [ ] Are mixed-term disposals reported as separate Form 8949 rows across Part I and Part II rather than one aggregate line?
- [ ] Does a rejected disposal (insufficient inventory, missing identification, bad input) leave lot quantities untouched?
