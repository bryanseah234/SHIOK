# 2026-08-01 Mayflower MRT Lambda/Detour Experiment

Postal: `560231`

Destination focus: `MAYFLOWER MRT STATION Exit 5`

Network: `processed/network_island.parquet`

Result:

| Shelter lambda | Detour budget | Exit 5 final distance | Exit 5 sheltered ratio |
| --- | --- | --- | --- |
| 2.0 | 1.25 | 425.9 m | 31.2% |
| 2.0 | 3.0 | 425.9 m | 31.2% |
| 5.0 | 1.25 | 425.9 m | 31.2% |
| 5.0 | 3.0 | 425.9 m | 31.2% |
| 10.0 | 1.25 | 425.9 m | 31.2% |
| 10.0 | 3.0 | 425.9 m | 31.2% |
| 25.0 | 1.25 | 425.9 m | 31.2% |
| 25.0 | 3.0 | 425.9 m | 31.2% |

Observation:

- Increasing lambda and detour budget does not discover a more-sheltered route
  to Mayflower Exit 5.
- Other Mayflower exits can improve with higher lambda/detour, e.g. Exit 4 can
  reach roughly 59.4% sheltered at 896.7 m when detour budget allows it.

Conclusion:

The `560231` -> Mayflower Exit 5 false-negative is not fixed by exposing a
Max Shelter slider. It needs graph/data work: covered/HDB/bridge evidence is
nearby, but the connected routable path to Exit 5 is still missing or snapped
incorrectly.
