/**
 * DATA ACCESS MODULE
 * To switch from mock to real data, change DATA_BASE to "/data/":
 *   const DATA_BASE = "/data/";
 */
export const DATA_BASE = "/data/mock/";

import type { ScoreRecord, PostalGeom, Manifest } from "./types";
import { latLngToCell } from "h3-js";

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------
export async function fetchManifest(): Promise<Manifest> {
  const res = await fetch(`${DATA_BASE}manifest.json`);
  if (!res.ok) throw new Error(`manifest fetch failed: ${res.status}`);
  return res.json() as Promise<Manifest>;
}

// ---------------------------------------------------------------------------
// Score records
// The pipeline shards scores by planning-area into files named by planning
// area slug (e.g. "ANG_MO_KIO.json").  For the mock we use a single area
// file that holds all five test postals.  fetchScoreForPostal searches every
// area file via the area-to-postals index; in mock mode the index is trivial.
// ---------------------------------------------------------------------------

/** Area-index maps area-slug → [postal, …] so we can look up which file to fetch. */
let _areaIndex: Record<string, string[]> | null = null;
let _geomIndex: Record<string, string[]> | null = null;

async function getAreaIndex(): Promise<Record<string, string[]>> {
  if (_areaIndex) return _areaIndex;
  const res = await fetch(`${DATA_BASE}scores/index.json`);
  if (!res.ok) {
    // Graceful fallback for mock: derive from the single mock file
    _areaIndex = { ANG_MO_KIO: ["560123", "560456", "560789", "018989", "627961"] };
    return _areaIndex;
  }
  _areaIndex = await res.json();
  return _areaIndex!;
}

async function fetchAreaRecords(areaSlug: string): Promise<ScoreRecord[]> {
  const res = await fetch(`${DATA_BASE}scores/${areaSlug}.json`);
  if (!res.ok) throw new Error(`area fetch failed for ${areaSlug}: ${res.status}`);
  return res.json() as Promise<ScoreRecord[]>;
}

export async function fetchScoreForPostal(
  postal: string
): Promise<ScoreRecord | null> {
  const index = await getAreaIndex();
  for (const [slug, postals] of Object.entries(index)) {
    if (postals.includes(postal)) {
      const records = await fetchAreaRecords(slug);
      return records.find((r) => r.postal === postal) ?? null;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Geometry
// The client resolves which H3 res-8 shard to fetch using the postal's lat/lng
// (supplied from the OneMap search result).  geom/h3/{cell}.json holds an
// array of PostalGeom for all postals in that cell.
// ---------------------------------------------------------------------------
export async function fetchGeomForPostal(
  postal: string,
  lat?: number,
  lng?: number
): Promise<PostalGeom | null> {
  if (typeof lat === "number" && typeof lng === "number") {
    const cell = latLngToCell(lat, lng, 8);
    const res = await fetch(`${DATA_BASE}geom/h3/${cell}.json`);
    if (!res.ok) {
      console.warn(`geom shard not found for cell ${cell} (postal ${postal})`);
      return null;
    }
    const records: PostalGeom[] = await res.json();
    return records.find((r) => r.postal === postal) ?? null;
  }

  if (!DATA_BASE.includes("/mock/")) return null;

  if (!_geomIndex) {
    const res = await fetch(`${DATA_BASE}geom/index.json`);
    if (!res.ok) return null;
    _geomIndex = await res.json();
  }

  for (const [cell, children] of Object.entries(_geomIndex ?? {})) {
    const shardIds = Array.from(new Set([cell, ...children]));
    for (const shardId of shardIds) {
      const res = await fetch(`${DATA_BASE}geom/h3/${shardId}.json`);
      if (!res.ok) continue;
      const records: PostalGeom[] = await res.json();
      const found = records.find((r) => r.postal === postal);
      if (found) return found;
    }
  }
  return null;
}
