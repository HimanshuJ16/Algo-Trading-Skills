# Standards and Evidence for Cross-Market Link Arbitration

**No standards body publishes a propagation constant, an availability target, or a
failover threshold for financial trading links.** The values below are engineering
figures traceable to the sources named against them, not mandates. Every threshold
in this skill's engine is configurable for that reason, and the two that would
require inventing a number (`min_snr_db`, `max_telemetry_age_s`) default to
disabled rather than to a fabricated default.

## Propagation constants

| Quantity | Value | Source | Status |
|---|---|---|---|
| Speed of light in vacuum | 299,792.458 km/s | SI definition of the metre | Exact by definition |
| Radio refractive index of air | $n = 1.000315$ from $N_0 = 315$ N-units | ITU-R P.453-14, average sea-level radio refractivity | Reference value; varies with temperature, pressure and water vapour |
| Microwave propagation | 299,698.05 km/s (3.336692 µs/km) | Derived: $c / 1.000315$ | Engineering default |
| Fiber group index, G.652 SMF | $n_g = 1.4682$ at 1550 nm (1.4677 at 1310 nm) | Corning SMF-28e product specification, "Effective Group Index of Refraction" | Vendor-specified typical value |
| Fiber propagation, G.652 | 204,190.48 km/s (4.897388 µs/km) | Derived: $c / 1.4682$ | Engineering default |
| Fiber propagation, G.655 NZ-DSF | 203,940 km/s ($n = 1.470$) | Bozkurt et al. (2018), citing Corning WP8080 | Alternative fiber type |
| Fiber propagation, ultra-low-latency | 205,056 km/s ($n = 1.462$) | Bozkurt et al. (2018), citing Corning WP8080 | Alternative fiber type |

The radio index differs from the *optical* index of air (1.000273) because radio
refractivity carries a water-vapour term. Over a 1,186 km corridor the two differ
by 0.06 µs — far below the equipment terms that dominate — but the radio value is
the correct provenance for a radio link. The sibling skill
`co-location-provider-selection-and-network-topology` uses $n_{\text{air}} = 1.0003$;
the resulting figures agree to well under a microsecond on this corridor.

Fiber defaults to G.652 — the *fastest* of the three common types — because that is
the conservative choice when quoting a microwave advantage: it understates the
advantage rather than flattering it.

## Corridor reference geometry

| Path | Figure | Source |
|---|---|---|
| CME Aurora → Equinix NY4 (Secaucus), geodesic | 1,186 km | Bhattacherjee et al., IMC '20, Table 2 |
| CME Aurora → NYSE (Mahwah), geodesic | 1,174 km | Bhattacherjee et al., IMC '20, Table 2 |
| CME Aurora → Nasdaq (Carteret), geodesic | 1,176 km | Bhattacherjee et al., IMC '20, Table 2 |
| Spread Networks Chicago–NY fiber, glass length | 1,328 km (825 route miles) | Spread Networks network map, via Bozkurt et al. (2018) |
| Same route, ground path excluding slack | 1,253 km | Bozkurt et al. (2018), §"Slack loops and tube design" |
| Implied slack fiber | ~6% | Bozkurt et al. (2018) |

Geodesic 1,176 km → 1,328 km of glass is **+12.9%**, decomposing into route
sinuosity and slack. Installation guidance cited by the same paper recommends slack
loops "at least totaling 5% of the cable length."

## Published latency figures

| Figure | Value | Source | Nature |
|---|---|---|---|
| Microwave RTT, Aurora → Secaucus | 8.27 ms | McKay Brothers press release (2012) | Vendor-published, measured |
| Microwave RTT, Aurora → Newark 165 Halsey | 8.23 ms | McKay Brothers press release (2012); The Register (2013) | Vendor-published, measured |
| Microwave one-way, Aurora → Secaucus NY2 | 4.015 ms | McKay Brothers / Quincy Extreme Data (May 2016) | Vendor-published, measured |
| Microwave one-way, Aurora → Carteret | 3.982 ms | McKay Brothers / Quincy Extreme Data (May 2016) | Vendor-published, measured |
| Fiber RTT, Spread Networks ULL dark fiber | 13.1 ms at launch, later 12.98 ms | Spread Networks, via Lightwave / The Register | Vendor-published |
| Modelled one-way, best network CME → NY4 | 3.96171 ms | Bhattacherjee et al., IMC '20 | **Modelled**, excludes tower overhead |
| Modelled one-way, best network CME → Nasdaq | 3.92728 ms | Bhattacherjee et al., IMC '20 | **Modelled**, excludes tower overhead |

The modelled and measured figures are not interchangeable. The IMC '20 model states
plainly that it "does not capture the overheads from signal repetition or
regeneration at towers," so its figures are floors. The measured 2016 figure and the
2020 modelled figure are from different years, networks and methodologies; the gap
between them illustrates *that* an equipment term exists, and is not a calibration
of its size.

## The equipment term

- Competing Chicago–NJ networks are separated by **0.4–8.1 µs** one-way across the
  three destination data centres (Bhattacherjee et al., IMC '20).
- The same paper: Jefferson Microwave has the fewest towers (**22**) on the shortest
  CME–NY4 path, and "if both NLN and JM were using the same radios, and the
  per-tower added latency was higher than 1.4 µs, JM would offer lower end-end
  latency." A propagation-only model cannot rank two carriers on this corridor.
- Data centres connect to the first/last tower over fiber, assumed up to 50 km in
  that study. The radio path is hybrid.
- **This skill supplies no default per-repeater latency.** It comes from the radio
  vendor's datasheet.

## Availability — the actual trade-off

- McKay Brothers co-founder Bob Meade, quoted in The Register (27 Feb 2013): the
  microwave network "was down 1 per cent of the time during trading hours in
  December and January" — i.e. ~99% availability, against Spread Networks' claimed
  **99.999%** for fiber.
- Quincy Data's response in the same article — "Better to be fast 99 per cent of the
  time than slow 99.999 per cent of the time" — is a strategy-design assertion, not
  a network fact. It holds only if the edge survives at fiber latency during the
  weather-correlated 1%.
- Redundancy is measurable. Bhattacherjee et al. compute "alternate path
  availability": Webline Holdings scores 85%/92%/80% against New Line Networks'
  54%/58%/30% on the three CME–NJ paths, "even though WH's latency is higher than
  NLN's by a few microseconds. Thus, in challenging conditions, WH could offer lower
  latencies than NLN."

## Rain fade and frequency

- **ITU-R P.530** (*Propagation data and prediction methods required for the design
  of terrestrial line-of-sight systems*): rain attenuation "can be ignored at
  frequencies below about 5 GHz, but must be included in design calculations at
  higher frequencies, where its importance increases rapidly."
- **ITU-R P.838** (*Specific attenuation model for rain for use in prediction
  methods*): specific attenuation $\gamma_R = k R^\alpha$ dB/km, with $k$ and
  $\alpha$ functions of frequency, polarisation and elevation angle; valid
  1–1000 GHz.
- **ITU-R P.837**: rain rate statistics, the $R$ that feeds P.838.
- **This module implements none of these.** It consumes an SNR threshold you derived
  from them. Embedding a partially transcribed coefficient table would be worse than
  omitting it.
- Chicago–NJ networks differ materially by band: Webline Holdings runs >94% of its
  CME–NY4 shortest-path frequencies under 7 GHz, while New Line Networks is
  primarily 11 GHz (Bhattacherjee et al.). Shorter hops also help — WH's median
  tower-to-tower link is 36 km against NLN's 48.5 km.

## Regulatory and spectrum surface

- **47 CFR Part 101 (Fixed Microwave Services)** governs US fixed point-to-point
  microwave. Licensing is mandatory and applicants must use an FCC-recognised
  frequency coordinator in shared bands such as 6 GHz and 11 GHz. The FCC
  coordinates channel bandwidths up to 60 MHz at 6 GHz and 80 MHz at 11 GHz — the
  constraint behind the serialization term.
- The FCC's April 2020 *Unlicensed Use of the 6 GHz Band* Report and Order permits
  unlicensed operation in the band, protecting incumbent fixed microwave links via
  Automated Frequency Coordination keyed to the Universal Licensing System
  database. Incumbents raised interference-cost concerns during the proceeding.
  Interference is therefore a link-availability risk distinct from weather, and one
  a weather-only failover trigger will not catch — which is why the engine also
  accepts SNR and packet-loss signals.
- Licensed link data is public in the FCC ULS, which is how the IMC '20 study
  reconstructed these networks. Your competitors' paths are not secret; neither
  is yours.

## Sources

1. ITU-R Recommendation P.453-14 (08/2019), *The radio refractive index: its formula and refractivity data* — https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.453-14-201908-I!!PDF-E.pdf
2. ITU-R Recommendation P.530, *Propagation data and prediction methods required for the design of terrestrial line-of-sight systems* — https://www.itu.int/rec/R-REC-P.530-18-202109-S/en
3. ITU-R Recommendation P.838-3, *Specific attenuation model for rain for use in prediction methods* — https://www.itu.int/rec/r-rec-p.838-3-200503-i/en
4. Corning SMF-28e Optical Fiber Product Information (effective group index 1.4677 @1310 nm, 1.4682 @1550 nm) — https://princetel.com/wp-content/uploads/2023/12/SMF28e.pdf
5. D. Bhattacherjee, W. Aqeel, G. Laughlin, B. M. Maggs, A. Singla, "A Bird's Eye View of the World's Fastest Networks," *ACM IMC '20* — https://bdebopam.github.io/papers/imc2020-hft.pdf
6. I. N. Bozkurt et al., "Dissecting Latency in the Internet's Fiber Infrastructure," 2018 — https://arxiv.org/pdf/1811.10737
7. "Microwaves thrash fibre on speed… if you like two-nines uptime," *The Register*, 27 Feb 2013 — https://www.theregister.com/2013/02/27/microwave_versus_fibre/
8. McKay Brothers, "McKay Brothers announces new milestones in low latency networks," PR Newswire — https://www.prnewswire.com/news-releases/mckay-brothers-announces-new-milestones-in-low-latency-networks-189880081.html
9. 47 CFR Part 101, Fixed Microwave Services — https://www.ecfr.gov/current/title-47/chapter-I/subchapter-D/part-101
10. FCC, *Unlicensed Use of the 6 GHz Band*, Report and Order (April 2020) — https://www.federalregister.gov/documents/2020/05/26/2020-11236/unlicensed-use-of-the-6-ghz-band
