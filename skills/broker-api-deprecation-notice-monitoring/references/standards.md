# Standards: API Deprecation (RFC 8594)

## RFC 8594: The Sunset HTTP Header
Standardizes a mechanism for origin servers to communicate that a URI is likely to become unresponsive at a specified point in the future.
- **Header**: `Sunset`
- **Format**: HTTP-date (e.g., `Wed, 11 Nov 2026 00:00:00 GMT`)
- **Semantics**: Indicates the date and time when a specific resource or endpoint is scheduled to be retired.

## The Deprecation HTTP Header
An IETF draft standard often used in conjunction with `Sunset`.
- **Header**: `Deprecation`
- **Format**: Boolean (`true`), HTTP-date, or contextual string.
- **Semantics**: Indicates the endpoint is no longer recommended for use, though it may not have a hard sunset date yet.

## The Link Header (RFC 5988)
Often used to provide developers a URL containing migration instructions or sunset rationale.
- **Header**: `Link`
- **Format**: `<https://developer.broker.com/sunset-v1>; rel="sunset"; type="text/html"`

## Timezone Safety (UTC)
Algorithmic trading systems must normalize all chronological states to Coordinated Universal Time (UTC) to prevent logic drift in distributed microservices. All dates extracted from HTTP headers or changelogs must be localized to `datetime.timezone.utc` before performing days-remaining calculus.
