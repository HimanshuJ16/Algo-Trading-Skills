# Workflows for Philippine Stock Exchange API Integration

The order of these steps matters. The Reference Price must be resolved and
validated first, because it fixes the board lot, the tick size **and** the static
band — everything downstream depends on it.

## 1. Resolve the Reference Price and the Market Segment

The **Reference Price** is the previous session's closing price, or the Last
Adjusted Closing Price (LACP) where a corporate action intervened. It is a
property of the security for the trading day. It is *not* the order price and
*not* the last traded price.

The **market segment** is `PHP` (peso-denominated, the main board) or `DDS`
(dollar-denominated). Take it from reference data. Getting it wrong is silent:
USD 1.50 requires a 20-share lot under the DDS table and a 1,000-share lot under
the peso one, and nothing in the order payload reveals the mistake.

Validate the Reference Price is finite and strictly positive **before using it**.
A NaN Reference Price makes every `<=` comparison return `False`, which disguises
a data fault as a rule breach.

## 2. Board Lot & Tick Size Lookup

Article IV Section 8: *"The Board Lot and Price Fluctuation of a Security for any
Trading Day shall be based on the Security's Reference Price."*

1. Select the band containing the **Reference Price**. Both the `From` and the
   `To` bound are inclusive.
2. The `(tick, lot)` pair returned governs every order in that security for the
   whole trading day. It does **not** change when the stock trades through a band
   boundary intraday.
3. A Reference Price strictly between two bands is off the PSE lattice. Assign it
   the band **below** — the conservative choice, never a coarser tick nor a
   smaller lot — and log it. It usually means an LACP was adjusted for a
   corporate action without being re-rounded.

Full 15-band peso table and 10-band DDS table: `standards.md`.

## 3. Static Threshold Audit — $+50\% / -30\%$

1. $\text{Raw ceiling} = P_{\text{ref}} \times 1.50$; $\text{Raw floor} = P_{\text{ref}} \times 0.70$.
2. Round onto the **Reference Price's** tick: the ceiling **down**, the floor
   **up**. Rounding the floor down would publish a bound representing a fall of
   more than the permitted 30%.
3. Both bounds are **inclusive**. An order at exactly the ceiling is the ceiling
   price and trades.

The band has been asymmetric since **24 March 2020** (CN-2020-0028). For any
replay of a session on or before 23 March 2020, use the symmetric ±50% figure.

Worked example from PSE: Reference Price PHP 1,642.00 → PHP 1.00 tick → ceiling
PHP 2,463.00, floor PHP 1,149.40 rounded up to **PHP 1,150.00**.

## 4. Dynamic Threshold Audit

A second band, symmetric about the **Last Traded Price**, at the percentage PSE
publishes for that security's trade-frequency cluster (20% / 15% / 10% for
clusters A / B / C). The tick still comes from the Reference Price's band.

Supply the last traded price and the percentage **together** or omit both.
Supplying one alone must raise: silently skipping the check is the dangerous
reading of a half-filled payload, and the percentage cannot be inferred from the
price.

An order can be well inside the static band and still fail here — with a last
traded price of PHP 100.00 in a cluster-C security, the dynamic band is
PHP 90.00 – PHP 110.00 while the static band is PHP 70.00 – PHP 150.00.

## 5. Order Divisibility & Tick Increment Validation

1. $\text{quantity} \bmod \text{board\_lot} = 0$, with `quantity` a strictly
   positive integer. Reject `bool` explicitly — `True` is an `int` in Python and
   would pass as a 1-share order.
2. The price must be an exact **`Decimal`** multiple of the tick. Do not scale and
   round: `round(price * 10000) % round(tick * 10000)` discards the sub-tick
   remainder, so PHP 1,000.00005 passes as a clean multiple of PHP 1.00.

## 6. Audit Report Generation

Emit the applied board lot and tick size, the Reference Price, and **both** band
bounds — static, and dynamic where checked. A rejection that reports only
"outside the band" forces the caller to re-derive it; a rejection that reports the
band can be repriced to the ceiling or the floor directly.

Malformed input — unknown side or market, non-integer or non-positive quantity,
non-positive or non-finite price or Reference Price, a half-supplied dynamic
threshold — is **raised**, never folded into a status. A caller bug must be
distinguishable from an exchange-rule rejection.
