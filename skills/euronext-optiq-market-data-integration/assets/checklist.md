# Pre-Flight Checklist — Euronext Optiq MDG Feed Handler

## Framing and decoding

- [ ] Is the packet header read as 16 bytes, little-endian, in the order Packet Time (u64),
      Packet Sequence Number (u32), Packet Flags (u16), Channel ID (u16)?
- [ ] Are message blocks decoded from the SBE template XML for the certified SBE version,
      rather than from a hard-coded layout?
- [ ] Is each message located via its Frame field, and is the packet discarded in full when
      the Frame lengths do not sum to the body length?
- [ ] Is Packet Flags bit 0 checked before parsing, and is the LZ4-decompressed body bounded
      at 8192 bytes?

## Sequencing and recovery

- [ ] Is gap detection driven by the **Packet Sequence Number** only, never by the Market
      Data Sequence Number?
- [ ] Are Packet Flags bits 4–6 folded into the PSN so a rollover past 2^32 is not read as
      a four-billion-packet gap?
- [ ] Is a PSN below the high-water mark classified as a duplicate or reordered packet
      rather than as a gap?
- [ ] Does a change in Packet Flags bits 1–3 trigger a full book rebuild rather than being
      read as reordering?
- [ ] Is line A/B arbitration in place, with snapshot resynchronization when both lines drop
      the same packet?
- [ ] Is the book marked unsynchronized on any gap or restart, and does quoting stay frozen
      until a snapshot or book retransmission has been applied?

## Book state

- [ ] Are prices scaled by the instrument's Price/Index Level Decimals from Standing Data
      (1007), with no fallback default?
- [ ] Is the null price (`-2^63`) distinguished from a genuine price of zero?
- [ ] Is a quantity of 0 handled as a limit deletion, and update type 254 as a full book
      clear?
- [ ] Are limits keyed on the venue's integer price rather than on a float?
- [ ] Is the depth book built from either BBO messages or full-depth limits, never both?
- [ ] Is there one engine instance per instrument, and one PSN tracker per channel?

## Trading state and quoting gate

- [ ] Are Book State (1–9) and Order Entry Qualifier (0–3) both read from Market Status
      Change (1005), with absent and `255` treated as "unchanged"?
- [ ] Does an unrecognised enum value raise and alert, rather than defaulting to a
      permissive state?
- [ ] Does quoting require all of: synchronized book, Book State Continuous, Order Entry
      Enabled, and an uncrossed book?
- [ ] Is a crossed book tolerated during Call but treated as a fault during Continuous?
- [ ] Is the auction-quoting decision made deliberately by the strategy, rather than assumed
      from the protocol in either direction?

## Operational

- [ ] Is the handler re-verified after every SBE template upgrade, including enum sets?
- [ ] Is a captured-session replay reconciled against the snapshot channel at each End Of
      Snapshot (2102) boundary?
- [ ] Are gap counts, desynchronization events and restart detections exported as metrics
      with alerting, not only logged?
