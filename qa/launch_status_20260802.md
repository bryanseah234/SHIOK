# Launch Status - 2026-08-02

Production URL: https://sgshiok.vercel.app/
Vercel project: `theprawnvercel/sgshiok`
Root directory: `web`

## Current Live State

- Live bundle: `generated_20260801_direct_bus_all_targeted`
- Live bundle manifest: HTTP 200
- Record count: 124,032

## Pending Fresh Bundle

- Pending bundle: `generated_20260801_165500`
- Local readiness: OK
- Remote manifest: HTTP 404, not deployed yet
- State counts: 112,880 `SCORED`, 1,449 `SCORED_PARTIAL`, 9,384 `NO_TRANSIT_IN_RANGE`, 319 `NOT_YET_SCORED`
- Static validation: 124,032 indexed postals, 114,329 geometry postals, 114,329 geometry postals with route segments, 6,011 transit features

## Verified Checks

- Python tests: 155 passed
- Web tests: 29 passed
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
- Local launch-check wrapper full smoke artifact set `20260802_044529`: passed
- Known-postal smoke including 570234: passed

## Next Production Command

Run only after the Vercel Hobby quota window resets:

```powershell
.\scripts\release-data-bundle.bat -DataBundle generated_20260801_165500 -ConfirmProduction
```

## Not Done

- Deploy and activate the pending bundle.
- Run broader keyboard-only and multi-postal mobile QA after activation.
- Resolve the Mayflower 560231/560234 MRT shelter false-negative with source-backed connector evidence or owner-approved audited correction.
- Close the canonical ~140k postal universe only with a licensed/permitted source.
