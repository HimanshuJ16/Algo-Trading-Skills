# Institutional Financial Web Sentiment Pipeline Workflows

## Workflow 1: Web Scraping, Text Cleaning, and Lexicon Scoring Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Scraper as Web Scraper (News / Reddit / Twitter)
    participant Engine as Web Sentiment Pipeline Engine
    participant Lexicon as Loughran-McDonald Dictionary
    participant DB as Sentiment Feature Store

    Scraper->>Engine: Ingest Raw Scraped Items (Text, Source, Ticker, Timestamp)
    Engine->>Engine: 1. Strip HTML Tags, URLs, Cashtags ($AAPL), Special Chars
    Engine->>Engine: 2. Tokenize Cleaned Text Stream
    
    Engine->>Lexicon: Match Tokens against Positive/Negative Wordlists
    Lexicon-->>Engine: Return Positive Word Count & Negative Word Count
    
    Engine->>Engine: 3. Compute Normalized Raw Sentiment Score = (Pos - Neg)/(Pos + Neg)
    Engine-->>DB: Store ScoredSentimentItem Records
```

---

## Workflow 2: Sentiment Anomaly Signal Generation Pipeline
```mermaid
flowchart TD
    A[Fetch Daily Scored Sentiment Items for Ticker] --> B[Calculate Daily Ticker Sentiment Mean S_mean]
    
    B --> C[Fetch 30-Day Rolling Baseline (Mean mu_30d, Std Dev sigma_30d)]
    C --> D[Calculate Sentiment Z-Score: Z = (S_mean - mu_30d) / sigma_30d]
    
    D --> E{Abs(Z-Score) >= 1.5?}
    
    E -- No --> F[Output NEUTRAL Signal]
    E -- Yes --> G{Z-Score >= +1.5?}
    
    G -- Yes --> H[Output LONG Signal for Target Ticker]
    G -- No --> I[Output SHORT Signal for Target Ticker]
    
    H --> J[Pass Signal & Confidence Score to Trading Portfolio]
    I --> J
```