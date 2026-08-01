# Comfort Modes and Data QA Backlog

Status: product/data backlog. Keep this file honest: do not mark any mode or
data source as production-real until the source is ingested, hashed, tested, and
visible in artifact provenance.

## Fast Recompute Rule

Route metrics should be computed once per postal/node set, then reused:

- shortest distance
- Shiokest distance
- covered length and ratio
- exposed gaps
- crossings
- bus stops and service headways
- future shade/leaf/building-shadow exposure

User modes must be score permutations over those metrics, not separate route
runs. This keeps Rain + AM, Rain + PM, Sunny + AM, Sunny + PM, and midday
views cheap in the frontend and avoids re-running the island batch for UI-only
weight changes.

When a real source/model change requires all static artifacts to change, use
the guarded parallel helper:

```powershell
.\scripts\full-rescore-production.bat -ConfirmFullBatch -Workers 4
```

Use `-Deploy` only after the bundle validates and should become production.

## Route Geometry Contract

Implemented on 2026-07-30 in code, pending next scored-bundle export:

- Backend route results now expose edge lists for both Shortest and Shiokest.
- Static geometry shards can emit `route_segments.shortest` and
  `route_segments.sheltered`.
- Each segment has encoded geometry, length, and `is_covered`.
- The frontend parser and map layers remain backward-compatible with the
  existing bundle, but future bundles can render covered and exposed portions
  for both route types.

Evidence:

- Focused tests cover routing edge lists, JSON-safe score chunks, export
  segment records, and frontend GeoJSON conversion.
- `qa/partial_resnap_rescore_sample.json` proves a bounded current-bundle
  sample after the 50 m resnap fix: 18 of 32 sampled NO_TRANSIT records now
  score. This is not live until a later bundle rescore/export.

## Mode Matrix

MVP-ready client modes:

- Balanced: current PRD weights.
- Rain + AM: higher rain shelter and bus weight.
- Rain + PM: same scoring shape as Rain + AM until PM bus headway parsing is
  wired.
- Sunny + AM: higher heat comfort weight.
- Sunny + PM: same scoring shape as Sunny + AM until PM bus headway parsing is
  wired.
- Sunny midday: maximum heat comfort weight.

Honesty labels:

- Bus is scheduled frequency, not historical arrival reliability.
- Heat is provisional. The current model uses rain shelter plus NParks greenery
  proxy shade on uncovered segments, weighted by `heat_comfort.shade_proxy_weight`.
  Leaf Area Index and time-of-day building shadow are still future calibration
  work.
- Shelter is rain protection. Tree foliage is shade, not rain shelter.

## Bus as Transit

Current target:

- Include bus stops as transit candidates when they are within 300 m direct
  radius and have scheduled service headway data.
- Reuse the combined postal-to-node route result to compute bus connectivity.
- Keep bus quality dependent on expected wait/service coverage so a weak nearby
  bus stop does not score like an MRT station.

Later refinement:

- Parse DataMall AM peak, PM peak, and off-peak service frequencies separately.
- Add route evidence for bus stop side-of-road access where graph data allows.
- Keep direct-line fallback visible only as "nearby bus stop"; do not invent a
  sheltered routed walk if the graph cannot route it.

Current implementation:

- `generated_20260801_direct_bus_targeted` patches 80 sampled prior
  `NO_TRANSIT_IN_RANGE` records with the direct-bus fallback.
- 67 records now emit `SCORED_PARTIAL` when DataMall has a bus stop with
  service headway evidence within 300 m direct radius but the graph cannot route
  to transit.
- The line geometry is explicitly `direct_bus_fallback_unrouted`; rain, heat,
  and crossing subscores remain null.
- A wider 2026-08-01 bounded QA sample rescored 128 selected records, including
  126 prior `NO_TRANSIT_IN_RANGE` records plus `560231`/`560234` controls.
  Result: 62 of 126 no-transit records converted to `SCORED_PARTIAL`, 64
  remained `NO_TRANSIT_IN_RANGE`, and both controls remained `SCORED`. This is
  evidence for a broader targeted refresh, not a substitute for the full bundle
  rescore.

## Actual Bus Arrivals

DataMall Bus Arrival is live/current data. It does not give historical
reliability by itself.

Current source finding:

- LTA DataMall `v3/BusArrival` provides live bus ETA/load/location evidence and
  is refreshed frequently, but it requires the DataMall `AccountKey`. Do not
  call it directly from browser code.
- Runtime use needs a thin Vercel API route with key secrecy and short caching,
  or a local collector that publishes aggregated static artifacts.
- LTA does not provide open real-time train-arrival data. MRT/LRT can show
  static station/line/exit references and DataMall disruption/crowd datasets,
  but not live train ETA unless a future official source appears.
- MRT first/last train timings are available on official LTA/operator pages,
  but not as a confirmed bulk API. Treat them as manually curated/static unless
  permission and a stable ingestion path are established.

To build actual arrival reliability:

- Run a local collector on this Windows machine.
- Poll selected bus stops at a conservative interval.
- Store timestamped arrivals in local Parquet or SQLite.
- Aggregate by stop, service, direction, day type, and time band.
- Publish only aggregated static artifacts, never a runtime database.

This is not an MVP blocker. It needs days or weeks of collection before it is
product-trustworthy.

Collector entry point:

```powershell
uv run python run.py bus-arrivals collect --stop 54211 --samples 60 --interval-sec 60 --output raw\bus_arrivals\mayflower_54211.jsonl
```

This captures live snapshots only. A later aggregation job must convert those
snapshots into reliability metrics before the frontend can display them.

## Shade and Leaf Coverage

Verified official/static source status:

- NParks Leaf Area Index on data.gov.sg, tracked in `pipeline/config/sources.yaml` as an XLSX calibration dataset.
- NParks LAI is not spatial route geometry. It is useful for future species or
  vegetation calibration, not enough to compute route-level shade by itself.
- LTA Covered Linkway remains the strongest official rain-shelter and covered
  pedestrian geometry source.
- NParks Nature Ways, Park Connector routes/tracks, and Heritage Trees are
  active spatial shade/greenery proxies in Heat Comfort. Current buffers:
  8 m for line features, 6 m for point features. They do not affect Rain
  Shelter.

Expected use:

- Treat Leaf Area Index as calibration evidence only until paired with spatial
  canopy/green-route geometry.
- Do not count trees as rain shelter.
- Current Heat Comfort combines covered paths with NParks proxy shade on
  uncovered path length. Later work can add building shadow by time of day and
  richer canopy geometry.

## Postal Universe

Current honest state:

- The legitimate open/current universe in the shipped bundle is 124,032 records,
  not the target ~140k.
- Remaining expansion should come from a legitimate source, not OneMap brute
  force.
- Other-UEN registered entities are a legitimate incremental source and are
  wired into the candidate universe path, but the 2026-07-31 check found only
  173 net-new registered/live postals over the current production universe.
  They do not solve canonical postal coverage.
- SingPost SGLocate is the most plausible canonical address/postal universe,
  but it is a subscription/commercial data product. Use only if licensed.
- data.gov.sg datasets with postal-code fields can supplement official POI and
  facility postals, but they are not expected to close the full gap alone.

## Missing Network Feature Checklist

Keep these in algorithm/data QA until each is source-backed and regression
tested:

- HDB void decks.
- HDB block-to-block sheltered precinct paths.
- Covered linkway-to-footway connectors.
- Covered overpass, bridge, and underpass endpoint snapping.
- MRT exit snap quality.
- Bus stop side-of-road access.
- Public indoor links and mall links where source-backed.
- Arcades and covered public walkways.
- PCN and park paths.
- Stairs, ramps, lifts, and escalators.
- Barriers, gates, private access, and construction closures.
- Tree canopy / Leaf Area Index.
- Building shadow by time of day.

2026-08-01 AMK/Mayflower user-route audit:

- Reran `qa/user_feedback_amk_20260730T090609Z.json` against
  `processed/network_island.parquet` at both 50 m and 100 m search radii.
- Both radii produced the same 32-segment classification:
  - 22 `covered_evidence_nearby_check_connectivity_or_snap`
  - 6 `hdb_void_deck_evidence_nearby_check_connectivity`
  - 3 `bridge_underpass_evidence_nearby_check_endpoint_snap`
  - 1 `user_marked_exposed_no_shelter_expected`
- For `560231` -> Mayflower MRT Exit 5, the production MRT/LRT route is still
  426 m and 31% sheltered. Best transit correctly chooses a nearer bus stop,
  but that can hide the Mayflower MRT false-negative from the default view.
- Diagnosis: source evidence is nearby; next fix should target graph
  connectivity/snap behavior for covered/HDB/bridge evidence, not a
  postal-specific score override.
- A 2026-08-01 lambda/detour experiment for `560231` -> Mayflower MRT Exit 5
  tested shelter lambda values 2, 5, 10, and 25 with detour budgets 1.25, 1.5,
  2.0, and 3.0. Exit 5 stayed 425.9 m and 31.2% sheltered in every case.
  Therefore the Exit 5 false-negative is not solved by a Max Shelter slider;
  it needs graph/data connectivity work.
- A component-aware rerun of the same AMK feedback audit produced:
  - 19 `covered_evidence_nearby_check_connectivity_or_snap`
  - 8 `hdb_void_deck_component_gap`
  - 2 `hdb_void_deck_evidence_nearby_check_connectivity`
  - 2 `bridge_underpass_evidence_nearby_check_endpoint_snap`
  - 1 `user_marked_exposed_no_shelter_expected`
- For `560231`, the user-marked Mayflower path now exposes specific graph
  breaks rather than a generic shelter-label problem: segment 1 is a 92.9 m HDB
  component gap, segment 2 is a 37.7 m HDB component gap, and segment 6 is a
  128.7 m HDB component gap. This confirms the next work is a source-backed
  HDB/covered-component connector model, not score-weight tuning or a manual
  route override.
- `qa/route_feedback_component_gap_candidates_amk_20260801.geojson` exports the
  component-gap connector candidates as QA-only map lines. It contains 8
  features: 3 for `560231` and 5 for `560225`. Each feature is marked
  `evidence_status=qa_candidate_not_scoring`; these are inspection candidates,
  not production shelter corrections.

## Human Feedback Loop

Add a static-first "Suggest better route" flow:

- User draws the route they actually walk.
- User labels each segment: sheltered, void deck, bridge, underpass, exposed, or
  blocked.
- App exports copyable JSON with postal, destination, waypoints, segment labels,
  and user note.
- Treat the submission as QA evidence only.
- Promote it only through a general model fix or audited correction layer.

No postal-specific score override.
