# Institutional UK FCA SM&CR Algorithmic Governance Standards

## 1. Senior Management Functions (SMFs) for Algorithmic Trading
| SMF Role Code | Title | Mandated Scope & Responsibilities | Key Regulatory Artifact |
| :--- | :--- | :--- | :--- |
| **SMF24** | Chief Operations | Algorithmic trading infrastructure, IT systems resilience, RTS 6 compliance | Statement of Responsibilities (SoR) |
| **SMF16** | Compliance Oversight | Regulatory reporting, market abuse surveillance (MAR), compliance monitoring | Compliance Monitoring Plan |
| **SMF4** | Chief Risk Officer | Market risk limits, credit risk caps, pre-trade collar threshold sign-offs | Risk Governance Framework |
| **SMF1** | Chief Executive | Executive oversight of business units operating algorithmic trading | Overall Management Map |

## 2. Certification Function (Fitness & Propriety - F&P) Standards
- **Scope**: Covers all quantitative researchers, software engineers, and algo traders who design, write, test, or deploy algorithmic trading code.
- **Annual F&P Assessment**:
  1. **Honesty, Integrity & Reputation**: Background checks, regulatory reference checks (6-year history).
  2. **Competence & Capability**: Technical proficiency in market microstructure, RTS 6 controls, and risk limits.
  3. **Financial Soundness**: Credit checks, absence of personal bankruptcy or severe debt defaults.
- **Accreditation Mandate**: Every Certified Developer must be accredited by an FCA-approved SMF holder.

## 3. Statutory "Reasonable Steps" Audit Framework (FCA FG18/9)
To defend against personal regulatory liability under the FCA Duty of Responsibility, an SMF holder must demonstrate:
- **Step 1**: Formal review of algorithm strategy specifications, pre-trade risk controls, and market impact models.
- **Step 2**: Verification of RTS 6 stress testing and Kill Switch latency benchmarks (< 1ms execution).
- **Step 3**: Execution of explicit written sign-off (`DeploymentSignOff`) containing comprehensive audit notes.
- **Step 4**: Ongoing post-deployment monitoring and annual Management Responsibilities Map (MRM) audit reviews.

