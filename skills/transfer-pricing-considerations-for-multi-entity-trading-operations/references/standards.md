# Institutional OECD Transfer Pricing & Multi-Entity Standards

## 1. OECD Transfer Pricing Methodologies for Trading Operations
| Methodology | Full Name | Application in Trading Operations | Typical Benchmark Range |
| :--- | :--- | :--- | :--- |
| **Cost Plus Method (CPM)** | Cost Plus | IT Infrastructure, Middle/Back-Office Support, Compliance | 5% – 15% Cost Markup |
| **CUP Method** | Comparable Uncontrolled Price | Market Data Feeds, Order Execution Routing, Broker Fees | External Exchange/Broker Schedule |
| **TNMM** | Transactional Net Margin Method | Asset Management Operations, Quant Research Labs | Berry Ratio: 1.05 – 1.25 |
| **Profit Split Method (PSM)** | Residual Profit Split | Global Trading PnL split based on DEMPE IP ownership | Relative DEMPE Contribution % |

## 2. Mathematical Transfer Pricing Formulas
1. **Cost Plus Fee ($)**:
   $$\text{Arm's Length Fee} = \text{Base Operating Cost} \times \left(1 + \frac{\text{Markup \%}}{100}\right)$$

2. **CUP Fee ($)**:
   $$\text{Arm's Length Fee} = \text{Execution Volume (Orders/Shares)} \times \text{Benchmark Unit Price}$$

3. **Berry Ratio**:
   $$\text{Berry Ratio} = \frac{\text{Gross Profit}}{\text{Operating Expenses}}$$
   *(Target Range: $1.05 \le \text{Berry Ratio} \le 1.25$)*

4. **OECD DEMPE Composite Score**:
   $$\text{DEMPE Score} = \frac{W_{\text{Dev}} + W_{\text{Enh}} + W_{\text{Maint}} + W_{\text{Prot}} + W_{\text{Expl}}}{5}$$
   $$\text{Profit Share}_i = \frac{\text{DEMPE Score}_i}{\sum_{j} \text{DEMPE Score}_j} \times \text{Global Trading PnL}$$

## 3. OECD BEPS Compliance Documentation Requirements
- **Master File**: High-level overview of global trading strategy, IP ownership (quant algos), and intercompany financing agreements.
- **Local File**: Transactional transfer pricing documentation detailing specific intercompany service charges and benchmarking studies.
- **Country-by-Country (CbC) Report**: Annual breakdown of revenue, tax paid, profit before tax, and employee headcounts per tax jurisdiction.