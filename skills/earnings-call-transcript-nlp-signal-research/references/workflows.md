# Workflows for Earnings Call Transcript NLP Signal Research

## 1. Transcript Section Segmentation

Split the transcript into Prepared Remarks and Q&A Session before scoring. Most
vendor transcripts mark the boundary with an operator line ("we will now begin
the question-and-answer session"). If the boundary cannot be located reliably,
stop — do not score the call as a single blob, because a combined score averages
away the divergence this workflow measures.

Discard operator boilerplate and the safe-harbour statement. The safe-harbour
paragraph is dense in LM Uncertainty terms ("may", "could", "risks",
"anticipate") and is scripted legal text, so leaving it in inflates the
uncertainty ratio in a way that has nothing to do with management tone.

## 2. Lexicon Loading

Load the current Loughran-McDonald Master Dictionary and pass the Positive,
Negative and Uncertainty lists to `EarningsTranscriptNlpEngine`. The bundled
`DEFAULT_LM_*` sets are a verified but tiny subset intended only to make the
module runnable; research runs must use the full lists.

Confirm the licence position first: the lists are free for academic research,
and commercial use requires a licence from the authors.

## 3. Tokenization and Negation

Tokenize lower-cased text, keeping contractions and hyphenated compounds intact.
Apply the LM negation rule: a positive word with one of `no, not, none, neither,
never, nobody` in the three preceding tokens is counted as negative. Negation is
not applied to negative words.

## 4. Section Scoring

For each section compute:

- `net_sentiment = (positive - negative) / (positive + negative)`, defined as
  `0.0` when no polarity words are present.
- `uncertainty_ratio_pct = uncertainty / total_tokens * 100`.

Reject the section as an insufficient sample when it is below the configured
token floor or polarity-term floor.

## 5. Divergence and Pooled Metrics

- `qa_tone_divergence = qa.net_sentiment - prepared.net_sentiment`.
- `overall_net_sentiment` pools the raw counts of both sections; it is not the
  mean of the two section scores, so a long Q&A dominates a short preamble.
- `overall_uncertainty_ratio_pct` pools uncertainty hits over pooled tokens.

## 6. Signal Emission

Evaluate in precedence order and stop at the first match:

1. `INSUFFICIENT_DATA` — either section failed the sample gate.
2. `BEARISH_QA_DIVERGENCE` — divergence below the configured threshold.
3. `BULLISH_EARNINGS_TONE` — pooled sentiment above the sentiment threshold and
   pooled uncertainty below the uncertainty ceiling.
4. `NEUTRAL`.

## 7. Calibration Before Use

Estimate the divergence, sentiment and uncertainty thresholds on a historical
sample of your own universe, out-of-sample, before trading them. Tone
distributions differ by sector, market cap, and transcript vendor; the shipped
defaults are illustrative.

## 8. Point-in-Time Alignment

Stamp each report with the timezone-aware transcript publication timestamp and
align backtest entries to that time (plus your own ingestion latency), not to
the call date or the earnings release date.
