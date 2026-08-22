# Workflows for Central Bank NLP & Macro Signal Generation

## End-to-End Processing Architecture

```
[Central Bank Web / Wire Feed]
              │
              ▼
[1. Ingestion & Embargo Verification]
   ├── Check Release Timestamp (e.g. 14:00:00 ET)
   └── Strip Navigation Boilerplate & Legal Disclaimers
              │
              ▼
[2. Sentence Segmentation]
   ├── Split on [. ! ? ; \n]
   └── Isolate Sentence Contexts (No Cross-Sentence Leakage)
              │
              ▼
[3. Multi-Word Collocation Matching]
   ├── Longest-Match First ("quantitative tightening", "labor market slack")
   └── Mask Matched Tokens to Prevent Double Counting
              │
              ▼
[4. Sentence-Bounded Negation Scoring]
   ├── Scan 3-Token Preceding Lookback Window
   └── Invert Polarity: Negated Hawkish -> Dovish, Negated Dovish -> Hawkish
              │
              ▼
[5. Metric Computation]
   ├── Normalized Stance Index: (H - D) / (H + D)
   ├── Densities per 1,000 words (Hawkish, Dovish, Uncertainty)
   └── Extract Matched Phrase Inventory
              │
              ▼
[6. Sequential Statement Diffing (FOMC Redline)]
   ├── Load Prior Meeting Statement
   ├── Compute Delta Shock: Delta = NetScore_t - NetScore_{t-1}
   └── Calculate Jaccard / Cosine Similarity & Phrase Changes
              │
              ▼
[7. Macro Execution & Portfolio Allocation Engine]
```

---

## Detailed Step-by-Step Procedures

### Phase 1: Ingestion & Text Normalization
1. **Fetch Policy Text**: Ingest the raw release immediately upon embargo lift (e.g. 14:00:00 US Eastern for FOMC).
2. **Boilerplate Removal**: Strip standard web headers, committee member voter lists, and legal disclaimers. Retain only the narrative policy statement and implementation note.
3. **Sentence Splitting**: Mask decimals (`0.25`, `2.5`) and dotted abbreviations (`U.S.`, `e.g.`), then partition text using regex `r'[.!?;\n]+'` into clean sentence units, restoring the masked periods afterwards. An unmasked split fragments *"no 0.25 percentage point hike"* and silently inverts its stance. Single-token abbreviations (`Mr.`, `No.`) remain unprotected.

### Phase 2: Term Extraction & Negation Resolution
1. **Multi-Word Phrase Matching**:
   - Compare lowercase sentence tokens against `HAWKISH_PHRASES` and `DOVISH_PHRASES` in a single pass ordered by descending phrase length, so a longer opposite-polarity collocation is not pre-empted by a shorter one.
   - On match, record token indices as consumed to prevent single-word double counting.
2. **Local Negation Window**:
   - For every candidate phrase/word match at index $i$, inspect tokens in range $[\max(0, i-3), i)$.
   - If any token is in `NEGATION_WORDS` (*"not"*, *"no"*, *"less"*, *"without"*, *"hardly"*, *"rarely"*, *"neither"*, *"nor"*), invert the polarity.
   - **Crucial Rule**: Because evaluation occurs within a single sentence, a negation in sentence $k$ cannot contaminate words in sentence $k+1$.

### Phase 3: Stance Index & Rhetoric Density Calculation
1. Calculate the total hawkish and dovish signal count:
   $$\text{Signals} = \text{Hawkish} + \text{Dovish}$$
2. Compute the normalized net stance score:
   $$\text{Net Score} = \begin{cases} \frac{\text{Hawkish} - \text{Dovish}}{\text{Signals}} & \text{if } \text{Signals} > 0 \\ 0.0 & \text{otherwise} \end{cases}$$
3. Compute document densities per 1,000 words:
   $$\text{Density}_{\text{Hawkish}} = \frac{\text{Hawkish}}{\text{Total Words}} \times 1000$$
   $$\text{Density}_{\text{Dovish}} = \frac{\text{Dovish}}{\text{Total Words}} \times 1000$$
   $$\text{Density}_{\text{Uncertainty}} = \frac{\text{Uncertainty}}{\text{Total Words}} \times 1000$$

### Phase 4: Sequential Statement Comparison (FOMC Redline)
1. Ingest the baseline statement from meeting $t-1$.
2. Compute the **Policy Surprise Delta**:
   $$\Delta \text{Score} = \text{NetScore}_t - \text{NetScore}_{t-1}$$
3. Compute lexical Jaccard similarity and term-frequency Cosine similarity to quantify structural rewording.
4. Extract set differences:
   - $\text{Added Hawkish} = \text{Hawkish}_t \setminus \text{Hawkish}_{t-1}$
   - $\text{Removed Hawkish} = \text{Hawkish}_{t-1} \setminus \text{Hawkish}_t$
   - $\text{Added Dovish} = \text{Dovish}_t \setminus \text{Dovish}_{t-1}$
   - $\text{Removed Dovish} = \text{Dovish}_{t-1} \setminus \text{Dovish}_t$

### Phase 5: Quantitative Strategy Integration

> The thresholds and trade expressions below are **illustrative defaults, not calibrated
> parameters**. The $\pm 0.3$ delta cut-off is not derived from published research; calibrate
> it against your own event-study of realised post-release moves before risking capital.

1. **Rates & Fixed Income**:
   - Strong Hawkish Surprise ($\Delta \text{Score} > +0.3$): Short 2Y/5Y Treasury futures, pay fixed in SOFR OIS swaps.
   - Strong Dovish Surprise ($\Delta \text{Score} < -0.3$): Long Treasury futures, receive fixed in SOFR OIS swaps.
2. **Foreign Exchange (FX)**:
   - Hawkish US Surprise: Long USD vs EUR, JPY, GBP.
   - Dovish US Surprise: Short USD, long high-beta G10 currencies (AUD, NZD, CAD).
3. **Volatility & Equities**:
   - Elevated Uncertainty Score: Long VIX futures, reduce equity gross leverage.