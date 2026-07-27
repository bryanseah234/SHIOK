# Postal Universe Source Policy

Last updated: 2026-07-27.

The project still does not have an authoritative free all-Singapore postal-code
download from SingPost or SLA. `pipeline.postal_universe` therefore builds
explicit universe candidates and records source provenance instead of hiding the
coverage tradeoff.

## Modes

| Mode | Sources | Intended Use |
| --- | --- | --- |
| `official_current` | Current HDB Existing Building + current SLA Dwelling Information + current OSM `addr:postcode` inside Singapore planning-boundary bbox | Conservative current open-data baseline |
| `candidate_full_registered` | `official_current` + OneMap-derived 2020 dump + ACRA registered/live postals | Recommended candidate for human review before full batch |
| `candidate_full_all` | `official_current` + OneMap-derived 2020 dump + all ACRA postals, including deregistered entities | Coverage stress test, not recommended as default |

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
- OneMap API brute-force enumeration remains forbidden. The only acceptable
  OneMap API use for remaining gaps is bounded geocoding of source-derived
  postals at the ratified 0.5 req/s throttle.
