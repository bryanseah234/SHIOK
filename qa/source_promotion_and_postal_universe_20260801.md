# 2026-08-01 Source Promotion + Postal Universe Review

## Question

- Can Bellingcat OSM Search, OpenInfraMap, or extra Overpass pulls become
  separate production ingestion sources?
- Can the project honestly close the full ~140k Singapore postal universe now?

## Findings

### OSM Discovery Tools

| Source | Decision | Reason |
| --- | --- | --- |
| Bellingcat OSM Search | QA/discovery only | Search UI over OSM. No unique source dataset. Hosted dependency would weaken deterministic builds. |
| OpenInfraMap | QA/discovery only | Rendered thematic OSM map, mostly infrastructure tags. Useful for inspection, not a shelter/postal feed. |
| Overpass / Overpass Turbo | QA-only by default | Useful for bounded OSM query development. Public service is not a production batch/runtime dependency. |

Production OSM evidence should continue to come from the hashed local OSM PBF.
If a bounded Overpass response is promoted, archive the exact query, response,
timestamp, `osm_base`, and SHA256 under `raw/` before ingestion.

### Postal Universe

Current production-safe universe remains 124,032 source-derived postals.

- 123,713 have usable coordinates.
- 319 remain `NOT_YET_SCORED` after bounded OneMap geocoding.
- Active bundle `generated_20260801_no_transit_wide_targeted` emits:
  - 112,880 `SCORED`
  - 129 `SCORED_PARTIAL`
  - 10,704 `NO_TRANSIT_IN_RANGE`
  - 319 `NOT_YET_SCORED`

The commonly cited ~140k figure is not currently reproducible as unique,
current, legally usable postals under the project constraints. The third-party
OneMap-derived 2020 dump has 141,848 raw address rows but only 121,515 unique
valid postal codes after dedupe.

Sidecar review on 2026-08-01 confirmed that the ~140k target likely mixes raw
address records with unique postal codes. No reviewed source closed the
remaining gap under the $0/non-commercial constraints.

## Decision

Do not add separate production ingestion for Bellingcat, OpenInfraMap, or hosted
Overpass today. Keep them as research/QA inputs unless a concrete archived raw
artifact is accepted.

Do not block production on the unresolved ~140k target. Ship the 124,032-record
source-derived universe honestly, with explicit `NOT_YET_SCORED` and
`NO_TRANSIT_IN_RANGE` states.

## Human Actions To Close True Canonical Coverage

1. Ask SLA/GeoWorks for no-cost non-commercial permission to use SLA Address
   Point for S.H.I.O.K. and publish derived comfort scores.
2. Ask SingPost/SGLocate for free non-commercial dataset access or derived-score
   redistribution rights.
3. Ask data.gov.sg/SLA whether Address Point or an equivalent address-point
   dataset can be released under the Singapore Open Data Licence.

Without one of those, full canonical ~140k coverage remains blocked by source
availability, not code.
