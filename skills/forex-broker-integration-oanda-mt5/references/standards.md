# Broker & Framework Coverage — forex-broker-integration-oanda-mt5

| Broker / Framework | Relevance to this skill |
|---|---|
| OANDA v20 REST & Streaming API | Practice vs. Live endpoint isolation, `pipLocation` instrument metadata, pricing streams, lot-to-unit conversions. |
| MetaTrader 5 (MT5) Python API | Terminal IPC integration, `symbol_info().digits` precision metadata, bridge liveness monitoring, Expert Advisor MQL5 interaction. |

## Category

`global-market-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Broker & Market Conventions

| Claim used by this skill | Source |
|---|---|
| OANDA `pipLocation`: "The decimal position of the pip in this Instrument's price can be found at 10 ^ pipLocation." `displayPrecision` gives price display decimals. | [OANDA v20 Instrument definition](https://developer.oanda.com/rest-live-v20/primitives-df/) |
| OANDA practice and live are separate host families: REST `api-fxpractice.oanda.com` / `api-fxtrade.oanda.com`, streaming `stream-fxpractice.oanda.com` / `stream-fxtrade.oanda.com`. | [OANDA v20 development guide](https://developer.oanda.com/rest-live-v20/development-guide/) |
| The official `MetaTrader5` Python package publishes `win_amd64` wheels only, so the bridge host must be Windows x86-64 (or a Wine / broker-gateway equivalent). | [MetaTrader5 on PyPI](https://pypi.org/project/MetaTrader5/) |
| `terminal_info()` exposes `connected` and `trade_allowed`; it returns `None` when no terminal is attached. | [MQL5 Python integration docs](https://www.mql5.com/en/docs/python_metatrader5) |
| Triple swap follows the value date, not the weekday: spot FX settles T+2, so the Friday→Monday value-date roll lands on the Wednesday rollover. USD/CAD, USD/TRY and USD/RUB settle T+1, moving their triple-swap rollover to Thursday. | [Dukascopy — Rollovers](https://www.dukascopy.com/wiki/en/forex-cfds/account-management/rollovers/) |

Lot conventions (standard 100,000 / mini 10,000 / micro 1,000 / nano 100 units) and the
weekly 17:00 America/New_York close are widespread retail conventions rather than
standards — confirm both against the specific broker's contract specifications.

## Regulatory & Operational Notes

Leverage and order-handling limits are jurisdiction-specific and change. Verify the current
position with the relevant regulator before relying on any figure below.

- **United States (CFTC / NFA), retail forex.** [17 CFR § 5.9](https://www.law.cornell.edu/cfr/text/17/5.9)
  requires FCMs and retail foreign exchange dealers to collect security deposits of **2% of
  notional for major currency pairs and 5% for all other pairs** — equivalent to roughly 50:1
  and 20:1 maximum leverage respectively. The rule does not itself define "major currency": it
  delegates that designation to the registered futures association (NFA), which reviews the list
  annually, and permits the association to require greater amounts than the CFTC minimum. A flat
  "50:1 US limit" is therefore incomplete — it does not hold for minors and exotics.
- **United States (NFA), offsetting positions.** NFA Compliance Rule 2-43(b) requires forex
  dealer members to offset positions on a first-in-first-out basis and bars customers from
  holding opposing ("hedged") positions in the same account, for positions established after
  15 May 2009; a customer may direct the FDM to offset same-size transactions ahead of older
  transactions of a different size. This constrains any strategy design that assumes independent
  long and short legs in one account, and any position-tracking or tax-lot logic that assumes the
  broker will close the lot the strategy nominates. Read the current text in the
  [NFA rulebook](https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=RULE+2-43&Section=4)
  before relying on this summary — it is a paraphrase, not the rule.
- **European Union (ESMA), CFDs offered to retail clients.** ESMA's product intervention
  measures set leverage limits of **30:1 for major currency pairs and 20:1 for non-major pairs,
  gold and major indices**, alongside lower caps for other asset classes. These were adopted
  under [Article 40 of MiFIR](https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors)
  as **temporary** measures renewable only in three-month increments, and were last renewed by
  ESMA with effect from 1 May 2019. Because ESMA's own measures were time-limited rather than
  permanent EU-wide law, the limit binding on a given client today is whichever measure the
  relevant national competent authority has in force — verify with that authority rather than
  citing the ESMA measures as current. The limits apply to retail clients; elected professional
  clients are treated differently.
- **Scope.** These are retail-client protections. Requirements for professional clients,
  eligible counterparties, and non-EU/non-US jurisdictions (FCA, ASIC, MAS, FSA Japan) differ
  and are not covered here.
