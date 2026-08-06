# Institutional Global Instrument Name Sanitization Workflows

## Workflow 1: Multi-Exchange Reference Data Sanitization Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Feed as Exchange Reference Data Feed (Raw Bytes/String)
    participant Engine as Global Instrument Name Sanitizer
    participant Normalizer as Unicode Normalizer (NFC)
    participant DB as Security Master Database

    Feed->>Engine: Ingest Raw Instrument Name (e.g. b'\xef\xbb\xbfSoci\xc3\xa9t\xc3\xa9 G\xc3\xa9n\xc3\xa9rale')
    
    Engine->>Engine: 1. Strip UTF-8 BOM (0xEF 0xBB 0xBF)
    Engine->>Engine: 2. Fallback Multi-Encoding Decode (Shift-JIS, GBK, Latin-1)
    Engine->>Engine: 3. Strip Control & Zero-Width Chars (U+200B, U+FEFF)
    Engine->>Engine: 4. Detect & Repair Mojibake (SociÃ©tÃ© -> Société)
    
    Engine->>Normalizer: Apply NFC Normalization (Canonical Composition)
    Normalizer-->>Engine: Standardized NFC String ("Société Générale")
    
    Engine->>Engine: 5. Transliterate NFD -> ASCII Slug ("SOCIETE GENERALE")
    
    Engine-->>DB: Store Sanitized Instrument (UTF-8 NFC Name & Transliterated ASCII Slug)
```

---

## Workflow 2: Mojibake Detection & Security Master Match Decision Tree
```mermaid
flowchart TD
    A[Ingest Instrument Name String] --> B{Contains UTF-8 BOM or Zero-Width Chars?}
    
    B -- Yes --> C[Strip BOM & Zero-Width Chars: U+FEFF, U+200B]
    B -- No --> D{Contains Mojibake Patterns? e.g. Ã©, Ã¼}
    C --> D
    
    D -- Yes --> E[Execute repair_mojibake(): Re-encode Latin-1 -> Decode UTF-8]
    D -- No --> F[Apply Unicode NFC Normalization]
    E --> F
    
    F --> G[Generate Transliterated ASCII Slug: NFD Decomposition]
    G --> H[Query Security Master Index by NFC Name & ASCII Slug]
    
    H --> I{Match Found?}
    I -- Yes --> J[Link Existing Security Master ID (Prevent Duplicate Entry)]
    I -- No --> K[Create New Security Master Instrument Record]
```

