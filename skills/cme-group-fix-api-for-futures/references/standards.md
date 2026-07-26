# Standards for CME Group FIX API

| Metric | Engineering Standard |
|---|---|
| Manual Order Indicator | Tag 1028 MUST be populated on every order (`N` for automated algorithms, `Y` for manual trader order entry). |
| Self-Match Prevention | Tag 7928 (SMP ID) and Tag 8000 (SMP Instruction) MUST be included on all automated order flows to prevent accidental self-crossing. |
| Sequence Recovery | Gap detection MUST automatically trigger a FIX `ResendRequest` (`35=2`) to recover missing messages. |
