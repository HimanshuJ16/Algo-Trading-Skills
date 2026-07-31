---
name: model-inference-latency-budget-for-live-trading
description: >-
  Quantitative model inference latency budget and percentile SLA profiling engine, managing real-time ONNX/TensorRT execution budgets and automated model fallbacks.
domain: Market Microstructure Latency
subdomain: ML Inference Budgeting & Real-Time SLA Governance
tags: ["model-inference", "latency-budget", "onnx", "tensorrt", "p99-latency", "sla-governance", "quantized-fallback", "tick-to-trade"]
brokers_frameworks: ["ONNX Runtime", "TensorRT", "CUDA Graphs", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying machine learning alpha models, deep neural networks (LSTMs, Transformers), or XGBoost models into real-time live trading execution pipelines. Every tick-to-trade path has a strict total latency budget ($\tau_{\text{total}} = \tau_{\text{market\_data}} + \tau_{\text{features}} + \tau_{\text{inference}} + \tau_{\text{risk}} + \tau_{\text{wire}}$). Un-profiled ML inference causes $P_{99}$ latency spikes, resulting in adverse selection and exchange slippage. This module profiles sliding window inference samples, computes $P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$ percentiles and jitter ($\sigma$), enforces SLA limits, and triggers automated model fallbacks (INT8 ONNX quantization or linear heuristics).

## Prerequisites

- Inference budget configuration (`model_id`, `max_inference_budget_ms`: e.g. 1.0, `warning_threshold_ms`: e.g. 0.8, `fallback_action`: `'QUANTIZED_ONNX_FALLBACK'`, `'LINEAR_HEURISTIC_FALLBACK'`, `'SKIP_SIGNAL'`).
- Ingested inference sample latencies in milliseconds (`sample_latencies_ms`: list of float).

## Workflow

1. **Inference Latency Percentile Profiling**:
   - Sort sample latencies and compute $P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$ percentiles.
   - Compute jitter (standard deviation $\sigma_{\text{latency}}$).
2. **SLA Compliance Audit**:
   - If $P_{99} \le \tau_{\text{warning}} \implies$ Status `INFERENCE_LATENCY_NORMAL`.
   - If $\tau_{\text{warning}} < P_{99} \le \tau_{\text{max}} \implies$ Status `INFERENCE_LATENCY_WARNING_NEAR_LIMIT`.
   - If $P_{99} > \tau_{\text{max}} \implies$ Status `INFERENCE_LATENCY_SLA_BREACH`.
3. **Automated Fallback Triggering**:
   - If SLA breach occurs $\implies$ Recommend configured fallback action (e.g. switch to INT8 ONNX or linear heuristic model).
4. **Audit Report Generation**: Output structured `InferenceBudgetReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Average Latency ($P_{50}$)**: Evaluating models using median latency, hiding massive 10ms $P_{99}$ spikes during volatile market open events.
- **Un-Warmed GPU Kernels**: Failing to run pre-market dummy inference warm-ups, causing initial CUDA JIT compilation latency spikes.
- **Dynamic Heap Memory Allocation During Inference**: Allocating new memory buffers per inference step, causing non-deterministic Garbage Collection pauses.

## Verification

- Instantiate `ModelInferenceLatencyBudgeterEngine`. Profile 100 inference samples ($P_{50} = 0.4\text{ ms}, P_{99} = 0.75\text{ ms}$, max budget $= 1.0\text{ ms}$) $\implies$ verify `INFERENCE_LATENCY_NORMAL`. Inject a $1.5\text{ ms}$ spike pushing $P_{99} = 1.2\text{ ms} > 1.0\text{ ms}$ budget $\implies$ verify `INFERENCE_LATENCY_SLA_BREACH` and recommended fallback `QUANTIZED_ONNX_FALLBACK`.
- Run `python scripts/test_inference_latency_budgeter.py`.

## Related Skills

- `latency-monitoring-percentile-based-slas`
- `strategy-latency-budget-decomposition`
---
