# Standards for Social Media Sentiment Signal With Bot Filtering

These are engineering standards for this skill, not regulatory requirements. **No
regulator, exchange or platform publishes a minimum account age, a maximum posting
rate, or a sentiment Z-score threshold.** "MUST" below means "MUST, to produce a
defensible signal with this module". Every numeric default in the table is a house
value and must be calibrated per asset and per venue before live use.

| Parameter / rule | Engineering standard | Basis |
|---|---|---|
| Baseline units | `historical_baseline_mean` and `historical_baseline_std` MUST describe the distribution of the *filtered aggregate statistic* (e.g. past daily filtered means for this asset, same filters, same window), never the distribution of individual post scores. | The standard deviation of a mean of $n$ observations is smaller than the per-post standard deviation by roughly $\sqrt{n}$. A per-post $\sigma$ in the denominator understates every $Z$ by that factor while still producing a well-formed number. The engine cannot distinguish the two. |
| Degenerate baseline | A `historical_baseline_std` that is zero, negative, NaN or infinite MUST raise at construction. It MUST NOT be absorbed into $Z = 0$ / `NEUTRAL`. | A confident `NEUTRAL` produced by a broken configuration is indistinguishable in the audit record from a measured flat market. |
| Minimum sample | Below `min_effective_sample` contributors the engine MUST emit `INSUFFICIENT_DATA` and `sentiment_z_score = None`. A number MUST NOT be substituted. | One surviving post scoring $+1.0$ against $\sigma = 0.15$ yields $Z \approx 6.3$ — a maximum-conviction signal off a sample of one. Default `20`: a house floor, not a validated constant. |
| Effective sample size | The sample size MUST be the number of independent contributors, not the number of posts. Each author's posts are averaged to one contribution before the cross-author mean. | Cresci et al. (2019) found coordinated bot *groups* in stock microblogs; post counts are the quantity such a group controls directly. |
| Duplicate collapse | Near-duplicate post text within a batch MUST be collapsed before aggregation, keeping the first occurrence. | Per-account rules cannot see coordination. Thirty established, low-rate accounts posting the same line pass every per-post screen. Collapse only ever removes contributions, so it biases toward `INSUFFICIENT_DATA` and never inflates a signal. |
| Verification badges | A platform verification badge MUST NOT exempt an account from any screen unless the badge has been independently established to mean identity verification (`trust_verified_accounts`, default `False`). | European Commission, IP/25/2934 (5 December 2025), fining X €120M under the DSA: the blue checkmark is deceptive because "anyone can pay to obtain the 'verified' status without the company meaningfully verifying who is behind the account". X's own criteria are a Premium subscription plus display name, profile photo, confirmed phone number and activity in the past 30 days; X states the checkmark does not mean the account has been ID verified. |
| Account age screen | Unverified accounts below `min_account_age_days` are rejected. The *direction* of the rule is supported; the number is not. | SEC/OIEA "Social Media and Investment Fraud" investor alert: "Fraudsters can set up new accounts specifically designed to carry out their scam while concealing their true identities"; "Be skeptical of information from social media accounts that lack a history of prior postings or that contain minimal original content." The alert specifies no threshold. Default `30` days is this engine's. |
| Posting-rate screen | Accounts strictly above `max_posts_per_hour` are rejected. Default `40`. | No published source establishes a bot/human posting-rate boundary. Treat as a coarse burst filter and calibrate against the observed rate distribution of the venue. |
| Timestamp discipline | Post timestamps MUST be timezone-aware ISO-8601; naive values MUST be rejected, not assumed UTC. | X API v2 data dictionary: response datetimes are ISO-8601/RFC 3339 and "always returned in UTC" with a trailing `Z`, so compliant feeds already satisfy this. A naive timestamp compared against an `as_of` cutoff silently mixes clocks — a look-ahead leak that leaves no trace in the output. |
| Point-in-time cutoff | The publication cutoff MUST be enforced by filtering on an explicit `as_of` instant. A configured `lookback_window_minutes` with no `as_of` MUST raise. | A window that is recorded but not applied produces a backtest that appears point-in-time correct and is not. |
| Metadata snapshotting | `user_account_age_days`, `user_follower_count` and `user_posts_last_hour` MUST be captured at ingestion, not backfilled from a later query. | These are as-of-query values. Backfilled, every historical account passes the age screen and the backtest measures a filter that never ran. The engine cannot detect this. |
| Asset alignment | A post whose `asset_id` differs from the requested asset MUST raise, not be silently dropped. | Quiet filtering hides a broken ingestion join; aggregating it publishes a signal for an instrument it was never measured on. |
| Band decision | Directional bands MUST be decided on the unrounded $Z$; only the reported figure is rounded. | `round(1.9951, 2) == 2.0` promotes `BULLISH` to `STRONG_BULLISH` on a value the data does not support. |
| Signal vocabulary | `INSUFFICIENT_DATA` MUST be distinct from `NEUTRAL` in both the return value and any downstream consumer. | `NEUTRAL` is a measurement of a flat balance of opinion; `INSUFFICIENT_DATA` is an absence of evidence. Collapsing them invites a consumer to act on the second. |
| Threshold calibration | `signal_z` (default `0.75`) and `strong_signal_z` (default `2.0`) are illustrative defaults, NOT validated constants, and MUST be re-estimated out-of-sample per asset universe before live use. | No published study establishes universal Z thresholds for a social-sentiment rule. |
| Screen scope | This module MUST NOT be described as bot detection in any internal or external document. | Cresci et al. (2017) benchmarked Twitter itself, human annotators and state-of-the-art tools against modern social spambots and found none of them accurate — human annotators labelled them correctly under 24% of the time. |

## Compliance notes

- **Reading is not posting.** This module consumes a public stream. Wiring the same
  system to *post* about an instrument it trades is market manipulation in every
  jurisdiction that matters; keep the read path and any write path in separate
  systems with separate credentials.
- **Entitlement is separate from access.** What a platform API returns and what its
  terms permit you to store, redistribute or trade on are different questions, and
  they change. Establish the licence position before building on a feed. See
  `alternative-data-vendor-due-diligence-checklist` and
  `data-vendor-contractual-usage-restriction-tracking`.
- **Alternative data can carry material non-public information.** Sentiment over
  public posts normally does not, but the surrounding controls belong to
  `insider-trading-controls-for-alternative-data-usage`, not to this module.

## Sources

- Cresci, S., Lillo, F., Regoli, D., Tardelli, S. & Tesconi, M. (2019). "Cashtag
  Piggybacking: Uncovering Spam and Bot Activity in Stock Microblogs on Twitter."
  *ACM Transactions on the Web* 13(2), article 11. <https://doi.org/10.1145/3313184>
  (preprint arXiv:1804.04406). 9M stock-related tweets; coordinated bot groups
  piggybacking on cashtags; 71% of the authors of suspicious financial tweets
  classified as bots.
- Cresci, S., Di Pietro, R., Petrocchi, M., Spognardi, A. & Tesconi, M. (2017).
  "The Paradigm-Shift of Social Spambots: Evidence, Theories, and Tools for the Arms
  Race." *Proceedings of the 26th International Conference on World Wide Web
  Companion*, 963–972. <https://doi.org/10.1145/3041021.3055135> (preprint
  arXiv:1701.03017). Neither the platform, nor human annotators, nor state-of-the-art
  tools detect modern social spambots accurately.
- European Commission (5 December 2025). "Commission fines X €120 million under the
  Digital Services Act." IP/25/2934.
  <https://digital-strategy.ec.europa.eu/en/news/commission-fines-x-eu120-million-under-digital-services-act>
  — deceptive-design finding on the blue checkmark.
- X Help Center, "About X Blue Checkmark."
  <https://help.x.com/en/managing-your-account/about-x-bluecheck> — the checkmark
  means an active Premium subscription meeting eligibility criteria (display name,
  profile photo, confirmed phone number, activity in the past 30 days) and does not
  mean the account has been ID verified.
- SEC Office of Investor Education and Advocacy, "Social Media and Investment Fraud —
  Investor Alert."
  <https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/social-media-and-investment-fraud-investor-alert>
  — new accounts created to conceal identity; scepticism toward accounts lacking a
  posting history; pump-and-dump mechanics.
- X Developer Platform, "X API v2 data dictionary."
  <https://docs.x.com/x-api/fundamentals/data-dictionary> — response datetimes are
  ISO-8601/RFC 3339, always returned in UTC with a trailing `Z`.
