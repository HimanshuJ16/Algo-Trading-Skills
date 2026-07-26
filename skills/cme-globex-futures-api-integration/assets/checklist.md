# Pre-Flight Checklist

- [ ] Is Tag 50 (Operator ID) validated for presence and length (2-18 chars) on every order?
- [ ] Are contract parameters (tick size, price band, protection points) loaded for each traded symbol?
- [ ] Does the engine convert Market orders into Market-With-Protection (MWP) limit orders?
- [ ] Are limit prices validated against price bands before sending to the socket?
