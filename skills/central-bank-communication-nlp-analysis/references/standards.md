# Standards for Central Bank NLP & Macro Communication Analysis

## Academic Foundations & Quantitative Frameworks

| Research Framework | Citation | Core Methodology & Implementation Standard |
|---|---|---|
| **Directional Monetary Lexicon** | Apel & Blix Grimaldi, *The Information Content of Central Bank Minutes*, Sveriges Riksbank Working Paper No. 261 (2012); extended as *How Informative Are Central Bank Minutes?*, Review of Economics 65(1), 2014. [Riksbank archive](https://archive.riksbank.se/en/Web-archive/Published/Other-reports/Working-Paper-Series/2012/No-261-The-Information-Contentof-Central-Bank-Minutes/index.html) | Directional scoring categorizing words into monetary tightening (hawkish) vs easing (dovish), rejecting generic positive/negative polarity. |
| **Topic & Stance Modeling** | Hansen & McMahon, *Shocking Language: Understanding the Macroeconomic Effects of Central Bank Communication*, Journal of International Economics 99(S1), 2016, S114-S133, doi:10.1016/j.jinteco.2015.12.008 ([PDF](https://sekhansen.github.io/pdf_files/jie_2016.pdf)) | Separating forward-looking forward guidance from current economic state assessments; measuring policy shocks via textual delta. |
| **Domain-Specific Lexicons** | Loughran & McDonald, *When Is a Liability Not a Liability? Textual Analysis, Dictionaries, and 10-Ks*, Journal of Finance 66(1), 2011 | General sentiment dictionaries (e.g. Harvard General Inquirer, VADER) fail in finance. Macro analysis requires custom central bank terminology. |
| **Statement Diffing & Redline** | Federal Reserve FOMC statement archive, [FOMC meeting calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | Consecutive meeting comparison to detect word substitution, clause insertion, and deleted guidance. |

> **Lexicon provenance**: the word and phrase lists shipped in `scripts/central_bank_nlp_engine.py` are hand-curated in the *directional* spirit of the Apel-Blix Grimaldi and Hansen-McMahon work (tightening vs easing, not positive vs negative). They are **not** a reproduction of any published dictionary, have not been validated against those papers' results, and must be recalibrated per central bank and per policy regime before production use.

---

## Quantitative Metric Standards & Formulas

### 1. Normalized Stance Index (Net Polarity)
Measures the directional balance of monetary policy rhetoric:
$$\text{Net Score} = \begin{cases} \frac{\text{Hawkish Count} - \text{Dovish Count}}{\text{Hawkish Count} + \text{Dovish Count}} & \text{if } (\text{Hawkish} + \text{Dovish}) > 0 \\ 0.0 & \text{otherwise} \end{cases}$$
- Range: $[-1.0, 1.0]$
  - $+1.0$: Exclusively hawkish rhetoric (tightening bias).
  - $-1.0$: Exclusively dovish rhetoric (easing bias).
  - $0.0$: Perfectly balanced or neutral communication.

### 2. Stance Density (Mentions per 1,000 Words)
Standardizes signal intensity across documents of differing lengths (e.g., short 500-word policy statement vs 10,000-word meeting minutes):
$$\text{Hawkish Density} = \left(\frac{\text{Hawkish Count}}{\text{Total Word Count}}\right) \times 1000$$
$$\text{Dovish Density} = \left(\frac{\text{Dovish Count}}{\text{Total Word Count}}\right) \times 1000$$
$$\text{Uncertainty Density} = \left(\frac{\text{Uncertainty Count}}{\text{Total Word Count}}\right) \times 1000$$

### 3. Policy Surprise Delta ($\Delta \text{Score}$)
Captures the unexpected shift in monetary stance relative to the market-priced prior baseline:
$$\Delta \text{Score} = \text{NetScore}_t - \text{NetScore}_{t-1}$$
- $\Delta \text{Score} > 0$: Hawkish policy surprise (bullish short rates / USD, bearish bond prices / gold).
- $\Delta \text{Score} < 0$: Dovish policy surprise (bearish short rates / USD, bullish bond prices / risk assets).

### 4. Textual & Semantic Similarity
- **Jaccard Similarity**: Measures verbatim lexical overlap of word sets $A$ and $B$:
  $$J(A, B) = \frac{|A \cap B|}{|A \cup B|}$$
- **Cosine Similarity**: Vector-space raw term-frequency overlap (bag of words, **no IDF weighting** — common boilerplate therefore inflates similarity):
  $$\cos(\vec{u}, \vec{v}) = \frac{\vec{u} \cdot \vec{v}}{\|\vec{u}\| \|\vec{v}\|}$$

---

## Central Bank Release Hierarchy & Timestamp Standards

| Release Type | Typical Frequency | Embargo Release Time (US ET) | Alpha Horizon | Look-Ahead Boundary |
|---|---|---|---|---|
| **FOMC Policy Statement** | 8 times / year | Exactly 14:00:00 ET | Immediate (Seconds to Minutes) | Embargo lifts strictly at 14:00:00 ET. |
| **FOMC Press Conference** | 8 times / year | Customarily 14:30 ET (verify per meeting; not published as an exact embargo) | Intraday (Minutes to Hours) | Live speech; audio streaming required. |
| **Summary of Economic Projections (SEP)** | 4 times / year | 14:00:00 ET (with Statement) | Multi-week / Quarterly | Dot plot and GDP/CPI forecast tables. |
| **FOMC Meeting Minutes** | 8 times / year | 14:00:00 ET (3 weeks post-meeting)| Multi-day to Multi-week | Historical reflection; do NOT back-align to statement date! |

Statement, Implementation Note, and Projection Materials release times ("Released ... at 2:00 p.m.") and the three-week minutes lag are as published on the Federal Reserve's [FOMC meeting calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) and per-meeting pages. Times are US Eastern (ET, i.e. EST/EDT as applicable). Other central banks publish on their own schedules (e.g. Bank of England and ECB release outside US hours) — do not reuse the FOMC clock for them.