# Institutional Global Instrument Name Sanitization Workflows

## Workflow 1: Multi-Exchange Reference Data Sanitization Pipeline

The step order below is load-bearing. Mojibake repair runs **before** control stripping,
because the Latin-1 Mojibake of typographic punctuation is itself C1 control characters.

```mermaid
sequenceDiagram
    autonumber
    participant Feed as Exchange Reference Data Feed (raw bytes)
    participant Engine as Global Instrument Name Sanitizer
    participant Normalizer as Unicode Normalizer (NFC)
    participant Review as Review Queue
    participant DB as Security Master Database

    Feed->>Engine: Ingest raw name (e.g. b'\xef\xbb\xbfSoci\xc3\xa9t\xc3\xa9 G\xc3\xa9n\xc3\xa9rale')

    Engine->>Engine: 1. Decode: declared source_encoding, else BOM (UTF-8/16/32), else default, else guess
    Note over Engine: Result carries decode_confidence:<br/>declared / bom / default / guessed / lossy

    Engine->>Engine: 2. Repair Mojibake by strict CP1252 then Latin-1 round trip (max 3 rounds)
    Engine->>Engine: 3. Strip BOM / zero-width / control chars (joiners optional)
    Engine->>Normalizer: 4. Apply NFC (UAX #15)
    Normalizer-->>Engine: "Société Générale"
    Engine->>Engine: 5. Transliterate to ASCII slug, recording dropped characters

    alt is_trustworthy (declared or BOM decode, nothing lossy or dropped)
        Engine-->>DB: Persist cleaned_name (NFC) + ascii_slug + audit_actions
    else guessed encoding, lossy decode, or lossy slug
        Engine-->>Review: Queue with warnings; do NOT write unattended
    end
```

## Workflow 2: Decode-confidence decision tree

The single most damaging failure in this domain is a *successful* decode with the wrong
codec — no exception is raised and the name looks plausible.

```mermaid
flowchart TD
    A[Raw bytes from feed] --> B{source_encoding declared for this venue?}
    B -- Yes --> C[Decode strictly with the declared codec]
    C --> C1{Decoded?}
    C1 -- Yes --> T[confidence = declared]
    C1 -- No --> X[Raise UnicodeProcessingError<br/>Do NOT fall through to a guess:<br/>a plausible wrong name is worse<br/>than a failed record]

    B -- No --> D{BOM present? Test 4-byte signatures BEFORE 2-byte}
    D -- Yes --> E[Strip BOM, decode with that codec] --> T2[confidence = bom]
    D -- No --> F{Strict UTF-8 decodes?}
    F -- Yes --> T3[confidence = default]
    F -- No --> G[Try fallback codecs in order]
    G --> H{Any codec accepted the bytes?}
    H -- Yes --> I[confidence = guessed<br/>Log WARNING: overlapping CJK codecs<br/>decode each other's bytes silently]
    H -- No --> J[UTF-8 with errors=replace<br/>confidence = lossy, count U+FFFD]

    T --> K[Continue pipeline]
    T2 --> K
    T3 --> K
    I --> L[Continue, but flag for review]
    J --> L
```

## Workflow 3: Mojibake repair — round trip, not substitution table

A substitution table cannot distinguish corruption from correct text. `SÃO MARTINHO S.A.`
(B3: SMTO3) is correctly encoded Portuguese; a table with a bare `"Ã"` key rewrites it.

```mermaid
flowchart TD
    A[Decoded string] --> B{Any char in U+0080-U+00FF<br/>or the CP1252 0x80-0x9F punctuation set?}
    B -- No --> Z[Nothing to repair]
    B -- Yes --> C[Re-encode to CP1252]
    C --> C1{Encodes AND decodes as STRICT UTF-8?}
    C1 -- No --> D[Re-encode to Latin-1]
    C1 -- Yes --> E{Mojibake score strictly lower<br/>and no U+FFFD introduced?}
    D --> D1{Encodes AND decodes as STRICT UTF-8?}
    D1 -- No --> R[No whole-string round trip applies]
    D1 -- Yes --> E
    E -- No --> R
    E -- Yes --> F[Accept repair; repeat, max 3 rounds<br/>doubly-encoded Mojibake is real]
    F --> B
    R --> S[Residual pass: apply the 2-character substitution table<br/>longest key first, for partially-corrupt strings only]
    S --> Z
```

The **strict** UTF-8 decode is the safety guard. `SÃO MARTINHO S.A.` re-encodes to
`b"S\xc3O ..."`; `\xc3` starts a multi-byte sequence and `O` is not a continuation byte,
so the decode fails and the string is left alone.

## Workflow 4: Security master match and FIX emission

```mermaid
flowchart TD
    A[SanitizedInstrumentName] --> B{is_trustworthy?}
    B -- No --> Q[Review queue with warnings + audit_actions]
    B -- Yes --> C[Look up security master by cleaned_name NFC]
    C --> D{Exact NFC match?}
    D -- Yes --> E[Link existing security master ID]
    D -- No --> F[Secondary lookup by ascii_slug]
    F --> G{Slug match?}
    G -- Yes --> H[Candidate match: slug is many-to-one<br/>Muller and Müller both slug to MULLER<br/>Confirm before linking]
    G -- No --> I[Create new security master record]

    E --> J[Emit to FIX]
    H --> J
    I --> J
    J --> K{ascii_slug_is_lossy?}
    K -- No --> L[SecurityDesc 107 / Symbol 55 = ascii_slug<br/>EncodedSecurityDesc 351 = native name<br/>MessageEncoding 347 = UTF-8]
    K -- Yes --> M[Slug does not represent the name.<br/>Use a curated romanization for 107/55;<br/>never emit an empty Symbol 55]
```

## Workflow 5: Per-venue encoding declaration

Encoding guessing is the root cause of silent corruption, so the ingest configuration —
not the sanitizer — should own the answer.

```python
VENUE_ENCODINGS = {
    "XTKS": "cp932",     # Tokyo: Shift-JIS family, NEC/IBM extension kanji present
    "XSHG": "gb18030",   # Shanghai
    "XKRX": "cp949",     # Korea Exchange (UHC)
    "XPAR": "utf-8",
    "XCSE": "utf-8",
}

config = InstrumentSanitizerConfig(source_encoding=VENUE_ENCODINGS[mic])
result = GlobalInstrumentNameSanitizer(config).sanitize_instrument_name(raw_bytes)
```

When a declared codec starts failing, that is a feed change to investigate — not a reason
to relax the declaration back to guessing.
