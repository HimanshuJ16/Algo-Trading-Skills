/**
 * Per-domain presentation metadata. Sixteen hand-written entries — the only place in the
 * site where copy is authored by hand rather than generated. Keys must match the
 * `metadata.subdomain` values enumerated in docs/ROADMAP_500.md; lib/content.ts throws at
 * build time if index.json contains a subdomain that is missing here.
 */
export type DomainMeta = {
  slug: string;
  label: string;
  short: string;
  blurb: string;
  hue: number;
};

export const DOMAINS: DomainMeta[] = [
  {
    slug: "broker-integration",
    label: "Broker Integration",
    short: "Auth, tokens, idempotent orders",
    blurb:
      "Headless auth over REST and Selenium, token lifecycle via live probing, order idempotency, per-broker rate limiting, borrow cost modeling, cost budgeting.",
    hue: 172,
  },
  {
    slug: "real-time-architecture",
    label: "Real-Time Architecture",
    short: "Tick pipelines and backpressure",
    blurb:
      "Producer-consumer tick pipelines, burst-safe buffering, explicit backpressure policy, WebSocket subscription reconciliation after a reconnect.",
    hue: 199,
  },
  {
    slug: "backtesting-methodology",
    label: "Backtesting Methodology",
    short: "Lookahead, walk-forward, realism",
    blurb:
      "Lookahead bias elimination, walk-forward validation, realistic slippage/fee/latency simulation, synthetic data generation, standardized tearsheets.",
    hue: 265,
  },
  {
    slug: "financial-ml",
    label: "Financial ML",
    short: "Leakage-free features and models",
    blurb:
      "Leakage-free feature engineering, offline-train/online-infer deployment, triple barrier labeling, sample weighting, model staleness detection.",
    hue: 288,
  },
  {
    slug: "risk-management",
    label: "Risk Management",
    short: "Kill switches and exposure limits",
    blurb:
      "Kill switches and drawdown circuit breakers, correlation-aware exposure limits, Kupiec test VaR backtesting, tail risk hedging, escalation matrices.",
    hue: 4,
  },
  {
    slug: "deployment-ops",
    label: "Deployment & Ops",
    short: "Supervision, promotion, chaos",
    blurb:
      "systemd process supervision, paper-to-live promotion checklists, infrastructure as code for trading hosts, canary releases, chaos engineering, secrets vaults.",
    hue: 28,
  },
  {
    slug: "global-market-integration",
    label: "Global Market Integration",
    short: "Exchanges and venues worldwide",
    blurb:
      "Crypto exchange APIs (Binance, Coinbase, Kraken, Deribit, Bybit, OKX), FX via OANDA and MT5, CME Globex, Eurex, HKEX, SGX, ASX, JPX, CBOE, LSE, Xetra.",
    hue: 152,
  },
  {
    slug: "regulatory-compliance-global",
    label: "Regulatory Compliance",
    short: "SEC, FINRA, MiFID II, FCA, SEBI",
    blurb:
      "US SEC Rule 15c3-5, PDT and FINRA rules, EU MiFID II / RTS 6 / MAR, UK FCA, ASIC, MAS, India SEBI, Canada IIROC, Hong Kong SFC, Japan FSA.",
    hue: 218,
  },
  {
    slug: "multi-asset-derivatives",
    label: "Multi-Asset Derivatives",
    short: "Options, futures, swaps",
    blurb:
      "SPAN margin calculation, futures contract roll automation, real-time Greeks aggregation, perpetual funding rates, variance swaps, CDS, quanto options.",
    hue: 322,
  },
  {
    slug: "execution-algorithms",
    label: "Execution Algorithms",
    short: "TWAP, VWAP, POV, routing",
    blurb:
      "TWAP and VWAP order slicing, POV execution, implementation shortfall minimization, iceberg detection, smart order routing, dark pools, auctions.",
    hue: 46,
  },
  {
    slug: "data-management-global",
    label: "Data Management",
    short: "Calendars, timezones, symbology",
    blurb:
      "Global exchange holiday calendars, DST transition handling, multi-timezone session scheduling, multi-currency P&L, ISIN/CUSIP/SEDOL cross-referencing.",
    hue: 186,
  },
  {
    slug: "crypto-custody-security",
    label: "Crypto Custody & Security",
    short: "Keys, wallets, withdrawals",
    blurb:
      "Wallet key custody, hot-cold split, withdrawal whitelisting, multi-signature approval, HSM integration, Shamir secret sharing, MPC custody.",
    hue: 88,
  },
  {
    slug: "portfolio-multi-strategy",
    label: "Portfolio & Multi-Strategy",
    short: "Allocation across strategies",
    blurb:
      "Cross-strategy correlation monitoring, performance-based capital reallocation, retirement criteria, risk parity allocation, meta-strategy signal arbitration.",
    hue: 246,
  },
  {
    slug: "market-microstructure-latency",
    label: "Microstructure & Latency",
    short: "Colocation, clocks, tick-to-trade",
    blurb:
      "Colocation latency budgets, PTP clock synchronization, tick-to-trade measurement, order book signals, adverse selection measurement, FPGA and microwave evaluation.",
    hue: 16,
  },
  {
    slug: "quant-research-alt-data",
    label: "Quant Research & Alt Data",
    short: "Signals from non-market data",
    blurb:
      "Satellite imagery signals, credit card transaction data, web-scraped sentiment, supply chain networks, Google Trends, social bot filtering, transcript NLP.",
    hue: 128,
  },
  {
    slug: "tax-accounting-reporting-global",
    label: "Tax, Accounting & Reporting",
    short: "Lots, elections, reconciliation",
    blurb:
      "US wash sale tracking, FIFO versus specific-lot accounting, Section 475 mark-to-market election, crypto tax lots, 1099-B reconciliation, Section 1256 futures.",
    hue: 62,
  },
];

export const DOMAIN_BY_SLUG = new Map(DOMAINS.map((d) => [d.slug, d]));

/**
 * The one tint formula, shared by the `.tint` class in globals.css and by anything that
 * draws with JavaScript (the canvas graph, the spectrum). Dark lifts lightness so a hue
 * reads on near-black; light drops it so the same hue holds AA contrast on paper.
 */
export function tintFor(hue: number, dark: boolean, alpha = 1): string {
  return dark
    ? `hsl(${hue} 72% 66% / ${alpha})`
    : `hsl(${hue} 62% 40% / ${alpha})`;
}

export function domainMeta(slug: string): DomainMeta {
  const meta = DOMAIN_BY_SLUG.get(slug);
  if (!meta) throw new Error(`Unknown subdomain "${slug}" — add it to lib/domains.ts`);
  return meta;
}
