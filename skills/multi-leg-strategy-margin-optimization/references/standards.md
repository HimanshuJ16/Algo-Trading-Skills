# Standards for Multi-Leg Strategy Margin Optimization

| Metric | Engineering Standard |
|---|---|
| Naked Call Margin | $\max(0.20 S - \text{OTM} + P, 0.10 S + P) \times 100 \times Q$. |
| Vertical Spread Margin | Max Margin = $(\text{Spread Width}) \times 100 \times Q - \text{Net Credit}$. |
| Iron Condor Margin | Max Margin = $\max(\text{Width}_{\text{call}}, \text{Width}_{\text{put}}) \times 100 \times Q - \text{Net Credit}$. |
