# Standards for PTP Clock Synchronization

| Metric | Engineering Standard |
|---|---|
| Hardware Mode | All production trading servers MUST use hardware timestamping (`-H`). Software mode (`-S`) is strictly prohibited for live execution hosts. |
| Target Offset (HFT) | Target RMS offset for HFT execution hosts is $< 100\text{ ns}$ from the Grandmaster. |
| MiFID II Limit | Maximum allowable offset from UTC under MiFID II RTS 25 is $100\ \mu\text{s}$ ($100,000\text{ ns}$). |
| Dual Daemon Sync | Both `ptp4l` (Grandmaster -> PHC) and `phc2sys` (PHC -> `CLOCK_REALTIME`) MUST be running and verified in `SLAVE`/`s2` state. |
