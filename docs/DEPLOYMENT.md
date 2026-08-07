# Deployment

## Normal Production Deploy

Run this from the repo root:

```powershell
.\scripts\deploy-production.bat
```

That wrapper runs:

1. `npm --prefix web test`
2. `uv run python run.py publish --input web/public/data/<current bundle> --deploy --confirm-production`

If `web/node_modules/` was cleaned locally, the production wrappers now run
`.\scripts\ensure-web-deps.bat` automatically before any web test/build step.
You can also run it directly; it performs `npm ci` only when required web
binaries are missing.

`run.py publish` then validates static data, runs `npm audit`, runs `npm run build`, checks Vercel auth, and deploys production.

The current bundle is configured in `web/data-bundle.json`.

## Fast Production Preflight

Before deploying code-only changes or before deciding whether a long full
rescore is worth running, use the non-deploying preflight:

```powershell
.\scripts\preflight-production.bat
```

That wrapper checks:

1. current Git status
2. island network QA gates
3. island network input/hash preflight
4. active static data bundle validation
5. web tests

It does not build a new network, score postals, update `web/data-bundle.json`, or
deploy to Vercel. For a faster UI-only check, skip the network gates:

```powershell
.\scripts\preflight-production.bat -SkipNetworkPreflight
```

For a compact JSON status report that does not run `npm build`, score postals,
or deploy, use:

```powershell
uv run python run.py readiness
```

This reports the active bundle state counts, static artifact validation, island
network QA, batch-plan gates, Vercel root-directory/data strategy, and known
incorporated versus pending data features.

If local cleanup removes the ignored `qa/island_debug.geojson` artifact,
regenerate the compact residual-point debug GeoJSON before readiness:

```powershell
uv run python run.py network-debug
```

## Vercel Git Auto-Deploy

The Vercel project root directory must be `web`.

Project settings currently expected:

- Install Command: `npm ci`
- Build Command: `npm run build`
- Output Directory: `.next`

With that root directory, every push to `main` builds the Next.js app from
`web/`. Code-only deploys are safe because `web/scripts/ensure-data-bundle.mjs`
downloads the configured production data bundle during `next build` when
`web/public/data/<bundle>` is absent.

The same script also materializes derived lookup shards for existing local
bundles: `geom/postal-prefix/*.json.gz`, `transit/h3/*.json.gz`, and gzip
companions for core indexes. This keeps direct local deploys and Git deploys on
the same data contract.

`web/vercel.json` also sets `ignoreCommand` to
`node scripts/ignore-build.mjs`. That means Git commits which do not touch the
`web` project, such as docs-only or QA-evidence commits, are intentionally
skipped by Vercel instead of consuming Hobby build quota. In the Vercel
deployments list, these ignored builds appear as short `Canceled` deployments
with a zero-millisecond build, not as failed builds. Changes under `web/`,
including `web/data-bundle.json`, still trigger a real build.

Only the `sgshiok` Vercel project should be connected to this GitHub repo. On
2026-08-01, the older `shiok` Vercel project was disconnected from
`hongyime/sgSHIOK2026` because it was still creating duplicate failed commit
statuses and burning Hobby deploy quota.

## Why This Exists

`web/public/data/` is generated and intentionally ignored by Git. The current
active bundle is hundreds of MB, so committing it would make GitHub/Vercel
deploys brittle and noisy.

For direct local deploys, Vercel receives the local bundle in `web/public/data/`.

For Git auto-deploys, the bundle is not in the repository. Before `next build`, the web build downloads the configured bundle from the current production site into `web/public/data/`. This makes code-only commits safe as long as `web/data-bundle.json` still points to a bundle already published in production.

## Data Refresh Deploy

After a new score/export batch is generated and validated:

1. Validate the new bundle with `uv run python run.py validate --input
   web/public/data/<bundle>`.
2. Direct-deploy that local bundle first:

   ```powershell
   .\scripts\deploy-production.bat -DataBundle <bundle>
   ```

   The deploy helper stages only the selected bundle and writes matching
   `.vercelignore` rules into the staged source.
3. Inspect the deployment until it is Ready.
4. Activate the bundle only after it is published in production:

   ```powershell
   .\scripts\activate-data-bundle.bat -DataBundle <bundle>
   ```

   This validates the local bundle and confirms the production manifest at
   `https://sgshiok.vercel.app/data/<bundle>/manifest.json` matches before
   writing `web/data-bundle.json`.
5. Run `.\scripts\preflight-production.bat -SkipNetworkPreflight`.
6. Commit and push the source/config/docs changes. The following Git
   auto-deploy can now download the already-published bundle during `next build`.

Do not rely on Git auto-deploy for the first deploy of a brand-new data bundle; the Git build cannot download a bundle that is not published yet.

## Guarded Targeted Refresh

For a candidate targeted report that includes both improvements and regressions,
do not promote the whole report. First extract the safe subset:

```powershell
uv run python run.py compare-targeted `
  --candidate qa\<candidate_report>.json `
  --output qa\<comparison_report>.json `
  --safe-postals-output qa\<safe_postals>.txt
```

Then patch only the safe-postal file:

```powershell
uv run python scripts\targeted_bundle_refresh.py `
  --postal-file qa\<safe_postals>.txt `
  --target-bundle <new_bundle> `
  --output qa\targeted_bundle_refresh_<new_bundle>.json
uv run python run.py validate --input web\public\data\<new_bundle>
.\scripts\launch-check.bat -DataBundle <new_bundle> -SkipPythonTests
```

`targeted_bundle_refresh.py` only reads a partial report when
`--from-partial-report` is explicitly supplied. This prevents a safe-list run
from silently adding unrelated postals from
`qa/partial_resnap_rescore_sample.json`.

On 2026-08-03, this flow produced bundle
`generated_20260803_safe_mayflower_560234_targeted`, patching exactly one
postal: `560234`. Static validation passed, browser smokes passed, direct
production deploy succeeded, the remote manifest was verified, and
`web/data-bundle.json` was activated.

As of 2026-08-03, the active production bundle configured in
`web/data-bundle.json` is
`generated_20260803_safe_mayflower_560234_targeted`. It is a one-postal safe
targeted refresh over `generated_20260802_endpoint_connector_guard_targeted`.
Its manifest has 124,032 score records: 112,913 `SCORED`, 1,414
`SCORED_PARTIAL`, 9,386 `NO_TRANSIT_IN_RANGE`, and 319 `NOT_YET_SCORED`. Do
not run a release command for older bundle names unless that bundle has first
been regenerated or restored and validated locally.

## Vercel Hobby Limits

If Git deploy shows `Deployment rate limited` or direct CLI deploy returns
`api-deployments-free-per-day`, stop deploy attempts until the 24-hour Hobby
quota window resets. Continue committing source changes to `main`; docs-only and
QA-only commits should be skipped by `web/scripts/ignore-build.mjs`, while real
`web/` changes will build after the quota resets.

For the next generated data bundle, deploy it through the bundle-aware helper
rather than raw `vercel deploy`:

```powershell
.\scripts\release-data-bundle.bat -DataBundle <validated_bundle> -ConfirmProduction
```

Before running the production release, use the local launch-check wrapper to run
the repeatable checks without deploying:

```powershell
.\scripts\launch-check.bat -DataBundle <validated_bundle>
```

It runs Python tests, web tests, a fresh-bundle build, readiness, scored/no-score
browser smokes, and a safe release plan. It never passes `-ConfirmProduction`.

Use raw `vercel deploy` only for code-only manual deploys where
`web/data-bundle.json` already points at a bundle that is reachable in
production. New generated bundles must go through `deploy-production.bat`
because it stages the otherwise ignored `web/public/data/<bundle>` directory.

To see the full sequence without deploying:

```powershell
.\scripts\release-data-bundle.bat -DataBundle <validated_bundle> -PlanOnly
```

Omitting both `-PlanOnly` and `-ConfirmProduction` is also safe: the helper
prints the same plan and exits without deploying.

## Lookup Transfer Check

Use this from the repo root to measure a postal lookup against the active local
bundle:

```powershell
npm --prefix web run measure:lookup -- 560234
```

For a generated bundle that is not yet active in `web/data-bundle.json`, set
`SHIOK_DATA_BUNDLE`:

```powershell
$env:SHIOK_DATA_BUNDLE = "<validated_bundle>"
npm --prefix web run measure:lookup -- 560234
Remove-Item Env:\SHIOK_DATA_BUNDLE
```

After derived shards are materialized, Postal 560234 measured 337.8 KB gzipped
for lookup-specific static artifacts, under the 500 KB PRD target. The initial
full transit POI overlay is separate and measured 358.0 KB gzipped.

## Browser Smoke Check

After a local `npm --prefix web run build` and while `npm --prefix web run
start -- -p <port>` is serving the build, run a routed browser smoke test:

```powershell
npm --prefix web run qa:browser -- --url http://127.0.0.1:<port>/ --postal 560234 --out ..\qa\browser_smoke_560234.json --screenshots
```

For a broader no-screenshot launch smoke, run several known postals through the
same browser session:

```powershell
npm --prefix web run qa:browser -- --url http://127.0.0.1:<port>/ --postals 560231,560234,570234 --out ..\qa\browser_smoke_launch.json
```

To verify an explicit no-score state, set the expected state:

```powershell
npm --prefix web run qa:browser -- --url http://127.0.0.1:<port>/ --postal 567754 --expected-state no_transit --out ..\qa\browser_smoke_no_transit.json
```

To force-check the known Mayflower MRT path even when Best transit chooses a
nearer bus stop:

```powershell
npm --prefix web run qa:browser -- --url http://127.0.0.1:<port>/ --postal 560231 --transit-mode mrt_lrt --must-include "Mayflower MRT Station" --out ..\qa\browser_smoke_mayflower_mrt.json
```

To verify the Shortest/Shiokest compare control on a postal where the two paths
actually differ:

```powershell
npm --prefix web run qa:browser -- --url http://127.0.0.1:<port>/ --postal 560109 --route-mode both --must-include "shortest segments" --out ..\qa\browser_smoke_route_compare.json
```

The script launches headless Chrome, focuses the postal input, types with
Chrome's keyboard input API, submits with Enter, checks the score card, route
legend, map text equivalent, and short-mobile card fit, then writes a JSON
report. Multi-postal runs write one `results[]` entry per postal. `--input-mode
programmatic` is available only as a diagnostic fallback. `--expected-state`
defaults to `scored`; use `no_transit` or `not_yet_scored` when validating
explicit non-score states. `--transit-mode mrt_lrt` or `--transit-mode bus`
clicks the visible transit target control after lookup; `--must-include` asserts
specific text is present in the resulting score card or map summary.
`--route-mode both` or `--route-mode shortest` clicks the visible route display
control after lookup. PNG screenshots under `qa/` remain ignored by Git; commit
the JSON/Markdown summaries instead.

## Local Cleanup

Use the cleanup helper to inspect removable local clutter from `logs/` and
temporary staged deploy/browser directories under `tmp/`:

```powershell
.\scripts\cleanup-local-artifacts.bat
```

The default is a dry run. To delete only the listed ignored runtime artifacts:

```powershell
.\scripts\cleanup-local-artifacts.bat -ConfirmCleanup
```

The helper intentionally does not remove `qa/` evidence files or generated data
bundles.

## Full Rescore Helper

For a postal-universe source refresh, first prepare the scoreable universe
without scoring:

```powershell
.\scripts\prepare-postal-universe.bat -Mode candidate_full_registered -ConfirmBoundedGeocode -DownloadMissing
```

That helper refreshes `processed\postal_universe_candidate_full_registered.parquet`,
runs bounded OneMap geocode only for source-derived `NEEDS_GEOCODE` gaps, and
prints the batch plan. It does not score postals, activate a bundle, or deploy.
Omitting `-ConfirmBoundedGeocode` prints the plan and exits.

For a source/model change that requires every known postal to be rescored after
the prep step, run the guarded helper from the repo root:

```powershell
.\scripts\full-rescore-production.bat -ConfirmFullBatch -Workers 4 -SkipActivateBundle
```

Add `-Deploy` only when the generated bundle validates and should be pushed to
production immediately:

```powershell
.\scripts\full-rescore-production.bat -ConfirmFullBatch -Workers 4 -Deploy
```

On the current 14-logical-core / 64 GB Windows machine, use `-Workers 4` by
default. A bounded 800-postal real scoring benchmark on 2026-07-30 measured
4 workers at 338.00 seconds (2.367 records/sec) and 8 workers at 410.61 seconds
(1.948 records/sec), so 8 workers is slower for this workload. The earlier
4-worker bus-as-transit full batch completed scoring in roughly 9.5 hours.

The helper partitions the postal universe, runs parallel score batches, combines
chunks, exports `web/public/data/generated_<stamp>`, validates it, updates
`web/data-bundle.json` unless `-SkipActivateBundle` is set, and optionally calls
`deploy-production.ps1`.

Ad hoc scoring via `uv run python run.py score ...` defaults to the island
network, `processed/network_island.parquet`. Pass `--network
processed\network.parquet` only for explicit pilot-network diagnostics.
