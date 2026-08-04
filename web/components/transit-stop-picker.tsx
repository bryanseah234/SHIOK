"use client";

import React, { useMemo, useRef } from "react";
import {
  candidateComparison,
  nextChipAction,
  type CandidateComparison,
  type TransitCandidate,
} from "../lib/nearest-transit";
import styles from "./transit-stop-picker.module.css";

export const RESET_CHIP_ID = "__reset__";

export interface TransitStopPickerProps {
  candidates: TransitCandidate[];
  /** POI id of the currently displayed stop (null means "use auto-picked best"). */
  activeStopId: string | null;
  /** POI id of the auto-picked best transit stop for the current transit mode. */
  bestStopId: string | null;
  onSelect: (stopId: string | null) => void;
  /**
   * Optional override — normally the picker computes comparison from candidates
   * directly, but tests may inject a comparison to exercise formatting.
   */
  comparison?: CandidateComparison | null;
}

function formatMeters(m: number): string {
  if (!Number.isFinite(m)) return "\u2014";
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

function chipKindLabel(kind: TransitCandidate["kind"]): string {
  return kind === "mrt_exit" ? "MRT" : "Bus";
}

/**
 * Public copy used by the comparison note under the chip row.
 * Kept simple because sheltered % is NOT known for non-best candidates today.
 * See TODO(stop-picker) in `nearest-transit.ts` for the data gap.
 */
export function buildComparisonText(
  comparison: CandidateComparison | null | undefined
): string | null {
  if (!comparison) return null;
  const pct = Math.round(comparison.fartherPct);
  if (pct <= 0) return null;
  return `${pct}% farther than best (straight-line)`;
}

export function TransitStopPicker({
  candidates,
  activeStopId,
  bestStopId,
  onSelect,
  comparison: comparisonOverride,
}: TransitStopPickerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const chipIds = useMemo(() => {
    if (candidates.length === 0) return [];
    const ids: string[] = [];
    const showReset = activeStopId !== null && activeStopId !== bestStopId;
    if (showReset) ids.push(RESET_CHIP_ID);
    for (const candidate of candidates) ids.push(candidate.id);
    return ids;
  }, [candidates, activeStopId, bestStopId]);

  const comparison = useMemo<CandidateComparison | null>(() => {
    if (comparisonOverride !== undefined) return comparisonOverride;
    if (activeStopId === null || activeStopId === bestStopId) return null;
    const active = candidates.find((candidate) => candidate.id === activeStopId);
    const best = candidates.find((candidate) => candidate.id === bestStopId);
    return candidateComparison(active ?? null, best ?? null);
  }, [candidates, activeStopId, bestStopId, comparisonOverride]);

  if (candidates.length === 0) return null;

  const activeForRender = activeStopId ?? bestStopId;

  function focusChipByIndex(index: number) {
    if (!containerRef.current) return;
    const buttons = containerRef.current.querySelectorAll<HTMLButtonElement>(
      "button[data-chip-id]"
    );
    const target = buttons[index];
    if (target) target.focus();
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const el = event.target as HTMLElement;
    const chipId = el.getAttribute("data-chip-id");
    if (!chipId) return;
    const currentIndex = chipIds.indexOf(chipId);
    const action = nextChipAction(chipIds, currentIndex, event.key);
    if (action.kind === "focus") {
      event.preventDefault();
      focusChipByIndex(action.index);
      return;
    }
    if (action.kind === "activate") {
      event.preventDefault();
      onSelect(action.chipId === RESET_CHIP_ID ? null : action.chipId);
    }
  }

  const showReset = activeStopId !== null && activeStopId !== bestStopId;
  const comparisonText = buildComparisonText(comparison);

  return (
    <div className={styles.pickerShell} aria-label="Transit stop picker">
      <div className={styles.pickerHeader}>Nearby transit</div>
      <div
        ref={containerRef}
        className={styles.chipRow}
        role="group"
        aria-label="Nearby transit stops"
        onKeyDown={handleKeyDown}
      >
        {showReset && (
          <button
            type="button"
            data-chip-id={RESET_CHIP_ID}
            className={styles.chipReset}
            onClick={() => onSelect(null)}
            aria-label="Reset to auto-picked best transit"
          >
            Reset to best
          </button>
        )}
        {candidates.map((candidate) => {
          const isActive = candidate.id === activeForRender;
          const isBest = candidate.id === bestStopId;
          return (
            <button
              key={candidate.id}
              type="button"
              data-chip-id={candidate.id}
              data-kind={candidate.kind}
              data-is-best={isBest ? "true" : undefined}
              className={`${styles.chip} ${isActive ? styles.chipActive : ""}`}
              aria-current={isActive ? "true" : undefined}
              onClick={() => onSelect(candidate.id)}
              title={`${candidate.name} (~${formatMeters(candidate.straight_line_m)})`}
            >
              <span className={styles.chipKind}>{chipKindLabel(candidate.kind)}</span>
              <span className={styles.chipName}>{candidate.name}</span>
              <span className={styles.chipDistance}>
                {formatMeters(candidate.straight_line_m)}
              </span>
            </button>
          );
        })}
      </div>
      {comparisonText && (
        <p className={styles.comparisonText} role="status">
          {comparisonText}
        </p>
      )}
    </div>
  );
}
