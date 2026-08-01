# Full Rescore Summary

Date: 2026-08-01.

Bundle: `generated_20260801_165500`

## Commands

- `.\scripts\full-rescore-production.bat -ConfirmFullBatch -Workers 4 -ChunkSize 500 -SkipActivateBundle -Stamp 20260801_165500`
- The wrapper stopped before combine/export with `One or more score-batch workers failed`, but every worker output manifest reported `ok: true` and stderr logs were empty.
- Completed chunks were combined manually into `processed\score_batches\full_rescore_20260801_165500\combined`.
- Export command: `uv run python run.py export --records-dir processed\score_batches\full_rescore_20260801_165500\combined --output web\public\data\generated_20260801_165500 --full-batch --confirm-full-batch --postal-universe processed\postal_universe_candidate_full_registered_geocoded.parquet --network processed\network_island.parquet`

## Worker Outputs

| Part | Rows | Ready | Records Written | Not Yet |
| --- | ---: | ---: | ---: | ---: |
| part01 | 31,008 | 30,922 | 31,008 | 86 |
| part02 | 31,008 | 30,933 | 31,008 | 75 |
| part03 | 31,008 | 30,927 | 31,008 | 81 |
| part04 | 31,008 | 30,931 | 31,008 | 77 |

## Combined Manifest

- chunks: 252
- records: 124,032
- `SCORED`: 112,880
- `SCORED_PARTIAL`: 1,449
- `NO_TRANSIT_IN_RANGE`: 9,384
- `NOT_YET_SCORED`: 319

## Validation

- `uv run python run.py validate --input web\public\data\generated_20260801_165500`: OK
- file count: 1,657
- indexed postals: 124,032
- geometry postals: 114,329
- geometry postals with route segments: 114,329
- transit features: 6,011
- score prefixes: 530

## Readiness

- `uv run python run.py readiness --bundle-dir web\public\data\generated_20260801_165500`: OK
- warnings: none
- active bundle reflects current network: true
- bundle generated at: `2026-08-01T19:16:04.897025+00:00`
- network mtime: `2026-08-01T07:48:53.021482+00:00`

## Web Build

- `SHIOK_DATA_BUNDLE=generated_20260801_165500`
- `NEXT_PUBLIC_DATA_BASE=/data/generated_20260801_165500/`
- `npm --prefix web run build`: OK

## Deployment State

This bundle is not activated in `web\data-bundle.json` and is not deployed.
The direct production deploy is blocked by Vercel Hobby daily deploy quota
(`api-deployments-free-per-day`). After quota reset, deploy this bundle first,
then update `web\data-bundle.json` only after the bundle exists in production.
