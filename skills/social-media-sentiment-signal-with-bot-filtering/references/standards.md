# Standards for Social Media Sentiment Signal With Bot Filtering

| Metric | Threshold Standard |
|---|---|
| Min Account Age | Unverified accounts $< 30$ days old MUST be filtered out as potential bots. |
| Max Posting Burst | Accounts posting $> 40$ posts/hour MUST be filtered as automated spammers. |
| Signal Threshold | Z-Score $\ge +2.0 \implies$ Strong Bullish; Z-Score $\le -2.0 \implies$ Strong Bearish. |