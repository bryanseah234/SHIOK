# Deployment

## Normal Production Deploy

Run this from the repo root:

```powershell
.\scripts\deploy-production.bat
```

That wrapper runs:

1. `npm --prefix web test`
2. `uv run python run.py publish --input web/public/data/<current bundle> --deploy --confirm-production`

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
skipped by Vercel instead of consuming Hobby build quota. Changes under `web/`,
including `web/data-bundle.json`, still trigger a real build.

Only the `sgshiok` Vercel project should be connected to this GitHub repo. On
2026-08-01, the older `shiok` Vercel project was disconnected from
`bryanseah234/sgSHIOK2026` because it was still creating duplicate failed commit
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

Current pending local bundle, blocked only by Vercel Hobby deploy quota on
2026-08-01:

```powershell
.\scripts\deploy-production.bat -DataBundle generated_20260801_165500
.\scripts\activate-data-bundle.bat -DataBundle generated_20260801_165500
```

## Vercel Hobby Limits

If Git deploy shows `Deployment rate limited` or direct CLI deploy returns
`api-deployments-free-per-day`, stop deploy attempts until the 24-hour Hobby
quota window resets. Continue committing source changes to `main`; docs-only and
QA-only commits should be skipped by `web/scripts/ignore-build.mjs`, while real
`web/` changes will build after the quota resets.

After the limit resets, deploy the pending local bundle through the
bundle-aware helper rather than raw `vercel deploy`:

```powershell
.\scripts\release-data-bundle.bat -DataBundle generated_20260801_165500 -ConfirmProduction
```

Use raw `vercel deploy` only for code-only manual deploys where
`web/data-bundle.json` already points at a bundle that is reachable in
production. New generated bundles must go through `deploy-production.bat`
because it stages the otherwise ignored `web/public/data/<bundle>` directory.

To see the full sequence without deploying:

```powershell
.\scripts\release-data-bundle.bat -DataBundle generated_20260801_165500 -PlanOnly
```

## Lookup Transfer Check

Use this from the repo root to measure a postal lookup against the active local
bundle:

```powershell
npm --prefix web run measure:lookup -- 560234
```

For a generated bundle that is not yet active in `web/data-bundle.json`, set
`SHIOK_DATA_BUNDLE`:

```powershell
$env:SHIOK_DATA_BUNDLE = "generated_20260801_165500"
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

The script launches headless Chrome, searches the postal through the real form,
checks the score card, route legend, map text equivalent, and short-mobile card
fit, then writes a JSON report. PNG screenshots under `qa/` remain ignored by
Git; commit the JSON/Markdown summaries instead.

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

For a source/model change that requires every known postal to be rescored, run
the guarded helper from the repo root:

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
