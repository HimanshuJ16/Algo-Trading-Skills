# Pre-Flight Checklist

## Partitioning — is per-symbol ordering actually guaranteed?

- [ ] Is the partitioner **named explicitly** rather than inherited from a client default?
- [ ] Do **all** producers on this topic use the same hash? (`confluent-kafka`/librdkafka default to CRC32; Java, `kafka-python` and `aiokafka` default to murmur2 — with 16 partitions `AAPL` lands on 12 under one and 1 under the other.)
- [ ] Has `diagnose_partitioner_divergence(universe)` been run and returned empty, or has the fleet been standardised on one hash?
- [ ] Is the record key the **canonicalized** symbol (`normalize_symbol_key`), published as that exact string?
- [ ] Is a missing or blank symbol rejected before publish? (An empty key is *randomly* partitioned under librdkafka's default, not sent to partition 0.)

## Producer — can a retry reorder ticks within a partition?

- [ ] `enable.idempotence = true`?
- [ ] `acks = all`?
- [ ] `max.in.flight.requests.per.connection <= 5`?  (All three are jointly required — keying alone does not preserve order.)
- [ ] Is `linger.ms` a deliberate choice? It adds up to that many milliseconds of latency per tick.
- [ ] Is `batch.size` calibrated rather than inherited? (Kafka default 16 KB, librdkafka default 1 MB, this skill's default 128 KB — none of them a standard.)

## Consumer lag & staleness

- [ ] Is lag monitored across **all** partitions, not a sampled subset?
- [ ] Is the `offset_basis` recorded — committed group offsets or current position? (They differ by up to one commit interval.)
- [ ] Is the lag threshold calibrated to the symbol's tick rate rather than applied uniformly?
- [ ] Is a **time-based** staleness budget (`max_tick_age_ms`) set? Message-count lag alone does not answer "is this quote old?".
- [ ] Is a consumed offset ahead of the log end offset treated as a **measurement failure** rather than clamped to zero lag?
- [ ] Are feed-handler and consumer clocks synchronised, and is skew **reported**? A tick timestamped ahead of the local clock gives a negative age, which never trips a positive staleness budget.
- [ ] Is `clock_skew_tolerance_ms` set to the host's actual PTP/NTP sync tolerance?

## Capacity & topology

- [ ] Is partition skew monitored, with the audit suppressed when distinct symbols < partitions?
- [ ] Is the partition count sized for the hot names, not just the symbol count? (Key routing spreads *symbols*, not *volume*.)
- [ ] Before any partition-count change: has `symbols_remapped_by_partition_growth` been run, and is there a drain-or-replay plan for the symbols it lists?

## Data integrity

- [ ] Does a batch containing an invalid tick leave partition state unchanged (no partial publish)?
- [ ] Is upstream out-of-order arrival surfaced rather than assumed away? Kafka preserves append order; it cannot repair a feed that was already out of sequence.
- [ ] Are reported throughput figures **measured**, not derived from a constant?
