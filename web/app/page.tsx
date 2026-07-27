"use client";

import React, { useMemo, useState } from "react";
import { DATA_BASE, fetchGeomForPostal, fetchManifest, fetchScoreForPostal } from "../lib/data";
import type { Manifest, PostalGeom, ScoreRecord, Subscores } from "../lib/types";
import { decodePolyline, type LatLng } from "../lib/polyline";

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

const DEMO_RESULTS: SearchResult[] = [
  {
    BUILDING: "High coverage demo",
    ROAD_NAME: "Mock data",
    POSTAL: "560123",
    LATITUDE: "1.36959",
    LONGITUDE: "103.84932",
    SEARCHVAL: "S560123",
  },
  {
    BUILDING: "Mid coverage demo",
    ROAD_NAME: "Mock data",
    POSTAL: "560456",
    LATITUDE: "1.37008",
    LONGITUDE: "103.84698",
    SEARCHVAL: "S560456",
  },
  {
    BUILDING: "Low coverage demo",
    ROAD_NAME: "Mock data",
    POSTAL: "560789",
    LATITUDE: "1.37142",
    LONGITUDE: "103.84472",
    SEARCHVAL: "S560789",
  },
];

const SUBSCORE_LABELS: Array<[keyof Subscores, string]> = [
  ["access", "Transit access"],
  ["bus", "Bus connectivity"],
  ["rain", "Rain shelter"],
  ["heat", "Heat comfort"],
  ["crossing", "Crossings"],
];

function normalizePostal(value: string): string | null {
  const trimmed = value.trim();
  if (!/^\d{1,6}$/.test(trimmed)) return null;
  return trimmed.padStart(6, "0");
}

function resultTitle(result: SearchResult): string {
  if (result.BUILDING && result.BUILDING !== "N/A") return result.BUILDING;
  return result.SEARCHVAL || `S${result.POSTAL}`;
}

function resultSubtitle(result: SearchResult): string {
  const road = result.ROAD_NAME && result.ROAD_NAME !== "N/A" ? result.ROAD_NAME : "";
  return [road, result.POSTAL && result.POSTAL !== "N/A" ? `S${result.POSTAL}` : ""]
    .filter(Boolean)
    .join(" ");
}

function scoreTone(total: number | null): string {
  if (total === null) return "#64748b";
  if (total >= 80) return "#16a34a";
  if (total >= 55) return "#d97706";
  return "#dc2626";
}

function formatScore(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(1) : "Pending";
}

function projectPoints(paths: LatLng[][], width: number, height: number): LatLng[][] {
  const all = paths.flat();
  if (all.length === 0) return paths;
  const lats = all.map(([lat]) => lat);
  const lngs = all.map(([, lng]) => lng);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs);
  const maxLng = Math.max(...lngs);
  const latRange = Math.max(maxLat - minLat, 0.00001);
  const lngRange = Math.max(maxLng - minLng, 0.00001);
  const pad = 24;

  return paths.map((path) =>
    path.map(([lat, lng]) => {
      const x = pad + ((lng - minLng) / lngRange) * (width - pad * 2);
      const y = pad + ((maxLat - lat) / latRange) * (height - pad * 2);
      return [y, x];
    })
  );
}

function svgPath(points: LatLng[]): string {
  return points.map(([y, x], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
}

function RoutePreview({ geom }: { geom: PostalGeom | null }) {
  const width = 560;
  const height = 280;
  const paths = useMemo(() => {
    if (!geom) return null;
    const shortest = decodePolyline(geom.shortest);
    const sheltered = decodePolyline(geom.sheltered);
    const gaps = geom.exposure_gaps.map((gap) => decodePolyline(gap.geom));
    const [shortestProjected, shelteredProjected, ...gapProjected] = projectPoints(
      [shortest, sheltered, ...gaps],
      width,
      height
    );
    return { shortestProjected, shelteredProjected, gapProjected };
  }, [geom]);

  if (!geom || !paths) {
    return (
      <div style={emptyBoxStyle}>
        <strong>Route evidence unavailable</strong>
        <span>Geometry appears when a scored postal has a matching route shard.</span>
      </div>
    );
  }

  return (
    <div style={routePanelStyle}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Route evidence preview" style={{ width: "100%", height: "100%" }}>
        <rect x="0" y="0" width={width} height={height} fill="#f8fafc" />
        <path d={svgPath(paths.shortestProjected)} fill="none" stroke="#64748b" strokeWidth="6" strokeDasharray="8 7" strokeLinecap="round" strokeLinejoin="round" />
        <path d={svgPath(paths.shelteredProjected)} fill="none" stroke="#0284c7" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
        {paths.gapProjected.map((gap, index) => (
          <path key={index} d={svgPath(gap)} fill="none" stroke="#dc2626" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
        ))}
      </svg>
      <div style={legendStyle}>
        <span><i style={{ background: "#0284c7" }} />Sheltered route</span>
        <span><i style={{ background: "#64748b" }} />Shortest route</span>
        <span><i style={{ background: "#dc2626" }} />Exposed gaps</span>
      </div>
    </div>
  );
}

function ScorePanel({ selection, manifest }: { selection: LoadedSelection | null; manifest: Manifest | null }) {
  if (!selection) {
    return (
      <section style={panelStyle}>
        <div style={emptyBoxStyle}>
          <strong>Select an address</strong>
          <span>Search for a Singapore address or use a demo postal to view the score record.</span>
        </div>
      </section>
    );
  }

  const { score } = selection;
  if (!score) {
    return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{resultTitle(selection.result)}</h2>
        <div style={emptyBoxStyle}>
          <strong>Not yet scored</strong>
          <span>This postal is not present in the current static score bundle.</span>
        </div>
      </section>
    );
  }

  const totalTone = scoreTone(score.total);

  return (
    <section style={panelStyle}>
      <div style={scoreHeaderStyle}>
        <div>
          <h2 style={sectionTitleStyle}>{resultTitle(selection.result)}</h2>
          <div style={mutedStyle}>{resultSubtitle(selection.result)}</div>
        </div>
        <div style={{ ...scoreBadgeStyle, borderColor: totalTone, color: totalTone }}>
          {formatScore(score.total)}
        </div>
      </div>

      <div style={statusLineStyle}>
        <span>{score.state.replaceAll("_", " ")}</span>
        <span>{score.best_node?.name ?? "No transit node selected"}</span>
        <span>{manifest?.data_as_of ? `Data as of ${manifest.data_as_of}` : "Data date pending"}</span>
      </div>

      {score.subscores && (
        <div style={barGridStyle}>
          {SUBSCORE_LABELS.map(([key, label]) => {
            const value = score.subscores?.[key] ?? null;
            return (
              <div key={key}>
                <div style={barLabelStyle}>
                  <span>{label}</span>
                  <span>{formatScore(value)}</span>
                </div>
                <div style={barTrackStyle}>
                  <div style={{ ...barFillStyle, width: `${Math.max(0, Math.min(value ?? 0, 100))}%`, background: scoreTone(value) }} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {score.paths && (
        <div style={metricGridStyle}>
          <Metric label="Shortest" value={`${score.paths.shortest_m} m`} />
          <Metric label="Sheltered" value={`${score.paths.sheltered_m} m`} />
          <Metric label="Detour" value={`${score.paths.detour_pct}%`} />
          <Metric label="Covered" value={`${Math.round((score.paths.covered_ratio ?? 0) * 100)}%`} />
        </div>
      )}

      <RoutePreview geom={selection.geom} />

      {score.exposure_gaps && score.exposure_gaps.length > 0 && (
        <div style={gapListStyle}>
          {score.exposure_gaps.slice(0, 4).map((gap, index) => (
            <div key={`${gap.label}-${index}`} style={gapItemStyle}>
              <strong>{gap.len_m} m</strong>
              <span>{gap.label}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={metricStyle}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>(DEMO_RESULTS);
  const [selection, setSelection] = useState<LoadedSelection | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      setSelection({ result: { ...result, POSTAL: postal }, score, geom });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load score data.");
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (e: React.FormEvent) => {
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
        setError("Rate limit exceeded. Please try again in a moment.");
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
    <div style={pageStyle}>
      <header style={headerStyle}>
        <div style={headerInnerStyle}>
          <div>
            <h1 style={titleStyle}>S.H.I.O.K. Index</h1>
            <p style={subtitleStyle}>Singapore walk-to-transit comfort score</p>
          </div>
          <span style={modePillStyle}>{DATA_BASE.includes("/mock/") ? "Mock data" : "Real data"}</span>
        </div>
      </header>

      <main style={mainStyle}>
        <section style={searchPanelStyle}>
          <form onSubmit={handleSearch} style={formStyle}>
            <input
              id="postal-search-input"
              type="text"
              placeholder="Search address or postal"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={inputStyle}
            />
            <button id="postal-search-button" type="submit" disabled={loading} style={buttonStyle}>
              {loading ? "Loading" : "Search"}
            </button>
          </form>

          {error && <div style={errorStyle}>{error}</div>}

          <div style={resultListStyle}>
            {results.map((item, idx) => (
              <button key={`${item.POSTAL}-${idx}`} type="button" onClick={() => loadSelection(item)} style={resultButtonStyle}>
                <span>
                  <strong>{resultTitle(item)}</strong>
                  <small>{resultSubtitle(item)}</small>
                </span>
                <span style={postalPillStyle}>S{normalizePostal(item.POSTAL) ?? item.POSTAL}</span>
              </button>
            ))}
          </div>
        </section>

        <ScorePanel selection={selection} manifest={manifest} />
      </main>

      <footer style={footerStyle}>
        <strong>S.H.I.O.K. Index is a free, non-commercial civic open-data project.</strong>
        <span>LTA DataMall, data.gov.sg, OneMap, and OpenStreetMap attribution applies to shipped data and routes.</span>
      </footer>
    </div>
  );
}

const pageStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minHeight: "100vh",
  background: "#f8fafc",
  color: "#0f172a",
};

const headerStyle: React.CSSProperties = {
  borderBottom: "1px solid #d8dee7",
  background: "#ffffff",
};

const headerInnerStyle: React.CSSProperties = {
  maxWidth: "1180px",
  margin: "0 auto",
  padding: "18px 24px",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "16px",
};

const titleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: "26px",
  lineHeight: 1.1,
  fontWeight: 700,
};

const subtitleStyle: React.CSSProperties = {
  margin: "4px 0 0",
  color: "#64748b",
  fontSize: "14px",
};

const modePillStyle: React.CSSProperties = {
  border: "1px solid #cbd5e1",
  color: "#475569",
  padding: "7px 10px",
  borderRadius: "999px",
  fontSize: "12px",
  whiteSpace: "nowrap",
};

const mainStyle: React.CSSProperties = {
  flex: 1,
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 340px), 1fr))",
  gap: "20px",
  width: "100%",
  maxWidth: "1180px",
  margin: "0 auto",
  padding: "24px",
};

const searchPanelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "16px",
};

const panelStyle: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #d8dee7",
  borderRadius: "8px",
  padding: "20px",
  minHeight: "560px",
};

const formStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr auto",
  gap: "8px",
};

const inputStyle: React.CSSProperties = {
  minWidth: 0,
  padding: "12px 13px",
  borderRadius: "8px",
  border: "1px solid #cbd5e1",
  color: "#0f172a",
  fontSize: "15px",
};

const buttonStyle: React.CSSProperties = {
  padding: "12px 14px",
  borderRadius: "8px",
  border: "1px solid #0f172a",
  background: "#0f172a",
  color: "#ffffff",
  fontSize: "15px",
  fontWeight: 600,
  cursor: "pointer",
};

const errorStyle: React.CSSProperties = {
  padding: "12px",
  borderRadius: "8px",
  border: "1px solid #fecaca",
  background: "#fef2f2",
  color: "#991b1b",
  fontSize: "14px",
};

const resultListStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "8px",
};

const resultButtonStyle: React.CSSProperties = {
  border: "1px solid #d8dee7",
  borderRadius: "8px",
  background: "#ffffff",
  padding: "12px",
  display: "flex",
  justifyContent: "space-between",
  gap: "12px",
  textAlign: "left",
  cursor: "pointer",
  color: "#0f172a",
};

const postalPillStyle: React.CSSProperties = {
  fontSize: "12px",
  color: "#475569",
  paddingTop: "2px",
};

const sectionTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: "22px",
  lineHeight: 1.2,
};

const mutedStyle: React.CSSProperties = {
  color: "#64748b",
  fontSize: "14px",
  marginTop: "4px",
};

const scoreHeaderStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: "16px",
  marginBottom: "14px",
};

const scoreBadgeStyle: React.CSSProperties = {
  border: "2px solid",
  borderRadius: "8px",
  minWidth: "92px",
  textAlign: "center",
  padding: "10px",
  fontSize: "28px",
  fontWeight: 700,
};

const statusLineStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "8px",
  color: "#475569",
  fontSize: "13px",
  marginBottom: "18px",
};

const barGridStyle: React.CSSProperties = {
  display: "grid",
  gap: "12px",
  marginBottom: "18px",
};

const barLabelStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: "10px",
  fontSize: "13px",
  marginBottom: "5px",
};

const barTrackStyle: React.CSSProperties = {
  height: "8px",
  borderRadius: "999px",
  background: "#e2e8f0",
  overflow: "hidden",
};

const barFillStyle: React.CSSProperties = {
  height: "100%",
  borderRadius: "999px",
};

const metricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
  gap: "10px",
  marginBottom: "18px",
};

const metricStyle: React.CSSProperties = {
  border: "1px solid #e2e8f0",
  borderRadius: "8px",
  padding: "10px",
  display: "flex",
  flexDirection: "column",
  gap: "3px",
  fontSize: "13px",
};

const routePanelStyle: React.CSSProperties = {
  border: "1px solid #d8dee7",
  borderRadius: "8px",
  overflow: "hidden",
  height: "320px",
  marginBottom: "14px",
};

const legendStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "12px",
  padding: "8px 10px",
  borderTop: "1px solid #d8dee7",
  background: "#ffffff",
  color: "#475569",
  fontSize: "12px",
};

const emptyBoxStyle: React.CSSProperties = {
  minHeight: "180px",
  border: "1px dashed #cbd5e1",
  borderRadius: "8px",
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  alignItems: "center",
  gap: "6px",
  color: "#64748b",
  textAlign: "center",
  padding: "20px",
};

const gapListStyle: React.CSSProperties = {
  display: "grid",
  gap: "8px",
};

const gapItemStyle: React.CSSProperties = {
  display: "flex",
  gap: "10px",
  alignItems: "baseline",
  color: "#475569",
  fontSize: "13px",
};

const footerStyle: React.CSSProperties = {
  borderTop: "1px solid #d8dee7",
  background: "#ffffff",
  padding: "16px 24px",
  color: "#475569",
  fontSize: "12px",
  display: "flex",
  flexWrap: "wrap",
  gap: "10px",
  justifyContent: "center",
};
