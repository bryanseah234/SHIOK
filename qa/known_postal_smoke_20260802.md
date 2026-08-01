# Known Postal Smoke - 2026-08-02

Bundle: `generated_20260801_165500`

| Postal | State | Total | Best node | Best type | Routed | Covered | MRT/LRT option | Bus option |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- |
| 560234 | SCORED | 90/100 | Mayflower Sec Sch | bus_stop | 326.2 m | 76% | SCORED 73/100 | SCORED 90/100 |
| 560231 | SCORED | 100/100 | Opp Mayflower Sec Sch | bus_stop | 128.1 m | 100% | SCORED 72/100 | SCORED 100/100 |
| 560225 | SCORED | 85/100 | Mayflower Sec Sch | bus_stop | 425 m | 70% | SCORED 60/100 | SCORED 85/100 |
| 560700 | SCORED | 85/100 | Blk 700B | bus_stop | 169.6 m | 64% | SCORED 77/100 | SCORED 85/100 |
| 560710 | SCORED | 100/100 | Aft Ang Mo Kio Int | bus_stop | 76.6 m | 100% | SCORED 87/100 | SCORED 100/100 |
| 570234 | SCORED | 100/100 | Opp Bishan Nth Shop Mall | bus_stop | 118.9 m | 100% | NO_TRANSIT_IN_RANGE 0/100 | SCORED 100/100 |

Checks:

```json
{
  "all_found": true,
  "postal_570234_scored_not_pending": true,
  "postal_560234_defaults_to_bus": true,
  "mayflower_controls_have_mrt_option": true
}
```

Interpretation: `570234` is scored in the fresh bundle. `560234` defaults to nearby bus access, while MRT/LRT mode remains available for Mayflower-specific QA.
