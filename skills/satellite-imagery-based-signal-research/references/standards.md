# Standards for Satellite Imagery Based Signal Research

## Directional sign convention

The direction to take **in the traded instrument** when the observed metric prints
high relative to its point-in-time baseline. A low print inverts the sign. These
are supply/demand conventions, not forecasts — they say nothing about what the
market already expects.

| Domain | Raw metric | Economic reading of a high print | Direction on high print |
|---|---|---|---|
| Retail parking | Vehicle count / lot occupancy | More footfall → more revenue | Long the retailer (+1.0) |
| Oil storage | External floating-roof fill fraction | Inventory build → more supply | Short crude (−1.0) |
| Agriculture | NDVI composite | Greener canopy → larger harvest | Short the crop (−1.0) |

The `±1.5` Z threshold shipped as the engine default is an **engineering
placeholder**, not a validated or standardised constant. Calibrate it against the
measured signal-to-noise of the specific vendor panel.

## Sensor cadence and resolution

Revisit interval bounds how often a signal can genuinely update. Interpolating
between acquisitions is a forecast, not an observation.

| Constellation | Revisit | Resolution |
|---|---|---|
| Sentinel-2 (2 satellites) | 5 days globally; 2–3 days at mid-latitudes | 4 bands @ 10 m, 6 @ 20 m, 3 @ 60 m |
| Landsat 8 / 9 | 16-day repeat per satellite | 30 m |

- Sentinel-2 mission and revisit: <https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html>
- Landsat missions: <https://www.usgs.gov/landsat-missions>

## Cloud and usable-pixel masking

Optical sensors return nothing through cloud. Sentinel-2 **Level-2A** products ship
a **Scene Classification Layer (SCL)** identifying saturated/defective pixels,
cloud shadows, low/medium/high-probability cloud, and thin cirrus; derive
`usable_pixel_fraction` from it rather than from visual inspection.

Source: Copernicus Data Space Sentinel-2 documentation (link above).

## NDVI definition

```
NDVI = (NIR − Red) / (NIR + Red)        range [−1, +1]

Landsat 4–7 : (Band 4 − Band 3) / (Band 4 + Band 3)
Landsat 8–9 : (Band 5 − Band 4) / (Band 5 + Band 4)
```

Higher values indicate denser, healthier vegetation; negative values indicate
water, snow, or cloud. **NDVI saturates over dense canopy** — beyond canopy
closure additional biomass barely moves the index, which is the motivation for
EVI, which USGS describes as correcting for atmospheric conditions and canopy
background noise and as more sensitive in densely vegetated areas.

- NASA Earthdata NDVI: <https://www.earthdata.nasa.gov/topics/land-surface/normalized-difference-vegetation-index-ndvi>
- USGS Landsat NDVI: <https://www.usgs.gov/landsat-missions/landsat-normalized-difference-vegetation-index>
- USGS Landsat EVI: <https://www.usgs.gov/landsat-missions/landsat-enhanced-vegetation-index>

## Floating-roof tank fill estimation

An external floating-roof tank casts two shadows: an **exterior** shadow, which
scales with the tank's full height, and an **interior** shadow inside the rim,
which scales with how far the roof has sunk. Both lengthen and shorten together as
solar elevation changes through the year, so the standard estimator takes their
**ratio**, which cancels the sun-angle dependence:

```
fill fraction ≈ 1 − (interior shadow area / exterior shadow area)
```

The ratio estimator itself is industry practice rather than a formal standard; it
is documented in vendor and practitioner write-ups, e.g. Planet's
*A Beginner's Guide to Calculating Oil Storage Tank Occupancy* —
<https://medium.com/planet-stories/a-beginners-guide-to-calculating-oil-storage-tank-occupancy-with-help-of-satellite-imagery-e8f387200178>.
Treat it as a documented convention, not a validated constant.

The **peer-reviewed** work sits one level down, on measuring tank geometry
precisely: Wang, T.; Li, Y.; Yu, S.; Liu, Y., *Estimating the Volume of Oil Tanks
Based on High-Resolution Remote Sensing Images*, **Remote Sensing 2019, 11(7),
793** — <https://www.mdpi.com/2072-4292/11/7/793>. That method extracts the shadow
by Otsu thresholding in HSV, measures shadow length by a median method with
sub-pixel subdivision positioning to recover tank height, and finds the tank top
and radius by Hough transform. Reported accuracy on their test tanks: absolute
error 416–3,050 m³, relative error **0.38%–2.78%**. Note this yields tank *volume
and geometry*, which is the denominator; converting a roof position into a fill
fraction is the separate step above.

**Coverage caveat:** this method sees external floating-roof tanks only. Fixed-roof
tanks, pipeline fill, and volumes in transit are invisible to it but are counted in
official inventory statistics.

## Benchmark statistic release schedules

The published series a satellite signal is meant to anticipate. Times are the
official release times; front-running value depends on beating these, and on the
reference period each one actually covers.

| Statistic | Agency | Release | Reference period |
|---|---|---|---|
| Weekly Petroleum Status Report | EIA | Wednesdays; summary, overview and Tables 1–14 (CSV/XLS) after **10:30 a.m. ET**, remaining PDF/HTML after **1:00 p.m. ET**. Some holiday weeks delayed one day. | Stocks as of the **previous Friday** |
| Crop Progress | USDA NASS | **4:00 p.m. ET**, first business day of each week, **April 1 – Nov 30** | Reporters respond **as of Sunday** |
| WASDE | USDA | **12:00 p.m. ET (11:00 a.m. CT)**, between the **8th and 12th** of each month | Monthly global S&D forecast |

- EIA WPSR schedule: <https://www.eia.gov/petroleum/supply/weekly/schedule.php>
- NASS Crop Progress: <https://www.nass.usda.gov/Surveys/Guide_to_NASS_Surveys/Crop_Progress_and_Condition/index.php>
- NASS release calendar: <https://www.nass.usda.gov/Publications/Reports_by_Release_Day/index.php>

## Regulatory touchpoints

**Operator licensing (US) — not a consumer obligation.** Commercial imaging
operators are licensed under the Land Remote Sensing Policy Act (51 U.S.C. 60101
et seq.) and 15 CFR Part 960, administered by the NOAA / Office of Space Commerce
Commercial Remote Sensing Regulatory Affairs office, in consultation with DoD and
State. The licensing duty falls on the **operator of the space system**, not on a
firm buying derived data; a data purchaser's obligations run through the vendor
contract instead.

- 15 CFR Part 960: <https://www.ecfr.gov/current/title-15/subtitle-B/chapter-IX/subchapter-D/part-960>
- Office of Space Commerce CRSRA: <https://space.commerce.gov/regulations/commercial-remote-sensing-regulatory-affairs/licensing/>

**Alternative-data provenance.** On 14 September 2021 the SEC settled its **first
enforcement action against an alternative-data provider**, charging App Annie Inc.
and its founder under Exchange Act §10(b) and Rule 10b-5 with misrepresenting how
its data was derived to firms trading on it. App Annie paid a $10 million penalty;
the founder paid $300,000 and accepted a three-year officer-and-director bar. The
conduct charged was the provider's, but the practical lesson for a subscriber is
that representations about **derivation and permitted use** are diligence items,
not marketing copy.

- SEC press release 2021-176: <https://www.sec.gov/newsroom/press-releases/2021-176>
- Administrative proceeding 34-92975: <https://www.sec.gov/enforcement-litigation/administrative-proceedings/34-92975-s>

**Not covered here.** Whether a given dataset constitutes material non-public
information, and whether the vendor's collection breached a duty or a contract, are
outside this skill. See `insider-trading-controls-for-alternative-data-usage` and
`alternative-data-vendor-due-diligence-checklist`.
