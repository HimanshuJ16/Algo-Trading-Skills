# Pre-Flight Checklist

- [ ] Does the `CboeMultilegOrder` class enforce GCD normalization on leg ratios?
- [ ] Is the repeating group for `NoLegs` formatted correctly in the outgoing FIX string (no missing delimiters)?
- [ ] Are stock-option legs correctly designated (e.g., `LegSecurityType` = `CS` for Common Stock)?
- [ ] Is the total `OrderQty` properly adjusted if leg ratios were reduced?
