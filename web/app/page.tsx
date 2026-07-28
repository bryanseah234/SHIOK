"use client";

import React, { useMemo, useState } from "react";
import { fetchGeomForPostal, fetchManifest, fetchScoreForPostal } from "../lib/data";
import type { Manifest, PostalGeom, ScoreRecord, Subscores } from "../lib/types";
import { RouteEvidenceMap, type RouteDisplayMode, type RouteMapItem } from "../components/route-evidence-map";
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

const REASON_COPY: Record<keyof Subscores, { low: string; high: string }> = {
  access: { low: "Longer walk to transit", high: "Short walk to transit" },
  rain: { low: "Mostly exposed to rain", high: "Good rain shelter coverage" },
  heat: { low: "Low shade and shelter comfort", high: "Better heat comfort" },
  bus: { low: "Limited bus connectivity", high: "Strong bus connectivity" },
  crossing: { low: "More crossing friction", high: "Easy crossing profile" },
};

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
  return typeof value === "number" ? `${Math.round(value)}` : "Pending";
}

function formatScoreWithMax(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value)}/100` : "Pending";
}

function formatDistance(value: number | undefined): string {
  if (typeof value !== "number") return "Pending";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} km` : `${Math.round(value)} m`;
}

function formatPercent(value: number | null): string {
  return typeof value === "number" ? `${value}%` : "Pending";
}

function routeSame(selection: LoadedSelection | null): boolean {
  if (!selection?.geom || !selection.score?.paths) return false;
  return (
    selection.geom.shortest === selection.geom.sheltered ||
    Math.round(selection.score.paths.shortest_m) === Math.round(selection.score.paths.sheltered_m)
  );
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
      color: "#007a78",
    });
  }
  return items;
}

function scoreReasons(score: ScoreRecord): string[] {
  if (!score.paths || !score.best_node) return ["No routed transit stop nearby", "Score is pending route evidence"];
  if (!score.subscores) return ["Score breakdown pending", "Route evidence available"];

  const values = SUBSCORE_LABELS.map(([key]) => ({
    key,
    value: score.subscores?.[key] ?? 0,
  })).sort((a, b) => a.value - b.value);

  const lowReasons = values.filter((item) => item.value < 55).map((item) => REASON_COPY[item.key].low);
  if (lowReasons.length >= 2) return lowReasons.slice(0, 2);
  if (lowReasons.length === 1) {
    const strongest = [...values].reverse()[0];
    return [lowReasons[0], REASON_COPY[strongest.key].high];
  }

  return [...values]
    .reverse()
    .slice(0, 2)
    .map((item) => REASON_COPY[item.key].high);
}

function RouteModeControl({
  mode,
  setMode,
  disabled,
  sameRoute,
}: {
  mode: RouteDisplayMode;
  setMode: (mode: RouteDisplayMode) => void;
  disabled: boolean;
  sameRoute: boolean;
}) {
  if (sameRoute) {
    return (
      <div className={styles.sameRouteNote}>
        Shortest is already the Shiokest route.
      </div>
    );
  }

  return (
    <div className={styles.segmented} aria-label="Route display">
      <button
        type="button"
        className={mode === "shiokest" ? styles.segmentedActive : undefined}
        disabled={disabled}
        onClick={() => setMode("shiokest")}
      >
        Shiokest
      </button>
      <button
        type="button"
        className={mode === "shortest" ? styles.segmentedActive : undefined}
        disabled={disabled}
        onClick={() => setMode("shortest")}
      >
        Shortest
      </button>
    </div>
  );
}

function ScoreCard({
  selection,
  manifest,
  routeMode,
  setRouteMode,
}: {
  selection: LoadedSelection | null;
  manifest: Manifest | null;
  routeMode: RouteDisplayMode;
  setRouteMode: (mode: RouteDisplayMode) => void;
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
  const selectedRouteLabel = routeMode === "shortest" && !sameRoute ? "Shortest walk" : "Shiokest walk";
  const stationName = toProperCase(score.best_node?.name ?? "No transit found nearby");
  const reasons = scoreReasons(score);
  const dataDate = manifest?.data_as_of
    ? new Date(manifest.data_as_of).toLocaleDateString("en-SG", { day: "numeric", month: "short", year: "numeric" })
    : "Pending";

  return (
    <section className={styles.scoreCard} aria-label="Score panel">
      <div className={styles.scoreHeader}>
        <div>
          <h2>{postalTitle(selection)}</h2>
          <p>{stationName}</p>
        </div>
        <div className={`${styles.scoreBadge} ${scoreClass(score.total)}`}>
          <strong>{formatScore(score.total)}</strong>
          <span>/100</span>
        </div>
      </div>

      <div className={styles.summaryGrid}>
        <Metric label={selectedRouteLabel} value={formatDistance(selectedDistance)} />
        <Metric label="Sheltered" value={formatPercent(selectedCoverage)} />
        <Metric label="Extra walk" value={sameRoute || !extraWalkM ? "0 m" : `+${Math.round(extraWalkM)} m`} />
      </div>

      <div className={styles.reasonList} aria-label="Score reasons">
        {reasons.map((reason) => (
          <span key={reason}>{reason}</span>
        ))}
      </div>

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

      <RouteModeControl mode={routeMode} setMode={setRouteMode} disabled={false} sameRoute={sameRoute} />

      <details className={styles.detailBlock}>
        <summary>Details</summary>

      {score.subscores && (
        <div className={styles.subscoreGrid}>
          {SUBSCORE_LABELS.map(([key, label]) => {
            const value = score.subscores?.[key] ?? null;
            return (
              <div key={key} className={styles.subscoreRow}>
                <div>
                  <span>{label}</span>
                  <strong>{formatScoreWithMax(value)}</strong>
                </div>
                <div className={styles.barTrack} aria-hidden="true">
                  <div
                    className={styles.barFill}
                    style={{ width: `${Math.max(0, Math.min(value ?? 0, 100))}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {score.paths && (
        <div className={styles.routeFacts}>
          <Metric label="Shiokest" value={formatDistance(score.paths.sheltered_m)} />
          <Metric label="Shortest" value={formatDistance(score.paths.shortest_m)} />
          <Metric label="Extra walk" value={sameRoute || !extraWalkM ? "0 m" : `+${Math.round(extraWalkM)} m`} />
          <Metric label="Detour" value={`${Math.round(score.paths.detour_pct ?? 0)}%`} />
          <Metric label="Shiokest sheltered" value={formatPercent(coveredRatio)} />
          <Metric label="Shortest sheltered" value={formatPercent(shortestCoveredRatio)} />
        </div>
      )}

      {score.exposure_gaps && score.exposure_gaps.length > 0 && (
        <div className={styles.gapList}>
          <h3>Exposed gaps</h3>
          {score.exposure_gaps.slice(0, 3).map((gap, index) => (
            <div key={`${gap.label}-${index}`} className={styles.gapItem}>
              <strong>{formatDistance(gap.len_m)}</strong>
              <span>{toProperCase(gap.label)}</span>
            </div>
          ))}
        </div>
      )}

        <div className={styles.dataLine}>Data as of {dataDate}</div>
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
  const [routeMode, setRouteMode] = useState<RouteDisplayMode>("shiokest");
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mapRoutes = useMemo(() => buildRouteItems(primary), [primary]);
  const mapRouteMode = routeSame(primary) ? "shiokest" : routeMode;
  const showDetailOverlay = Boolean(primary);

  const loadSelection = async (result: SearchResult) => {
    const postal = normalizePostal(result.POSTAL);
    if (!postal) {
      setError("Selected result has no usable postal code.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const lat = Number.parseFloat(result.LATITUDE);
      const lng = Number.parseFloat(result.LONGITUDE);
      const [loadedManifest, score, geom] = await Promise.all([
        manifest ? Promise.resolve(manifest) : fetchManifest(),
        fetchScoreForPostal(postal),
        fetchGeomForPostal(postal, Number.isFinite(lat) ? lat : undefined, Number.isFinite(lng) ? lng : undefined),
      ]);
      setManifest(loadedManifest);
      setPrimary({ result: { ...result, POSTAL: postal }, score, geom });
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
        ROAD_NAME: "Direct lookup",
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

  return (
    <main className={styles.appShell}>
      <RouteEvidenceMap routes={mapRoutes} mode={mapRouteMode} />

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
              selection={primary}
              manifest={manifest}
              routeMode={mapRouteMode}
              setRouteMode={setRouteMode}
            />
          </aside>
        )}
      </section>
    </main>
  );
}
