# Launch Status - 2026-08-02

Production URL: https://sgshiok.vercel.app/
Vercel project: `theprawnvercel/sgshiok`
Root directory: `web`

## Current Live State

- Live bundle: `generated_20260801_direct_bus_all_targeted`
- Live bundle manifest: HTTP 200
- Record count: 124,032
- Latest pushed commit: pending update after this validation commit

## Pending Fresh Bundle

- Pending bundle: `generated_20260801_165500`
- Local readiness: OK
- Remote manifest: HTTP 404, not deployed yet
- State counts: 112,880 `SCORED`, 1,449 `SCORED_PARTIAL`, 9,384 `NO_TRANSIT_IN_RANGE`, 319 `NOT_YET_SCORED`
- Static validation: 124,032 indexed postals, 114,329 geometry postals, 114,329 geometry postals with route segments, 6,011 transit features
- Transit POIs refreshed locally with official LTA DataMall Train Station Codes
  workbook: 774 features now have station codes; 182 of 190 station centroid
  features have station codes. `MAYFLOWER MRT STATION` is now enriched as
  `TE6`, `Thomson-East Coast Line`.

## Prepared Next Postal Universe

- URA No of Dwelling Units is now wired as an official postal-universe source.
- Prepared candidate universe: 124,443 records.
- Ready to score after bounded OneMap geocode: 123,967.
- Remaining unresolved source-derived postals: 476 `NOT_YET_SCORED`.
- Bounded geocode status: 575 queued, 99 filled, 476 not found, 0 latest HTTP requests from cache.
- URA sample score QA: 200 sampled URA-backed postals, 186 `SCORED`, 1 `SCORED_PARTIAL`, 13 `NO_TRANSIT_IN_RANGE`.
- Not live yet: requires full rescore/export/deploy after the current pending bundle deployment is resolved.

## Verified Checks

- Python tests: 172 passed
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
- OneMap walk-validation evaluation: failed launch gate honestly; `qa/onemap_validation_cached_report_20260802.json` has 1 invalid OneMap zero-distance result, median absolute delta 11.458% vs 10.0% max, and p95 absolute delta 94.037% vs 25.0% max.
- Temporary-file cleanup: removed local browser smoke caches, local Next build cache, obsolete bad OneMap cache, smoke/retry QA JSONs, and temporary probe parquets; retained corrected `raw/validation/onemap_walk_od` validation cache.

## Next Production Command

Run only after the Vercel Hobby quota window resets:

```powershell
.\scripts\release-data-bundle.bat -DataBundle generated_20260801_165500 -ConfirmProduction
```

## Not Done

- Deploy and activate the pending bundle.
- Full rescore/export/deploy using the URA-expanded 124,443-record universe.
- Run broader keyboard-only and multi-postal mobile QA after activation.
- Investigate the failed 2,000-postal OneMap walk-validation gate; collection is complete, but evaluation has `gate_passed=false`.
- Resolve the Mayflower 560231/560234 MRT shelter false-negative with source-backed connector evidence or owner-approved audited correction.
- Close the canonical ~140k postal universe only with a licensed/permitted source.
