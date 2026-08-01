# Pre-Flight Checklist

- [ ] Are Python and core ML package versions identical between research and production?
- [ ] Is floating-point precision matched (`float64`) across environments?
- [ ] Are feature calculation code hashes verified prior to deployment?
- [ ] Has shadow execution diffing confirmed live signals match research outputs within 0.1% tolerance?