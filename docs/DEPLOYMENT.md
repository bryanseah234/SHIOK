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

## Why This Exists

`web/public/data/` is generated and intentionally ignored by Git. The current bundle is about 347 MB, so committing it would make GitHub/Vercel deploys brittle and noisy.

For direct local deploys, Vercel receives the local bundle and the build copies it into `.next/static/data`.

For Git auto-deploys, the bundle is not in the repository. The web build therefore downloads the configured bundle from the current production site and includes it in the new build. This makes code-only commits safe as long as `web/data-bundle.json` still points to a bundle already published in production.

## Data Refresh Deploy

After a new score/export batch is generated and validated:

1. Update `web/data-bundle.json` to the new `generated_...` directory.
2. Run `.\scripts\deploy-production.bat`.
3. Commit and push the source/config changes.

Do not rely on Git auto-deploy for the first deploy of a brand-new data bundle; the Git build cannot download a bundle that is not published yet.
