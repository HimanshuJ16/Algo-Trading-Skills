# Broker & Framework Coverage — producer-consumer-tick-pipeline

The callback thread column decides whether the handoff onto the consumer queue has to be
thread-safe. Verify it against the SDK version you actually pin — it is an implementation
detail of the client library, not a guaranteed part of the broker's API contract.

| Broker / Streaming Protocol | Callback thread | Relevance to this skill |
|---|---|---|
| Fyers WebSocket API v3 (`fyers-apiv3`) | Background socket thread — `onmessage` fires off the event loop | High-frequency streaming tick ingestion; needs a thread-safe handoff. |
| Zerodha Kite Connect Ticker (`pykiteconnect`) | Twisted reactor thread; a background daemon thread under `connect(threaded=True)` | Binary WebSocket tick stream parsing and queueing; needs a thread-safe handoff. |
| IBKR TWS / Gateway API | SDK-dependent — `ibapi` dispatches from its reader thread; wrappers differ | Real-time market depth and quote streaming; confirm the wrapper's threading model. |
| Alpaca Real-Time Market Data Stream (`alpaca-py`) | Asyncio-native — the handler runs on the event loop | Stock and crypto WebSocket ingestion; a direct `put_nowait` is correct here. |

## Regulatory & Operational Notes

This is an engineering concern rather than a rule-driven one: no regulator prescribes a queue
topology. It does sit underneath capacity and resilience obligations where those apply.

- **EU/EEA — MiFID II (Directive 2014/65/EU), Article 17(1):** an investment firm engaging in
  algorithmic trading "shall have in place effective systems and risk controls suitable to the
  business it operates to ensure that its trading systems are resilient and have sufficient
  capacity", together with business continuity arrangements and systems that are "fully tested
  and properly monitored". Capacity and monitoring of the ingestion path are in scope; the
  design of that path is not prescribed. Applies to authorised investment firms in the EU/EEA
  (the UK has an onshored equivalent) — it does not apply to an individual trading own capital
  outside that perimeter.
- **RTS 6 (Commission Delegated Regulation (EU) 2017/589)** elaborates Article 17 with testing,
  capacity and real-time monitoring requirements for the same population of firms. Check the
  current article text on EUR-Lex before relying on a specific article number; the consolidated
  text is the authority, not this table.

Operationally, the pipeline also intersects with tick timestamp preservation (arrival timestamps
must be captured at ingestion, not at processing, or queue wait time silently contaminates any
latency measurement) and with exchange connectivity SLAs, where a stalled read loop shows up as a
broker-side disconnect rather than as a local error.

## Sources

- Directive 2014/65/EU (MiFID II), Article 17 — Algorithmic trading.
  https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32014L0065
- Commission Delegated Regulation (EU) 2017/589 (RTS 6).
  https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng
- Python documentation, `asyncio` queues — "This class is not thread safe."
  https://docs.python.org/3/library/asyncio-queue.html
- `pykiteconnect` `kiteconnect/ticker.py` (Twisted reactor; `threaded=True` daemon thread).
  https://github.com/zerodha/pykiteconnect/blob/master/kiteconnect/ticker.py
- Fyers API v3 Python WebSocket samples (`FyersDataSocket` background thread).
  https://github.com/FyersDev/fyers-api-sample-code/tree/sample_v3/v3/python/websocket/data_socket
