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

Active shipped bundle `generated_20260801_direct_bus_targeted`

- total score records: 124,032
- `SCORED`: 112,880
- `SCORED_PARTIAL`: 67
- `NO_TRANSIT_IN_RANGE`: 10,766
- `NOT_YET_SCORED`: 319
- targeted direct-bus refresh: 80 sampled prior `NO_TRANSIT_IN_RANGE` postals
  were patched; 67 converted to `SCORED_PARTIAL` with a straight-line bus
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
