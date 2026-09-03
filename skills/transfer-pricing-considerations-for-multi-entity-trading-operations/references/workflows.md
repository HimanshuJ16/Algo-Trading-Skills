# Transfer Pricing Workflows

## Workflow 1: Intercompany service fee settlement

The Berry ratio step is conditional. It runs only when the provider's COGS and operating
expenses are supplied; otherwise the settlement carries `berry_ratio=None` and a note saying
why, because a ratio inferred from the cost base is the cost-plus markup factor relabelled.

```mermaid
sequenceDiagram
    autonumber
    participant Provider as Service provider (UK quant lab)
    participant Engine as TransferPricingEngine
    participant Accounting as Intercompany ledger / ERP
    participant Recipient as Recipient (US IP holdco)

    Provider->>Engine: Cost base $500,000 + benchmarked markup 10%
    Engine->>Engine: Characterise service (core business -> no simplified approach)
    Engine->>Engine: calculate_cost_plus_fee(500000, 10.0) -> (550000.00, 50000.00)
    alt COGS and opex supplied
        Engine->>Engine: Berry = (fee - COGS) / opex
    else not supplied
        Engine->>Engine: berry_ratio = None + note (TPG 2.106/2.107)
    end

    Engine-->>Accounting: Intercompany invoice $550,000.00
    Accounting->>Recipient: Dr intercompany service expense $550,000.00
    Accounting->>Provider: Cr intercompany service revenue $550,000.00

    Engine->>Engine: Emit settlement warnings + Local File audit trail
```

**Berry ratio worked example.** A Singapore execution entity bills $1,200,000, buys
$700,000 of exchange capacity (COGS) and runs $400,000 of its own operating expenses.

```
Gross profit = 1,200,000 - 700,000 = 500,000
Berry ratio  = 500,000 / 400,000    = 1.25
```

Dividing the whole fee by the whole cost base gives 1,200,000 / 1,100,000 = 1.09, which is a
different and wrong number. The two coincide only when COGS is genuinely zero.

---

## Workflow 2: DEMPE-keyed profit split — contribution vs residual

Both branches use the same allocation key. They are different analyses under OECD TPG 2022
and produce different allocations, so the Local File must say which one was run.

```mermaid
flowchart TD
    A["Combined trading PnL: $10,000,000"] --> B["Functional analysis:<br/>who performs AND controls each DEMPE function"]

    B --> C["US IP entity<br/>D=1.0 E=0.9 M=0.8 P=1.0 X=0.7<br/>equal-weighted key = 0.88"]
    B --> D["UK manager entity<br/>D=0.2 E=0.3 M=0.4 P=0.1 X=0.5<br/>equal-weighted key = 0.30"]

    C & D --> E{"Can any contribution be<br/>benchmarked one-sided?"}

    E -->|"No — routine_returns_usd omitted"| F["CONTRIBUTION ANALYSIS<br/>TPG para. 2.150<br/>split the whole $10,000,000"]
    F --> G["US 0.88/1.18 = 74.576% -> $7,457,627.12"]
    F --> H["UK 0.30/1.18 = 25.424% -> $2,542,372.88"]

    E -->|"Yes — routine returns priced first"| I["RESIDUAL ANALYSIS<br/>TPG para. 2.152<br/>routine US $500,000 + UK $1,500,000<br/>residual $8,000,000"]
    I --> J["US $500,000 + 74.576% x $8,000,000 = $6,466,101.69"]
    I --> K["UK $1,500,000 + 25.424% x $8,000,000 = $3,533,898.31"]

    G & H & J & K --> L["Archive key derivation, functional analysis,<br/>agreements, Master File / Local File"]
```

**The equal weighting is an assumption, not a rule.** The OECD publishes no numeric DEMPE
score. Pass `dimension_weights` to weight the five functions differently and record the
evidence for whichever weighting you use (TPG paras. 2.166, 2.170, 2.171).

```python
split = engine.calculate_profit_split(
    10_000_000.0,
    [dempe_us, dempe_uk],
    routine_returns_usd={"ENT-US": 500_000.0, "ENT-UK": 1_500_000.0},
    dimension_weights={
        "development": 0.35, "enhancement": 0.25, "maintenance": 0.10,
        "protection": 0.10, "exploitation": 0.20,
    },
)
assert split.split_basis == "RESIDUAL_ANALYSIS"
```

---

## Workflow 3: Choosing a markup before choosing a number

```mermaid
flowchart TD
    A["Intercompany service identified"] --> B{"Is it the group's<br/>core business, R&D,<br/>engineering/scientific,<br/>or a financial transaction?"}

    B -->|Yes| C["No simplified approach available<br/>TPG 7.47 / Treas. Reg. 1.482-9(b)(4)"]
    C --> D{"Is there a third-party<br/>price for the same service?"}
    D -->|Yes| E["CUP — register benchmark_cup_rate_usd"]
    D -->|No| F["Benchmarking study -> markup_pct<br/>Cost Plus (gross) or TNMM (net PLI)"]

    B -->|No| G{"Which regime applies<br/>to the paying entity?"}
    G -->|"OECD simplified<br/>(para. 7.61)"| H["markup_pct = 5.0<br/>exclude pass-through costs"]
    G -->|"US SCM<br/>(1.482-9(b))"| I["markup_pct = 0.0<br/>covered services only"]
    G -->|"IRAS Annex C"| J["markup_pct = 5.0<br/>related parties only, all costs in base"]

    E & F & H & I & J --> K["process_intercompany_transaction()<br/>then read settlement.warnings"]
```

A `markup_pct` of `0.0` is a deliberate, defensible value under the US Services Cost Method
and IRAS strict pass-through cost pooling. It is not a missing input, and the engine accepts
it. A markup below -100% is rejected: it would invert the fee.
