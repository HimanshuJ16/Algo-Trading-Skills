# Real-Time Architecture Standards — sequence-number-gap-detection-for-feeds

| Condition | Sync State | Action |
|---|---|---|
| In-order sequence ($S = S_{\text{expected}}$) | `SYNCED` | Process immediately; drain buffer |
| Out-of-order gap ($S > S_{\text{expected}}$) | `DIRTY_SYNC_PENDING` | Buffer frame; request missing range |
| Duplicate / Stale ($S < S_{\text{expected}}$) | Unchanged | Discard frame; log warning |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
