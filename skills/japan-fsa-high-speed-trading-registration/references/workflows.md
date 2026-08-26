# Workflows for Japan FSA HST Compliance

## 1. Validate the payload before classifying

A compliance gate must fail loudly on bad data rather than silently approve it.
Reject a blank `trader_id`; reject an `order_value_jpy` that is not finite and
positive; reject a `latency_ms` that is negative or not finite; reject a bare
string passed as `notified_strategy_types`, which would otherwise iterate
character by character and fail every membership test in a way nothing surfaces.

Note the NaN trap specifically: `float('nan') <= limit` is `False`, so an
unvalidated NaN order value happens to reject, while an unvalidated NaN in any
field that gates *classification* would silently push the order out of scope.
Validate rather than rely on which way the comparison happens to fall.

## 2. Classify under FIEA art. 2(41)

Evaluate four legs and take the conjunction:

| Leg | Source | Input |
|---|---|---|
| Automated decision-making | FIEA art. 2(41) | `is_algo_automated` |
| Designated destination venue | 定義府令 art. 26(1) + FSA notice | `venue` ∈ designated set |
| Co-location / proximity of the order server | 定義府令 art. 26(2)(i) | `is_colocated` |
| Contention-free transmission | 定義府令 art. 26(2)(ii) | `has_contention_free_transmission` |

Do **not** consult `latency_ms`. It is carried and reported as an operational
metric only; no latency threshold exists in this regime.

Resolve unknowns conservatively. A blank `venue` or a `None`
`has_contention_free_transmission` resolves to *satisfied* and records a warning.
The asymmetry is deliberate: the cost of wrongly classifying an order as
high-speed trading is a rejected order, while the cost of wrongly classifying it
as out of scope is unregistered trading.

If the order is not high-speed trading, skip steps 3–6 but still apply step 7 —
the firm's value limits are a house control that does not depend on the FIEA
definition.

## 3. Select the registration route

Branch on whether the entity is already a registered financial instruments
business operator or registered financial institution.

- **FIBO / registered financial institution** — no HST registration exists or is
  required. Audit that the FIEA art. 29-2(1)(vii) notification has been filed.
  Never demand a 関東財務局長（高速）第N号 number here.
- **Anyone else** — audit the FIEA art. 66-50 registration. Absence rejects.
  Registration asserted without a recorded number rejects separately: a
  registration you cannot evidence on the order cannot be relied on in an audit.

## 4. Verify the registration number format

Parse the number against `関東財務局長（高速）第N号` (an ASCII rendering is also
accepted). A number that does not parse **warns rather than rejects** — the FSA
publishes the register as text, and an unfamiliar rendering is not by itself
proof of an invalid registration. Verify a warned number against the published
register before trading.

## 5. Audit the Japan representative — foreign entities only

FIEA art. 66-53(5)(c) and (6)(b) make failure to appoint a representative or
agent in Japan a refusal ground for **foreign corporations and non-resident
individuals**. Do not apply it to a domestic entity. Treat unknown domicile as
foreign.

Guidelines III-3-1-3(1)(i)(g) sets the substantive bar: the appointee must have a
working knowledge of the FIEA as it applies to high-speed trading and be able to
respond accurately to a report demand — not merely pass messages along.

## 6. Audit the exchange-level per-order obligations

- **HST order flag** — TSE Business Regulations art. 14(1)(7).
- **Trading strategy type** — TSE Brokerage Agreement Standards art. 6(5)
  requires it on each entrustment. Validate against `MARKET_MAKING`,
  `ARBITRAGE`, `DIRECTIONAL`, `OTHER` (Guidelines III-3-1-1(2)(i)) and, where
  the caller supplies `notified_strategy_types`, against the strategies actually
  recorded in the 業務方法書. Running a strategy the authorities were never told
  about is a filing breach the order-level flag alone will not catch.

## 7. Audit the kill switch and the pre-trade value limits

Guidelines III-2-1-2 expects **both** a hard and a soft limit, calibrated to the
firm, plus a kill switch capable of cancelling anomalous orders already sent to
the market.

Model the two limits differently, because that is the whole reason there are two:

- hard limit breached $\implies$ reject the order;
- soft limit breached but hard limit respected $\implies$ warn and let it through.

The yen figures are firm parameters. Nothing in the FIEA or FSA guidance
prescribes an amount, so a default in code is a placeholder to be replaced with
the limit the firm has calibrated and documented.

## 8. Generate the audit report

Run every check; short-circuit nothing. Emit:

- `breaches` — the complete set of failures, so remediation sees everything at
  once rather than discovering the next problem on the next attempt;
- `warnings` — conservatively-resolved unknowns, unparsed registration numbers,
  soft-limit breaches;
- `status` — the most serious breach, ranked in this order: unregistered HST,
  missing registration id, unnotified FIBO, no Japan representative, missing kill
  switch, missing HST order flag, invalid trading strategy, pre-trade limit
  exceeded. With no breaches the status distinguishes `FSA_HST_APPROVED` from
  `NOT_HIGH_SPEED_TRADING`.

Never populate a report field with an affirmative value for a check that did not
run. Each boolean on `JapanFsaHstReport` reflects a check that actually
executed.

## 9. Keep the designated-venue list current

The designating FSA notice under 定義府令 art. 26(1) is amended over time. Put a
periodic review of `designated_venues` on the compliance calendar alongside the
FSA register check, and pass the current list explicitly in production rather
than inheriting the dated default.
