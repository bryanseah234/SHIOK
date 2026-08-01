# Postal Universe Source Policy

Last updated: 2026-08-01.

The project still does not have an authoritative free all-Singapore postal-code
download from SingPost or SLA. `pipeline.postal_universe` therefore builds
explicit universe candidates and records source provenance instead of hiding the
coverage tradeoff.

## Modes

| Mode | Sources | Intended Use |
| --- | --- | --- |
| `official_current` | Current HDB Existing Building + current SLA Dwelling Information + current OSM `addr:postcode` inside Singapore planning-boundary bbox | Conservative current open-data baseline |
| `candidate_full_registered` | `official_current` + OneMap-derived 2020 dump + ACRA registered/live postals + Other-UEN registered/live postals | Recommended candidate for human review before full batch |
| `candidate_full_all` | `official_current` + OneMap-derived 2020 dump + all ACRA/Other-UEN postals, including deregistered entities | Coverage stress test, not recommended as default |

## Evidence From 2026-07-27 Local Run

`uv run python run.py postal-universe --mode official_current`

- total unique postals: 29,515
- ready to score: 29,515
- needs geocode: 0
- HDB Existing Building: 13,436 valid unique postals
- SLA Dwelling Information: 1,420 valid unique postals
- OSM `addr:postcode`: 25,629 valid unique postals

`uv run python run.py postal-universe --mode candidate_full_registered --download-missing`

- total unique postals: 124,032
- ready to score: 123,546
- needs geocode: 486
- OneMap-derived 2020 dump: 121,515 valid unique postals from 141,848 address records
- ACRA registered/live: 47,075 valid unique postals, no coordinates

`processed/postal_universe_candidate_full_registered_geocoded_summary.json`

- total unique postals: 124,032
- ready to score after bounded OneMap geocode: 123,713
- needs geocode after bounded OneMap geocode: 319
- bounded OneMap fill: 167 successes from 486 queued postals at 2.0s delay

Active web bundle `generated_20260801_direct_bus_all_targeted`

- total score records: 124,032
- `SCORED`: 112,880
- `SCORED_PARTIAL`: 1,449
- `NO_TRANSIT_IN_RANGE`: 9,384
- `NOT_YET_SCORED`: 319
- targeted direct-bus and wide refreshes: all 1,320 current
  `NO_TRANSIT_IN_RANGE` postals with scheduled bus-stop candidates within
  300 m direct radius were patched to `SCORED_PARTIAL` with a straight-line bus
  estimate and untrusted rain/heat/crossing subscores left null.

`uv run python run.py postal-universe --mode candidate_full_all --download-missing`

- total unique postals: 132,174
- ready to score: 123,546
- needs geocode: 8,628
- ACRA all statuses: 91,816 valid unique postals, no coordinates

## Caveats

- SLA Dwelling Information is an official data.gov.sg/SLA GeoJSON containing
  point records with `POSTAL_CODE`, `HOUSE_BLK_NO`, `STREET_NAME`, dwelling
  type, and unit counts. It adds private-dwelling coverage, but it is not a
  complete all-address universe.
- The OneMap-derived 2020 dump is a third-party repository snapshot:
  `https://github.com/xuancong84/singapore-address-heatmap`. Its README states
  that it was retrieved from OneMap on 10 Jun 2020 and is governed by the OneMap
  Open Data Licence. It is stale and must be explicitly accepted before a full
  production batch uses it.
- ACRA postal codes are registered-entity addresses. They are legitimate open
  data, but they are not a delivery-point universe. `registered/live` is safer
  than `all`; `all` includes deregistered entities.
- Other-UEN registered entities are legitimate open data and can add a small
  number of candidate entity-address postals. A 2026-07-31 source check found
  7,745 registered/live unique postals in the source, but only 173 net-new
  postals beyond the current 124,032-record production universe. Including
  deregistered rows would add 329 net-new postals. This is useful incremental
  coverage, not the missing ~16k needed to reach a canonical ~140k universe.
- OneMap API brute-force enumeration remains forbidden. The only acceptable
  OneMap API use for remaining gaps is bounded geocoding of source-derived
  postals at the ratified 0.5 req/s throttle.

## Source Scan From 2026-07-28

No new free authoritative all-postal-code source was found.

- HDB Property Information on data.gov.sg still directs users to SingPost for
  postal-code information and notes that charges may apply.
- SingPost advertises the SGLocate address dataset as a business data solution,
  not a free open-data download.
- SLA lists Address Point under licensable digitised land information, not a
  free bulk open-data file.
- Commercial postal-code vendors exist, but they conflict with the $0 budget.
- Community OneMap dumps remain useful evidence for candidate coverage, but they
  are stale third-party snapshots and must stay warning-gated.
- Other-UEN registered entities are open and legitimate, but they are still
  entity-address evidence and do not solve the delivery-point universe.

Therefore the current honest production posture remains: ship the scored
source-derived universe, expose `NOT_YET_SCORED`/missing states honestly, and do
not claim complete ~140k coverage until a legitimate source is accepted.

## 2026-07-31 Decision

Do not block MVP launch on the unsolved ~140k target. Current production should
ship the 124,032-record candidate universe with explicit state counts. The
remaining gap needs either a new official open dataset, a licensed SingPost/SLA
address product, or owner-approved legal rights to another canonical source.
Brute-forcing OneMap remains prohibited.

## 2026-08-01 Review

Parallel source review checked whether Bellingcat OSM Search, OpenInfraMap,
Overpass/Overpass Turbo, OSM extracts, data.gov.sg, OneMap, SLA Address Point,
and SingPost/SGLocate could close the remaining postal-universe gap.

Result: no free, current, authoritative bulk all-postal source was identified.
The ~140k target should not be claimed until SLA Address Point, SingPost/SGLocate,
or another canonical source is licensed or explicitly permitted for this
non-commercial use. The current production-safe stance remains 124,032
source-derived records plus honest unresolved states.

## 2026-08-01 Follow-up Source Check

Current public-source search still supports the same launch stance:

- SLA lists Address Point under licensable digitised land information, not a
  free bulk open-data download:
  `https://www.sla.gov.sg/geospatial/digitised-land-information/`
- SingPost presents SGLocate as a business data solution / API for standardised
  Singapore postal addresses, not a free static dataset:
  `https://www.singpost.com/business/promote-your-products/data-solutions`
- HDB Property Information on data.gov.sg still tells users to approach
  SingPost for postal-code information and notes that charges may apply:
  `https://data.gov.sg/datasets/d_17f5382f26140b1fdae0ba2ef6239d2f/view`
- data.gov.sg search results for "postal code" expose useful agency-specific
  datasets, but not a canonical all-delivery-point postal universe.
- Bellingcat OSM Search, OpenInfraMap, and Overpass remain useful OSM QA tools,
  not independent postal-universe sources.

Decision remains unchanged: ship the 124,032-record source-derived universe
honestly, do not brute-force OneMap, and treat full canonical coverage as a data
licensing/source-acquisition problem rather than an engineering shortcut.

## 2026-08-01 Overture Addresses Candidate Probe

Overture Maps Addresses is the strongest new no-cost candidate found so far,
but it is not a drop-in solution for claiming canonical ~140k postal coverage.

Source:

- Overture Addresses docs list Singapore at 142,210 address rows in the July
  2026 coverage table.
- Release path probed locally:
  `s3://overturemaps-us-west-2/release/2026-07-22.0/theme=addresses/type=address/*`
- Overture marks the Addresses theme Alpha and documents address IDs as
  unstable; source attribution for SG samples is
  `OpenAddresses/Singapore Land Authority`.

Local DuckDB probe on 2026-08-01:

```text
rows=142210
unique_six_digit_postcodes=123883
missing_postcode_rows=0
bbox=(103.61080169677734, 1.2073525190353394,
      104.05919647216797, 1.4701491594314575)
```

Comparison against
`processed/postal_universe_candidate_full_registered_geocoded.parquet`:

```text
overture_unique_postcodes=123883
current_unique_postcodes=124032
intersection=122196
new_from_overture=1687
current_missing_from_overture=1836
```

Reproducible command:

```powershell
uv run python run.py overture-addresses --archive-raw
```

Archived local probe artifact:

```text
raw/cded7259e2c1aedf9c2146d5ae4ae3fb107a6b37e3424257bca929eda20ab5ca/overture_addresses_sg_postcode_candidates.parquet
sha256=cded7259e2c1aedf9c2146d5ae4ae3fb107a6b37e3424257bca929eda20ab5ca
```

Samples of Overture-only postcodes:

```text
018895, 018963, 018970, 019916, 019917, 019921, 019927, 019933,
019960, 019964, 038965, 039953, 039969, 059828, 059829, 059937,
059964, 069834, 069835, 069846
```

Decision:

- Promote Overture Addresses to a reviewed candidate source, not production
  source-of-record yet.
- It can improve QA and add about 1,687 candidate postcodes, but by unique
  postcode count it is slightly smaller than the current source-derived
  universe and misses 1,836 current postcodes.
- Before using it in a score batch, build a gated ingestion step that archives
  the raw SG subset under `raw/<sha256>/`, records query/release/provenance,
  dedupes by six-digit postcode, compares coordinate deltas against current
  records, and sample-validates coordinates.
- Do not claim full canonical ~140k coverage from Overture alone.

Implementation status:

- `uv run python run.py overture-addresses --archive-raw` archives an Overture
  postcode-candidate Parquet with one representative WGS84 coordinate per
  six-digit postcode plus source/provenance fields.
- `uv run python run.py postal-universe --mode candidate_full_registered
  --include-overture-candidate --output tmp\postal_universe_candidate_full_registered_overture_probe.parquet
  --summary tmp\postal_universe_candidate_full_registered_overture_probe_summary.json`
  builds an optional probe universe without touching production defaults.

Optional Overture-inclusive probe result on 2026-08-01:

```text
total_unique_postals=125876
ready_to_score=125400
needs_geocode=476
overture_addresses_sg_candidate_source_only=1671
```

Coordinate QA against the current geocoded universe:

```text
overlap_with_current_coordinates=122195
delta_m_p50=1.4
delta_m_p95=23.5
within_50m=120531
within_100m=121713
over_100m=482
over_250m=126
over_1000m=41
largest_delta=26004.4m at postcode 079000
largest_delta_current_source=osm_addr_postcode
largest_delta_overture_source=OpenAddresses/Singapore Land Authority
```

This is useful progress, but still not full ~140k coverage. The coordinate
distribution is strong enough to keep Overture as a serious candidate for
targeted validation and future universe expansion, but the outliers prove it
must remain gated until the largest coordinate mismatches are reviewed and any
production inclusion is followed by a rescore.

Review artifact:

```text
qa/overture_coordinate_outliers_20260801.geojson
qa/overture_addresses_sg_candidate_report_20260801.json
```

The GeoJSON contains 482 `coordinate_outlier_review_not_scoring` LineString
features from current-coordinate point to Overture-coordinate point for every
postcode whose Overture representative coordinate differs from the current
universe coordinate by more than 100 m. This is a QA layer only; it does not
change scoring.

## 2026-08-02 Source Refresh

Current public-source search still did not identify a free authoritative
all-delivery-point postal universe:

- SingPost SGLocate documentation describes postcode/street/block address
  search APIs and enhanced address details, with additional charges possible:
  `https://www.sglocate.com/documentations.aspx`
- SingPost business data pages describe SGLocate Dataset as a business data
  solution containing standardised Singapore postal addresses:
  `https://www.singpost.com/business/promote-your-products/data-solutions`
- SLA continues to list Address Point under licensable digitised land
  information, alongside Street Directory, cadastral, road-network, and building
  outline data:
  `https://www.sla.gov.sg/geospatial/digitised-land-information/`
- data.gov.sg search exposes agency-specific datasets with postal-code fields,
  but not a canonical all-address/postal-code table:
  `https://data.gov.sg/datasets?coverage=&query=postal+code`
- Third-party/commercial postal-code vendors exist, but they do not satisfy the
  $0/open-source production constraint.

Decision unchanged: the launchable universe remains the 124,032 source-derived
records with explicit `SCORED`, `SCORED_PARTIAL`, `NO_TRANSIT_IN_RANGE`, and
`NOT_YET_SCORED` states. The ~140k canonical target needs a licensed/permitted
SingPost/SLA-equivalent source or a new official open release; it should not be
closed with OneMap brute force.
