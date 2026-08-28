# Workflows for Nasdaq TotalView-ITCH Feed Parsing

Deep procedure reference for `NasdaqITCH50ParserEngine`. Field offsets and
semantics are sourced in `references/standards.md`.

## 1. De-frame the transport

The engine consumes exactly one ITCH message, starting at its 1-byte Message
Type. Framing is upstream:

- **MoldUDP64**: 20-byte downstream packet header, then N message blocks. Each
  block starts with a 2-byte big-endian Message Length that excludes itself.
  Read the length, slice exactly that many bytes, pass those bytes in.
- **SoupBinTCP**: the session layer delivers one Sequenced Data packet payload
  per ITCH message.

Sequence-gap detection, A/B line arbitration and retransmission belong here, not
in the decoder — see `sequence-number-gap-detection-for-feeds` and
`exchange-multicast-feed-handling`.

**Failure mode:** a leftover 2-byte length prefix makes every field land two
bytes late. The engine's exact-length check catches this as `ITCHParseError`
rather than letting it become a plausible-looking misparse.

## 2. Dispatch on the type byte

| Byte | Handler | Book effect |
|---|---|---|
| `A`, `F` | `_parse_add` | inserts a resting order |
| `E`, `C` | `_parse_executed` | cumulative share deduction |
| `X` | `_parse_cancel` | cumulative share deduction (partial) |
| `D` | `_parse_delete` | removes the order entirely |
| `U` | `_parse_replace` | removes the original, inserts under the new ref |
| `P` | `_parse_trade` | none — print only |

Any other type raises `ITCHParseError`. In a real feed handler, catch that and
skip the message by its **transport-declared** length; never advance by a
guessed one, or you desynchronise the rest of the packet.

## 3. Decode and validate fields

Per message, in order:

1. Exact length check against the spec total for that type.
2. `struct.unpack` with a big-endian (`>`) layout — no native (`=`) formats,
   which would introduce alignment padding.
3. 6-byte timestamp via `int.from_bytes(..., "big")` — 48-bit unsigned.
4. Alpha fields decoded as ASCII with right padding stripped only. Non-ASCII
   bytes raise, because they almost always mean a wrong offset.
5. Enum validation: Buy/Sell Indicator must be `B` or `S`; the `C` Printable
   flag must be `Y` or `N`.
6. Price range check against the spec ceiling `0x77359400` (200,000.0000).

Prices are carried as **integer ticks** (`price_ticks`) as the authoritative
value; `price_usd` is the derived float convenience. Never key or compare on the
float.

## 4. Apply book state

- **Add (`A`/`F`)** — insert keyed by Order Reference Number. A reference number
  already live is `DUPLICATE_ORDER_ID` (they are day-unique); the new order still
  replaces the old so the book keeps moving, but the run is flagged.
- **Execute (`E`/`C`) and Cancel (`X`)** — deduct. Deducting more than is resting
  is `OVER_EXECUTE` / `OVER_CANCEL`, and the order is removed rather than left at
  a negative size. Reaching exactly zero removes the order with no violation.
- **Delete (`D`)** — remove all remaining shares.
- **Replace (`U`)** — pop the original, insert under the *new* reference number,
  inheriting side and stock from the original and taking Shares as the new
  absolute displayed quantity. If the original is absent, record `UNKNOWN_ORDER`
  and create **nothing**: the replace message carries no side, so any synthesised
  replacement would be liquidity invented on a guessed side.
- **Trade (`P`)** — do not touch the book. Its Order Reference Number is zero and
  its Buy/Sell Indicator is always `B`; neither identifies a resting order.

Any modify/delete/replace naming an order that is not on the book is
`UNKNOWN_ORDER`, never a silent no-op.

## 5. Choose an integrity policy before the run

- `NasdaqITCH50ParserEngine(strict=True)` raises `ITCHBookIntegrityError` on the
  first violation. Use in a validated production pipeline, where divergence
  should stop the process rather than propagate into signals.
- The default `strict=False` counts violations in `violations_by_kind` and
  continues. Use for exploratory replay of an imperfect archive.

Either way, a non-zero `integrity_violation_count` means the reconstructed book
diverges from the venue. Statistics computed from that replay are not clean and
must not be reported as such.

## 6. Generate the audit report

`generate_report(last_msg)` returns an `ITCHParserReport`:

- `status` is `PARSER_SUCCESS` only when `integrity_violation_count == 0`,
  otherwise `PARSER_INTEGRITY_VIOLATIONS`.
- `violations_by_kind` is a snapshot dict, safe to retain after further parsing.
- `audit_notes` carries a human-readable per-kind breakdown for the run log.

## 7. Reconcile

Periodically verify the reconstructed book against Nasdaq's GLIMPSE snapshot
service or an independent archive. Decode-time validation proves the bytes were
well formed; only reconciliation proves the book matches the venue.
