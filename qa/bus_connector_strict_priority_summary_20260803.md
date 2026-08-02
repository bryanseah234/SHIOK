# Strict Missing-Bus Connector QA Summary - 2026-08-03

Inputs:

- `qa/onemap_validation_cached_report_active_safe_mayflower_abs_delta_20260803.json`
- `qa/onemap_outlier_replay_bus_longer_profile_active_safe_mayflower_guarded_20_20260803.json`
- `qa/onemap_outlier_replay_shorter_profile_active_safe_mayflower_20260803.json`
- `qa/onemap_missing_bus_connector_strict_priority_active_safe_mayflower_abs_delta_guarded_20260803.geojson`

Command:

```powershell
uv run python run.py bus-connector-diagnostics --priority-geojson qa\onemap_missing_bus_connector_strict_priority_active_safe_mayflower_abs_delta_guarded_20260803.geojson --output qa\bus_connector_diagnostics_strict_missing_bus_active_safe_mayflower_abs_delta_guarded_20260803.json --geojson-output qa\bus_connector_diagnostics_strict_missing_bus_active_safe_mayflower_abs_delta_guarded_20260803.geojson
```

Result:

- Features diagnosed: 4.
- Current graph route state: 4 `implausible_detour`.
- Diagnostic class: 4 `alternate_bus_snap_candidate`.
- Same validation/current stop name: 4 true.
- Current score state: all 4 remain `SCORED_PARTIAL` with `direct_bus_fallback_unrouted`.

Rows:

| Postal | Stop | Direct m | OneMap walk m | Current graph m | Best alt route+snap m | Best alt snap m |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 530535 | Blk 535 | 28.9 | 43.0 | 347.9 | 340.4 | 38.7 |
| 417092 | Opp Hong San Si Tp | 133.0 | 176.0 | 860.6 | 827.5 | 35.2 |
| 534317 | Raya Gdn | 51.7 | 97.0 | 457.4 | 306.2 | 20.3 |
| 637814 | Aft Tuas Sth St 2 | 127.8 | 150.0 | 613.4 | 589.8 | 28.8 |

Interpretation:

These are not safe wins for a looser bus-route trust threshold. Even the best
alternate graph snaps leave route+snap walks far above OneMap and far above
direct distance. Keep them as partial direct-bus fallback until source-backed
connector geometry, side-of-road bus-stop access, or audited correction evidence
explains the missing walk.
