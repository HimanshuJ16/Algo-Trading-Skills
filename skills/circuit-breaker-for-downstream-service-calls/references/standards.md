# Standards for Downstream-Service Circuit Breakers

## Engineering standards

| Property | Standard | Why |
|---|---|---|
| Fail-fast cost | When the circuit is OPEN, **no network I/O may be attempted**. The refusal must cost orders of magnitude less than the call it replaces. Measured for `scripts/circuit_breaker.py` on CPython 3.11 (Windows, `timeit` median of 5 × 20 000 iterations): ~3 µs per refused call and ~2 µs of overhead on a permitted call; a single cold refusal in the end-to-end smoke test measured ~15 µs. A pure-Python breaker cannot be sub-microsecond — the state check plus exception construction alone cost more than that. Against a multi-second HTTP timeout this is still a reduction of five to six orders of magnitude. Re-measure on your own interpreter and hardware before quoting any number. | The value of the pattern is entirely in what it *does not* do. |
| Specificity | The breaker must trip only on infrastructure faults — timeouts, connection failures, 502/503/504. It must **not** trip on deterministic errors: HTTP 400, 401, 404, insufficient funds, unknown symbol. | Retrying a deterministic error never succeeds, and opening a circuit on one hides the real defect behind a fake outage. |
| Exception hierarchy | Verify the tuple against the actual client library. `requests.exceptions.ConnectionError` is **not** a subclass of the builtin `ConnectionError`, and `requests.exceptions.Timeout` is **not** a subclass of `TimeoutError`; both derive from `RequestException`, which derives from `OSError`. `requests.exceptions.HTTPError` derives from the same base. (Verified against requests 2.34.2.) | A tuple of builtins never fires against `requests`; a tuple of `OSError` or `RequestException` fires on 4xx business errors. |
| Half-open concurrency | HALF_OPEN must admit a **bounded** number of probes (default one) under a lock; all other callers keep failing fast until a probe resolves. Any probe failure returns the circuit to OPEN immediately. | Microsoft Azure Architecture Center: the half-open state "helps prevent a recovering service from suddenly being flooded with requests". |
| Non-blocking | State transitions are taken under a lock; the wrapped call is **never** invoked while the lock is held. | Azure Architecture Center, "Concurrency": the implementation "shouldn't block concurrent requests or add excessive overhead to each call". |
| Clock source | Recovery timing must use a monotonic clock (`time.monotonic()`), never `time.time()`. | The `time` module documents `monotonic()` as unaffected by system clock updates; a wall clock can be stepped by NTP or an operator, making the window elapse instantly or never. |
| Closed-state counting | Failures must be consecutive (reset on success) and should age out. | Azure Architecture Center: "The failure counter for the Closed state is time based. It automatically resets at periodic intervals … to help prevent the circuit breaker from entering the Open state if it experiences occasional failures." |
| Recovery escalation | The OPEN duration should increase on repeated failed recoveries, bounded by a ceiling, with jitter where many processes share a dependency. | Azure Architecture Center: "you can apply an increasing time-out timer … place the circuit breaker in the Open state for a few seconds initially. If the failure isn't resolved, increase the time-out to a few minutes." Note that this is *not* universal: Resilience4j documents a fixed `waitDurationInOpenState` (default 60s). |
| Slow calls | A dependency that answers correctly but far too late must be treatable as a failure. | Azure Architecture Center lists protecting "against slow dependencies … to maintain your service-level objectives" as a primary use of the pattern. |
| Resource granularity | One breaker per independently failing resource. Never one breaker across several shards, venues or accounts. | Azure Architecture Center, "Resource differentiation": merging error responses makes the application block access to shards that would have succeeded. |
| Observability | Every state transition must be published (gauge + alert), and the count of short-circuited calls must be recorded. | An open circuit is a silent, ongoing functional degradation; the short-circuit count is the size of it. |
| Manual override | An operator must be able to force the circuit open and to reset it. | Azure Architecture Center, "Manual override", for dependencies whose recovery time is highly variable. |

## Regulatory boundary

A client-side circuit breaker is an **engineering** control. It is not, and must not be
presented as, a regulatory control. The relevant obligations constrain *where you are
allowed to put one*:

- **MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589, Article 12 ("Kill
  functionality")**: an investment firm "shall be able to cancel immediately, as an
  emergency measure, any or all of its unexecuted orders submitted to any or all trading
  venues to which the investment firm is connected." A breaker that can refuse a cancel
  or kill instruction works directly against this. Never place one in that path.
  *Applicability: investment firms engaging in algorithmic trading in the EU (and the
  onshored UK equivalent). Mandatory.*
- **MiFID II RTS 6, Article 14 ("Business continuity arrangements")**: firms must have
  business-continuity arrangements for their algorithmic trading systems "appropriate to
  the nature, scale and complexity of [the] business". Graceful degradation of a
  non-critical dependency is one such arrangement, and the fallback behaviour, its
  triggers and its alerting should be documented as part of them. *Mandatory for firms in
  scope; the specific mechanism is not prescribed.*
- **SEC Rule 15c3-5 (US, broker-dealers with market access)**: financial and regulatory
  risk-management controls must be applied on a pre-trade basis and be under the
  broker-dealer's direct and exclusive control. If a control cannot be evaluated because a
  service is unavailable, the compliant response is to stop sending orders — not to fail
  open past the check. A circuit breaker must never be used to bypass one.
  *Applicability: US broker-dealers with market access. Mandatory.*

Nothing above requires a circuit breaker, and no regulator prescribes threshold values for
one. Any threshold in your configuration is an engineering choice you own and must be able
to justify — do not describe it as a regulatory requirement.

## Sources

- Microsoft, *Circuit Breaker pattern*, Azure Architecture Center (page dated 2025-02-05) —
  https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker
  (states, half-open flooding, concurrency, resource differentiation, manual override,
  increasing time-outs, "Inappropriate time-outs on external services").
- Resilience4j, *CircuitBreaker* documentation — https://resilience4j.readme.io/docs/circuitbreaker
  (permitted number of calls in half-open state, default `waitDurationInOpenState` of 60s,
  failure-*rate* thresholds over a sliding window as an alternative to consecutive counting).
- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Articles 12 and 14 —
  https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589
- SEC Rule 15c3-5, *Risk Management Controls for Brokers or Dealers with Market Access*
  (17 CFR 240.15c3-5; adopting release No. 34-63241, 15 November 2010) —
  https://www.federalregister.gov/documents/2010/11/15/2010-28303/risk-management-controls-for-brokers-or-dealers-with-market-access
- Python Software Foundation, `time` module documentation (`time.monotonic`) —
  https://docs.python.org/3/library/time.html#time.monotonic
- M. Nygard, *Release It! Design and Deploy Production-Ready Software*, 2nd ed. —
  origin of the Circuit Breaker stability pattern.
