import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";

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

async function fetchJson(remoteBase, relPath) {
  const url = new URL(relPath, remoteBase).href;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`download failed ${response.status}: ${url}`);
  }
  return response.json();
}

async function downloadJson(remoteBase, targetRoot, relPath) {
  const payload = await fetchJson(remoteBase, relPath);
  writeJson(join(targetRoot, relPath), payload);
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
  rmSync(targetRoot, { recursive: true, force: true });

  const manifest = await downloadJson(remoteBase, targetRoot, "manifest.json");
  const geomIndex = await downloadJson(remoteBase, targetRoot, "geom/index.json");
  await downloadJson(remoteBase, targetRoot, "geom/postal-index.json");
  await downloadJson(remoteBase, targetRoot, "scores/index.json");
  await downloadJson(remoteBase, targetRoot, "transit/pois.json");

  for (const shard of manifest.scores?.shards || []) {
    await downloadJson(remoteBase, targetRoot, `scores/${shard}.json`);
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
    await downloadJson(remoteBase, targetRoot, `geom/h3/${shard}.json`);
  }
}

const bundle = normalizeBundle(process.argv[2] || process.env.SHIOK_DATA_BUNDLE || configuredBundle());
const source = join(process.cwd(), "public", "data", bundle);
const target = join(process.cwd(), ".next", "static", "data", bundle);

if (existsSync(source)) {
  rmSync(target, { recursive: true, force: true });
  mkdirSync(join(process.cwd(), ".next", "static", "data"), { recursive: true });
  cpSync(source, target, { recursive: true });
  console.log(`copied ${source} -> ${target}`);
} else {
  await downloadRemoteBundle(bundle, target);
  console.log(`downloaded ${bundle} -> ${target}`);
}
