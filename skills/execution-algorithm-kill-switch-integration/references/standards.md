# Standards for Execution Algo Kill Switch Integration

| Metric | Engineering Standard |
|---|---|
| SEC Rule 15c3-5 Compliance | Kill switch controls MUST be hard-coded with exclusive firm admin control. |
| Kill Switch Response Time | MassCancel and order lockout MUST execute within $< 50\text{ms}$. |
| FIX Mass Cancel Standard | Order cancellation MUST utilize FIX MassCancelRequest (Tag 530 = 7). |
