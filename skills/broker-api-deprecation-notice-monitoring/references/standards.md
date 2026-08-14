# Standards: API Deprecation and Sunset Signalling

## RFC 8594 — The Sunset HTTP Header Field (May 2019)

Standardises how an origin server communicates that a URI is likely to become
unresponsive at a specified point in the future.

- **Header**: `Sunset`
- **Syntax** (Section 3): `Sunset = HTTP-date`, where HTTP-date is defined by
  RFC 7231 Section 7.1.1.1. Example: `Sunset: Sat, 31 Dec 2018 23:59:59 GMT`.
- **HTTP-date admits three forms**, and a recipient must accept all of them: the
  preferred IMF-fixdate, the obsolete RFC 850 form
  (`Sunday, 06-Nov-94 08:49:37 GMT`), and asctime (`Sun Nov  6 08:49:37 1994`).
  Python's `email.utils.parsedate_tz` parses all three.
- **It is a hint, not a contract.** Section 3: "Clients SHOULD treat Sunset timestamps
  as hints: it is not guaranteed that the resource will, in fact, be available until
  that time and will not be available after that time." After the sunset time,
  Section 3 notes interactions are likely to yield 4xx or 3xx responses, or to fail
  entirely.
- **The header may appear late.** Section 9 notes it need not be present from the
  beginning, and typically appears once the sunset date is within some window.
- **`sunset` link relation type** (Section 6): identifies a resource describing the
  sunset policy, the upcoming sunset, or mitigation strategies. The specification
  places no constraints on the type or scope of the linked resource.

## RFC 9745 — The Deprecation HTTP Response Header Field (Standards Track, March 2025)

Supersedes the long-running `draft-dalal-deprecation-header` /
`draft-ietf-httpapi-deprecation-header` series.

- **Header**: `Deprecation`
- **Syntax** (Section 2.1): "Deprecation is an Item Structured Header Field; its value
  MUST be a Date as per Section 3.3.7 of [RFC9651]" — an `@` followed by seconds since
  the Unix epoch. Example: `Deprecation: @1688169599` (30 June 2023, 23:59:59 UTC).
- **The boolean form is no longer conformant.** Earlier drafts defined
  `Deprecation = IMF-fixdate / "true"`, and gateways deployed against those drafts
  still emit `Deprecation: true`. A monitor must accept it as "deprecated, date
  unknown" while recognising it carries no date.
- **A past value is meaningful**: the resource *was* deprecated at that date. A future
  value announces the effective deprecation date.
- **Deprecation is not removal.** The RFC notes "the act of deprecation does not change
  any behavior of the resource" — escalation urgency belongs to the sunset date.
- **Ordering constraint** (Section 4): "The timestamp given in the `Sunset` HTTP header
  field MUST NOT be earlier than the one given in the `Deprecation` header field." A
  response violating this has inconsistent lifecycle metadata and neither date should
  be trusted without confirmation.
- **`deprecation` link relation type** (Section 3): "Refers to documentation (intended
  for human consumption) about the deprecation of the link's context." Example:
  `Link: <https://developer.example.com/deprecation>; rel="deprecation"; type="text/html"`.

## RFC 8288 — Web Linking (October 2017)

Defines the `Link` header serialisation. **It obsoletes RFC 5988**, which older
integration guides still cite.

- **Format**: `<https://developer.broker.com/sunset-v1>; rel="sunset"; type="text/html"`
- Several links may appear in one header field, comma-separated — but a comma is also
  legal inside a link target, so splitting the raw header on `,` corrupts such targets.
- A single `rel` parameter may carry multiple space-separated relation types
  (`rel="sunset alternate"`), so relation matching must compare whole tokens rather
  than testing for a substring.

## Timezone handling

All extracted dates are normalised to `datetime.timezone.utc` before any countdown
arithmetic. Two consequences matter in practice:

- An HTTP-date or ISO 8601 value carrying a non-UTC offset must be **converted**, not
  truncated. Reading only the leading `YYYY-MM-DD` of `2026-11-25T00:00:00+05:30`
  places the deadline 5.5 hours later than the broker announced.
- A naive clock injected into the monitor is interpreted as UTC rather than raising,
  because the header inspector runs inline with live trading requests.

## Non-standard headers

`X-API-Deprecation-Warning` and `X-Deprecation-Warning` are vendor conventions with no
specification behind them. Treat their contents as free-text operator context, never as
a parseable date source.
