# PTP Clock Synchronization: Sources and Scope

Two kinds of number appear below and they must not be mixed. **Regulatory ceilings** are
published by a regulator and bind a named activity in a named jurisdiction. **Engineering
targets** are what a stack is designed to achieve and are set from measurement of your own
hardware, path and grandmaster — no regulator publishes them.

## 1. Regulatory ceilings

### EU — MiFID II RTS 25

**Commission Delegated Regulation (EU) 2017/574** of 7 June 2016 (RTS 25), Annex Table 2,
binds members and participants of EU trading venues by *type of trading activity*:

| Type of trading activity | Maximum divergence from UTC | Granularity |
|---|---|---|
| High frequency algorithmic trading technique | 100 microseconds | 1 microsecond or better |
| Any other trading activity | 1 millisecond | 1 millisecond or better |
| Voice / RFQ with human intervention / negotiated transactions | 1 second | 1 second or better |

<https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0574>

Confirmed against ESMA's Q&A on MiFID II market structures topics, which restates the 1 µs
granularity for HFT technique and 1 ms for other algorithmic trading under Table 2:
<https://www.esma.europa.eu/publications-data/questions-answers/1609>

**Article 4 — traceability.** Entities must establish a system of traceability to UTC,
document its design, functioning and specifications, identify the exact point at which a
timestamp is applied, and review compliance of the traceability system at least annually.
A monitored PTP stack is evidence *inside* that system, not a substitute for it.

**Granularity is a separate obligation from divergence.** Holding 100 µs divergence while
recording at millisecond granularity still fails Table 2. This skill addresses divergence;
granularity is a property of the recording path.

Annex Table 1 binds *venue operators* by gateway-to-gateway latency, not members. It is
noted here only so the two tables are not confused. The full breakdown of both tables lives
in `clock-drift-monitoring-alerting-thresholds/references/standards.md`; it is not
duplicated here.

### US — CAT / FINRA

**FINRA Rule 6820** requires Industry Member Business Clocks used for CAT reportable events
to be synchronized to within **50 milliseconds** of the NIST atomic clock (1 second for
clocks used solely for Manual Order Events).
<https://www.finra.org/rules-guidance/rulebooks/finra-rules/6820>

The US requirement is 500× looser than the EU HFT row. Configuring a US-only stack at
100 µs buys no compliance and manufactures alerts.

## 2. linuxptp behaviour (sourced from the man pages)

From `ptp4l(8)` and `phc2sys(8)` (linuxptp), Ubuntu noble manpages:
<https://manpages.ubuntu.com/manpages/noble/man8/ptp4l.8.html> ·
<https://manpages.ubuntu.com/manpages/noble/man8/phc2sys.8.html>

| Item | Documented behaviour |
|---|---|
| `-H` | Select hardware time stamping. **This is the default.** All ports must attach to the same PHC. |
| `-S` | Select software time stamping. Opt-in, and unusable for a microsecond-scale claim. |
| `-s` | Enables `clientOnly` mode. The older `slaveOnly` option is deprecated and slated for removal. |
| `-2` | Select the IEEE 802.3 network transport. **Not the default** — `network_transport` defaults to `UDPv4`. |
| `message_tag` | "The tag which is added to all messages printed to the standard output or system log." Any log parser must tolerate arbitrary text between the daemon timestamp and the keyword. |
| `summary_interval` | Prints RMS offset, maximum absolute offset, frequency mean/stddev and path delay mean/stddev instead of per-sample lines. Summary lines carry **no servo state**. |
| `phc2sys -w` | "Wait until ptp4l is in a synchronized state. If the `-O` option is not used, also keep the offset between the sink and source times updated according to the `currentUtcOffset` value obtained from ptp4l." |
| `phc2sys -O` | Sets the sink-to-source offset in seconds explicitly. |
| `phc2sys -u` | Number of clock updates included in summary statistics. |

**The TAI/UTC consequence.** The PTP timescale does not apply leap seconds. Without `-w` or
`-O`, `phc2sys` disciplines `CLOCK_REALTIME` onto TAI, leaving it a whole number of seconds
from UTC (37 s at the time of writing) while all offset telemetry reads nominal. Offset
telemetry cannot detect this class of fault; only an out-of-band UTC comparison can.

**Log field units and states**, as reproduced in the Red Hat and SUSE system tuning guides:
<https://doc.opensuse.org/documentation/leap/tuning/html/book-tuning/cha-tuning-ptp.html>

- `master offset` and `path delay` are in **nanoseconds**; `freq` is in parts per billion.
- Servo states on the offset line: `s0` unlocked, `s1` clock step, `s2` locked.
- IEEE 1588 **port** states are separate and appear on transition lines:
  `INITIALIZING`, `LISTENING`, `UNCALIBRATED`, `SLAVE`, `PRE_MASTER`, `MASTER`, `PASSIVE`,
  `FAULTY`, `DISABLED`. IEEE 1588-2019 replaces `MASTER`/`SLAVE` with
  `TIME_TRANSMITTER`/`TIME_RECEIVER`; the manager accepts both spellings and fails closed on
  anything it does not recognise.
- **`path delay` can be negative.** Reported negative path delays trace to the correction
  applied to the `t4` timestamp and indicate hardware or driver timestamp problems — the
  sample is diagnostic, not noise, and must not be discarded by the parser.

## 3. PTP profiles — the transport is not a free choice

There is no single "finance" PTP profile. The transport, domain, delay mechanism and
message rates must match the grandmaster:

- **Enterprise Profile — RFC 9760** (May 2025), "Enterprise Profile for the Precision Time
  Protocol with Mixed Multicast and Unicast Messages". Transport is **UDP over IPv4 or
  IPv6**; Sync and Announce are multicast, Delay_Req may be multicast or unicast; the
  **End-to-End** delay measurement method MUST be used; default Sync, Announce and
  Delay_Req rates are once per second; time receivers must support both one-step and
  two-step. Multiple domains are recommended for redundancy against a faulty grandmaster
  reporting as healthy. <https://www.rfc-editor.org/rfc/rfc9760.txt>
- **ITU-T G.8275.1** (telecom, full timing support) uses **Ethernet/L2 multicast only**, no
  IP layer, with two-way delay request/response mandatory and no unicast.
  <https://www.itu.int/en/ITU-T/studygroups/2022-2024/15/Documents/flyers/Flyer_ITU-T_G.8275.1-.2.pdf>

Consequently `-2` is correct for a G.8275.1 grandmaster and **wrong** for an Enterprise
Profile one. Read the grandmaster's profile before choosing.

## 4. Figures this skill does *not* source

Treat these as engineering targets to be measured, never as standards:

- **Sub-microsecond or sub-100 ns host offsets.** Achievable on good hardware over a short,
  symmetric path, but not published by anyone as a requirement. The default
  `target_hft_offset_ns` of 1000 ns is an internal design target with no regulatory basis;
  set it from your own measured offset distribution.
- **Path asymmetry budget.** PTP assumes a symmetric path and cannot observe asymmetry;
  half of any asymmetry appears as a fixed offset error invisible to the servo. Characterise
  it by measurement against a co-located reference.
- **`max_sample_age_s`.** A function of your configured log interval and how long a gap you
  are willing to call healthy. There is no generic value, which is why it is `None` by
  default and why leaving it unset is a documented blind spot rather than a safe default.
- **The alarm point below the ceiling.** The regulatory number is where you are already
  non-compliant. The alarm must sit below it by at least the drift accumulated over your
  detection-and-halt latency.
