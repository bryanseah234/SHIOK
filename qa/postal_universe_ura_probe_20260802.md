# Postal Universe URA Probe - 2026-08-02

Purpose: test whether official URA No of Dwelling Units can legitimately reduce
the postal-universe gap without OneMap brute-force enumeration.

## Source

- dataset: URA No of Dwelling Units
- data.gov.sg id: `d_be71daeab5930f96b90ad2857454d876`
- raw artifact sha256: `9d249959b4010d00a7d91f8161f22188bbb0203a27185f91f46b41595f4884f0`
- raw path: `raw\9d249959b4010d00a7d91f8161f22188bbb0203a27185f91f46b41595f4884f0\ura_no_dwelling_units.geojson`
- raw records: 83,541

## Official-Current Probe

Command:

```powershell
uv run python run.py postal-universe --mode official_current --download-missing --output tmp\postal_universe_official_current_ura_probe.parquet --summary tmp\postal_universe_official_current_ura_probe_summary.json
```

Result:

- total unique postals: 105,462
- ready to score: 105,462
- needs geocode: 0
- URA valid unique postals: 83,541
- URA source-only postals: 75,947

## Candidate Production-Mode Probe

Command:

```powershell
uv run python run.py postal-universe --mode candidate_full_registered --download-missing --output tmp\postal_universe_candidate_full_registered_ura_probe.parquet --summary tmp\postal_universe_candidate_full_registered_ura_probe_summary.json
```

Result:

- total unique postals: 124,443
- ready to score: 123,868
- needs geocode: 575
- URA source-only postals: 238
- net gain versus 124,032-record current production candidate universe: 411

## Conclusion

URA is worth keeping as official-source evidence. It greatly improves the
conservative `official_current` universe but does not close the canonical
~140k target. Continue to describe the production universe as source-derived,
not complete.
