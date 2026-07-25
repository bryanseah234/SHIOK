"use client";

import React, { useState } from "react";

interface SearchResult {
  BUILDING: string;
  ROAD_NAME: string;
  POSTAL: string;
  LATITUDE: string;
  LONGITUDE: string;
  SEARCHVAL: string;
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    setResults([]);

    try {
      const res = await fetch(`/api/onemap-search?searchVal=${encodeURIComponent(query)}`);
      if (res.status === 429) {
        setError("Rate limit exceeded (30 req/min). Please try again in a moment.");
        setLoading(false);
        return;
      }

      if (!res.ok) {
        throw new Error(`Search failed with status ${res.status}`);
      }

      const data = await res.json();
      setResults(data.results || []);
    } catch (err: any) {
      setError(err.message || "Failed to search postal location.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      {/* Header */}
      <header
        style={{
          padding: "24px 32px",
          borderBottom: "1px solid #1e293b",
          background: "linear-gradient(180deg, #1e293b 0%, #0f172a 100%)",
        }}
      >
        <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
          <h1
            style={{
              fontSize: "28px",
              fontWeight: 700,
              margin: 0,
              background: "linear-gradient(135deg, #38bdf8 0%, #818cf8 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}
          >
            S.H.I.O.K. Index
          </h1>
          <p style={{ color: "#94a3b8", margin: "6px 0 0 0", fontSize: "15px" }}>
            Singapore Walk-to-Transit Comfort Index — Rain Shelter, Heat, Crossing Friction, & Access
          </p>
        </div>
      </header>

      {/* Main Content */}
      <main style={{ flex: 1, padding: "40px 24px", maxWidth: "900px", margin: "0 auto", width: "100%" }}>
        <section
          style={{
            background: "#1e293b",
            borderRadius: "16px",
            padding: "32px",
            boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.3)",
            border: "1px solid #334155",
          }}
        >
          <h2 style={{ fontSize: "20px", marginTop: 0, marginBottom: "16px", fontWeight: 600 }}>
            Lookup Address or Postal Code
          </h2>
          <form onSubmit={handleSearch} style={{ display: "flex", gap: "12px" }}>
            <input
              id="postal-search-input"
              type="text"
              placeholder="e.g. Toa Payoh, 310100, Orchard..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{
                flex: 1,
                padding: "14px 18px",
                borderRadius: "10px",
                border: "1px solid #475569",
                backgroundColor: "#0f172a",
                color: "#fff",
                fontSize: "16px",
                outline: "none",
              }}
            />
            <button
              id="postal-search-button"
              type="submit"
              disabled={loading}
              style={{
                padding: "14px 28px",
                borderRadius: "10px",
                border: "none",
                background: "linear-gradient(135deg, #0284c7 0%, #4f46e5 100%)",
                color: "#fff",
                fontSize: "16px",
                fontWeight: 600,
                cursor: "pointer",
                transition: "opacity 0.2s",
              }}
            >
              {loading ? "Searching..." : "Search"}
            </button>
          </form>

          {error && (
            <div
              style={{
                marginTop: "20px",
                padding: "14px 18px",
                borderRadius: "10px",
                backgroundColor: "#450a0a",
                border: "1px solid #991b1b",
                color: "#fca5a5",
                fontSize: "14px",
              }}
            >
              {error}
            </div>
          )}

          {/* Results List */}
          {results.length > 0 && (
            <div style={{ marginTop: "24px" }}>
              <h3 style={{ fontSize: "16px", color: "#94a3b8", marginBottom: "12px" }}>
                Top Results ({results.length}):
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {results.map((item, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: "16px",
                      borderRadius: "10px",
                      backgroundColor: "#0f172a",
                      border: "1px solid #334155",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: "16px", color: "#f8fafc" }}>
                        {item.BUILDING !== "N/A" ? item.BUILDING : item.SEARCHVAL}
                      </div>
                      <div style={{ fontSize: "14px", color: "#94a3b8", marginTop: "4px" }}>
                        {item.ROAD_NAME !== "N/A" ? item.ROAD_NAME : ""} {item.POSTAL !== "N/A" ? `(S${item.POSTAL})` : ""}
                      </div>
                    </div>
                    <div style={{ fontSize: "12px", color: "#64748b", textAlign: "right" }}>
                      Lat: {item.LATITUDE}
                      <br />
                      Lng: {item.LONGITUDE}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      </main>

      {/* Site Footer with Attribution Matrix (T0.5) */}
      <footer
        style={{
          borderTop: "1px solid #1e293b",
          backgroundColor: "#090d16",
          padding: "32px 24px",
          color: "#94a3b8",
          fontSize: "13px",
          lineHeight: "1.6",
        }}
      >
        <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
          <div style={{ marginBottom: "16px", fontWeight: 600, color: "#cbd5e1" }}>
            S.H.I.O.K. Index — Non-Commercial Civic Open Data Project
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "20px" }}>
            <div>
              <strong style={{ color: "#e2e8f0" }}>Data Mall & Transport Data</strong>
              <p style={{ margin: "4px 0" }}>
                Contains information from Covered Linkway, Overhead Bridge/Underpass, Bus Stops, Bus Services, and Bus Routes datasets accessed from LTA DataMall under the Singapore Open Data Licence v1.0.
              </p>
            </div>
            <div>
              <strong style={{ color: "#e2e8f0" }}>Government Open Datasets</strong>
              <p style={{ margin: "4px 0" }}>
                Contains information from MRT Station Exits, Traffic Signals, Lamp Posts, Building Information, and URA Master Plan 2019 Planning Area Boundary datasets accessed from data.gov.sg under the Singapore Open Data Licence v1.0.
              </p>
            </div>
            <div>
              <strong style={{ color: "#e2e8f0" }}>Basemap & Map Geometries</strong>
              <p style={{ margin: "4px 0" }}>
                Search & Basemap tiles © Singapore Land Authority (OneMap). Pedestrian network © OpenStreetMap contributors under ODbL.
              </p>
            </div>
          </div>

          <div style={{ marginTop: "24px", paddingTop: "16px", borderTop: "1px solid #1e293b", textAlign: "center", color: "#64748b" }}>
            S.H.I.O.K. Index is free and open source. Hosted on Vercel Hobby tier under non-commercial terms. $0 standing infrastructure cost.
          </div>
        </div>
      </footer>
    </div>
  );
}
