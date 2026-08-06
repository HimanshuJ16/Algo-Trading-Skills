# Institutional Financial Web Sentiment Pipeline Standards

## 1. Loughran-McDonald Financial Lexicon Classification Standard
| Word Category | Example Tokens | Financial Context | Sentiment Score Contribution |
| :--- | :--- | :--- | :--- |
| **Positive Financial** | `growth`, `profit`, `outperform`, `surge`, `upgrade`, `dividend`, `beat` | Strong earnings, guidance upgrade, revenue growth | $+1.0$ per match |
| **Negative Financial** | `loss`, `decline`, `slump`, `default`, `bankruptcy`, `downgrade`, `lawsuit` | Earnings miss, default risk, legal liability | $-1.0$ per match |
| **Neutral / Ignored** | `cost`, `shares`, `board`, `tax`, `president`, `stock`, `report` | Standard financial terminology | $0.0$ (Neutral) |

---

## 2. Quantitative Sentiment Scoring & Anomaly Formulas

### A. Loughran-McDonald Normalized Sentiment Score ($S_{\text{raw}}$):
$$S_{\text{raw}} = \frac{N_{\text{positive}} - N_{\text{negative}}}{N_{\text{positive}} + N_{\text{negative}}}$$

Where $N_{\text{positive}}$ and $N_{\text{negative}}$ are counts of Loughran-McDonald dictionary words matched in the cleaned document. Range: $[-1.0, +1.0]$.

### B. Volume-Weighted Daily Ticker Sentiment Mean ($\bar{S}_{\text{ticker}, t}$):
$$\bar{S}_{\text{ticker}, t} = \frac{1}{M} \sum_{i=1}^{M} S_{\text{raw}, i}$$

Where $M$ is the number of scraped posts/articles for the ticker on day $t$.

### C. Sentiment Anomaly Z-Score ($Z_{\text{sentiment}}$):
$$Z_{\text{sentiment}} = \frac{\bar{S}_{\text{ticker}, t} - \mu_{30\text{d}}}{\sigma_{30\text{d}}}$$

Where $\mu_{30\text{d}}$ and $\sigma_{30\text{d}}$ are the 30-day rolling baseline mean and standard deviation of sentiment scores for the target ticker.

---

## 3. Signal Decision Standard Matrix
- **If $Z_{\text{sentiment}} \ge +1.5$**: Strong positive sentiment surge $\rightarrow$ **LONG Signal**
- **If $Z_{\text{sentiment}} \le -1.5$**: Severe negative sentiment slump $\rightarrow$ **SHORT Signal**
- **If $-1.5 < Z_{\text{sentiment}} < +1.5$**: Normal sentiment fluctuations $\rightarrow$ **NEUTRAL Signal**