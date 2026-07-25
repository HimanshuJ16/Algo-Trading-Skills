# Workflows for Central Bank NLP

1. **Text Acquisition**: Fetch the HTML/PDF of the central bank statement immediately upon release.
2. **Text Cleaning**: Strip boilerplate headers, footers, and legal disclaimers. Only keep the policy narrative.
3. **NLP Pipeline Execution**:
   - Split text into sentences.
   - For each sentence, scan for words in the `HAWKISH_LEXICON` (e.g., *tighten, inflation, hike, strong*).
   - Scan for words in the `DOVISH_LEXICON` (e.g., *accommodative, ease, cut, weak*).
   - Apply the negation lookback window: if a negation word (*not, less, without*) appears within 3 words preceding a lexicon match, invert its classification (Hawkish -> Dovish, Dovish -> Hawkish).
4. **Index Calculation**: 
   - $Net\_Score = \frac{Hawkish\_Count - Dovish\_Count}{Hawkish\_Count + Dovish\_Count + 1}$
5. **Shock Calculation**: Subtract the $Net\_Score$ of the previous meeting from the current meeting to generate the `Communication Shock` signal.