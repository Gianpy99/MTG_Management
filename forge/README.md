# 🧪 MTG Forge Engine

Self-hosted [Forge](https://github.com/Card-Forge/forge) rules engine that runs
AI-vs-AI simulations and deck audits for the decks in the collection app. It runs on
the Raspberry Pi as its own Docker container (`mtg-forge`, port **8787**, LAN only),
built and deployed by the same Jenkins pipeline as the collection app.

```
collection app (mtg-collection :8094)  ──HTTP──▶  mtg-forge :8787
  reads the collection DB                          REST API + job queue (1 worker)
  exports decks to Forge .dck                      Forge CLI  (java -jar forge.jar sim …)
                                                   forge_results.db + raw logs  (volume mtg-forge-data)
```

The Forge service never touches the collection database: it receives `.dck` text and
stores its own results.

## Layout

| Path | Purpose |
| --- | --- |
| `forge.version` | **Pinned Forge release** (version + SHA256 of the Linux tarball) |
| `Dockerfile` | Downloads + verifies Forge, Java 17, Python API (`--target test` = unit tests) |
| `service/forge_engine/` | API (`api.py`), queue/worker (`worker.py`), card index + `.dck` normaliser (`cards.py`), log parser, SQLite store |
| `service/tests/` | Unit tests (a fake `java` emits real Forge output, so the whole queue is tested) |
| `scripts/forge_pipeline.sh` | Jenkins steps: `unit`, `build`, `integration`, `deploy`, `rollback` |
| `tolkien_forge_decks_fixed/` | Reference decks baked into the image (test opponents + smoke test) |

## Updating Forge

1. Pick a release on <https://github.com/Card-Forge/forge/releases> and get the SHA256 of
   `forge-installer-<version>.tar.bz2` (e.g. `sha256sum` after downloading it once).
2. Edit `forge.version` (`FORGE_VERSION` and `FORGE_SHA256`), commit, push to `main`.
3. Jenkins: unit tests → build → **real Forge game in the new image** → deploy → health
   check → **real Forge game inside the deployed container** (outside the job queue, so it
   never waits behind user simulations). If the deploy check fails, the previous image
   (`mtg-forge:previous`) is restarted automatically and the build fails.

A candidate that failed is tagged `mtg-forge:failed` and is not redeployed until
something under `forge/` changes (`FORGE_FORCE_DEPLOY=true` forces a retry). A Forge
failure marks the Jenkins build as failed but does not block deploying the collection
app. Builds where neither the image nor the run configuration changed (e.g. app-only
commits) skip the Forge test/deploy and do **not** restart Forge, so queued simulations
keep running. Manual rollback on the Pi: `bash forge/scripts/forge_pipeline.sh rollback`
from a checkout. `python -m forge_engine smoke` (inside the container) submits a real
audit through the API for manual checks.

## API (http://192.168.1.129:8787)

```
GET  /health                        API + DB + worker + Forge jar + Java + card index
GET  /api/forge/status              versions, platform, worker, queue, memory, last jobs
GET  /api/forge/test-decks          bundled reference decks
POST /api/forge/audit               {"deck": <deck>, "format"?, "games"?: 1}
POST /api/simulations               {"deck_a": <deck>, "deck_b": <deck>, "games": 100,
                                     "format"?: "constructed|commander", "priority"?: "HIGH|NORMAL|LOW",
                                     "seed"?, "allow_invalid"?: false}
GET  /api/simulations[?deck=&status=&kind=&limit=]
GET  /api/simulations/{id}          status, progress, wins, quality flags, decks (sha256), issues
GET  /api/simulations/{id}/results  + per-game winner / turns / duration
GET  /api/simulations/{id}/log      raw Forge output (never discarded)
POST /api/simulations/{id}/cancel
GET  /api/decks/{name}/audit        latest audit for a deck (name or collection slug)
GET  /api/decks/{name}/history
GET  /api/matchups                  win rates aggregated from completed simulations only
```

`<deck>` is either the name of a bundled reference deck (`"Abzan_Food"`) or
`{"name": "...", "dck": "<.dck text>", "ref": "<collection slug>"}`.
Arbitrary commands are never accepted; deck names, formats and game counts are validated.

```powershell
$b = "http://192.168.1.129:8787"
Invoke-RestMethod -Method Post "$b/api/simulations" -ContentType application/json `
  -Body '{"deck_a":"Abzan_Food","deck_b":"Rakdos_Orcs","games":100}'
Invoke-RestMethod "$b/api/simulations/<job_id>"
```

From the collection app the same features are under `/api/forge/*` and
`/api/decks/{slug}/forge/audit`, and in the UI: **Decks → 🧪 Forge — testa il mazzo**.

## How decks are checked

Forge silently **drops** cards it cannot resolve and still plays the game with a smaller
deck, and silently falls back to another printing when a set/collector number is wrong.
So before every run the service checks each line against Forge's own card data:

| Severity | Meaning |
| --- | --- |
| `ERROR` | structure: malformed line, Commander ≠ 100 cards / 1–2 commanders, Constructed < 60 |
| `RED` | Forge cannot resolve the card (it would be dropped) — also parsed from the Forge log |
| `YELLOW` | Forge plays the card but its AI uses a generic fallback (`Warning: default … implementation`, `No AI assigned for API`) — **not** an unsupported card |
| `INFO` | conversion notes: `A // B` adventure/DFC names renamed to the front face, unknown set codes / collector numbers dropped |

Simulations with `ERROR`s are rejected unless `allow_invalid` is set. Every job stores
`quality_flags` (`unsupported_cards`, `ai_fallback`, `clock_draws`, `structure_invalid`,
`small_sample` < 30 games). Audit verdict: `PASS` / `WARN` (yellow only) / `FAIL`.
AI warnings only cover cards that were actually played during the run.

## Configuration (environment variables)

| Variable | Default | |
| --- | --- | --- |
| `FORGE_JAVA_OPTS` | `-Xmx2g …` | JVM heap cap — the real memory guard (the Pi kernel has the memory cgroup disabled, so `docker --memory` is not enforced) |
| `FORGE_MAX_GAMES` | `1000` | max games per request |
| `FORGE_MAX_QUEUED_JOBS` | `50` | |
| `FORGE_GAME_CLOCK_CONSTRUCTED` / `_COMMANDER` | `180` / `300` s | Forge per-game clock; slower games end as draws (`clock_draws` flag) |
| `FORGE_MAX_RUNTIME_SECONDS` | `43200` | hard cap; job budget = 300 s + games × (clock + 30 s) |

Jobs survive restarts: anything left `RUNNING` is re-queued and re-run from scratch.

## Measured on the Pi 5 (8 GB, 2.0.15, Java 17)

- JVM start + card DB: ~10 s; card index for pre-checks: ~3 s at service start
- Constructed game: ~5–12 s; Commander game: ~20–90 s
- Forge JVM RSS ≈ 0.8 GB during a run (heap cap 2 GB)

## Local development

```powershell
cd forge\service
..\..\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
..\..\.venv\Scripts\python.exe -m pytest -q tests
```

Point a local collection app at the Pi engine with `$env:FORGE_URL="http://192.168.1.129:8787"`.
