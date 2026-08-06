# Institutional Financial ML Transfer Learning Workflows

## Workflow 1: Source Pre-Training & Target Fine-Tuning Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Data as Feature Pipeline
    participant Engine as Transfer Learning Engine
    participant Source as Source Model (Liquid Asset)
    participant Target as Target Model (Cold-Start Asset)
    participant Evaluator as OOS Performance Evaluator

    Data->>Engine: Load Source Dataset (SPY - 100k samples) & Target Dataset (NEW_ETF - 50 samples)
    Engine->>Engine: calculate_correlation(Source_Y, Target_Y)
    Engine->>Engine: calculate_covariate_shift(Source_X, Target_X)

    alt Covariate Shift > 2.0 OR Correlation < 0.60
        Engine-->>Evaluator: REJECT TRANSFER (Risk of Negative Transfer)
    else Metrics Validated
        Engine->>Source: fit_source_model(Source Dataset)
        Source-->>Engine: Base Parameters (weights_src, feature_means, feature_stds)
        
        Engine->>Target: fine_tune_target_model(params_src, Target Dataset, L2_lambda)
        Target-->>Engine: Adapted Parameters (weights_tgt)
        
        Engine->>Evaluator: evaluate_transfer_performance(Source, Target)
        Evaluator-->>Engine: TransferEvaluation (R² Direct vs R² Transfer)
    end
```

---

## Workflow 2: Negative Transfer Prevention & Model Deployment Decision Tree
```mermaid
flowchart TD
    A[Cold-Start Target Asset Identified] --> B[Identify Liquid Correlated Source Asset]
    B --> C[Compute Pairwise Return Correlation r]
    
    C -- r < 0.60 --> D[REJECT: Insufficient Source Correlation]
    C -- r ≥ 0.60 --> E[Compute Covariate Shift Domain Distance]
    
    E -- Shift > 2.0 --> F[REJECT: Extreme Domain Shift]
    E -- Shift ≤ 2.0 --> G[Pre-train Source Model on Liquid Data]
    
    G --> H[Fine-tune Target Model using L2 Regularization λ]
    H --> I[Evaluate Out-of-Sample R² Gain]
    
    I -- Transfer R² ≤ Direct Target R² --> J[REJECT: Negative Transfer Detected]
    I -- Transfer R² > Direct Target R² --> K[APPROVE: Deploy Transferred Model to Live Trading]
```