# Standards for Earnings Call Transcript NLP Signal Research

These are engineering standards for this skill, not regulatory requirements. No
regulator mandates a transcript-sentiment methodology; "MUST" below means "MUST,
to produce a defensible research signal with this module".

| Metric | Engineering Standard | Basis |
|---|---|---|
| Financial Lexicon Rule | Transcript sentiment MUST use the Loughran-McDonald (LM) financial lists, not a general-English lexicon. Every custom word MUST be verified against the Master Dictionary category flags before use. | Loughran & McDonald (2011): in 10-Ks filed 1994–2008, "almost three-fourths of the words identified as negative by the widely used Harvard Dictionary are words typically not considered negative in financial contexts." |
| Category Discipline | `risk`/`risks` are LM **Uncertainty**, not LM Negative. Category overlap that exists in LM (e.g. `volatility` is both Negative and Uncertainty) MUST be preserved rather than resolved. | LM Master Dictionary category flags. |
| Negation Handling | A positive word preceded within three tokens by one of `no, not, none, neither, never, nobody` MUST be reclassified as negative. Negation MUST NOT be applied to negative words. | Loughran & McDonald (2011): "Simple negation is taken to be observations of one of six words (no, not, none, neither, never, nobody) occurring within three words preceding a positive word… we do not consider negation for the negative word lists." |
| Section Segmentation | Transcripts MUST be split into Prepared Remarks vs Q&A before scoring; the two sections MUST be scored independently. | Price, Doran, Peterson & Bliss (2012): the Q&A portion has incremental explanatory power for post-earnings-announcement drift beyond the presentation section. |
| Sample Sufficiency | A section below the configured minimum token count or minimum polarity-term count MUST emit `INSUFFICIENT_DATA` rather than a tone signal. | Net sentiment is a ratio over polarity hits; on one or two hits it saturates at ±1.0 and carries no information. |
| Threshold Calibration | The default thresholds ($\Delta_{\text{tone}} < -0.15$; pooled sentiment $> 0.40$; uncertainty $< 1.5\%$) are illustrative defaults, NOT validated constants, and MUST be re-estimated out-of-sample per universe before live use. | No published study establishes universal values for these cut-offs. |
| Point-in-Time Alignment | Signals MUST be stamped with the timezone-aware transcript **publication** time, not the call date. Timezone-naive timestamps MUST be rejected. | Transcripts publish after the call ends; call-time alignment trades on text that did not yet exist. |

## Sources

- Loughran, T. & McDonald, B. (2011). "When Is a Liability Not a Liability? Textual Analysis, Dictionaries, and 10-Ks." *Journal of Finance* 66(1), 35–65. Negation rule and Fin-Neg/Fin-Pos construction: Section I.
- Loughran-McDonald Master Dictionary and Sentiment Word Lists, University of Notre Dame SRAF: <https://sraf.nd.edu/loughranmcdonald-master-dictionary/>. Categories: Negative, Positive, Uncertainty, Litigious, Strong Modal, Weak Modal, Constraining. Free for academic research; commercial use requires a licence from the authors.
- Price, S. M., Doran, J. S., Peterson, D. R. & Bliss, B. A. (2012). "Earnings conference calls and stock returns: The incremental informativeness of textual tone." *Journal of Banking & Finance* 36(4), 992–1011.
