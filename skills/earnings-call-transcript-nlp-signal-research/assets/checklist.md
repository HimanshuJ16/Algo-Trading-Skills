# Pre-Flight Checklist

## Lexicon

- [ ] Is the full Loughran-McDonald Master Dictionary loaded, rather than the bundled `DEFAULT_LM_*` sample subset?
- [ ] Has every custom or added word been checked against the LM category flags (not assumed from intuition)?
- [ ] Are `risk` / `risks` classified as **Uncertainty** and not as Negative?
- [ ] Is LM's own category overlap preserved (e.g. `volatility` in both Negative and Uncertainty)?
- [ ] Is the LM licence position cleared for this use (academic research is free; commercial use requires a licence)?

## Text Handling

- [ ] Are transcripts split into Prepared Remarks and Q&A Session before scoring?
- [ ] Is operator boilerplate and the safe-harbour statement stripped, so scripted legal hedging does not inflate the uncertainty ratio?
- [ ] Is the LM negation rule applied (six negators, three-token look-back, positive words only)?
- [ ] Is the transcript English and human-transcribed, rather than translated or ASR-generated?

## Signal Quality

- [ ] Is Q&A tone divergence ($\Delta_{\text{tone}}$) computed from independently scored sections?
- [ ] Does every section clear the minimum token and minimum polarity-term floors, so no signal is emitted on a saturated ±1.0 score from one or two hits?
- [ ] Are zero-sentiment sections interpreted as "no evidence" rather than "neutral tone" (check `has_sufficient_sample`)?
- [ ] Have the divergence, sentiment and uncertainty thresholds been recalibrated out-of-sample on this universe instead of inherited from the defaults?

## Point-in-Time Integrity

- [ ] Is the transcript **publication** timestamp used, not the call date or earnings date?
- [ ] Is that timestamp timezone-aware?
- [ ] Does the backtest add realistic ingestion/parsing latency after publication before the signal becomes tradable?
