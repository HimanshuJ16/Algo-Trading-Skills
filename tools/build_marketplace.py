#!/usr/bin/env python3
"""
Generates .claude-plugin/marketplace.json: one Claude Code plugin per domain plus an
all-skills plugin, all sharing this repository as their source.

    python tools/build_marketplace.py            # rewrite marketplace.json
    python tools/build_marketplace.py --check    # exit 1 if it is stale (CI)

Why per-domain plugins: Claude Code loads the name and description of every skill in
an installed plugin into the model's context at the start of every session, under a
character budget. One plugin carrying all the skills in this library is roughly 35k
tokens of descriptions per session and gets truncated. A per-domain plugin is a few
thousand tokens. Entries use `"source": "./"` with an explicit `skills` list, which
(per the Claude Code plugin docs) loads only the listed directories.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_index import build_index  # noqa: E402
from validate_skills import SUBDOMAINS  # noqa: E402

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(ROOT_DIR, ".claude-plugin", "plugin.json")
MARKET_PATH = os.path.join(ROOT_DIR, ".claude-plugin", "marketplace.json")

OWNER = {"name": "HimanshuJ16", "email": "himanshujangir16@gmail.com", "url": "https://github.com/HimanshuJ16"}
HOMEPAGE = "https://github.com/HimanshuJ16/Algo-Trading-Skills"

BLURBS = {
    "broker-integration": "headless broker auth, token lifecycle probing, order idempotency, rate limiting, per-broker API quirks (Fyers, Zerodha, Upstox, Alpaca, IBKR, Schwab, Questrade, Saxo, Tastytrade)",
    "real-time-architecture": "producer-consumer tick pipelines, burst buffering, backpressure policy, WebSocket reconnection, Kafka and Redis fan-out, sequence-gap detection",
    "backtesting-methodology": "lookahead bias elimination, walk-forward validation, realistic slippage and latency simulation, survivorship-free universes, tearsheets, determinism",
    "financial-ml": "leakage-free features, triple-barrier labels, sample weighting, model staleness and drift, offline-train online-infer deployment, explainability",
    "risk-management": "kill switches and drawdown circuit breakers, exposure and concentration limits, VaR backtesting, stress tests, risk-control governance and audit",
    "deployment-ops": "systemd supervision, paper-to-live promotion, canary and blue-green releases, secrets rotation, chaos engineering, on-call and incident runbooks",
    "global-market-integration": "exchange and venue APIs worldwide: CME, Eurex, HKEX, SGX, ASX, JPX, LSE, Xetra, B3, crypto venues (Binance, Coinbase, Kraken, Deribit, Bybit, OKX), FX (OANDA, MT5)",
    "regulatory-compliance-global": "SEC Rule 15c3-5, Reg NMS, Reg SHO, FINRA, MiFID II RTS 6, MAR, UK FCA, ASIC, MAS, SEBI, IIROC, SFC, JFSA, sanctions screening",
    "multi-asset-derivatives": "SPAN margin, futures rolls, real-time Greeks, perpetual funding, options expiry and assignment, variance swaps, CDS, quanto and exotic structures",
    "execution-algorithms": "TWAP/VWAP/POV slicing, implementation shortfall, smart order routing, dark pools, auctions, iceberg orders, post-only and fee-tier optimisation, TCA",
    "data-management-global": "holiday calendars, DST and time zones, clock sync and timestamps, symbol cross-reference, corporate actions, data quality, lineage, retention",
    "crypto-custody-security": "hot/cold wallet split, withdrawal whitelists, multi-sig and MPC, HSM, Shamir backup, key rotation, air-gapped signing, custody due diligence",
    "portfolio-multi-strategy": "cross-strategy correlation, capital allocation and risk parity, strategy onboarding and retirement, performance attribution, reporting",
    "market-microstructure-latency": "colocation and latency budgets, PTP clocks, tick-to-trade measurement, order book signals, adverse selection, FPGA and microwave links",
    "quant-research-alt-data": "satellite imagery, card transactions, web-scraped sentiment, supply chains, search trends, job postings, patents, transcript NLP, vendor due diligence",
    "tax-accounting-reporting-global": "wash sales, tax lots, Section 475 and 1256, crypto lot tracking, 1099-B reconciliation, VAT/GST, cross-border tax residency",
}


def build_marketplace():
    with open(PLUGIN_PATH, encoding="utf-8") as fh:
        plugin = json.load(fh)
    index = build_index()
    by_domain = {d: [] for d in SUBDOMAINS}
    for s in index["skills"]:
        by_domain[s["subdomain"]].append(s["name"])

    common = {
        "version": plugin["version"],
        "author": {"name": OWNER["name"], "url": OWNER["url"]},
        "license": plugin.get("license", "Apache-2.0"),
        "category": "finance",
        "homepage": HOMEPAGE,
        "repository": HOMEPAGE,
    }
    plugins = []
    for domain in SUBDOMAINS:
        names = sorted(by_domain[domain])
        plugins.append({
            "name": f"algo-trading-{domain}",
            "source": "./",
            "description": f"{len(names)} algorithmic-trading skills for {domain.replace('-', ' ')}: {BLURBS[domain]}.",
            **common,
            "keywords": plugin.get("keywords", []) + [domain],
            "skills": [f"./skills/{n}" for n in names],
        })
    plugins.append({
        "name": "algo-trading-skills-all",
        "source": "./",
        "description": (f"All {index['total_skills']} skills in one plugin. Loads every skill description "
                        f"into context on every session (tens of thousands of tokens); prefer the "
                        f"per-domain algo-trading-<domain> plugins unless you have the context budget."),
        **common,
        "keywords": plugin.get("keywords", []),
        "skills": ["./skills/"],
    })
    return {
        "name": "himanshuj16-algo-trading-skills",
        "owner": OWNER,
        "metadata": {
            "description": f"{index['total_skills']} agentskills.io skills for algorithmic trading, packaged as one Claude Code plugin per engineering domain.",
            "version": plugin["version"],
        },
        "plugins": plugins,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = json.dumps(build_marketplace(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        current = open(MARKET_PATH, encoding="utf-8").read() if os.path.isfile(MARKET_PATH) else ""
        if current != text:
            print("marketplace.json is out of date -- run python tools/build_marketplace.py")
            return 1
        print("marketplace.json is up to date.")
        return 0
    with open(MARKET_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"Wrote {os.path.relpath(MARKET_PATH, ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
