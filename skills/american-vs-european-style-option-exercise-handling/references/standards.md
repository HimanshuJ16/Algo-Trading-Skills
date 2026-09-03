# Standards for Holder-Side American Option Exercise

## Decision standards enforced by this skill

| Scenario | Standard |
|---|---|
| **Every ITM American option** | Exercise if and only if the intrinsic value exceeds the proceeds of selling. Exercising realises `spot − strike` (calls) or `strike − spot` (puts) and nothing more; selling realises the bid. The comparison MUST NOT be made against a mid or a last trade. |
| **Non-dividend call** | Early exercise is never optimal (Merton 1973). The optimal action is to sell, capturing intrinsic *and* time value. A quoted bid below parity is possible only as a stale, crossed or one-sided market, and MUST be verified as executable before it is acted on. |
| **Call on the last cum-dividend session** | The dividend MUST NOT be compared against the option's **cum-dividend** quoted time value. That quote already prices the coming drop, so the comparison counts the dividend twice. The exact condition is `D > TV_ex = p_ex + K(1 − e^{−rτ})`; expressed against a fair cum-dividend quote it reduces to the parity test above. |
| **Deep ITM put** | The early-exercise driver is interest on the strike, and it is already embedded in the quote. Exercise when the bid falls to or below intrinsic. A dividend before expiry makes put early exercise *less* attractive, not more: the exercising holder gives up the stock and the dividend with it. |
| **Boundary** | Strict inequality. At exact parity the two routes are worth the same, and the engine does not exercise — but it MUST warn that holding is worth less than either, because at parity there is no time value left to protect. |

## Quantitative basis

**The dominance argument (no model required).** Exercising a call delivers stock worth `S` (cum-dividend, while the underlying has not gone ex) against payment of `K`, so the holder realises exactly `S − K`. Selling realises the bid. Therefore exercise is optimal iff `intrinsic > bid`, for calls and puts alike. Equivalently, a holder who wants the shares can sell the option and buy the stock for `S − bid`, which beats the `K` paid on exercise exactly when `bid > intrinsic`. Interest rates, volatility and time to expiry do not appear because the quote already embeds all three.

**Why the desk rule over-triggers for a holder.** By put-call parity the cum-dividend time value of a call is `TV_cum = TV_ex − PV(D)`, where `TV_ex` is its time value at the ex-dividend underlying price. Substituting into the desk rule `D > TV_cum` gives `D > TV_ex − PV(D)`, i.e. a test satisfied whenever `TV_ex < D + PV(D)` — a strict superset of the exact condition `D > TV_ex`. The excess region is precisely `0 ≤ TV_cum < D`, where selling realises `intrinsic + TV_cum` and exercising realises only `intrinsic`. The same over-flagging is the *correct* direction for a writer screening assignment risk, and `early-exercise-assignment-risk-management` uses it deliberately for that purpose.

| Result | Source |
|---|---|
| Optimal early exercise never occurs for an American call on a non-dividend-paying underlying; exercise can be optimal only immediately before an ex-dividend date. | Merton, "Theory of Rational Option Pricing", *Bell Journal of Economics and Management Science* 4(1), 1973. |
| Exercise immediately before an ex-date is optimal exactly when `D > p_ex + K(1 − e^{−rτ})`, where `p_ex` is the same-strike same-expiry put and `K(1 − e^{−rτ})` is the interest forgone by paying the strike now rather than at expiry. Because `p_ex ≥ 0`, the textbook form `D > K(1 − e^{−rτ})` is necessary but **not** sufficient on its own. | Merton (1973); standard textbook restatement, e.g. <https://riskhub.org/frm-i/course-content/valuation-and-risk-models/the-black-scholes-merton-model/effect-of-dividends-on-early-exercising-656>. Consistent with the same condition as stated in `early-exercise-assignment-risk-management`. |

## Regulatory and market-structure touchpoints

All rows below are **US, exchange-listed options**. Deadlines, allocation methods and ex-date conventions in other jurisdictions are set by the local clearing house and are not covered by these sources.

| Fact used | Source | Applicability |
|---|---|---|
| "Option holders who hold expiring options have until 5:30 p.m. Eastern Time (ET) on the day of expiration to make a final exercise decision"; members may establish earlier cut-off times but may not accept instructions after 5:30 p.m. ET. Under a modified close of trading the deadline becomes 1 hour 30 minutes after the announced close. | FINRA Rule 2360(b)(23)(A); FINRA Information Notice, 3 Feb 2021 — <https://www.finra.org/rules-guidance/notices/information-notice-020321> | **Expiring options only.** This skill cites it to mark its limit: it does not govern early exercise on an ordinary session. |
| For an exercise to take effect on a given day the holder "must notify his brokerage firm before that day's cut-off time for accepting exercise instructions", and "most brokerage firms have an earlier cut-off time" than the exchanges' 4:30 p.m. CT (5:30 p.m. ET) deadline. | OIC, "Exercising Options" — <https://www.optionseducation.org/optionsoverview/exercising-options>; OIC, "Options Exercise" FAQ — <https://www.optionseducation.org/referencelibrary/faq/options-exercise> | Early exercise on an ordinary session. The operative deadline is firm-level and is **not** fixed by rule, so this skill takes no cut-off time as an input and models none. |
| Exercise-by-exception thresholds are "$.01 per contract in-the-money in the customer account; $.01 per contract in-the-money in firm and market maker accounts", measured against the closing price of the underlying. The clearing member may always instruct otherwise, so "exercise under these procedures is not, strictly speaking, 'automatic'". | OIC, "Options Exercise" FAQ (restating OCC Rule 805) — <https://www.optionseducation.org/referencelibrary/faq/options-exercise> | Expiration-day mechanics. Cited to correct the common claim that ITM options are exercised automatically; the expiry-day exposure itself belongs to `options-pin-risk-management-at-expiry`. |
| Since the T+1 transition on 28 May 2024 the ex-dividend date is the record date; under T+2 the ex-date was one business day earlier. | SEC Investor Bulletin, "New T+1 Settlement Cycle" — <https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin>; DTCC T+1 Dividend Processing FAQ — <https://www.dtcc.com/-/media/Files/PDFs/T2/T1-Dividend-Processing-FAQ.pdf> | US markets. Fixes the dividend-capture decision on the **session before the ex-date**: an exercise there settles T+1, landing the holder on the record-date books. |

## Limitations — not modelled here

- **Frictions.** Commissions, exercise fees and the spread paid on any subsequent stock trade are not netted out. They can flip a marginal case in either direction, and a holder who wants the shares anyway may rationally exercise slightly inside the boundary to avoid the equity leg's costs.
- **Post-exercise obligations.** Cash or margin to pay the strike (calls), and the locate, borrow cost and recall risk of the short stock position an exercised put creates when the holder does not own the shares.
- **Borrow and hard-to-borrow status**, which can drive call exercise absent any dividend.
- **Tax.** Exercise, sale and the resulting stock position have different treatments and holding-period consequences in every jurisdiction.
- **The wildcard option** in cash-settled American index options (Fleming & Whaley, 1994), which adds exercise value beyond the tests above.
- **Cut-off times, holiday calendars and session clocks.** Deliberately out of scope; the engine takes no time input at all.
