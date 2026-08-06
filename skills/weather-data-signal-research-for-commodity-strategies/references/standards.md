# Institutional Weather Data Signal Research Standards

## 1. Commodity & Weather Signal Correlation Matrix
| Commodity Futures | Primary Weather Metric | Base Temperature | Key Agricultural / Demand Region | Primary Driver |
| :--- | :--- | :--- | :--- | :--- |
| **Natural Gas (Henry Hub / NG)** | Heating Degree Days (HDD) | $65^\circ\text{F}\ (18.3^\circ\text{C})$ | US East & Midwest (Pop-Weighted) | Winter Heating Demand |
| **Electricity (ERCOT / PJM / CAISO)** | Cooling Degree Days (CDD) | $65^\circ\text{F}\ (18.3^\circ\text{C})$ | Texas, California, Mid-Atlantic | Summer Air Conditioning Peak |
| **Corn Futures (CBOT / C)** | Growing Degree Days (GDD) | $50^\circ\text{F}\ (10.0^\circ\text{C})$ | US Corn Belt (Iowa, Illinois) | Crop Thermal Maturity |
| **Soybean Futures (CBOT / S)** | EDDI / Soil Moisture Deficit | $50^\circ\text{F}\ (10.0^\circ\text{C})$ | US Midwest & Brazil (Mato Grosso) | Pod-Filling Yield Stress |

---

## 2. Quantitative Degree Day & Anomaly Equations

### A. Heating Degree Days (HDD):
$$\text{HDD} = \max\left(0,\; 65^\circ\text{F} - \frac{T_{\text{max}} + T_{\text{min}}}{2}\right)$$

### B. Cooling Degree Days (CDD):
$$\text{CDD} = \max\left(0,\; \frac{T_{\text{max}} + T_{\text{min}}}{2} - 65^\circ\text{F}\right)$$

### C. Population-Weighted Regional Aggregation ($HDD_{\text{region}}$):
$$\text{HDD}_{\text{region}} = \sum_{i=1}^{M} w_i \times \text{HDD}_i \quad \text{where} \ \sum_{i=1}^{M} w_i = 1.0$$

### D. Climate Anomaly Z-Score ($Z_{\text{weather}}$):
$$Z_{\text{weather}} = \frac{X_{\text{forecast}} - \mu_{\text{baseline, 10yr}}}{\sigma_{\text{baseline, 10yr}}}$$

Where $X_{\text{forecast}}$ is the model forecast metric (e.g. 14-day cumulative HDD), $\mu$ is 10-year mean, and $\sigma$ is standard deviation.

---

## 3. Signal Decision Standard Matrix
- **If $Z_{\text{weather}} \ge +1.5$ (Energy)**: Implying severe cold/heat snap $\rightarrow$ **LONG Futures / Call Options**.
- **If $Z_{\text{weather}} \le -1.5$ (Energy)**: Implying mild weather $\rightarrow$ **SHORT Futures / Put Options**.
- **If $Z_{\text{weather}} \ge +1.5$ (Agricultural)**: Implying crop stress/drought $\rightarrow$ **LONG Futures**.