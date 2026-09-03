# Web-Scraped Sentiment Pipeline — Standards and Formula Provenance

## 1. The Loughran-McDonald lexicon

### 1.1 What it is

Loughran, T. and McDonald, B. (2011), "When Is a Liability Not a Liability? Textual Analysis,
Dictionaries, and 10-Ks", *Journal of Finance* 66(1), 35-65. The authors show that general-purpose
word lists misclassify financial text: in a sample of 10-Ks filed 1994-2008, **almost
three-fourths of the words the Harvard-IV psychosocial dictionary marks negative are not
typically negative in a financial context** (the paper's title word, *liability*, is the standard
example — it is uncategorised in LM). They induced replacement word lists from the filings
themselves.

The Master Dictionary is maintained by the Software Repository for Accounting and Finance (SRAF)
at the University of Notre Dame and updated annually. Categories: Negative, Positive,
Uncertainty, Litigious, Constraining, Superfluous, Interesting, and strong/weak Modal. Category
membership is encoded as the *year* the word entered the category, so any non-zero value means
"in this category".

| Category | Entries (2014 release) |
| :--- | ---: |
| Negative | 2,355 |
| Positive | 354 |
| Uncertainty | 297 |
| Litigious | 903 |
| Constraining | 184 |

Source: <https://sraf.nd.edu/loughranmcdonald-master-dictionary/>

### 1.2 Licence

SRAF publishes the materials **free for use in academic research**; the site directs commercial
users to contact the authors. A production trading deployment is a commercial application.
Confirm entitlement before building a live signal on the dictionary — this is a licensing
question, not a technical one.

### 1.3 What this skill bundles

`LM_POSITIVE_WORDS` (80 terms) and `LM_NEGATIVE_WORDS` (140 terms) are the highest
document-frequency slice of the corresponding LM categories. **Every bundled term was verified
against the Master Dictionary; none was added by hand.** They are a subset for standalone
operation, not a substitute for the dictionary — call
`load_lm_lexicon_from_master_dictionary(csv_path)` to load all 354 / 2,355.

### 1.4 Category confusions this skill exists to prevent

Words routinely miscounted as LM-Negative that LM places elsewhere, or nowhere:

| Term | Actual LM classification | Why it matters |
| :--- | :--- | :--- |
| `risk` | **Uncertainty** | Folding Uncertainty into Negative changes what the score measures. |
| `lawsuit` | **Litigious** | Litigious counts legal-process language, not tone. |
| `drop` | **Interesting** | Not a sentiment category at all. |
| `growth`, `revenue`, `dividend`, `buy`, `record`, `momentum`, `bullish`, `rally`, `surge`, `beat`, `expansion` | **uncategorised** | Market vernacular, not LM Positive. |
| `slump`, `bearish`, `plunge`, `downfall`, `scandal`, `sell` | **uncategorised** | Market vernacular, not LM Negative. |
| `cost`, `shares`, `board`, `tax`, `president`, `stock`, `report`, `liability`, `capital`, `depreciation` | **uncategorised** | The Harvard-IV false positives LM was built to remove. |

### 1.5 Domain-transfer caveat

LM was induced from **10-K filings**. Some classifications are artefacts of filing prose and do
not transfer to news headlines or social posts:

| Term | LM category | Reading in a headline |
| :--- | :--- | :--- |
| `despite` | Positive | "profitable despite headwinds" — concessive, not positive |
| `closed` | Negative | "shares closed higher" |
| `volatility`, `volatile` | Negative | a neutral market descriptor |
| `claims`, `disclose`, `disclosed` | Negative | routine reporting verbs |
| `critical` | Negative | "critical to the strategy" |
| `force`, `against`, `late`, `restructuring`, `closing` | Negative | context-dependent |

These are collected in `FILING_SPECIFIC_TERMS` and excluded by default. Set
`exclude_filing_specific_terms=False` when the corpus **is** filing text.

---

## 2. Formulas

### 2.1 Document polarity, $S_{\text{pol}}$ — `raw_sentiment_score`

$$S_{\text{pol}} = \frac{N_{p} - N_{n}}{N_{p} + N_{n}} \in [-1, +1]$$

with $S_{\text{pol}} = 0$ by definition when $N_p + N_n = 0$.

**This is not the LM tone measure.** It is a conventional polarity ratio. Its failure mode is
saturation: a document matching one positive word scores exactly $+1.0$, identical to a document
matching fifty. `matched_word_count` is reported alongside it and `min_matched_words` gates it.

### 2.2 Document tone, $S_{\text{tone}}$ — `lm_tone`

$$S_{\text{tone}} = \frac{N_{p} - N_{n}}{N_{\text{tokens}}}$$

Loughran and McDonald normalise word-list counts by **the total number of words in the
document**; their portfolio sorts use the proportion of negative words on that denominator. The
paper additionally proposes a tf-idf weighting that down-weights words common across the
collection and adjusts for document length — not implemented here, and a documented limitation
rather than an omission.

### 2.3 Negation

A lexicon term whose index is within `NEGATION_WINDOW` (3) tokens **after** a syntactic negator
has its polarity flipped before counting. `NEGATORS` is restricted to purely syntactic negators;
a word that is itself a lexicon term (`failed`, `fails`) scores on its own polarity rather than
suppressing the term after it.

This is a shallow heuristic. It does not parse scope, handle double negation, or resolve
"not only ... but".

### 2.4 Daily aggregate, $\bar{S}_{t}$ — `current_sentiment_mean`

$$\bar{S}_{t} = \frac{1}{M}\sum_{i=1}^{M} S_{m, i}, \qquad m \in \{\text{polarity},\ \text{lm\_tone}\}$$

where $m$ is `score_metric` (default `polarity`).

over the $M$ **eligible** documents: correct ticker, timestamp in
$[\,t - w + 1,\; t\,]$ resolved in `session_timezone`, not a marked duplicate, and
$N_p + N_n \ge$ `min_matched_words`. Every exclusion is counted on the returned signal.

The mean is unweighted. Volume weighting requires a per-document credibility or reach weight that
`RawScrapedItem` does not carry; a claim to volume-weight without such a field would be false.

### 2.5 Anomaly Z-score

$$Z = \frac{\bar{S}_{t} - \mu_{\text{baseline}}}{\sigma_{\text{baseline}}}$$

with $\sigma_{\text{baseline}}$ the **sample** standard deviation (Bessel-corrected, $n - 1$
denominator) of the caller's baseline. Defined only when:

- $n_{\text{baseline}} \ge$ `min_baseline_observations` — a sample standard deviation is
  undefined at $n = 1$ and unstable just above it; and
- $\sigma_{\text{baseline}}$ is finite and $\ge$ `min_baseline_std`.

The floor is not decoration. Both supported metrics are bounded in $[-1, +1]$, so a $\sigma$ of
$10^{-160}$ is arithmetic noise, not dispersion — yet it is finite and strictly positive, so a
bare $\sigma > 0$ guard admits it and the engine then emits $|Z| \approx 10^{160}$ as a
maximum-conviction signal beside a `baseline_std` that rounds to `0.0` in the output. A baseline
whose squared deviations overflow the float range is rejected outright: values of that magnitude
are not sentiment aggregates.

Otherwise $Z$ is `None` and the direction is `INSUFFICIENT_DATA`. **No substitute $\sigma$ is
ever supplied.** Defaulting a degenerate $\sigma$ to $1.0$ converts a missing measurement into a
confident one.

**Units.** The baseline must be past values of $\bar{S}_t$ — the same daily aggregate, same
ticker, same filters, same window, **same `score_metric`**. `polarity` and `lm_tone` are not
interchangeable: tone values on a headline feed run roughly an order of magnitude smaller than
polarity values, so a tone mean standardised against a polarity baseline yields a Z-score that is
arithmetically valid and semantically meaningless. Not per-document scores either: $\sigma$ of a mean of $n$ draws is
smaller than the per-document $\sigma$ by roughly $\sqrt{n}$, so per-document values in the
denominator understate every $Z$ by that factor, and the output still looks like a valid
Z-score.

### 2.6 Conviction scale — `confidence_score`

$$c = \min\left(1,\; \frac{|Z|}{\theta \cdot k}\right), \quad \theta = \texttt{zscore\_threshold},\; k = \texttt{conviction\_saturation\_multiple}$$

A bounded presentation scale. **Not a probability, not a calibrated confidence interval, and not
a position size.** It has no statistical interpretation and must not be multiplied into notional.

---

## 3. Signal bands

Decided on the **unrounded** $Z$; the reported `sentiment_zscore` is rounded to 2 dp for display
only.

| Condition | Direction |
| :--- | :--- |
| $Z \ge +\theta$ | `LONG` |
| $Z \le -\theta$ | `SHORT` |
| $-\theta < Z < +\theta$ | `NEUTRAL` |
| $Z$ not computable | `INSUFFICIENT_DATA` |

`NEUTRAL` means the balance of opinion was measured and was flat. `INSUFFICIENT_DATA` means it
was not measured. A consumer may act on the first and must never act on the second.

---

## 4. Threshold provenance

Every default below is a **house heuristic**, not a standard, a regulation, or a published
result. They are starting points to be calibrated per ticker and per source mix on out-of-sample
data.

| Parameter | Default | Rationale |
| :--- | ---: | :--- |
| `zscore_threshold` | 1.5 | Conventional anomaly band. No published basis for 1.5 specifically. |
| `min_matched_words` | 2 | At 1, polarity saturates at $\pm1.0$ and carries no intensity. |
| `min_items` | 3 | A mean over one or two documents is not a distribution. |
| `min_baseline_observations` | 20 | Sample $\sigma$ is undefined at $n=1$ and unstable just above it. |
| `aggregation_window_days` | 1 | Matches the documented daily mean. |
| `min_baseline_std` | 1e-9 | Both metrics live in $[-1,+1]$; below this, $\sigma$ is float noise, not dispersion. |
| `score_metric` | `polarity` | Bounded and source-comparable. `lm_tone` retains intensity but its scale tracks document length. |
| `conviction_saturation_multiple` | 2.0 | Presentation only. |

---

## 5. Collection and regulatory touchpoints

This skill processes text that has already been collected; it does not scrape. The collection
step carries obligations this engine cannot enforce:

- **Site terms of service and `robots.txt`** govern automated collection independently of what a
  server returns to a request.
- **Platform API terms** (Reddit, X, StockTwits) govern storage, redistribution and derived-work
  rights separately from access. See `data-vendor-contractual-usage-restriction-tracking`.
- **SEC EDGAR** publishes a fair-access policy: a maximum of **10 requests per second** across
  all machines you operate, and a declared `User-Agent` identifying you with a contact address.
  Requests without one are refused with `403`; exceeding the rate returns `429` and a temporary
  block. Re-check the current terms at
  <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data> before
  configuring a crawler — the ceiling has been revised before.
- **Material non-public information.** A scraper that acquires MNPI creates an insider-trading
  exposure that no downstream filter cures — see
  `insider-trading-controls-for-alternative-data-usage`.
- **Point-in-time integrity.** Where a sentiment feature informs orders, the audit trail must show
  which documents were available at decision time. The exclusion counters on `SentimentSignal`
  exist for that record — see `backtest-audit-trail-for-regulatory-review`.

---

## 6. Sources

| Claim | Source |
| :--- | :--- |
| LM word lists, category counts, construction from 10-Ks, annual updates, academic-use licence | SRAF, University of Notre Dame — <https://sraf.nd.edu/loughranmcdonald-master-dictionary/> |
| Harvard-IV misclassifies ~three-fourths of its negative words in financial text; LM replacement lists | Loughran & McDonald (2011), *Journal of Finance* 66(1), 35-65 — <https://doi.org/10.1111/j.1540-6261.2010.01625.x> |
| Tone normalised by total document words; tf-idf weighting with a document-length adjustment | Loughran & McDonald (2011), Section I and equation (1) |
| Per-term category membership used to build and verify the bundled subsets | Loughran-McDonald Master Dictionary CSV, `Positive` / `Negative` columns |
| EDGAR fair-access: 10 requests/second ceiling, required identifying `User-Agent`, 403/429 enforcement | SEC, *Accessing EDGAR Data* — <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data> |
