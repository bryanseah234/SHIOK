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

1. Update `web/data-bundle.json` to the new `generated_...` directory.
2. Update both `.vercelignore` and `web/.vercelignore` so only that active
   generated bundle is whitelisted.
3. Run `.\scripts\preflight-production.bat -SkipNetworkPreflight`.
4. Run `vercel deploy --prod --yes --scope theprawnvercel --no-wait` from the
   repo root, then inspect the deployment until it is Ready.
5. Commit and push the source/config/docs changes. The following Git
   auto-deploy can now download the already-published bundle during `next build`.

Do not rely on Git auto-deploy for the first deploy of a brand-new data bundle; the Git build cannot download a bundle that is not published yet.

## Full Rescore Helper

For a source/model change that requires every known postal to be rescored, run
the guarded helper from the repo root:

```powershell
.\scripts\full-rescore-production.bat -ConfirmFullBatch -Workers 4
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
`web/data-bundle.json`, and optionally calls `deploy-production.ps1`.
