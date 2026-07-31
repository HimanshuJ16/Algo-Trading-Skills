# Standards for Multi-Signature Approval for Large Transfers

| Metric | Engineering Standard |
|---|---|
| Medium Tier Threshold | $T_{\text{auto}} \le \text{Val} \le T_{\text{high}} \implies$ $2$-of-$3$ distinct signatures. |
| High Tier Threshold | $\text{Val} > T_{\text{high}} \implies$ $3$-of-$5$ distinct signatures + Timelock. |
| Timelock Delay | Mandatory timelock delay ($\ge 3600\text{s}$) for transfers $> \$100,000$ USD. |