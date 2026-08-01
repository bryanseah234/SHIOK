# URA Postal Sample Score QA - 2026-08-02

Purpose: validate that the newly wired URA No of Dwelling Units postal source can
flow into real routing/scoring before running any full production batch.

## Sample

Result:

- source universe: `processed/postal_universe_candidate_full_registered_geocoded.parquet`
- URA-backed ready rows: 83,541
- deterministic sample rows: 200
- selection: sorted URA-backed `READY_TO_SCORE` rows, spread across the full source with 200 deterministic index positions
- first postal: `018965`
- last postal: `829769`
- sample path: `tmp\postal_universe_ura_only_sample_200.parquet`

## Score Run

Command:

```powershell
uv run python run.py score-batch --postal-universe tmp\postal_universe_ura_only_sample_200.parquet --network processed\network_island.parquet --output-dir tmp\score_ura_only_sample_200_20260802_0621 --full-batch --confirm-full-batch --chunk-size 200
```

Result:

- `ok`: true
- selected postals: 200
- ready postals selected: 200
- records written: 200
- chunk count: 1
- island network QA: true
- output chunk: `tmp\score_ura_only_sample_200_20260802_0621\chunks\chunk_00001_018965_829769.json`

## Score Summary

- `SCORED`: 186
- `SCORED_PARTIAL`: 1
- `NO_TRANSIT_IN_RANGE`: 13
- total score min: 7.7
- total score mean: 50.21
- total score max: 100.0
- access mean: 86.79
- rain mean: 18.98

Representative records:

| Postal | State | Total | Best node | Type | Routed m | Covered |
| --- | --- | ---: | --- | --- | ---: | ---: |
| 018965 | SCORED | 76.0 | Opp Downtown Stn | bus_stop | 286.8 | 45.1% |
| 098407 | SCORED | 12.9 | TELOK BLANGAH MRT STATION Exit A | mrt_lrt_exit | 1119.4 | 17.6% |
| 117484 | SCORED | 61.0 | Bef West Coast Pk | bus_stop | 484.4 | 13.1% |
| 118676 | SCORED | 60.0 | Whitehaven | bus_stop | 86.0 | 0.0% |
| 119432 | SCORED | 43.1 | PASIR PANJANG MRT STATION Exit A | mrt_lrt_exit | 464.7 | 16.2% |
| 127251 | SCORED | 38.9 | Blk 501 | bus_stop | 432.0 | 1.6% |
| 127872 | SCORED | 25.4 | The Japanese Sec Sch | bus_stop | 709.9 | 0.0% |
| 128746 | SCORED | 44.9 | Blk 701 | bus_stop | 322.5 | 9.7% |

## Conclusion

URA-backed postals can be scored by the real island network and scoring
pipeline. This is a sample QA pass only. The URA-expanded universe is not live
until a full rescore/export/deploy is completed and validated.
