# Standards for Macro NLP

| Metric | Engineering Standard |
|---|---|
| Negation Handling | The engine MUST support a lookback window of at least 3 tokens to catch modifiers (e.g., "is *not* currently looking to *hike*"). |
| Lexicon Precision | The lexicon must be strictly tailored to monetary policy. Words like "growth" can be context-dependent and require multi-word phrase matching (e.g., "strong growth" vs "weak growth"). |
| Normalization | Net scores must be normalized either by total word count or total matched words to account for varying document lengths over time. |