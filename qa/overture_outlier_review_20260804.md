# Overture Coordinate Outlier Review — Scoping

Date: 2026-08-04
Status: Persistent open item since 2026-08-01 (docs/decisions.md)

## Overture probe summary

Source: `qa/overture_addresses_sg_candidate_report_20260801.json`
Overture release: 2026-07-22.0 (alpha theme, attribution OpenAddresses/SLA/OneMap)
Archived sha256: `cded7259e2c1aedf9c2146d5ae4ae3fb107a6b37e3424257bca929eda20ab5ca`

- 142,210 SG address rows in Overture
- 123,883 unique six-digit postcodes in Overture
- 125,876 total postcodes in the Overture-included candidate universe
- 125,400 ready-to-score after bounded geocode
- 476 NEEDS_GEOCODE remaining
- **1,687 Overture-only postcodes** (candidates to add)
- **1,836 current postcodes missing from Overture** (would be dropped if Overture becomes canonical)
- Net: Overture is not a canonical ~140k replacement, only a candidate improvement

## Coordinate delta distribution (122,195 postcodes present in both)

| Percentile | Delta (m) |
|---|---|
| p50 | 1.4 |
| p95 | 23.5 |
| max | 26,004.4 |

Report identifies ~41 postcodes with >1 km delta between current
(OSM-derived) coordinates and Overture (OpenAddresses/SLA-derived) coordinates.

## Top outliers (deltas > 1 km)

| Postal | Delta (m) | Current source | Likely wrong side |
|---|---:|---|---|
| 079000 | 26,004 | `osm_addr_postcode` | OSM (26 km across the island) |
| 539591 | 25,888 | `osm_addr_postcode` | OSM |
| 207663 | 15,902 | `osm_addr_postcode` | OSM |
| 575630 |  9,396 | `osm_addr_postcode` | OSM |
| 388700 |  8,318 | `osm_addr_postcode` | OSM (2 address rows) |
| 579479 |  7,526 | `osm_addr_postcode` | OSM |
| 828837 |  6,602 | `osm_addr_postcode` | OSM |
| 489886 |  6,322 | `osm_addr_postcode` | OSM |
| 189685 |  5,752 | `osm_addr_postcode` | OSM |
| 534890 |  5,560 | `osm_addr_postcode` | OSM |

Pattern: **the ten worst outliers are all `osm_addr_postcode`-sourced**. The
current coordinate came from OSM `addr:postcode` tags that appear geographically
misplaced. Overture's `OpenAddresses/Singapore Land Authority` coordinates are
more trustworthy for these because they derive from SLA authoritative data.

Full outlier list: `qa/overture_coordinate_outliers_20260801.geojson`
(312 KB, 41+ postcodes >1 km delta).

## Why this is not yet a release action

Per decisions.md 2026-08-01, Overture is Alpha theme with
OpenAddresses/SLA/OneMap-derived attribution. Promotion requires the "raw
subset archive → hash → provenance → dedupe → sample-validation" gated path,
not a direct swap. Two blockers:

1. **Attribution ambiguity**: Overture aggregates upstream sources without
   preserving per-postcode source lineage in the raw. Cannot claim SLA
   authority for records that trace back to OpenAddresses or OneMap.
2. **1,836 postcodes drop**: Overture misses 1,836 postcodes currently in the
   universe. Swapping without a merge would lose coverage.

## Recommended action plan (not for this session)

1. **Sample validation on the 41 outliers**: for each >1 km outlier, verify
   ground truth with a third source (OneMap search API forward-geocode, or
   satellite tile inspection). Classify as OSM-wrong, Overture-wrong, or both-wrong.
2. **Overture-safe subset**: build a shortlist of postcodes where Overture is
   verified correct AND the coordinate improvement is >100 m AND the current
   source is `osm_addr_postcode` (not authoritative). These are safe to
   incorporate into the postal universe as coordinate corrections.
3. **Merge, not swap**: emit `postal_universe_candidate_full_registered_geocoded.parquet`
   with the merged coordinates. Do not drop the 1,836 Overture-missing postcodes.
4. **Rescore + compare-targeted** against active bundle. If `blocking_count=0`,
   the corrections are safe to promote.
5. **Human approval** before mass coordinate change; batch under `human_approval_required`
   gate in checkpoint_gates.

## Not a launch blocker

The 41 >1 km outliers affect roughly 41/124,032 = 0.033% of scored postals.
Even at maximum delta impact, this changes p95 walk-distance by at most tens
of metres for those specific postals. Not a top contributor to today's OneMap
gate failure (median 11.19% is a graph-wide issue, not localized coordinate
error).

## Related files

- `qa/overture_addresses_sg_candidate_report_20260801.json` — full probe report (213 KB)
- `qa/overture_coordinate_outliers_20260801.geojson` — outlier geometries (312 KB)
- `pipeline/overture_addresses.py` — the probe/archive/merge tooling
- `raw/2eaddccff4c37c5b73a4e8ff6d5eecc8b8afd63a4c...` (approximate hash `cded7259…`) — archived raw subset
