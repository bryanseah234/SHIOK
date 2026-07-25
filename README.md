# S.H.I.O.K. Index

A free, non-commercial civic web app giving every Singapore postal code an explainable
"comfort score" for the walk to transit — rain shelter, heat, crossing friction, transit
access, and bus frequency — computed on real routed paths from open government data.

**Status:** docs-first kickstart. No pipeline or frontend code exists yet — a coding agent
builds it by working through `docs/BUILD_PLAN.md` task by task.
**Environment:** Windows 11, native. No WSL, no Docker, no GitHub Actions, no paid services.

## Repo map

- `CLAUDE.md` — agent instructions: hard constraints, stack, layout, conventions. Read first.
  (Using a non-Claude agent? It still applies — point your agent at it explicitly.)
- `docs/PRD_v4.2.md` — the product spec (authoritative; includes all ratified amendments).
- `docs/BUILD_PLAN.md` — Phase 0–1 tasks with acceptance criteria. Work strictly in order.
- `docs/DATA_SOURCES.md` — every dataset, endpoint, licence, and gotcha.
- `docs/ENGINEERING_REVIEW.md` — pre-implementation review; explains the amended decisions.
- `docs/decisions.md` — running decision log (pre-seeded; agents append, never delete).
- `docs/ATTRIBUTION.md` — licence/attribution matrix (T0.5 completes it).
- `env.example` — copy to `.env` and fill in (see prerequisites).
- `run.py` — cross-platform task runner (check | ingest | network | route | score | export |
  validate | publish | test). `publish` always runs `validate` first.

## Human prerequisites (do these once — agents can't register accounts)

1. Install **Python 3.12+** and **uv** (https://docs.astral.sh/uv/), **Node LTS**, and **Git**.
2. One-time Windows setup, in an elevated PowerShell:
   - `git config --global core.longpaths true`
   - Enable long paths: `Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1`
   - Set UTF-8 for Python: `setx PYTHONUTF8 1`
   - (Optional, speeds up raw-data I/O): add the repo's `raw\` folder to Microsoft Defender exclusions.
3. Register a free **LTA DataMall** account → `AccountKey` (datamall.lta.gov.sg).
4. Register a free **OneMap** developer account (onemap.gov.sg).
5. `copy env.example .env` and fill in the credentials.
6. `npm i -g vercel`, then `vercel login` once (Hobby tier, no card).

## Kickoff prompt for the coding agent

> Read CLAUDE.md, docs/PRD_v4.2.md, and docs/BUILD_PLAN.md. Then start at T0.1 and work
> through the tasks strictly in order, verifying each task's acceptance criteria before
> moving on. Log every decision in docs/decisions.md. This machine is Windows 11 —
> PowerShell commands only, no WSL, no Docker.

## Ground rules (enforced in CLAUDE.md — summary)

$0 budget, no paid services, Vercel Hobby only, no Cloudflare, no GitHub Actions; all
heavy compute runs natively on this Windows machine (uv-managed Python); the only runtime
backend is the OneMap search proxy; all routing runs in python-igraph on the project's own
graph; every published score is reproducible from hashed inputs + tagged code.
