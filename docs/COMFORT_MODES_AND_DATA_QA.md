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

Implemented in code and active in the current
`generated_20260801_direct_bus_all_targeted` bundle:

- Backend route results expose edge lists for both Shortest and Shiokest.
- Static geometry shards emit `route_segments.shortest` and
  `route_segments.sheltered`.
- Each segment has encoded geometry, length, and `is_covered`.
- The frontend parser and map layers remain backward-compatible with the
  older bundles, while the current active bundle can render covered and exposed
  portions for both route types.
- `uv run python run.py readiness` now reports
  `geometry_postals_with_route_segments=114329`, matching all active geometry
  postals.
- `qa/route_geometry_contract_560231_20260802.md` confirms the pending bundle
  has shortest and Shiokest segment arrays for `560231` in both Best Transit and
  MRT/LRT modes. The remaining Mayflower issue is a data/model gap, not a
  missing route-geometry export.

Evidence:

- Focused tests cover routing edge lists, JSON-safe score chunks, export
  segment records, and frontend GeoJSON conversion.
- `qa/partial_resnap_rescore_sample.json` proves a bounded current-bundle
  sample after the 50 m resnap fix: 18 of 32 sampled NO_TRANSIT records now
  score. This is not live until a later bundle rescore/export.

## OneMap Walk Validation Gate

PRD 2/12 requires a 2,000-postal stratified OneMap walk-routing comparison
before claiming the routing-distance launch gate.

Current implementation status:

- `uv run python run.py onemap-validation plan` builds a deterministic
  cache-first sample from the exported score and geometry shards.
- The planner derives start/end points from the same shortest-route geometry
  the frontend displays, records per-postal cache keys, and does not call
  OneMap.
- `uv run python run.py onemap-validation collect` is implemented as a
  resumable cache writer, but it refuses external calls unless
  `--confirm-onemap-collection` is provided.
- `qa/onemap_validation_sample_2000_20260802.json` was generated for
  `generated_20260801_165500`: 2,000 samples from 112,880 eligible scored
  records across 52 areas.
- At the ratified OneMap throttle of 2.0 seconds/request, collecting the full
  comparison is projected at 66.7 minutes before retry/backoff overhead.
- `qa/onemap_validation_cached_report_20260802.json` currently has 0 cached
  OneMap walk results, so `gate_passed=false`.

Next external-API collection step, when intentionally scheduled:

```powershell
uv run python run.py onemap-validation collect --sample qa\onemap_validation_sample_2000_20260802.json --output qa\onemap_validation_collect_report_20260802.json --confirm-onemap-collection
```

Then evaluate the collected cache:

```powershell
uv run python run.py onemap-validation evaluate --sample qa\onemap_validation_sample_2000_20260802.json --output qa\onemap_validation_cached_report_20260802.json
```

Do not treat this launch gate as passed from local route distances alone.

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
- A clean current-bundle replay audit on
  `generated_20260801_no_transit_wide_targeted` with `--replay-limit 4`
  classified all 4 sampled `NO_TRANSIT_IN_RANGE` records as
  `all_candidates_beyond_access_range`, not graph-disconnected. This does not
  clear the full 10,704-record no-transit bucket, but it proves at least some
  remaining cases are access-threshold/product-policy cases rather than missing
  bus/MRT source data.
- A broader 2026-08-01 replay audit on the same bundle with `--replay-limit 80`
  classified 48/80 sampled no-transit records as
  `all_candidates_beyond_access_range` and 32/80 as
  `candidate_graph_disconnected`. Among the disconnected sample, 11 were within
  75 m of a candidate transit component, so a future resnap/model change should
  be tested carefully instead of increasing the snap cap blindly.
- `generated_20260801_direct_bus_all_targeted` then patched all 1,320 current
  no-transit records that already had scheduled bus-stop candidates within the
  300 m direct-radius set. All 1,320 converted to `SCORED_PARTIAL`; active
  bundle counts became 112,880 `SCORED`, 1,449 `SCORED_PARTIAL`, 9,384
  `NO_TRANSIT_IN_RANGE`, and 319 `NOT_YET_SCORED`.
- A corrected post-refresh replay audit with `--replay-limit 80` found the
  remaining no-transit bucket has 0 records with direct bus candidates. The
  sample split was 74 `all_candidates_beyond_access_range` and 6
  `candidate_graph_disconnected`; only 1 of the disconnected sample was within
  75 m of a candidate transit component.

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

Current static transit POI data and remaining live/MRT metadata gaps are recorded
in `qa/transit_poi_status_20260802.md`.

## Shade and Leaf Coverage

Verified official/static source status:

- NParks Leaf Area Index on data.gov.sg, tracked in `pipeline/config/sources.yaml` as an XLSX calibration dataset.
- NParks LAI is not spatial route geometry. It is useful for future species or
  vegetation calibration, not enough to compute route-level shade by itself.
- LTA Covered Linkway remains the strongest official rain-shelter and covered
  pedestrian geometry source.
- NParks Nature Ways, Park Connector routes/tracks, Heritage Trees, and
  Heritage Road Green Buffers are active spatial shade/greenery proxies in Heat
  Comfort. Current buffers: 8 m for line features, 6 m for point features, and
  native polygons for green buffers. They do not affect Rain Shelter.

Expected use:

- Treat Leaf Area Index as calibration evidence only until paired with spatial
  canopy/green-route geometry.
- Do not count trees as rain shelter.
- Current Heat Comfort combines covered paths with NParks proxy shade on
  uncovered path length. Later work can add building shadow by time of day and
  richer canopy geometry.
- Current island QA shade proxy counts are recorded in
  `qa/shade_proxy_status_20260802.md`.

## Postal Universe

Current honest state:

- The legitimate open/current universe in the shipped bundle is 124,032 records,
  not the target ~140k.
- URA No of Dwelling Units is now wired and probed as an official-current
  source. It raises the official-current probe to 105,462 postals and the
  candidate production-mode probe to 124,443 total postals after source merge.
- Bounded OneMap geocode on the URA-expanded candidate fills 99 of 575
  source-derived gaps, producing 123,967 ready-to-score rows and 476 unresolved
  `NOT_YET_SCORED` rows. This has not been full-rescored or shipped yet.
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
- `qa/route_feedback_component_gap_source_audit_amk_20260801.json` audits those
  8 candidates against current network source/provenance evidence using an 8 m
  evidence buffer. Result: 4 `hdb_source_overlap_review`, 1
  `covered_source_overlap_review`, and 3 `insufficient_source_overlap`.
  Critically, the repeated 128.7 m Mayflower gap for `560231` segment 6 and
  `560225` segment 11 is still `insufficient_source_overlap` with only 10.5%
  HDB/covered overlap. Do not promote that connector into scoring without
  stronger source evidence or human-approved audited correction.
- The 2026-08-01 connector audit now emits explicit promotion buckets:
  5 `review_ready_not_scoring` drafts and 3
  `blocked_insufficient_source_overlap_not_scoring` candidates. The review-ready
  draft lines are exported to
  `qa/draft_audited_shelter_corrections_amk_20260801.geojson` with
  `status=needs_owner_review`, so the network builder ignores them unless a
  human verifies the source evidence and changes a reviewed feature to
  `status=approved` in `data/audited_shelter_corrections.geojson`.
- After adding NParks Heritage Road Green Buffers and rebuilding the island
  network, reran the same AMK feedback audit against
  `processed/network_island.parquet`. Results stayed materially the same:
  19 `covered_evidence_nearby_check_connectivity_or_snap`, 8
  `hdb_void_deck_component_gap`, 2
  `hdb_void_deck_evidence_nearby_check_connectivity`, 2
  `bridge_underpass_evidence_nearby_check_endpoint_snap`, and 1 expected exposed
  segment. The connector audit still has 5 review-ready draft corrections and 3
  blocked insufficient-overlap candidates. For `560231`, segment 6 remains
  blocked with only 10.5% HDB/covered overlap, confirming that greenery shade
  data does not solve the Mayflower rain-shelter/void-deck route gap.
- After promoting local-PBF `covered=building_arcade`, `covered=shelter`,
  `covered=roof`, `covered=booth`, and future `covered=canopy` values into
  `pipeline/config/osm_tags.yaml`, rebuilt the island network again. Network QA
  stayed green (`real_disconnection_count_final=0`, 653,107 nodes, 871,566
  edges). AMK feedback audit again stayed materially unchanged: 19 covered
  connectivity/snap checks, 8 HDB component gaps, 2 HDB nearby-connectivity
  checks, 2 bridge/underpass endpoint-snap checks, and 1 expected exposed
  segment. Connector promotion split remained 5 review-ready draft corrections
  and 3 blocked insufficient-overlap candidates. For `560231`, segment 6 remains
  blocked at 128.7 m with only 10.5% HDB/covered overlap. Therefore Bellingcat,
  OpenInfraMap, Overpass, and broader OSM covered-value extraction do not solve
  the Mayflower false-negative; the next fix is still source-backed connector
  geometry or owner-approved audited corrections.
- A targeted AMK rescore comparison against the active bundle selected 18
  records: controls `560231`, `560234`, `560225`, `560700`, `560710` plus prior
  no-transit records. Result: 7 of 13 prior `NO_TRANSIT_IN_RANGE` records became
  `SCORED_PARTIAL` through the existing direct-bus fallback, 6 remained
  no-transit, and all 5 controls stayed `SCORED` with unchanged metrics.
  `560231` still defaults to the nearby bus stop `Opp Mayflower Sec Sch` at
  128.1 m and 100/100; `560234` still defaults to bus stop `Mayflower Sec Sch`
  at 332.4 m, 76.6% covered, 90.6/100. This confirms Best Transit is now
  bus-aware, but it can hide the separate Mayflower MRT shelter-route QA issue.

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

Approved correction workflow:

1. Review a draft file such as
   `qa/draft_audited_shelter_corrections_amk_20260801_osm_covered_values_network.geojson`
   in QGIS/geojson.io against source evidence.
2. Promote only verified `review_ready_not_scoring` features with an explicit
   audit id, reviewer, and evidence note:

```powershell
uv run python scripts/promote_audited_shelter_corrections.py `
  --draft qa/draft_audited_shelter_corrections_amk_20260801_osm_covered_values_network.geojson `
  --approve feedback-560231-segment-1-hdb-source-overlap-review `
  --reviewer owner `
  --evidence-note "Reviewed against source-backed HDB/covered edges on map"
```

3. Rebuild the network, rerun network QA, rerun the AMK route-feedback audit,
   then run a targeted score refresh before any full batch.

The script refuses blocked insufficient-overlap candidates and keeps approvals
in `data/audited_shelter_corrections.geojson`, which the network builder ingests
only when `status=approved`.

`qa/mayflower_route_qa_summary_20260801.md` is the compact owner-review summary
for the current Mayflower state. It shows current best/bus/MRT route scores for
`560231`, `560234`, and `560225`, plus 5 review-ready connector candidates and
3 blocked candidates that need stronger source evidence.

The 5 review-ready source-backed connector candidates were promoted into
`data/audited_shelter_corrections.geojson` with `status=approved` after owner
approval for autonomous source-audited promotion. The 3
`blocked_insufficient_source_overlap_not_scoring` candidates remain excluded.
Next step is a network rebuild plus AMK targeted refresh to measure the real
route effect; no score override has been applied.
- After rebuilding the island network with those approved corrections,
  `qa/conflation_qa_island.json` stayed green with
  `real_disconnection_count_final=0`, `approved_features=5`, `added_edges=5`,
  and `covered_edge_length_m_audited_corrections=200.2`.
- A targeted Mayflower refresh wrote
  `generated_20260801_mayflower_approved_corrections_targeted` for `560231`,
  `560234`, `560225`, `560700`, and `560710`. It validated cleanly and changed
  no global state counts. `560231` still shows Mayflower MRT/LRT at 425.9 m and
  31.2% covered; `560234` improves the Mayflower MRT/LRT shortest route to
  545.6 m while remaining 52.3% covered. The false-negative is therefore
  narrowed but not solved.
- A broader local targeted refresh then selected all 1,920 known, ready-to-score
  postals within a 1 km EPSG:3414 buffer of the 5 approved correction lines and
  wrote `generated_20260801_mayflower_1km_approved_corrections_targeted`.
  Validation passed with 124,032 indexed postals, 114,329 geometry postals,
  114,329 geometry postals with route segments, 5,208 bus stops, 613 MRT exits,
  and 190 MRT/LRT stations. Readiness against this bundle reports no freshness
  warning because the bundle manifest is newer than `processed/network_island.parquet`.
  The state counts remain 112,880 `SCORED`, 1,449 `SCORED_PARTIAL`, 9,384
  `NO_TRANSIT_IN_RANGE`, and 319 `NOT_YET_SCORED`. It is ready for direct
  deploy after Vercel Hobby deployment quota resets, but it is not the Git
  default until that first data deploy succeeds.
- In the 1 km refresh, `560231` and `560234` still default to nearby bus stops
  (`Opp Mayflower Sec Sch` and `Mayflower Sec Sch`) rather than the Mayflower
  MRT evidence route. This confirms the default product behavior is bus-aware,
  while the MRT-specific 560231/560234 shelter-route false-negative remains a
  separate data/model QA issue.

2026-08-02 remaining no-transit replay:

- `qa/no_transit_replay_top_areas_20260802.json` reran an 80-postal sample from
  the pending `generated_20260801_165500` bundle against the current routing
  graph.
- Classification counts: 74 `all_candidates_beyond_access_range`, 6
  `candidate_graph_disconnected`.
- All 9,384 remaining `NO_TRANSIT_IN_RANGE` records in the pending bundle have
  zero direct bus candidates within 300 m, so the remaining population is not an
  obvious direct-bus fallback miss.
- Interpretation: most sampled remaining no-transit records are reachable but
  beyond the current 1.2 km transit-access cutoff. The next product decision is
  whether to keep them as explicit `NO_TRANSIT_IN_RANGE` or add a low/zero-credit
  scored state for far-but-reachable transit. The 6 disconnected samples still
  need targeted geometry QA before any broad resnap/connector change.
