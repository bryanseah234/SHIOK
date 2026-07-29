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

## Why This Exists

`web/public/data/` is generated and intentionally ignored by Git. The current bundle is about 347 MB, so committing it would make GitHub/Vercel deploys brittle and noisy.

For direct local deploys, Vercel receives the local bundle in `web/public/data/`.

For Git auto-deploys, the bundle is not in the repository. Before `next build`, the web build downloads the configured bundle from the current production site into `web/public/data/`. This makes code-only commits safe as long as `web/data-bundle.json` still points to a bundle already published in production.

## Data Refresh Deploy

After a new score/export batch is generated and validated:

1. Update `web/data-bundle.json` to the new `generated_...` directory.
2. Run `.\scripts\deploy-production.bat`.
3. Commit and push the source/config changes.

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

The helper partitions the postal universe, runs parallel score batches, combines
chunks, exports `web/public/data/generated_<stamp>`, validates it, updates
`web/data-bundle.json`, and optionally calls `deploy-production.ps1`.
