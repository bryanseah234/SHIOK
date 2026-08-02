"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  fetchGeomForPostal,
  fetchManifest,
  fetchScoreForPostal,
  fetchTransitPois,
  fetchTransitPoisForGeom,
} from "../lib/data";
import type {
  Manifest,
  PostalGeom,
  PostalRouteGeomOption,
  RouteSegment,
  ScoreRecord,
  Subscores,
  TransitAccessMode,
  TransitPoiCollection,
} from "../lib/types";
import {
  RouteEvidenceMap,
  type FeedbackPoint,
  type RouteDisplayMode,
  type RouteMapItem,
} from "../components/route-evidence-map";
import styles from "./page.module.css";

interface SearchResult {
  BUILDING: string;
  ROAD_NAME: string;
  POSTAL: string;
  LATITUDE: string;
  LONGITUDE: string;
  SEARCHVAL: string;
}

interface LoadedSelection {
  result: SearchResult;
  score: ScoreRecord | null;
  geom: PostalGeom | null;
}

const SUBSCORE_LABELS: Array<[keyof Subscores, string]> = [
  ["access", "Access"],
  ["rain", "Rain"],
  ["heat", "Heat"],
  ["bus", "Bus"],
  ["crossing", "Crossings"],
];

const TRANSIT_MODE_OPTIONS: Array<{ id: TransitAccessMode; label: string }> = [
  { id: "best_transit", label: "Best transit" },
  { id: "mrt_lrt", label: "MRT/LRT" },
  { id: "bus", label: "Bus" },
];

const SOURCE_LABELS: Record<string, string> = {
  lta_covered_linkway: "LTA shelter",
  osm_covered: "OSM covered",
  inferred_hdb_void_deck: "HDB inferred",
  bridge_underpass: "Bridge/underpass",
  audited_shelter_correction: "Audited shelter",
  direct_unrouted_bus: "Direct bus estimate",
  covered_unknown: "Covered",
  exposed: "Exposed",
};

const REASON_COPY: Record<keyof Subscores, { low: string; high: string }> = {
  access: { low: "Longer walk to transit", high: "Short walk to transit" },
  rain: { low: "Mostly exposed to rain", high: "Good rain shelter coverage" },
  heat: { low: "Low shade and shelter comfort", high: "Better heat comfort" },
  bus: { low: "Limited bus connectivity", high: "Strong bus connectivity" },
  crossing: { low: "More crossing friction", high: "Easy crossing profile" },
};

type ComfortMode = "balanced" | "rain_am" | "rain_pm" | "sunny_am" | "sunny_pm" | "sunny_midday";
type FeedbackSegmentLabel =
  | "sheltered"
  | "void_deck"
  | "covered_bridge"
  | "underpass"
  | "exposed"
  | "blocked"
  | "other";

const COMFORT_MODES: Array<{ id: ComfortMode; label: string; weights: Record<keyof Subscores, number> | null }> = [
  { id: "balanced", label: "Balanced", weights: null },
  { id: "rain_am", label: "Rain + AM", weights: { access: 0.3, bus: 0.25, rain: 0.3, heat: 0.1, crossing: 0.05 } },
  { id: "rain_pm", label: "Rain + PM", weights: { access: 0.3, bus: 0.25, rain: 0.3, heat: 0.1, crossing: 0.05 } },
  { id: "sunny_am", label: "Sunny + AM", weights: { access: 0.3, bus: 0.2, rain: 0.1, heat: 0.35, crossing: 0.05 } },
  { id: "sunny_pm", label: "Sunny + PM", weights: { access: 0.3, bus: 0.2, rain: 0.1, heat: 0.35, crossing: 0.05 } },
  { id: "sunny_midday", label: "Sunny midday", weights: { access: 0.3, bus: 0.15, rain: 0.05, heat: 0.45, crossing: 0.05 } },
];

const FEEDBACK_SEGMENT_OPTIONS: Array<{ id: FeedbackSegmentLabel; label: string }> = [
  { id: "sheltered", label: "Sheltered" },
  { id: "void_deck", label: "Void deck" },
  { id: "covered_bridge", label: "Covered bridge" },
  { id: "underpass", label: "Underpass" },
  { id: "exposed", label: "Exposed" },
  { id: "blocked", label: "Blocked" },
  { id: "other", label: "Other" },
];

function normalizePostal(value: string): string | null {
  const trimmed = value.trim();
  if (!/^\d{1,6}$/.test(trimmed)) return null;
  return trimmed.padStart(6, "0");
}

function resultTitle(result: SearchResult): string {
  if (result.BUILDING && result.BUILDING !== "N/A") return toProperCase(result.BUILDING);
  return result.SEARCHVAL || `S${result.POSTAL}`;
}

function resultSubtitle(result: SearchResult): string {
  const road = result.ROAD_NAME && result.ROAD_NAME !== "N/A" ? toProperCase(result.ROAD_NAME) : "";
  return [road, result.POSTAL && result.POSTAL !== "N/A" ? `S${result.POSTAL}` : ""]
    .filter(Boolean)
    .join(" ");
}

function toProperCase(value: string): string {
  return value
    .toLowerCase()
    .replace(/\b([a-z])/g, (match) => match.toUpperCase())
    .replace(/\bMrt\b/g, "MRT")
    .replace(/\bLrt\b/g, "LRT")
    .replace(/\bHdb\b/g, "HDB");
}

function scoreClass(total: number | null): string {
  if (total === null) return styles.scoreMuted;
  if (total >= 80) return styles.scoreGood;
  if (total >= 55) return styles.scoreMid;
  return styles.scoreLow;
}

function formatScore(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value)}` : "Not scored";
}

function formatScoreWithMax(value: number | null | undefined, fallback = "No score"): string {
  return typeof value === "number" ? `${Math.round(value)}/100` : fallback;
}

function formatDistance(value: number | undefined): string {
  if (typeof value !== "number") return "Unavailable";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} km` : `${Math.round(value)} m`;
}

function formatPercent(value: number | null): string {
  return typeof value === "number" ? `${value}%` : "Unavailable";
}

function transitModeLabel(mode: TransitAccessMode): string {
  if (mode === "mrt_lrt") return "MRT/LRT";
  if (mode === "bus") return "bus";
  return "transit";
}

function exposureGapCopy(lenM: number, index: number): string {
  const rank = index === 0 ? "Longest" : `Gap ${index + 1}`;
  if (lenM >= 300) return `${rank} exposed stretch`;
  if (lenM >= 100) return `${rank} open-air stretch`;
  return `${rank} short exposed stretch`;
}

function nestedNumber(value: unknown, path: string[]): number | null {
  let cursor = value;
  for (const key of path) {
    if (!cursor || typeof cursor !== "object" || !(key in cursor)) return null;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return typeof cursor === "number" && Number.isFinite(cursor) ? cursor : null;
}

function provenanceReason(score: ScoreRecord, transitMode: TransitAccessMode): string | null {
  if (transitMode !== "best_transit") return null;
  const provenance = score.provenance;
  if (!provenance || typeof provenance !== "object") return null;
  const reason = provenance.reason;
  return typeof reason === "string" ? reason : null;
}

function nearestRoutedTransitM(score: ScoreRecord, transitMode: TransitAccessMode): number | null {
  return transitMode === "best_transit"
    ? nestedNumber(score.provenance, ["routing_diagnostics", "nearest_routed_m"])
    : null;
}

function noTransitTitle(score: ScoreRecord, transitMode: TransitAccessMode): string {
  const reason = provenanceReason(score, transitMode);
  if (reason === "transit_candidates_graph_disconnected") return "Transit route not connected yet";
  if (reason === "no_transit_candidates_selected") return "No transit candidate nearby";
  return nearestRoutedTransitM(score, transitMode) !== null
    ? "Transit beyond scoring range"
    : `No routed ${transitModeLabel(transitMode)} within range`;
}

function scoreStateNote(score: ScoreRecord, transitMode: TransitAccessMode): string | null {
  if (score.state === "SCORED_PARTIAL") {
    return "Partial score: nearby bus service is counted, but exact walking-route shelter is still pending.";
  }
  if (score.state === "NO_TRANSIT_IN_RANGE") {
    const reason = provenanceReason(score, transitMode);
    if (reason === "transit_candidates_graph_disconnected") {
      return "Transit candidates exist, but the current walking graph could not connect a route yet.";
    }
    if (reason === "no_transit_candidates_selected") {
      return "No qualifying MRT/LRT exit or bus stop candidate was selected near this postal.";
    }
    const nearestM = nearestRoutedTransitM(score, transitMode);
    if (nearestM !== null) {
      return `Closest routed transit found is about ${formatDistance(nearestM)} away; current scoring range is 1.2 km.`;
    }
    return `No ${transitModeLabel(transitMode)} walk was found within the current scoring range.`;
  }
  if (score.state === "NOT_YET_SCORED") {
    return "This postal is in the source universe, but it still needs usable location evidence.";
  }
  return null;
}

function routeSame(selection: LoadedSelection | null): boolean {
  if (!selection?.geom || !selection.score?.paths) return false;
  if (selection.score.paths.routing_type === "direct_bus_fallback_unrouted") return true;
  return (
    selection.geom.shortest === selection.geom.sheltered ||
    Math.round(selection.score.paths.shortest_m) === Math.round(selection.score.paths.sheltered_m)
  );
}

function routeOptionScore(score: ScoreRecord, mode: TransitAccessMode): ScoreRecord {
  if (mode === "best_transit") return score;
  const option = score.route_options?.[mode];
  if (!option) {
    return {
      ...score,
      state: "NO_TRANSIT_IN_RANGE",
      total: null,
      subscores: null,
      best_node: null,
      paths: null,
      exposure_gaps: null,
    };
  }
  return {
    ...score,
    state: option.state,
    total: option.total,
    subscores: option.subscores,
    best_node: option.best_node,
    paths: option.paths,
    exposure_gaps: option.exposure_gaps,
  };
}

function optionGeomToPostalGeom(postal: string, option: PostalRouteGeomOption): PostalGeom {
  return {
    postal,
    shortest: option.shortest,
    sheltered: option.sheltered,
    shortest_parts: option.shortest_parts,
    sheltered_parts: option.sheltered_parts,
    exposure_gaps: option.exposure_gaps,
    route_segments: option.route_segments,
  };
}

function routeOptionGeom(geom: PostalGeom | null, mode: TransitAccessMode): PostalGeom | null {
  if (!geom) return null;
  if (mode === "best_transit") return geom;
  const option = geom.route_options?.[mode];
  return option ? optionGeomToPostalGeom(geom.postal, option) : null;
}

function selectionForTransitMode(
  selection: LoadedSelection | null,
  mode: TransitAccessMode
): LoadedSelection | null {
  if (!selection) return null;
  return {
    result: selection.result,
    score: selection.score ? routeOptionScore(selection.score, mode) : null,
    geom: routeOptionGeom(selection.geom, mode),
  };
}

function postalTitle(selection: LoadedSelection): string {
  return `Postal ${selection.result.POSTAL}`;
}

function buildRouteItems(primary: LoadedSelection | null): RouteMapItem[] {
  const items: RouteMapItem[] = [];
  if (primary?.geom) {
    items.push({
      id: "primary",
      label: resultTitle(primary.result),
      geom: primary.geom,
      color: "#008f86",
    });
  }
  return items;
}

function scoreReasons(score: ScoreRecord, transitMode: TransitAccessMode): string[] {
  if (score.state === "NO_TRANSIT_IN_RANGE") {
    const label = transitModeLabel(transitMode);
    const reason = provenanceReason(score, transitMode);
    if (reason === "transit_candidates_graph_disconnected") {
      return ["Transit candidate found", "Walking route not connected yet"];
    }
    if (reason === "no_transit_candidates_selected") {
      return ["No nearby transit candidate selected", "Outside current candidate thresholds"];
    }
    const nearestM = nearestRoutedTransitM(score, transitMode);
    return nearestM !== null
      ? [`Closest routed ${label} is ${formatDistance(nearestM)}`, "Current scoring range is 1.2 km"]
      : [`No ${label} walk within scoring range`, "Nearby transit may still exist outside the current threshold"];
  }
  if (score.state === "NOT_YET_SCORED") return ["Not scored in this bundle", "Needs usable location evidence"];
  if (score.paths?.routing_type === "direct_bus_fallback_unrouted") {
    return ["Nearby bus stop with service data", "Walking-route shelter not verified yet"];
  }
  if (!score.paths || !score.best_node) return ["Route evidence unavailable", "Score not available"];
  if (!score.subscores) return ["Score breakdown pending", "Route evidence available"];

  const measuredReasons: string[] = [];
  if (typeof score.paths.sheltered_m === "number") {
    measuredReasons.push(`${formatDistance(score.paths.sheltered_m)} to ${transitModeLabel(transitMode)}`);
  }
  if (typeof score.paths.covered_ratio === "number") {
    measuredReasons.push(`${Math.round(score.paths.covered_ratio * 100)}% sheltered on Shiokest`);
  }

  const values = SUBSCORE_LABELS.map(([key]) => ({
    key,
    value: score.subscores?.[key] ?? 0,
  })).sort((a, b) => a.value - b.value);

  const lowReasons = values.filter((item) => item.value < 55).map((item) => REASON_COPY[item.key].low);
  if (lowReasons.length >= 2) return lowReasons.slice(0, 2);
  if (lowReasons.length === 1) {
    return [lowReasons[0], measuredReasons[0] ?? REASON_COPY[[...values].reverse()[0].key].high];
  }

  return (measuredReasons.length >= 2 ? measuredReasons : [...values]
    .reverse()
    .slice(0, 2)
    .map((item) => REASON_COPY[item.key].high));
}

function routeSourceBreakdown(
  selection: LoadedSelection | null,
  routeMode: RouteDisplayMode,
  sameRoute: boolean
): Array<{ source: string; label: string; lenM: number }> {
  const segments =
    routeMode === "shortest" && !sameRoute
      ? selection?.geom?.route_segments?.shortest
      : selection?.geom?.route_segments?.sheltered;
  if (!segments?.length) return [];

  const totals = new Map<string, number>();
  for (const segment of segments as RouteSegment[]) {
    const source = segment.source_class ?? (segment.is_covered ? "covered_unknown" : "exposed");
    totals.set(source, (totals.get(source) ?? 0) + segment.len_m);
  }
  return Array.from(totals.entries())
    .map(([source, lenM]) => ({
      source,
      label: SOURCE_LABELS[source] ?? source.replaceAll("_", " "),
      lenM,
    }))
    .filter((item) => item.lenM > 0)
    .sort((a, b) => b.lenM - a.lenM)
    .slice(0, 4);
}

function modeAdjustedTotal(score: ScoreRecord, mode: ComfortMode): number | null {
  const config = COMFORT_MODES.find((item) => item.id === mode);
  if (!config?.weights) return score.total;
  if (!score.subscores) return null;
  return Object.entries(config.weights).reduce((total, [key, weight]) => {
    const value = score.subscores?.[key as keyof Subscores];
    return total + (typeof value === "number" ? value : 0) * weight;
  }, 0);
}

function modeStatus(mode: ComfortMode): string {
  if (mode === "balanced") return "Balanced scoring with scheduled buses";
  if (mode.startsWith("rain")) return "Rain gives shelter more weight";
  return "Sunny mode gives heat comfort more weight";
}

function buildFeedbackPayload({
  selection,
  transitMode,
  routeMode,
  points,
  segmentLabels,
  note,
}: {
  selection: LoadedSelection | null;
  transitMode: TransitAccessMode;
  routeMode: RouteDisplayMode;
  points: FeedbackPoint[];
  segmentLabels: FeedbackSegmentLabel[];
  note: string;
}) {
  return {
    postal: selection?.result.POSTAL ?? null,
    destination: selection?.score?.best_node?.name ?? null,
    transit_mode: transitMode,
    route_mode: routeMode,
    issue: "user_reported_better_walk_route",
    source: "user_drawn_qa_evidence_not_score_override",
    waypoints: points.map((point) => [point.lat, point.lng]),
    segment_labels: segmentLabels.slice(0, Math.max(0, points.length - 1)),
    user_note: note.trim() || null,
    current_score_state: selection?.score?.state ?? null,
    current_total: selection?.score?.total ?? null,
    current_best_node: selection?.score?.best_node ?? null,
    current_paths: selection?.score?.paths ?? null,
    created_at: new Date().toISOString(),
  };
}

function RouteModeControl({
  mode,
  setMode,
  disabled,
  sameRoute,
  directBusFallback,
}: {
  mode: RouteDisplayMode;
  setMode: (mode: RouteDisplayMode) => void;
  disabled: boolean;
  sameRoute: boolean;
  directBusFallback: boolean;
}) {
  if (directBusFallback) {
    return <div className={styles.sameRouteNote}>Direct line to bus stop; walking route pending.</div>;
  }

  if (sameRoute) {
    return (
      <div className={styles.sameRouteNote}>
        Shortest is already the Shiokest route.
      </div>
    );
  }

  return (
    <div className={`${styles.segmented} ${styles.routeSegmented}`} aria-label="Route display">
      <button
        type="button"
        className={mode === "shiokest" ? styles.segmentedActive : undefined}
        aria-pressed={mode === "shiokest"}
        disabled={disabled}
        onClick={() => setMode("shiokest")}
      >
        Shiokest
      </button>
      <button
        type="button"
        className={mode === "both" ? styles.segmentedActive : undefined}
        aria-pressed={mode === "both"}
        disabled={disabled}
        onClick={() => setMode("both")}
      >
        Both
      </button>
      <button
        type="button"
        className={mode === "shortest" ? styles.segmentedActive : undefined}
        aria-pressed={mode === "shortest"}
        disabled={disabled}
        onClick={() => setMode("shortest")}
      >
        Shortest
      </button>
    </div>
  );
}

function TransitModeControl({
  score,
  mode,
  setMode,
}: {
  score: ScoreRecord;
  mode: TransitAccessMode;
  setMode: (mode: TransitAccessMode) => void;
}) {
  if (!score.route_options) return null;
  return (
    <div className={`${styles.segmented} ${styles.transitSegmented}`} aria-label="Transit target">
      {TRANSIT_MODE_OPTIONS.map((option) => {
        const routeOption = option.id === "best_transit" ? score : score.route_options?.[option.id];
        const available = Boolean(routeOption?.paths);
        return (
          <button
            key={option.id}
            type="button"
            className={mode === option.id ? styles.segmentedActive : undefined}
            aria-pressed={mode === option.id}
            data-empty={!available}
            onClick={() => setMode(option.id)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function ComfortModeControl({
  mode,
  setMode,
}: {
  mode: ComfortMode;
  setMode: (mode: ComfortMode) => void;
}) {
  return (
    <label className={styles.modeSelect}>
      <span>Mode</span>
      <select value={mode} onChange={(event) => setMode(event.target.value as ComfortMode)}>
        {COMFORT_MODES.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function InlineRouteLegend({
  sameRoute,
  directBusFallback,
}: {
  sameRoute: boolean;
  directBusFallback: boolean;
}) {
  return (
    <div className={styles.inlineLegend} aria-label="Map legend">
      <span>
        <i className={directBusFallback ? styles.directBusLine : styles.shiokestLine} />
        {directBusFallback ? "Direct bus estimate" : "Shiokest"}
      </span>
      {!directBusFallback && (
        <>
          {!sameRoute && (
            <span>
              <i className={styles.shortestLine} />
              Shortest
            </span>
          )}
          <span>
            <i className={styles.gapLine} />
            Exposed
          </span>
          <span>
            <i className={styles.hdbLine} />
            HDB inferred
          </span>
          <span>
            <i className={styles.bridgeLine} />
            Bridge/underpass
          </span>
        </>
      )}
      <span>
        <i className={styles.mrtDot} />
        MRT/LRT
      </span>
      <span>
        <i className={styles.busDot} />
        Bus stop
      </span>
    </div>
  );
}

function ScoreCard({
  selection,
  manifest,
  routeMode,
  setRouteMode,
  transitMode,
  setTransitMode,
  comfortMode,
  setComfortMode,
  feedbackEnabled,
  setFeedbackEnabled,
  feedbackPoints,
  feedbackSegmentLabels,
  setFeedbackSegmentLabel,
  clearFeedback,
  feedbackNote,
  setFeedbackNote,
  copyFeedback,
  copyStatus,
}: {
  selection: LoadedSelection | null;
  manifest: Manifest | null;
  routeMode: RouteDisplayMode;
  setRouteMode: (mode: RouteDisplayMode) => void;
  transitMode: TransitAccessMode;
  setTransitMode: (mode: TransitAccessMode) => void;
  comfortMode: ComfortMode;
  setComfortMode: (mode: ComfortMode) => void;
  feedbackEnabled: boolean;
  setFeedbackEnabled: (enabled: boolean) => void;
  feedbackPoints: FeedbackPoint[];
  feedbackSegmentLabels: FeedbackSegmentLabel[];
  setFeedbackSegmentLabel: (index: number, label: FeedbackSegmentLabel) => void;
  clearFeedback: () => void;
  feedbackNote: string;
  setFeedbackNote: (note: string) => void;
  copyFeedback: () => void;
  copyStatus: string;
}) {
  if (!selection) {
    return (
      <section className={styles.scoreCard} aria-label="Score panel">
        <div className={styles.emptyState}>
          <strong>Find a postal code</strong>
          <span>Search any Singapore address to see its walk-to-transit comfort score.</span>
        </div>
      </section>
    );
  }

  const { score } = selection;
  if (!score) {
    return (
      <section className={styles.scoreCard} aria-label="Score panel">
        <h2>{postalTitle(selection)}</h2>
        <div className={styles.emptyState}>
          <strong>Not yet scored</strong>
          <span>This postal is not in the current score bundle.</span>
        </div>
      </section>
    );
  }

  const sameRoute = routeSame(selection);
  const directBusFallback = score.paths?.routing_type === "direct_bus_fallback_unrouted";
  const extraWalkM =
    score.paths && typeof score.paths.shortest_m === "number" && typeof score.paths.sheltered_m === "number"
      ? Math.max(0, score.paths.sheltered_m - score.paths.shortest_m)
      : null;
  const coveredRatio = score.paths?.covered_ratio !== undefined ? Math.round(score.paths.covered_ratio * 100) : null;
  const shortestCoveredRatio =
    score.paths?.shortest_covered_ratio !== undefined ? Math.round(score.paths.shortest_covered_ratio * 100) : null;
  const selectedDistance =
    routeMode === "shortest" && !sameRoute ? score.paths?.shortest_m : score.paths?.sheltered_m;
  const selectedCoverage = routeMode === "shortest" && !sameRoute ? shortestCoveredRatio : coveredRatio;
  const selectedRouteLabel = directBusFallback
    ? "Direct bus estimate"
    : routeMode === "shortest" && !sameRoute
      ? "Shortest walk"
      : "Shiokest walk";
  const stationName =
    score.state === "NO_TRANSIT_IN_RANGE"
      ? noTransitTitle(score, transitMode)
      : score.state === "NOT_YET_SCORED"
        ? "Location Evidence Missing"
      : toProperCase(score.best_node?.name ?? "No transit found nearby");
  const reasons = scoreReasons(score, transitMode);
  const stateNote = scoreStateNote(score, transitMode);
  const displayScore = modeAdjustedTotal(score, comfortMode);
  const sourceBreakdown = routeSourceBreakdown(selection, routeMode, sameRoute);
  const exposureGaps = score.exposure_gaps ? [...score.exposure_gaps].sort((a, b) => b.len_m - a.len_m) : [];
  const extraWalkLabel =
    extraWalkM === null ? "Unavailable" : sameRoute || extraWalkM === 0 ? "0 m" : `+${Math.round(extraWalkM)} m`;
  const dataDate = manifest?.data_as_of
    ? new Date(manifest.data_as_of).toLocaleDateString("en-SG", { day: "numeric", month: "short", year: "numeric" })
    : "Unavailable";

  return (
    <section className={styles.scoreCard} aria-label="Score panel">
      <div className={styles.scoreHeader}>
        <div>
          <h2>{postalTitle(selection)}</h2>
          <p>{stationName}</p>
        </div>
        <div className={`${styles.scoreBadge} ${scoreClass(displayScore)}`}>
          <strong>{formatScoreWithMax(displayScore)}</strong>
        </div>
      </div>

      <TransitModeControl score={score} mode={transitMode} setMode={setTransitMode} />

      {score.paths && <InlineRouteLegend sameRoute={sameRoute} directBusFallback={directBusFallback} />}
      {sourceBreakdown.length > 0 && (
        <div className={styles.sourceStrip} aria-label="Route source evidence">
          {sourceBreakdown.map((item) => (
            <span key={item.source} data-source={item.source}>
              {item.label} <strong>{formatDistance(item.lenM)}</strong>
            </span>
          ))}
        </div>
      )}
      {score.paths && (
        <>
          <div className={styles.modeRow}>
            <ComfortModeControl mode={comfortMode} setMode={setComfortMode} />
            <span>{modeStatus(comfortMode)}</span>
          </div>

          <div className={styles.summaryGrid}>
            <Metric label={selectedRouteLabel} value={formatDistance(selectedDistance)} />
            <Metric label="Sheltered" value={formatPercent(selectedCoverage)} />
            <Metric label="Extra walk" value={extraWalkLabel} />
          </div>
        </>
      )}

      <div className={styles.reasonList} aria-label="Score reasons">
        {reasons.map((reason) => (
          <span key={reason}>{reason}</span>
        ))}
      </div>
      {stateNote && <p className={styles.stateNote}>{stateNote}</p>}

      {score.subscores && (
        <div className={styles.scoreStrip} aria-label="Score breakdown">
          {SUBSCORE_LABELS.map(([key, label]) => {
            const value = score.subscores?.[key] ?? null;
            return (
              <span key={key}>
                {label} <strong>{formatScore(value)}</strong>
              </span>
            );
          })}
        </div>
      )}

      {score.paths && !directBusFallback && (
        <RouteModeControl
          mode={routeMode}
          setMode={setRouteMode}
          disabled={false}
          sameRoute={sameRoute}
          directBusFallback={directBusFallback}
        />
      )}

      <div className={styles.feedbackBlock}>
        <div className={styles.feedbackActions}>
          <button
            type="button"
            aria-pressed={feedbackEnabled}
            onClick={() => setFeedbackEnabled(!feedbackEnabled)}
          >
            {feedbackEnabled ? "Done tracing" : "Suggest better route"}
          </button>
          <button type="button" onClick={clearFeedback} disabled={feedbackPoints.length === 0}>
            Clear
          </button>
          <button type="button" onClick={copyFeedback} disabled={feedbackPoints.length < 2}>
            Copy QA JSON
          </button>
        </div>
        {feedbackPoints.length > 0 && (
          <div className={styles.feedbackEditor}>
            <div className={styles.feedbackMeta}>
              {feedbackPoints.length} points / {Math.max(0, feedbackPoints.length - 1)} segments
              {copyStatus ? <span>{copyStatus}</span> : null}
            </div>
            {feedbackSegmentLabels.map((label, index) => (
              <label key={`segment-${index}`} className={styles.segmentLabel}>
                <span>Segment {index + 1}</span>
                <select
                  value={label}
                  onChange={(event) =>
                    setFeedbackSegmentLabel(index, event.target.value as FeedbackSegmentLabel)
                  }
                >
                  {FEEDBACK_SEGMENT_OPTIONS.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <textarea
              value={feedbackNote}
              onChange={(event) => setFeedbackNote(event.target.value)}
              placeholder="Optional route note"
              rows={2}
            />
          </div>
        )}
      </div>

      <details className={styles.detailBlock}>
        <summary>Details</summary>

      {score.paths && (
        <div className={styles.routeFacts}>
          <Metric label="Shiokest" value={formatDistance(score.paths.sheltered_m)} />
          <Metric label="Shortest" value={formatDistance(score.paths.shortest_m)} />
          <Metric label="Extra walk" value={extraWalkLabel} />
          <Metric label="Extra vs shortest" value={`${Math.round(score.paths.detour_pct ?? 0)}%`} />
          <Metric label="Shiokest sheltered" value={formatPercent(coveredRatio)} />
          <Metric label="Shortest sheltered" value={formatPercent(shortestCoveredRatio)} />
          <Metric label="Shade proxy" value={formatPercent(Math.round((score.paths.shade_ratio ?? 0) * 100))} />
        </div>
      )}

      {exposureGaps.length > 0 && (
        <div className={styles.gapList}>
          <h3>Exposed gaps</h3>
          {exposureGaps.slice(0, 3).map((gap, index) => (
            <div key={`${gap.label}-${index}`} className={styles.gapItem}>
              <strong>{formatDistance(gap.len_m)}</strong>
              <span>{exposureGapCopy(gap.len_m, index)}</span>
            </div>
          ))}
        </div>
      )}

        <div className={styles.dataLine}>
          Data as of {dataDate} - Heat: shelter plus NParks shade proxy
        </div>
      </details>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [primary, setPrimary] = useState<LoadedSelection | null>(null);
  const [baseTransitPois, setBaseTransitPois] = useState<TransitPoiCollection>({ type: "FeatureCollection", features: [] });
  const [routeTransitPois, setRouteTransitPois] = useState<TransitPoiCollection>({ type: "FeatureCollection", features: [] });
  const [transitMode, setTransitMode] = useState<TransitAccessMode>("best_transit");
  const [routeMode, setRouteMode] = useState<RouteDisplayMode>("shiokest");
  const [comfortMode, setComfortMode] = useState<ComfortMode>("balanced");
  const [feedbackEnabled, setFeedbackEnabled] = useState(false);
  const [feedbackPoints, setFeedbackPoints] = useState<FeedbackPoint[]>([]);
  const [feedbackSegmentLabels, setFeedbackSegmentLabels] = useState<FeedbackSegmentLabel[]>([]);
  const [feedbackNote, setFeedbackNote] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeSelection = useMemo(() => selectionForTransitMode(primary, transitMode), [primary, transitMode]);
  const mapRoutes = useMemo(() => buildRouteItems(activeSelection), [activeSelection]);
  const mapRouteMode = routeSame(activeSelection) ? "shiokest" : routeMode;
  const mapTransitPois = routeTransitPois.features.length > 0 ? routeTransitPois : baseTransitPois;
  const showDetailOverlay = Boolean(primary);

  useEffect(() => {
    let active = true;
    void fetchTransitPois().then((pois) => {
      if (active) setBaseTransitPois(pois);
    });
    return () => {
      active = false;
    };
  }, []);

  const loadSelection = async (result: SearchResult) => {
    const postal = normalizePostal(result.POSTAL);
    if (!postal) {
      setError("Selected result has no usable postal code.");
      return;
    }
    setLoading(true);
    setError(null);
    setRouteTransitPois({ type: "FeatureCollection", features: [] });
    try {
      const lat = Number.parseFloat(result.LATITUDE);
      const lng = Number.parseFloat(result.LONGITUDE);
      const [loadedManifest, score, geom] = await Promise.all([
        manifest ? Promise.resolve(manifest) : fetchManifest(),
        fetchScoreForPostal(postal),
        fetchGeomForPostal(postal, Number.isFinite(lat) ? lat : undefined, Number.isFinite(lng) ? lng : undefined),
      ]);
      const nearbyTransitPois = await fetchTransitPoisForGeom(geom);
      setManifest(loadedManifest);
      setPrimary({ result: { ...result, POSTAL: postal }, score, geom });
      setRouteTransitPois(nearbyTransitPois);
      setTransitMode("best_transit");
      setRouteMode("shiokest");
      setFeedbackEnabled(false);
      setFeedbackPoints([]);
      setFeedbackSegmentLabels([]);
      setFeedbackNote("");
      setCopyStatus("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load score data.");
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!query.trim()) return;

    const directPostal = normalizePostal(query);
    if (directPostal) {
      await loadSelection({
        BUILDING: `Postal ${directPostal}`,
        ROAD_NAME: "",
        POSTAL: directPostal,
        LATITUDE: "",
        LONGITUDE: "",
        SEARCHVAL: `S${directPostal}`,
      });
      return;
    }

    setLoading(true);
    setError(null);
    setResults([]);

    try {
      const res = await fetch(`/api/onemap-search?searchVal=${encodeURIComponent(query)}`);
      if (res.status === 429) {
        setError("Search is busy. Please try again in a moment.");
        return;
      }
      if (!res.ok) throw new Error(`Search failed with status ${res.status}`);
      const data = await res.json();
      setResults(data.results || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to search postal location.");
    } finally {
      setLoading(false);
    }
  };

  const addFeedbackPoint = (point: FeedbackPoint) => {
    setCopyStatus("");
    setFeedbackPoints((current) => {
      if (current.length >= 24) return current;
      if (current.length >= 1) {
        setFeedbackSegmentLabels((labels) => [...labels, "sheltered"]);
      }
      return [...current, point];
    });
  };

  const setFeedbackSegmentLabel = (index: number, label: FeedbackSegmentLabel) => {
    setFeedbackSegmentLabels((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? label : item))
    );
  };

  const clearFeedback = () => {
    setFeedbackPoints([]);
    setFeedbackSegmentLabels([]);
    setFeedbackNote("");
    setCopyStatus("");
  };

  const copyFeedback = async () => {
    const payload = buildFeedbackPayload({
      selection: activeSelection,
      transitMode,
      routeMode: mapRouteMode,
      points: feedbackPoints,
      segmentLabels: feedbackSegmentLabels,
      note: feedbackNote,
    });
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopyStatus("Copied");
    } catch {
      setCopyStatus("Copy failed");
    }
  };

  return (
    <main className={styles.appShell}>
      <RouteEvidenceMap
        routes={mapRoutes}
        mode={mapRouteMode}
        transitPois={mapTransitPois}
        feedbackEnabled={feedbackEnabled}
        feedbackPoints={feedbackPoints}
        onFeedbackPoint={addFeedbackPoint}
      />

      <section className={styles.searchOverlay} aria-label="Address search">
        <div className={styles.brandRow}>
          <div>
            <h1>S.H.I.O.K. Index</h1>
            <p>Singapore walk-to-transit comfort</p>
            <p className={styles.sourceLine}>Data: LTA, data.gov.sg, OneMap, OSM</p>
          </div>
        </div>

        <form onSubmit={handleSearch} className={styles.searchForm}>
          <input
            id="postal-search-input"
            type="text"
            placeholder="Search address or postal"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search address or postal"
          />
          <button id="postal-search-button" type="submit" disabled={loading}>
            {loading ? "Loading" : "Search"}
          </button>
        </form>

        {error && <div className={styles.errorBox}>{error}</div>}

        {results.length > 0 && (
          <div className={styles.resultList}>
            {results.map((item, idx) => (
              <button key={`${item.POSTAL}-${idx}`} type="button" onClick={() => loadSelection(item)}>
                <span>
                  <strong>{resultTitle(item)}</strong>
                  <small>{resultSubtitle(item)}</small>
                </span>
                <em>S{normalizePostal(item.POSTAL) ?? item.POSTAL}</em>
              </button>
            ))}
          </div>
        )}

        {showDetailOverlay && (
          <aside className={styles.detailOverlay}>
            <ScoreCard
              selection={activeSelection}
              manifest={manifest}
              routeMode={mapRouteMode}
              setRouteMode={setRouteMode}
              transitMode={transitMode}
              setTransitMode={setTransitMode}
              comfortMode={comfortMode}
              setComfortMode={setComfortMode}
              feedbackEnabled={feedbackEnabled}
              setFeedbackEnabled={setFeedbackEnabled}
              feedbackPoints={feedbackPoints}
              feedbackSegmentLabels={feedbackSegmentLabels}
              setFeedbackSegmentLabel={setFeedbackSegmentLabel}
              clearFeedback={clearFeedback}
              feedbackNote={feedbackNote}
              setFeedbackNote={setFeedbackNote}
              copyFeedback={copyFeedback}
              copyStatus={copyStatus}
            />
          </aside>
        )}
      </section>
    </main>
  );
}
