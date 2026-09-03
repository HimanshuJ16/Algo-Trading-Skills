# US Wash Sale Standards — 26 U.S.C. § 1091 and Treas. Reg. § 1.1091-1

Jurisdiction: **United States federal income tax.** Every rule below is US
federal. Nothing here applies to another regime.

---

## 1. Statutory and regulatory matrix

| Rule | Authority | What it actually says | Effect in this engine |
| :--- | :--- | :--- | :--- |
| 61-day window | [26 U.S.C. § 1091(a)](https://www.law.cornell.edu/uscode/text/26/1091) | Loss disallowed where, "within a period beginning 30 days before the date of such sale or disposition and ending 30 days after such date", the taxpayer acquired (or entered into a contract or option to acquire) substantially identical stock or securities. | `abs((replacement_date − loss_date).days) <= 30`. Day 0 counts, so the window is 61 days. |
| Dealer exception | 26 U.S.C. § 1091(a) | The disallowance does not apply where "the taxpayer is a dealer in stock or securities and the loss is sustained in a transaction made in the ordinary course of such business". | Out of scope — do not run this engine for dealer inventory. |
| Fewer shares acquired than sold | 26 U.S.C. § 1091(b) | Which particular shares are nondeductible is "determined under regulations prescribed by the Secretary". | Partial disallowance capped at the replacement quantity. |
| More shares acquired than sold | 26 U.S.C. § 1091(c) | Which particular acquisitions caused the nondeductibility is likewise determined by regulation. | Only the matched slice of the replacement lot takes the basis adjustment. |
| Basis of replacement stock | 26 U.S.C. § 1091(d) | Basis of the replacement is "the basis of the stock or securities so sold or disposed of, increased or decreased … by the difference … between the price at which the property was acquired and the price at which such substantially identical stock or securities were sold". | See § 2B — algebraically identical to *replacement price + loss per share*. |
| Short sales | 26 U.S.C. § 1091(e) | Applies parallel rules to a loss realized on closing a short sale (or on a securities futures contract to sell). | **Not modelled.** An unmatched sell raises `WashSaleError`. |
| Cash-settled contracts | 26 U.S.C. § 1091(f) | The rule applies even where the contract or option "settles in (or could be settled in) cash or property other than such stock or securities". | Not modelled — equity executions only. |
| Order of applying losses | [Treas. Reg. § 1.1091-1(b)](https://www.law.cornell.edu/cfr/text/26/1.1091-1) | Where more than one loss is claimed in the year, apply § 1091 "in the order in which the stock or securities … were disposed of (beginning with the earliest disposition)". | The evaluation pass is chronological; within one disposition, loss slices come out in FIFO lot order. |
| Order of matching replacements | Treas. Reg. § 1.1091-1(c), (d) | Acquired shares are matched "in accordance with the order of their acquisition (beginning with the earliest acquisition)" with an equal number of the shares sold. | Replacement candidates are scanned in acquisition order. |
| One replacement per loss | Treas. Reg. § 1.1091-1(e) | "The acquisition of any share of stock or any security which results in the nondeductibility of a loss under the provisions of this section shall be disregarded in determining the deductibility of any other loss." | Per-acquisition replacement capacity, decremented on match and never reused. |
| Meaning of "acquired" | Treas. Reg. § 1.1091-1(f) | Acquisition by purchase or a fully taxable exchange, and "comprehends cases where the taxpayer has entered into a contract or option within the 61-day period to acquire by purchase or by such an exchange". | Options and contracts are **out of scope**; a caller trading options around a loss must handle this outside the engine. |
| Holding period tacking | [26 U.S.C. § 1223(3)](https://www.law.cornell.edu/uscode/text/26/1223) | The holding period of the shares whose loss was disallowed is added to the holding period of the replacement shares. | **Not modelled** — this engine tracks basis only. Classify STCG/LTCG in `fifo-vs-specific-lot-tax-accounting-methods`. |
| Replacement inside an IRA | [Rev. Rul. 2008-5](https://www.irs.gov/pub/irs-drop/rr-08-05.pdf) | Where the individual sells at a loss and causes their IRA or Roth IRA to buy substantially identical securities in the window, the loss is disallowed under § 1091 **and the individual's basis in the IRA is not increased by reason of § 1091(d)**. | Permanent disallowance, not a deferral. The engine cannot see it — the account must be scoped by the caller. |
| Broker reporting scope | [Instructions for Form 1099-B, Box 1g](https://www.irs.gov/instructions/i1099b), citing Treas. Reg. § 1.6045-1(d)(6)(iii) | "Report wash sale loss amount disallowed. You must report any loss disallowed under section 1091 if both the sale and purchase transactions occur in the same account with respect to covered securities with the same CUSIP number." | The broker's Box 1g is a **subset** of the taxpayer's § 1091 exposure. |
| Assessment period | [26 U.S.C. § 6501(a)](https://www.law.cornell.edu/uscode/text/26/6501), § 6501(e)(1)(A), § 6501(c)(1)–(3) | 3 years after the return is filed; 6 years where more than 25 percent of gross income is omitted; unlimited for a false or fraudulent return, a willful attempt to evade, or a return never filed. | Drives the retention period in `assets/checklist.md`. |

### "Substantially identical" has no bright-line test

§ 1091 says *substantially identical*, not *identical*. Neither the Code nor the
regulations define it; it is applied on the taxpayer's facts and circumstances.
Two consequences matter operationally:

- The **broker's** obligation is narrower — same account, same CUSIP — so a
  1099-B can be correct and still omit a wash sale the taxpayer must report.
- This engine has **no view** on the question. It treats the caller's `symbol` as
  the equivalence class. If two instruments are substantially identical on the
  taxpayer's facts, map them to one symbol before ingesting.

### Same-acquisition shares are not their own replacement

Selling all of a lot, or selling part of a single acquisition and keeping the
rest, is not a wash sale: the shares still held were not bought to replace the
shares sold. The engine implements this two ways — an acquisition is never a
replacement for a loss on its own shares, and a pre-existing acquisition is
replacement stock only to the extent it is **still held after** the loss sale.
Two *separate* purchases are treated differently: the IRS position is that the
second acquisition is replacement stock even if both occurred the same day.

Where the replacement acquisition **precedes** the acquisition of the shares
sold — old shares bought inside the window, newer shares specifically identified
and sold at a loss — the logical argument is that the older shares cannot be
replacements for shares bought after them, but the IRS has applied the rule to
such facts. This engine takes the conservative reading and disallows.

---

## 2. Formulas

### A. Disallowance

For a loss slice of $N_{\text{loss}}$ shares sold at $P_{\text{sell}}$ against an
adjusted basis $P_{\text{basis}}$ (where $P_{\text{sell}} < P_{\text{basis}}$):

$$\text{Loss Per Share} = P_{\text{basis}} - P_{\text{sell}}$$

$$\text{Matched Quantity} = \min\!\left(N_{\text{loss}},\; N_{\text{rep,available}}\right)$$

$$\text{Disallowed Loss} = \text{Matched Quantity} \times \text{Loss Per Share}$$

$N_{\text{rep,available}}$ is the replacement acquisition's quantity **less** any
capacity already consumed by an earlier loss (Treas. Reg. § 1.1091-1(e)), and —
for an acquisition preceding the sale — **capped at the quantity still held after
the sale**.

$P_{\text{basis}}$ is the lot's basis *including* any earlier § 1091(d)
adjustment. Using the unadjusted purchase price here is the defect that makes
chained wash sales come out wrong.

### B. Basis of the replacement shares — § 1091(d)

The statute states the adjustment relative to the basis of the shares sold:

$$P_{\text{adj}} = P_{\text{basis}} + \left(P_{\text{replacement}} - P_{\text{sell}}\right)$$

which rearranges to the form the engine uses:

$$P_{\text{adj}} = P_{\text{replacement}} + \left(P_{\text{basis}} - P_{\text{sell}}\right) = P_{\text{replacement}} + \text{Loss Per Share}$$

Only the **matched quantity** of the replacement acquisition takes this basis.
An over-sized replacement lot is split: the matched shares carry
$P_{\text{adj}}$, the remainder keeps $P_{\text{replacement}}$.

---

## 3. Mapping to Form 1099-B

| Box | Content | Engine field |
| :--- | :--- | :--- |
| 1d — Proceeds | $\sum P_{\text{sell}} \times N_{\text{sell}}$ | `total_proceeds_usd` |
| 1e — Cost or other basis | $\sum P_{\text{basis}} \times N_{\text{sell}}$, using basis **after** any § 1091(d) adjustment already carried into the lot | `total_cost_basis_usd` |
| 1g — Wash sale loss disallowed | $\sum \text{Disallowed Loss}$ | `total_disallowed_wash_loss_usd` |
| — | Net reportable gain/loss $= \text{1d} - \text{1e} + \text{1g}$ | `net_allowed_taxable_pnl_usd` |
| — | Disallowed loss still embedded in the basis of lots open at period end | `deferred_loss_in_open_lots_usd` |

Box 1e must be the **adjusted** basis. Reporting the unadjusted purchase price in
1e while also reporting the disallowance in 1g double counts the deferral.

Individual wash sale adjustments are reported by the taxpayer on **Form 8949**
with adjustment code **W** and the disallowed amount as a positive adjustment.
This engine emits the per-slice `WashSaleMatch` records that support those rows;
it does not render the form.

---

## 4. Sources

- 26 U.S.C. § 1091 — https://www.law.cornell.edu/uscode/text/26/1091
- 26 U.S.C. § 1223 — https://www.law.cornell.edu/uscode/text/26/1223
- 26 U.S.C. § 6501 — https://www.law.cornell.edu/uscode/text/26/6501
- Treas. Reg. § 1.1091-1 — https://www.law.cornell.edu/cfr/text/26/1.1091-1
- Rev. Rul. 2008-5, 2008-3 I.R.B. 271 — https://www.irs.gov/pub/irs-drop/rr-08-05.pdf
- IRS Publication 550, "Wash Sales" — https://www.irs.gov/publications/p550
- Instructions for Forms 1099-B (Box 1g) — https://www.irs.gov/instructions/i1099b
- Instructions for Form 8949 (column (f) code W) — https://www.irs.gov/instructions/i8949
