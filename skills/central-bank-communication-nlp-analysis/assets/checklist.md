# Pre-Flight Checklist

- [ ] Does the NLP engine properly handle negations, inverting the sentiment of the matched lexicon word?
- [ ] Is the text stripped of punctuation and converted to lowercase before matching?
- [ ] Are the output scores normalized (e.g., ranging from -1.0 to 1.0) so they can be compared across documents of different lengths?
- [ ] Is the lexicon specifically tuned for central bank rhetoric (Hawkish/Dovish) rather than generic positive/negative sentiment?