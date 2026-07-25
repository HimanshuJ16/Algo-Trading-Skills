# Workflow: Broker API Deprecation Monitoring

## 1. Real-time REST Header Extraction
Intercept API responses dynamically within the HTTP client session (e.g., using `requests` Session hooks or middleware in `aiohttp`).
- Check headers: `Sunset`, `Deprecation`, `Link` (with `rel="sunset"`), and `X-API-Deprecation-Warning`.
- Extract dates ensuring strict conversion to UTC using `email.utils.parsedate_tz` or ISO8601 parsing.
- Compute delta days against `datetime.now(timezone.utc)`.

## 2. Asynchronous Feed Polling
Run a background thread or asynchronous task polling the broker's changelog RSS/Atom feed or JSON API every 24 hours.
- Regex scan descriptions for: `\bdeprecated\b`, `\bsunset\b`, `\bbreaking change\b`.
- Use heuristics to extract ISO dates indicating sunset timelines.

## 3. In-Memory Deduplication
Maintain a thread-safe registry (using `threading.RLock`) of active deprecation notices keyed by `broker_name:endpoint`.
- Only trigger an alert callback if a new notice is discovered, or if the `urgency` tier of an existing notice escalates (e.g., transitions from 30-day warning to 7-day critical).

## 4. Alert Routing
Execute injected callbacks to route notices:
- `NOTICE`: Route to standard logs for next sprint planning.
- `WARNING_30_DAYS`: Route to Slack/Teams for engineering visibility.
- `CRITICAL_SUNSET_IMMINENT`: Route to PagerDuty/Opsgenie to prevent imminent bot crashes in production.
