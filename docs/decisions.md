# Decision log

Format: date — decision — rationale — decided by

- 2026-07-25 — **Router amendment:** both routing passes run on the project's own conflated
  graph via python-igraph (weights: `length_m`; `sheltered_cost = length_m × (1 + λ×(1−covered))`,
  λ in params.yaml). Valhalla removed from the stack; OneMap walk-routing remains the sole
  external validation baseline. — Scoring requires per-edge attribution on our graph; any
  external engine adds geometry→edge map-matching plus PBF write-back of synthesized linkway
  edges — two whole failure classes deleted (ENGINEERING_REVIEW.md §1). — Owner, on the
  Claude/Gemini engineering review.
- 2026-07-25 — **Artifact layout:** geometry sharded by H3 res-8 with adaptive res-9 splits
  for cells >250 KB raw (`geom/index.json` lists promotions); scores per planning area; ship
  plain `.json` and let Vercel edge compress. — Vercel's 15,000-source-file deployment cap
  and mobile `JSON.parse` cost on landed-heavy areas (ENGINEERING_REVIEW.md §2). — Owner.
- 2026-07-25 — **New tasks:** T0.6 bulk OneMap geocode (no single open file covers all
  ~140k postals); T0.4 gains a OneMap rate-limit probe; T0.3 gains a DataMall auth/discovery
  probe; dataset #12 (URA MP2019 Planning Area Boundary) added. — ENGINEERING_REVIEW.md §3. — Owner.

- 2026-07-25 — **Windows-native amendment:** owner's machine is Windows 11 without WSL; Docker Desktop requires a WSL2 backend, so Docker is dropped entirely. Pipeline runs on native Python 3.12 managed by uv (all stack libraries ship Windows wheels); `make` replaced by cross-platform `run.py`; Windows rules added to CLAUDE.md (spawn-safe multiprocessing, UTF-8, long paths, pathlib-only). Viable precisely because the router amendment already removed the only container dependency (Valhalla). — Owner.
- 2026-07-25 — **Command execution convention:** all project CLI tasks are run via `uv run python run.py <task>` to guarantee execution within the project venv. — Reviewer audit correction T0.1.
- 2026-07-25 — **Diff emission threshold:** for any file under 100 lines, edits are emitted as the complete final file rather than diff snippets to eliminate context line ambiguity. — Reviewer audit correction T0.1.
- 2026-07-25 — **Non-packaged workspace:** pyproject.toml sets `[tool.uv] package = false` (repo is scripts + configs, not an installable distribution). — Reviewer-approved preemptive fix T0.1.
- 2026-07-25 — **Task reporting protocol:** every task report ends with an EVIDENCE section containing verbatim terminal output for each AC command. — Reviewer audit protocol amendment.
- 2026-07-25 — **Agentic safety rules:** execution strictly scoped to repository directory; no global machine state modifications; network access restricted to package sync and explicit API endpoints; no deletion outside build artifacts; no force push or amending published commits. — Reviewer audit safety rules.
- 2026-07-25 — **Environment lockdown (T0.2):** locked native Windows Python 3.12 stack via uv.lock: geopandas 1.1.4, shapely 2.1.2, pyproj 3.7.2, duckdb 1.5.5, python-igraph 1.0.0, h3 4.5.0, httpx 0.28.1, python-dotenv 1.2.2, pytest 9.1.1, ruff 0.16.0. — T0.2 environment lockdown.

<!-- Agent: append new entries below. Never delete history. -->





