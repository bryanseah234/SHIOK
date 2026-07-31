import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { gunzipSync, gzipSync } from "node:zlib";
import { latLngToCell } from "h3-js";

function configuredBundle() {
  const configPath = new URL("../data-bundle.json", import.meta.url);
  const payload = JSON.parse(readFileSync(configPath, "utf8"));
  return String(payload.bundle || "").trim();
}

function normalizeBundle(value) {
  const bundle = String(value || "").trim();
  if (!bundle || bundle.includes("/") || bundle.includes("\\") || bundle !== basename(bundle)) {
    throw new Error("data bundle must be a directory name like generated_YYYYMMDD_HHMMSS");
  }
  return bundle;
}

function writeJson(path, payload) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(payload, null, 2), "utf8");
}

function writeGzJson(path, payload) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, gzipSync(JSON.stringify(payload)));
}

function writePostalPrefixShards(targetRoot, postalIndex) {
  const prefixes = new Map();
  for (const [postal, shard] of Object.entries(postalIndex || {})) {
    const prefix = String(postal).slice(0, 3);
    if (!prefixes.has(prefix)) prefixes.set(prefix, {});
    prefixes.get(prefix)[postal] = shard;
  }

  for (const [prefix, mapping] of prefixes) {
    writeGzJson(join(targetRoot, "geom", "postal-prefix", `${prefix}.json.gz`), mapping);
  }
}

function writeTransitH3Shards(targetRoot, transitPois) {
  const cells = new Map();
  for (const feature of transitPois?.features || []) {
    const [lng, lat] = feature?.geometry?.coordinates || [];
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    const cell = latLngToCell(lat, lng, 8);
    if (!cells.has(cell)) cells.set(cell, []);
    cells.get(cell).push(feature);
  }

  for (const [cell, features] of cells) {
    writeGzJson(join(targetRoot, "transit", "h3", `${cell}.json.gz`), {
      type: "FeatureCollection",
      features,
      provenance: {
        source: "transit/pois.json",
        h3_resolution: 8,
      },
    });
  }
}

function decodeArtifact(bytes, relPath, response) {
  if (!relPath.endsWith(".gz")) {
    return { storageBytes: bytes, text: bytes.toString("utf8") };
  }

  const contentEncoding = response.headers.get("content-encoding") || "";
  if (contentEncoding.toLowerCase().includes("gzip")) {
    return { storageBytes: gzipSync(bytes), text: bytes.toString("utf8") };
  }

  try {
    return { storageBytes: bytes, text: gunzipSync(bytes).toString("utf8") };
  } catch {
    return { storageBytes: gzipSync(bytes), text: bytes.toString("utf8") };
  }
}

async function fetchArtifact(remoteBase, relPath) {
  const candidates = [`${relPath}.gz`, relPath];
  const failures = [];
  for (const candidate of candidates) {
    const url = new URL(candidate, remoteBase).href;
    const response = await fetch(url);
    if (!response.ok) {
      failures.push(`${response.status}: ${url}`);
      continue;
    }
    const bytes = Buffer.from(await response.arrayBuffer());
    const decoded = decodeArtifact(bytes, candidate, response);
    return { relPath: candidate, ...decoded };
  }
  throw new Error(`download failed for ${relPath}; tried ${failures.join(", ")}`);
}

async function downloadArtifact(remoteBase, targetRoot, relPath) {
  const artifact = await fetchArtifact(remoteBase, relPath);
  const targetPath = join(targetRoot, artifact.relPath);
  mkdirSync(dirname(targetPath), { recursive: true });
  writeFileSync(targetPath, artifact.storageBytes);
}

async function downloadJson(remoteBase, targetRoot, relPath) {
  const artifact = await fetchArtifact(remoteBase, relPath);
  const payload = JSON.parse(artifact.text);
  if (artifact.relPath.endsWith(".gz")) {
    const targetPath = join(targetRoot, artifact.relPath);
    mkdirSync(dirname(targetPath), { recursive: true });
    writeFileSync(targetPath, artifact.storageBytes);
  } else {
    writeJson(join(targetRoot, relPath), payload);
  }
  return payload;
}

async function downloadRemoteBundle(bundle, targetRoot) {
  const remoteBase = new URL(
    process.env.SHIOK_REMOTE_DATA_BASE || `https://sgshiok.vercel.app/data/${bundle}/`
  );
  if (!remoteBase.pathname.endsWith("/")) {
    remoteBase.pathname = `${remoteBase.pathname}/`;
  }

  console.log(`local data missing; downloading ${remoteBase.href}`);

  const manifest = await downloadJson(remoteBase, targetRoot, "manifest.json");
  const geomIndex = await downloadJson(remoteBase, targetRoot, "geom/index.json");
  const geomPostalIndex = await downloadJson(remoteBase, targetRoot, "geom/postal-index.json");
  writePostalPrefixShards(targetRoot, geomPostalIndex);
  await downloadArtifact(remoteBase, targetRoot, "scores/index.json");
  await downloadArtifact(remoteBase, targetRoot, "scores/prefix-index.json");
  const transitPois = await downloadJson(remoteBase, targetRoot, "transit/pois.json");
  writeTransitH3Shards(targetRoot, transitPois);

  for (const shard of manifest.scores?.shards || []) {
    await downloadArtifact(remoteBase, targetRoot, `scores/${shard}.json`);
  }

  const geomShards = new Set();
  for (const [parent, children] of Object.entries(geomIndex)) {
    if (Array.isArray(children) && children.length) {
      for (const child of children) geomShards.add(String(child));
    } else {
      geomShards.add(String(parent));
    }
  }
  for (const shard of [...geomShards].sort()) {
    await downloadArtifact(remoteBase, targetRoot, `geom/h3/${shard}.json`);
  }
}

const bundle = normalizeBundle(process.argv[2] || process.env.SHIOK_DATA_BUNDLE || configuredBundle());
const target = join(process.cwd(), "public", "data", bundle);
const manifestPath = join(target, "manifest.json");

if (existsSync(manifestPath)) {
  console.log(`using local data bundle ${target}`);
} else {
  await downloadRemoteBundle(bundle, target);
  console.log(`downloaded data bundle ${target}`);
}
