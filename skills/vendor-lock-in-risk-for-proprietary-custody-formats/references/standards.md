# Institutional Custody Vendor Lock-In & Portability Standards

## 1. Key Export Format Classification Matrix
| Key Format Type | Industry Standard Status | Portability Assessment | Independent Recovery Tool Required? |
| :--- | :--- | :--- | :--- |
| **BIP39_MNEMONIC** | **Open Standard (BIP-39)** | **100% Portable** | Compatible with all open-source hardware/software wallets |
| **SLIP39_SHAMIR** | **Open Standard (SLIP-0039)** | **100% Portable** | Standardized Shamir M-of-N threshold recovery |
| **BIP32_HD_PATH** | **Open Standard (BIP-32/44)** | **100% Portable** | Standard derivation paths (`m/44'/60'/0'/0/0`) |
| **WIF_PRIVATE_KEY** | **Open Standard (WIF)** | **100% Portable** | Direct raw private key import |
| **PROPRIETARY_MPC_SHARE**| **Closed Proprietary** | **Low Portability** | Requires vendor-specific key reconstruction binary |
| **PROPRIETARY_HSM_BLOB** | **Closed Enclave** | **Zero Portability** | Non-exportable HSM blob; locked to vendor vault |

---

## 2. Lock-In Risk Classification Thresholds
| Lock-In Risk Level | Portability Score Range | Open Standard Ratio | Key Criteria & SLA Implications |
| :--- | :--- | :--- | :--- |
| **LOW** | $85.0 - 100.0$ | $\ge 75\%$ | Open standards used; offline open-source recovery tools provided. |
| **MEDIUM** | $60.0 - 84.9$ | $50\% - 74\%$ | Standard formats used; vendor active API required for export. |
| **HIGH** | $35.0 - 59.9$ | $1\% - 49\%$ | Proprietary MPC shares used; vendor binary required for recovery. |
| **CRITICAL** | $< 35.0$ | $0\%$ | Non-exportable proprietary HSM blobs; total vendor lock-in. |

---

## 3. Custody Migration Cost & Friction Formulas
1. **Total Migration Cost ($C_{\text{migration}}$)**:
   $$C_{\text{migration}} = \text{Fee}_{\text{vendor\_export}} + \sum_{i=1}^{W} \sum_{j=1}^{N} \left( \text{TxCount}_{i,j} \times \text{GasFee}_{j} \right)$$
   Where $W$ is total wallet count, $N$ is supported blockchain networks, and $\text{GasFee}_j$ is average network transaction fee.

2. **Total Exit Duration ($T_{\text{migration}}$)**:
   $$T_{\text{migration}} = T_{\text{contractual\_notice}} + T_{\text{on\_chain\_settlement}} + \Delta T_{\text{recovery\_tool}}$$
   Where $\Delta T_{\text{recovery\_tool}} = 14\ \text{days}$ if open-source recovery tools are not provided.