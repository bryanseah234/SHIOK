"""SCORED_PARTIAL regression diagnostic for full_rescore_20260804_205430.

Cross-references honesty55 (currently live) against the new full-rescore bundle,
and attributes each SCORED_PARTIAL record to a fallback reason using the
`provenance.direct_bus_fallback.reason` field emitted by scoring_integration.py.

Output: qa/scored_partial_regression_diagnosis_20260805.json
Also writes:  qa/scored_partial_regression_postals_20260805.txt
             (postals that were SCORED in honesty55 but SCORED_PARTIAL now)
"""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(r"C:\shiok")
NEW_CHUNKS = REPO / "processed/score_batches/full_rescore_20260804_205430/combined/chunks"
HONESTY55 = REPO / "web/public/data/generated_20260804_safe_bus_route_honesty55_targeted/scores"
OUT_JSON = REPO / "qa/scored_partial_regression_diagnosis_20260805.json"
OUT_POSTALS = REPO / "qa/scored_partial_regression_postals_20260805.txt"


def load_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_honesty55_states() -> dict[str, str]:
    """Return {postal: state} for every record in the honesty55 bundle."""
    states: dict[str, str] = {}
    for score_file in sorted(HONESTY55.glob("*.json")):
        if score_file.name in {"index.json", "prefix-index.json"}:
            continue
        try:
            data = load_json(score_file)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {score_file.name}: {exc}")
            continue
        if not isinstance(data, list):
            continue
        for rec in data:
            postal = rec.get("postal")
            state = rec.get("state")
            if postal and state:
                states[str(postal)] = str(state)
    return states


def main() -> None:
    print("Loading honesty55 states...", flush=True)
    honesty55 = load_honesty55_states()
    print(f"  honesty55: {len(honesty55)} postals", flush=True)

    chunks = sorted(NEW_CHUNKS.glob("*.json"))
    print(f"Scanning {len(chunks)} new-bundle chunks...", flush=True)

    # aggregated counters
    total_by_state: Counter[str] = Counter()
    partial_by_route_trust: Counter[str] = Counter()  # per candidate route_trust
    partial_by_best_transit_trust: Counter[str] = Counter()  # per best_transit routing_type
    partial_by_top_reason: Counter[str] = Counter()  # provenance.direct_bus_fallback.reason
    partial_by_reason_count_key: Counter[str] = Counter()  # keys of reason_counts

    # regression from honesty55
    honesty55_scored_now_partial = 0
    honesty55_partial_still_partial = 0
    honesty55_no_transit_now_partial = 0
    honesty55_not_yet_now_partial = 0
    new_postals_partial = 0  # not in honesty55 at all

    # sample postals per reason
    samples_by_reason: dict[str, list[str]] = defaultdict(list)
    SAMPLE_CAP = 10

    # regression-only breakdowns (SCORED -> SCORED_PARTIAL only)
    regression_by_top_reason: Counter[str] = Counter()
    regression_by_reason_count_key: Counter[str] = Counter()
    regression_samples: dict[str, list[str]] = defaultdict(list)

    total_new = 0
    for idx, chunk_path in enumerate(chunks, 1):
        try:
            data = load_json(chunk_path)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! chunk {chunk_path.name}: {exc}")
            continue
        if not isinstance(data, list):
            continue
        for rec in data:
            state = rec.get("state")
            postal = str(rec.get("postal") or "")
            if not state:
                continue
            total_new += 1
            total_by_state[state] += 1
            if state != "SCORED_PARTIAL":
                continue

            # per-candidate route_trust distribution
            for cand in rec.get("candidates") or []:
                rt = cand.get("route_trust")
                if rt:
                    partial_by_route_trust[rt] += 1

            # best_transit routing_type
            ro = rec.get("route_options") or {}
            bt = ro.get("best_transit") or {}
            bt_paths = bt.get("paths") or {}
            bt_rt = bt_paths.get("routing_type") or "unknown"
            partial_by_best_transit_trust[bt_rt] += 1

            # direct_bus_fallback reason
            prov = rec.get("provenance") or {}
            dbf = prov.get("direct_bus_fallback") or {}
            top_reason = dbf.get("reason")
            reason_counts = dbf.get("reason_counts") or {}
            if top_reason:
                partial_by_top_reason[top_reason] += 1
                if len(samples_by_reason[top_reason]) < SAMPLE_CAP:
                    samples_by_reason[top_reason].append(postal)
            else:
                partial_by_top_reason["_no_direct_bus_fallback_in_provenance"] += 1
                if len(samples_by_reason["_no_direct_bus_fallback_in_provenance"]) < SAMPLE_CAP:
                    samples_by_reason["_no_direct_bus_fallback_in_provenance"].append(postal)
            for key in reason_counts.keys():
                partial_by_reason_count_key[key] += 1

            # cross-ref honesty55
            prior_state = honesty55.get(postal)
            if prior_state is None:
                new_postals_partial += 1
            elif prior_state == "SCORED":
                honesty55_scored_now_partial += 1
                key = top_reason or "_no_direct_bus_fallback_in_provenance"
                regression_by_top_reason[key] += 1
                for rk in reason_counts.keys():
                    regression_by_reason_count_key[rk] += 1
                if len(regression_samples[key]) < SAMPLE_CAP:
                    regression_samples[key].append(postal)
            elif prior_state == "SCORED_PARTIAL":
                honesty55_partial_still_partial += 1
            elif prior_state == "NO_TRANSIT_IN_RANGE":
                honesty55_no_transit_now_partial += 1
            elif prior_state == "NOT_YET_SCORED":
                honesty55_not_yet_now_partial += 1

        if idx % 20 == 0 or idx == len(chunks):
            print(
                f"  {idx}/{len(chunks)} chunks | total={total_new} | "
                f"partial={total_by_state['SCORED_PARTIAL']}",
                flush=True,
            )

    verdict = decide_verdict(
        total_by_state,
        partial_by_top_reason,
        regression_by_top_reason,
    )

    output = {
        "total_scored_partial_new_bundle": total_by_state["SCORED_PARTIAL"],
        "total_scored_new_bundle": total_by_state["SCORED"],
        "total_no_transit_new_bundle": total_by_state["NO_TRANSIT_IN_RANGE"],
        "total_not_yet_new_bundle": total_by_state["NOT_YET_SCORED"],
        "total_records_new_bundle": total_new,
        "regression_from_honesty55": {
            "honesty55_scored_now_partial": honesty55_scored_now_partial,
            "honesty55_partial_still_partial": honesty55_partial_still_partial,
            "honesty55_no_transit_now_partial": honesty55_no_transit_now_partial,
            "honesty55_not_yet_now_partial": honesty55_not_yet_now_partial,
            "new_postals_from_universe_expansion_partial": new_postals_partial,
        },
        "by_route_trust": dict(partial_by_route_trust.most_common()),
        "by_best_transit_routing_type": dict(partial_by_best_transit_trust.most_common()),
        "by_fallback_reason_top": dict(partial_by_top_reason.most_common()),
        "by_fallback_reason_count_key": dict(partial_by_reason_count_key.most_common()),
        "regression_only_by_fallback_reason_top": dict(regression_by_top_reason.most_common()),
        "regression_only_by_fallback_reason_count_key": dict(
            regression_by_reason_count_key.most_common()
        ),
        "root_cause_verdict": verdict,
        "sample_postals_by_reason": {k: v for k, v in samples_by_reason.items()},
        "regression_sample_postals_by_reason": {k: v for k, v in regression_samples.items()},
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote {OUT_JSON}")

    # also write the affected-postal list for possible targeted rescore
    regressed_postals: list[str] = []
    # We already have per-reason samples; need full list. Do a second small pass.
    print("Building affected-postal list...", flush=True)
    for chunk_path in chunks:
        try:
            data = load_json(chunk_path)
        except Exception:
            continue
        for rec in data:
            if rec.get("state") != "SCORED_PARTIAL":
                continue
            postal = str(rec.get("postal") or "")
            if not postal:
                continue
            if honesty55.get(postal) == "SCORED":
                regressed_postals.append(postal)
    regressed_postals.sort()
    with OUT_POSTALS.open("w", encoding="utf-8") as f:
        for p in regressed_postals:
            f.write(p + "\n")
    print(f"Wrote {OUT_POSTALS} ({len(regressed_postals)} postals)")

    # summary print
    print("\n=========== SUMMARY ===========")
    print(f"total records:       {total_new}")
    print(f"  SCORED:            {total_by_state['SCORED']}")
    print(f"  SCORED_PARTIAL:    {total_by_state['SCORED_PARTIAL']}")
    print(f"  NO_TRANSIT:        {total_by_state['NO_TRANSIT_IN_RANGE']}")
    print(f"  NOT_YET:           {total_by_state['NOT_YET_SCORED']}")
    print()
    print("SCORED_PARTIAL breakdown by top reason:")
    for k, v in partial_by_top_reason.most_common():
        print(f"  {v:>8}  {k}")
    print()
    print("Regression cohort (SCORED in honesty55 -> SCORED_PARTIAL now):")
    print(f"  total:                                             {honesty55_scored_now_partial}")
    print("  by top reason:")
    for k, v in regression_by_top_reason.most_common():
        print(f"    {v:>8}  {k}")
    print()
    print(f"ROOT CAUSE VERDICT: {verdict}")


def decide_verdict(
    total_by_state: Counter[str],
    partial_by_top_reason: Counter[str],
    regression_by_top_reason: Counter[str],
) -> str:
    shorter = partial_by_top_reason.get("route_shorter_than_crow_flies_direct", 0)
    implausible = partial_by_top_reason.get(
        "implausible_graph_route_to_datamall_bus_stop_within_direct_radius", 0
    )
    multiple = partial_by_top_reason.get(
        "multiple_implausible_graph_routes_to_datamall_bus_stops_within_direct_radius",
        0,
    )
    no_dbf = partial_by_top_reason.get("_no_direct_bus_fallback_in_provenance", 0)

    reg_shorter = regression_by_top_reason.get("route_shorter_than_crow_flies_direct", 0)
    reg_implausible = regression_by_top_reason.get(
        "implausible_graph_route_to_datamall_bus_stop_within_direct_radius", 0
    )
    reg_multiple = regression_by_top_reason.get(
        "multiple_implausible_graph_routes_to_datamall_bus_stops_within_direct_radius",
        0,
    )

    total_partial = total_by_state["SCORED_PARTIAL"]
    parts: list[str] = []
    parts.append(f"{total_partial} SCORED_PARTIAL")
    parts.append(
        f"reasons: route_shorter={shorter}, implausible={implausible}, "
        f"multiple_implausible={multiple}, none_in_prov={no_dbf}"
    )
    parts.append(
        f"regression cohort reasons: shorter={reg_shorter}, "
        f"implausible={reg_implausible}, multiple={reg_multiple}"
    )
    # verdict logic:
    dominant_shorter = shorter > 0.5 * (shorter + implausible + multiple + no_dbf)
    dominant_old_guards = (implausible + multiple) > 0.5 * (
        shorter + implausible + multiple + no_dbf
    )
    if dominant_shorter:
        verdict = (
            "H1: Method 2 snap-bug guard too tight (route_shorter_than_crow_flies_direct dominates)"
        )
    elif dominant_old_guards:
        verdict = (
            "H2: pre-existing implausible-graph-route cases exposed by "
            "full-universe rescore (old guards dominate, not the new snap-bug guard)"
        )
    elif no_dbf > 0.5 * total_partial:
        verdict = (
            "H4: majority of SCORED_PARTIAL records have no direct_bus_fallback "
            "in provenance - non-fallback pathway causing partial state"
        )
    else:
        verdict = (
            "Mixed: no single reason dominates; needs manual inspection of "
            "the by_fallback_reason_top table"
        )
    return verdict + " | " + "; ".join(parts)


if __name__ == "__main__":
    main()
