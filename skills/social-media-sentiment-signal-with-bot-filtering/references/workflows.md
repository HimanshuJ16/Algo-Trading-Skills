# Workflows for Social Media Sentiment Signal With Bot Filtering

1. **Social Stream Ingestion**:
   - Stream raw social posts via API (StockTwits / X Twitter).
2. **Bot & Spam Filtering**:
   - Filter young accounts (<30d), high-frequency bursts (>40/hr), and spam links (`t.me/`).
3. **NLP Sentiment Scoring**:
   - Score clean posts using financial lexicon dictionary.
4. **Z-Score Signal Standardizing**:
   - Normalize sentiment score vs 30-day baseline mean and standard deviation.