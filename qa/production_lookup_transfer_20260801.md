# 2026-08-01 Production Lookup Transfer Check

Target: `https://sgshiok.vercel.app`

Postal tested: `560234`

Result:

- Page title: `S.H.I.O.K. Index`
- Lookup rendered `Postal 560234`
- No deployed JS chunk contains `shiok-data.vercel.app`
- Deployed JS chunk contains `generated_20260801_direct_bus_targeted`
- Runtime data fetches use bundled relative paths under
  `/data/generated_20260801_direct_bus_targeted/`

Observed data requests:

- `scores/prefix-index.json.gz`
- `geom/postal-prefix/560.json.gz`
- `manifest.json` after a harmless `manifest.json.gz` miss
- `geom/h3/88652636c1fffff.json.gz`
- `scores/ANG_MO_KIO_PART_001.json.gz`
- seven `transit/h3/*.json.gz` shards

Follow-up change:

- Git-built bundles now write both plain and gzipped JSON for downloaded
  manifest/index-style files so future deployments avoid the manifest `.gz`
  fallback miss.
