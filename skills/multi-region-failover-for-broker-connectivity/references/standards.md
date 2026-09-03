# Standards — multi-region-failover-for-broker-connectivity

Two kinds of statement appear below. **Cited** items come from a published regulation or
vendor specification and are reproduced with their source. **Calibrated** items are
engineering defaults with no external authority behind them — no regulator, exchange or
standards body publishes a failover threshold or a cooldown, and a document presenting
one as a standard is inventing it.

---

## 1. Calibrated defaults (re-derive these before use)

| Parameter | Default | What it actually controls |
|---|---|---|
| `failure_threshold` | 3 consecutive | Failed probes before an endpoint is `DOWN`. Below it, `DEGRADED`. Derive it from your primary's observed error distribution: too low abandons a working path on noise, too high leaves flow on a dead one. |
| `cooldown_seconds` | 60.0 | Minimum interval after any switch before a **failback** is permitted. Does not gate failover. |
| `max_health_age_seconds` | 30.0 | Age beyond which a successful probe no longer qualifies an endpoint as a failover target. **Must exceed your probe interval** with margin — the engine cannot see your scheduler and cannot check this for you. |
| `failback_success_threshold` | 3 consecutive | Successful probes the primary must accumulate before failback. Elapsed time measures patience, not recovery. |
| `require_fence` | `True` | Withholds the switch until the caller evidences the outgoing path cannot submit orders. |
| `max_failbacks_per_window` / `failback_window_seconds` | 3 / 3600.0 | Rate limit on **voluntary** switches. Failover is never rate limited. |

The cost asymmetry that should drive calibration: a late failover costs fills and leaves
positions exposed on a dead path; an early failover puts two live paths in front of one
account. Neither is the conservative direction in all cases, which is why the defaults
above are starting points and not recommendations.

---

## 2. Cited — a broker may permit only one session, which changes the design

Where a broker enforces session exclusivity per credential, there is no warm standby to
fail over *to*: the backup region's login is what terminates the primary's.

Interactive Brokers, Web API documentation, *Managing Multiple Sessions*: "Only a single
active brokerage session can exist for any username across all IBKR services." The
brokerage-session initialization endpoint exposes a parameter controlling whether other
sessions are disconnected in favour of the new connection.
<https://www.interactivebrokers.com/docs/web-api/authentication/multiple-sessions>

Interactive Brokers, TWS API documentation, *Logging into multiple applications*: "It is
not possible to login to multiple trading applications simultaneously with the same
username." The documented workaround is additional usernames on the same account, each
used by at most one application at a time.
<https://www.interactivebrokers.com/docs/tws-api/doc/connectivity/logging-into-multiple-applications>

Three consequences for a design built on this module:

- **The standby cannot be pre-warmed on the same credential.** Either provision a second
  credential per the broker's documented mechanism, or accept that connect-and-logon
  latency is inside your recovery time.
- **Session competition *is* the fence.** This is the specific, verified case in which
  `require_fence=False` is defensible — the broker guarantees the old session is gone.
  Verify it against your broker's current documentation; do not generalise IBKR's
  behaviour to other brokers.
- **Flapping costs more here.** Each switch tears down and re-establishes a session, and
  the disconnect is visible to any other application using that credential.

## 3. Cited — alternative endpoints are not necessarily equal-quality

Binance Spot REST API, *General API Information*, lists `https://api.binance.com`,
`https://api-gcp.binance.com` and `https://api1.binance.com` through `api4`, and states:
"The last 4 endpoints in the point above (`api1`-`api4`) should give better performance
but have less stability."
<https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information>

A vendor's own alternative endpoints can differ in stability, rate-limit treatment and
entitlements. Validate a backup under realistic load before relying on it, and re-validate
when the vendor changes terms.

## 4. Cited — the switch does not resolve in-flight orders

Same source: "HTTP `5XX` return codes are used for internal errors; the issue is on
Binance's side. It is important to **NOT** treat this as a failure operation; the
execution status is **UNKNOWN** and could have been a success."

An order submitted from the region you are abandoning may be resting, filled, or never
received, and the three are indistinguishable from the client side. Failing over changes
the network path and nothing else.

Endpoint failover does retain one advantage over account failover: both paths reach the
same account at the same broker, so whatever duplicate-submission protection that broker
offers — a client-supplied order id it rejects on reuse — still applies across the
switch, whereas a second broker has never seen the id and cannot recognise it. Confirm
your broker actually enforces that on the id field you use, and on what scope (per
symbol, per account, for how long); it is a broker-specific guarantee, not a property of
the pattern. See `order-placement-idempotency`. Either way it resolves duplicates, not
outcomes: reconciliation must still precede resumed flow.

## 5. Cited — business continuity is a regulatory obligation in the EU

Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) Article 14, "Business
continuity arrangements", within Section 3 of Chapter II on resilience of trading
systems. An investment firm must have business continuity arrangements for its
algorithmic trading systems appropriate to the nature, scale and complexity of its
business, documented in a durable medium, effectively dealing with disruptive incidents
and, where appropriate, ensuring timely resumption of algorithmic trading. The
arrangements must address adverse scenarios including the unavailability of systems,
staff, work space, external suppliers or data centres.

A redundant connectivity path is one such arrangement; the drill records this module
produces are evidence that it works. Article 12 (kill functionality) is a **separate**
control: switching the path stops nothing that is already resting at the broker.

Jurisdiction: EU-authorised investment firms engaged in algorithmic trading. Text:
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. Article numbers and titles were
confirmed against secondary indices of the regulation; consult the EUR-Lex text before
relying on any of it for a compliance decision. Note that ESMA has indicated Articles 14
and 18 of RTS 6 fall within the scope of the Digital Operational Resilience Act (DORA)
for firms in scope of it — confirm which framework applies to your entity.

## 6. Cited — risk controls apply to every path

17 CFR 240.15c3-5, applying to broker-dealers with market access. Paragraph (b) requires
a documented "system of risk management controls and supervisory procedures reasonably
designed to manage the financial, regulatory, and other risks" of market access;
paragraph (c)(1)(i) requires controls to prevent orders exceeding pre-set credit or
capital thresholds, and (c)(1)(ii) to prevent erroneous orders exceeding price or size
parameters.

Architectural consequence: the risk layer sits **above** the path selector, so no
endpoint can be routed around, and limits are enforced in aggregate rather than per path.
A failover that moves flow onto a path with looser controls has defeated the control.

Jurisdiction: US broker-dealers with market access. Not a rule about redundancy — it is a
constraint on where the failover sits in your architecture.

---

## 7. Clock discipline

Every interval in this module — cooldown, health freshness, the failback window — is
measured with `time.monotonic()`. `time.time()` is steppable: an NTP correction moves it
forwards or backwards, and a stepped wall clock either releases the failback gate early
or holds it shut far past its intended duration. Wall-clock timestamps are retained in
`BrokerEndpoint.last_check_time` and `FailoverEvent.timestamp` for audit correlation
only, never for arithmetic.

## 8. Known limitations

- **The engine cannot detect anything itself.** It has no I/O. Every state transition
  comes from a probe result you supply, and a probe that does not exercise
  authentication and order acceptance will report a path that cannot trade as healthy.
- **It does not fence.** `fence_confirmed` is the caller's assertion. The engine records
  that the assertion was made; it cannot verify it.
- **It does not reconcile.** Orders in flight at the abandoned endpoint are out of scope
  and unresolved.
- **It does not measure RTO.** Connect, logon, subscription restart and reconciliation
  dominate real recovery time and all happen outside this module.
- **Stale health is reported, never acted on.** A stalled probe loop is surfaced in
  `FailoverDecision.notes`; the engine will not fail over on it, because a dead monitor
  is not a dead endpoint.
- **One prober per endpoint.** Counters are updated under a lock, but the probe runs
  outside it so a blocking call cannot stall the order path. Two concurrent probes of the
  same endpoint may therefore apply their results in either order.
- **`failover_history` is unbounded.** It is an audit trail, one entry per switch. Drain
  or cap it if the process runs for months.
