# Launch Status - 2026-08-02

Production URL: https://sgshiok.vercel.app/
Vercel project: `theprawnvercel/sgshiok`
Root directory: `web`

## Current Live State

- Live bundle: `generated_20260801_direct_bus_all_targeted`
- Live bundle manifest: HTTP 200
- Record count: 124,032
- Latest pushed commit: `d19e340` (`feat: add exposed bus stop access connectors`)

## Local Bundle State

- Active local bundle retained: `generated_20260801_direct_bus_all_targeted`
- No inactive/pending local bundle is currently retained after cleanup.
- Earlier local pending bundle `generated_20260801_165500` was not deployed and has been removed from `web/public/data/` to save disk space.
- Targeted QA bundle `generated_20260802_bus_connector_targeted` was generated and validated for evidence, then removed from `web/public/data/` to save disk space.
- Any future data release must regenerate or recreate a validated local bundle before direct deploy.

## Active Bundle Counts

- Bundle: `generated_20260801_direct_bus_all_targeted`
- State counts: 112,880 `SCORED`, 1,449 `SCORED_PARTIAL`, 9,384 `NO_TRANSIT_IN_RANGE`, 319 `NOT_YET_SCORED`
- Static validation: 124,032 indexed postals, 114,329 geometry postals, 114,329 geometry postals with route segments, 6,011 transit features
- Transit POIs refreshed locally with official LTA DataMall Train Station Codes
  workbook: 774 features now have station codes; 182 of 190 station centroid
  features have station codes. `MAYFLOWER MRT STATION` is now enriched as
  `TE6`, `Thomson-East Coast Line`.

## Bus Connector Targeted Refresh Evidence

- Evidence bundle: `generated_20260802_bus_connector_targeted`
- Source bundle: `generated_20260801_direct_bus_all_targeted`
- Selected records: 1,449 prior direct-bus fallback partial records.
- Patched records: 1,449.
- State counts after targeted refresh: 112,944 `SCORED`, 1,384 `SCORED_PARTIAL`, 9,385 `NO_TRANSIT_IN_RANGE`, 319 `NOT_YET_SCORED`.
- State transitions: 64 `SCORED_PARTIAL` -> `SCORED`; 1,384 remained `SCORED_PARTIAL`; 1 became `NO_TRANSIT_IN_RANGE`.
- Route evidence after targeted refresh: 42 best routes use `sheltered_with_bus_stop_access_connector`; 51 records contain connector evidence.
- Static validation passed before cleanup: 124,032 indexed postals, 114,328 geometry postals, 114,328 geometry postals with route segments, 6,011 transit features.
- Report: `qa/targeted_bundle_refresh_generated_20260802_bus_connector_targeted.json`.
- Converted-postal list: `qa/bus_connector_converted_postals_20260802.txt`.
- Boundary regression found and fixed in code: postal `557323` moved from
  `SCORED_PARTIAL` to `NO_TRANSIT_IN_RANGE` because current source coordinates
  measured bus stop `66309` at 303.0 m, just outside the strict 300 m direct bus
  radius. The scoring engine now applies an explicit 5 m coordinate-noise
  tolerance and records true distance plus radius/tolerance provenance.
- Verification: `qa/score_557323_island_20260802.json` scores `557323` as
  `SCORED_PARTIAL`, total 54.2, direct bus fallback to `Blk 112`, measured
  303.0 m, policy radius 300.0 m, selection radius 305.0 m.

## Prepared Next Postal Universe

- URA No of Dwelling Units is now wired as an official postal-universe source.
- Prepared candidate universe: 124,443 records.
- Ready to score after bounded OneMap geocode: 123,967.
- Remaining unresolved source-derived postals: 476 `NOT_YET_SCORED`.
- Bounded geocode status: 575 queued, 99 filled, 476 not found, 0 latest HTTP requests from cache.
- URA sample score QA: 200 sampled URA-backed postals, 186 `SCORED`, 1 `SCORED_PARTIAL`, 13 `NO_TRANSIT_IN_RANGE`.
- Not live yet: requires full rescore/export/deploy after the current pending bundle deployment is resolved.

## Verified Checks

- Python tests: 200 passed
- Web tests: 31 passed
- Fresh-bundle web build: passed
- Lighthouse accessibility: 100
- Routed browser smoke for 560234: passed
- Multi-postal keyboard browser smoke for 560231, 560234, 570234: passed
- No-transit keyboard browser smoke for 567754: passed
- Not-yet-scored keyboard browser smoke for 000104: passed
- Route-compare browser smoke for 560109: passed
- Release helper safe-plan mode: passed
- Feedback-route drawing zoom-reset regression guard: passed
- Transit POI popup static-info regression tests: passed
- Pending-bundle readiness artifact `qa/readiness_pending_bundle_20260802_0424.json`: passed
- Mayflower MRT-only browser smoke for 560231: passed
- Local launch-check wrapper full smoke artifact set `20260802_064933`: passed
- Known-postal smoke including 570234: passed
- Postal-universe prep helper: default plan-only guard passed; confirmed prep run passed.
- URA 200-postal sample scoring: passed.
- Launch-check local server cleanup: passed; stale-port guard now chooses a free port and stops child `next start` listeners.
- OneMap walk-validation sample plan: passed; `qa/onemap_validation_sample_2000_20260802.json` contains 2,000 source-backed postal-to-transit samples from 112,880 eligible scored records across 52 areas, projected at 66.7 minutes at 2.0s/request.
- OneMap walk-validation collector dry-run: passed; `qa/onemap_validation_collect_dry_run_20260802.json` queued 2,000 requests, made 0 HTTP requests, and requires explicit `--confirm-onemap-collection` before external calls.
- OneMap walk-validation collection: passed as a collection job; `qa/onemap_validation_collect_report_20260802.json` made 2,000 HTTP requests, wrote 2,000 cache results, and returned `ok=true`.
- OneMap walk-validation evaluation: failed launch gate honestly; `qa/onemap_validation_cached_report_20260802.json` has 1 invalid OneMap zero-distance result, median absolute delta 11.458% vs 10.0% max, and p95 absolute delta 94.037% vs 25.0% max. Cached evaluation command exits 1 because the gate remains failed. Direct-distance sanity: 20 `onemap_materially_shorter_than_direct`, 38 `onemap_slightly_shorter_than_direct`, and 1,941 `plausible`.
- OneMap walk-validation failure classification: report now includes transit-type, direction, area, and top-outlier summaries with start/end coordinates. Bus-stop routes: 1,602 valid rows, median absolute delta 12.816%, p95 98.736%. MRT/LRT routes: 397 valid rows, median absolute delta 6.926%, p95 59.645%. Direction split: 922 project-longer-than-OneMap routes and 1,077 project-shorter-than-OneMap routes.
- Bus-route detour guard: implemented and tested `bus_route_should_use_direct_fallback`; future scoring downgrades implausible graph-routed bus-stop candidates to explicit `direct_bus_fallback_unrouted` partial evidence when direct distance is within 300 m, graph/direct ratio is at least 3.0x, and graph extra distance is at least 100 m.
- Real scoring probes after the guard: `532183` resolves as `SCORED_PARTIAL` direct-bus fallback; `618380` resolves to Lakeside MRT locally while bus remains `NO_TRANSIT_IN_RANGE`. These are local probes, not a shipped bundle refresh.
- Bounded outlier replay: `qa/bus_detour_guard_top_outlier_sample_20260802.json` replays the top 20 bus-stop project-longer validation outliers through current local scoring; 14/20 now expose bus as `direct_bus_fallback_unrouted`, 4/20 choose MRT/LRT as best transit, and 3/20 specifically trigger the new implausible-detour guard.
- Reusable outlier replay helper now defaults to the island graph: on 2026-08-02 its default network was corrected from `processed/network.parquet` to `processed/network_island.parquet`.
- Bus-stop access connector: implemented for implausible bus graph snaps. It searches up to 50 m around the actual DataMall bus stop and accepts only route+connector walks within 300 m, within 2.5x straight-line distance, and within +100 m extra walk. The connector is appended as exposed `bus_stop_access_connector` evidence; it is not counted as shelter.
- Project-longer replay helper: `uv run python run.py onemap-outlier-replay --limit 100 --output qa\onemap_outlier_replay_bus_longer_100_20260802.json` passed locally on `processed\network_island.parquet`. It selected 92 bus-stop/project-longer/>25% rows; current scoring yields 37 best-route direct bus fallbacks, 90 bus-stop best results, 2 MRT/LRT best results, and fallback reasons of 63 `implausible_graph_route_to_datamall_bus_stop_within_direct_radius` / 29 `none`.
- Project-shorter replay helper: `uv run python run.py onemap-outlier-replay --limit 100 --direction project_shorter_than_onemap --node-type any --output qa\onemap_outlier_replay_shorter_100_20260802.json` passed locally on `processed\network_island.parquet`. It selected 100 project-shorter/>25% rows; current scoring yields 9 best-route direct bus fallbacks, 91 bus-stop best results, 9 MRT/LRT best results, and fallback reasons of 26 `implausible_graph_route_to_datamall_bus_stop_within_direct_radius` / 74 `none`.
- Project-shorter route-source profile: `uv run python run.py onemap-outlier-replay --limit 100 --direction project_shorter_than_onemap --node-type any --route-source-profile --output qa\onemap_outlier_replay_shorter_profile_100_20260802.json` passed locally on `processed\network_island.parquet`. Of 100 profiled rows, 9 contain direct-bus fallback, 18 contain inferred HDB, 28 contain OSM shelter, 10 contain official LTA shelter, and 6 contain overhead bridge/underpass. The largest source layers are `unknown` 15,559.6 m, `direct_bus_fallback` 1,896.3 m, `osm_native_covered` 1,388.2 m, and `bus_stop_access_connector` 227.6 m.
- Project-longer route-source profile: `uv run python run.py onemap-outlier-replay --limit 100 --direction project_longer_than_onemap --node-type bus_stop --route-source-profile --output qa\onemap_outlier_replay_bus_longer_profile_100_20260802.json` passed locally on `processed\network_island.parquet`. Of 92 profiled rows, 37 contain direct-bus fallback, 19 contain inferred HDB, 17 contain OSM shelter, 3 contain official LTA shelter, and 2 contain overhead bridge/underpass. The largest source layers are `unknown` 10,986.6 m, `direct_bus_fallback` 5,051.2 m, `osm_native_covered` 1,664.8 m, and `bus_stop_access_connector` 479.0 m.
- OneMap outlier triage queues: `uv run python run.py onemap-outlier-triage --output qa\onemap_outlier_triage_queues_20260802.json --geojson-output qa\onemap_outlier_triage_queues_20260802.geojson --missing-bus-priority-geojson-output qa\onemap_missing_bus_connector_priority_20260802.geojson` passed locally. It read 192 replay rows, enriched them from `qa/onemap_validation_cached_report_20260802.json`, and emitted 37 `missing_bus_connector` cases, 89 `direct_bus_fallback_review` cases, 100 `possible_overpermissive_project_path` cases, 13 `mrt_lrt_outlier` cases, 45 `hdb_bridge_connector_review` cases, and 0 `still_unscored_or_no_best` cases.
- Missing-bus priority worklist: `qa/onemap_missing_bus_connector_priority_20260802.geojson` has 19 strict `missing_bus_connector` line features ranked by largest validation delta. Top rows are `530535`, `417092`, `534317`, `637814`, `320087`, `478983`, `806063`, `601291`, `627662`, and `729761`.
- Bus-connector diagnostic: `uv run python run.py bus-connector-diagnostics --output qa\bus_connector_diagnostics_priority_20260802.json --geojson-output qa\bus_connector_diagnostics_priority_20260802.geojson` passed locally on all 19 priority rows. Current route states are 16 `implausible_detour` and 3 `routable`; diagnostic classes are 15 `alternate_bus_snap_candidate` and 4 `changed_stop_between_validation_and_replay`.
- Temporary-file cleanup: removed reproducible caches, stale ignored web data bundles, and project `__pycache__` folders; retained only the active web bundle `generated_20260801_direct_bus_all_targeted`.

## Next Production Data Command

There is no currently retained pending local bundle to release. For the next
data deployment, first regenerate or recreate a validated bundle, then use:

```powershell
.\scripts\release-data-bundle.bat -DataBundle <validated_bundle> -ConfirmProduction
```

## Not Done

- Regenerate or recreate a validated post-connector bundle before any data deploy.
- Regenerate or recreate the connector-targeted bundle after the bus-radius
  tolerance fix; active production data does not yet include this code-level
  correction.
- Full rescore/export/deploy using the URA-expanded 124,443-record universe.
- Run broader keyboard-only and multi-postal mobile QA after activation.
- Work through the remaining `qa/bus_connector_diagnostics_priority_20260802.json` / `.geojson` rows after the general exposed bus-stop access connector fix.
- Use the reusable outlier replay helper after the next score/export bundle to check whether bus-stop validation outliers shrink before any full rescore.
- Resolve the Mayflower 560231/560234 MRT shelter false-negative with source-backed connector evidence or owner-approved audited correction.
- Close the canonical ~140k postal universe only with a licensed/permitted source.
