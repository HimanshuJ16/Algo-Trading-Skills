# Workflow: Short Selling Borrow Cost and Availability Modeling

1. **Initialize Modeler**
   Instantiate `BorrowCostModeler` with the broker's GC rate, base HTB rate, and max HTB rate.

2. **Load Utilization Data**
   Load historical short interest/utilization data for the universe.
   Create `BorrowStatus` objects and update the modeler: `modeler.update_status(BorrowStatus("TICKER", util))`.

3. **Check Availability (Pre-Trade)**
   Before emitting a short signal, call `modeler.can_short(ticker, requested_shares)`.
   If False, skip the trade.

4. **Calculate Drag (Post-Trade)**
   For every short position held overnight, calculate the drag by invoking `modeler.calculate_borrow_cost(trade)`.
   Subtract this cost from the daily P&L.
