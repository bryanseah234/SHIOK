/**
 * DATA ACCESS MODULE
 * Production cutover sets NEXT_PUBLIC_DATA_BASE="/data/" after checkpoint approval.
 * Default remains mock data for local/dev builds.
 */
export function normalizeDataBase(value?: string): string {
  const raw = value?.trim();
  if (!raw) return "/data/mock/";
  const withLeadingSlash =
    raw.startsWith("http://") || raw.startsWith("https://") || raw.startsWith("/")
      ? raw
      : `/${raw}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`;
}

export const DATA_BASE = normalizeDataBase(process.env.NEXT_PUBLIC_DATA_BASE);

import type { ScoreRecord, PostalGeom, Manifest } from "./types";
import { latLngToCell } from "h3-js";

type GeomIndex = Record<string, string[]>;

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
let _geomIndex: GeomIndex | null = null;

async function getAreaIndex(): Promise<Record<string, string[]>> {
  if (_areaIndex) return _areaIndex;
  if (DATA_BASE.includes("/mock/")) {
    _areaIndex = { ANG_MO_KIO: ["560123", "560456", "560789", "018989", "627961"] };
    return _areaIndex;
  }
  const res = await fetch(`${DATA_BASE}scores/index.json`);
  if (!res.ok) throw new Error(`score index fetch failed: ${res.status}`);
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

async function getGeomIndex(): Promise<GeomIndex | null> {
  if (_geomIndex) return _geomIndex;
  const res = await fetch(`${DATA_BASE}geom/index.json`);
  if (!res.ok) return null;
  _geomIndex = (await res.json()) as GeomIndex;
  return _geomIndex;
}

async function fetchGeomShard(shardId: string): Promise<PostalGeom[] | null> {
  const res = await fetch(`${DATA_BASE}geom/h3/${shardId}.json`);
  if (!res.ok) return null;
  return res.json() as Promise<PostalGeom[]>;
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
    const parentRecords = await fetchGeomShard(cell);
    const parentMatch = parentRecords?.find((r) => r.postal === postal);
    if (parentMatch) return parentMatch;

    const index = await getGeomIndex();
    for (const child of index?.[cell] ?? []) {
      const childRecords = await fetchGeomShard(child);
      const childMatch = childRecords?.find((r) => r.postal === postal);
      if (childMatch) return childMatch;
    }

    if (!parentRecords && !(index?.[cell]?.length)) {
      console.warn(`geom shard not found for cell ${cell} (postal ${postal})`);
    }
    return null;
  }

  if (!DATA_BASE.includes("/mock/")) return null;

  const geomIndex = await getGeomIndex();

  for (const [cell, children] of Object.entries(geomIndex ?? {})) {
    const parentRecords = await fetchGeomShard(cell);
    const parentMatch = parentRecords?.find((r) => r.postal === postal);
    if (parentMatch) return parentMatch;

    for (const shardId of children) {
      const childRecords = await fetchGeomShard(shardId);
      const childMatch = childRecords?.find((r) => r.postal === postal);
      if (childMatch) return childMatch;
    }
  }
  return null;
}
