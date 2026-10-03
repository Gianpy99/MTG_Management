# MTG Collection Forge Engine
## Product Requirements Document — Raspberry Pi + Jenkins Deployment

**Status:** Implementation-ready  
**Target:** Raspberry Pi 5 16 GB, Linux ARM64  
**Deployment controller:** Jenkins  
**Source control:** GitHub  
**Simulation engine:** Forge CLI / Java  
**Collection source of truth:** Existing MTG Collection Server + SQLite collection DB

---

## 1. Vision

Build a self-hosted MTG analysis, deck-testing and benchmarking platform on the Raspberry Pi.

The system integrates:

- existing MTG Collection Server
- existing SQLite collection database
- deck generation/management
- Forge rules engine and AI simulation
- automated deck compatibility audits
- benchmark tournaments
- historical results
- future Power BI visualisation

Architecture:

```text
GitHub
   |
   v
Jenkins
   |
   +--> tests
   +--> Forge integration tests
   +--> build/package
   +--> deploy
   +--> health check
   |
   v
Raspberry Pi
   |
   +--> MTG Collection Server
   +--> MTG Forge Service
   +--> Forge Worker
   +--> Forge
   +--> Results DB
```

---

## 2. Core separation of responsibilities

### Collection Server
Source of truth for:

- cards
- quantities
- collection
- wishlist
- personal decks
- deck versions

### Forge Service
Responsible for:

- Forge `.dck` export
- compatibility audits
- simulation jobs
- Forge execution
- parsing/storing results

### Jenkins
Responsible for:

- CI
- tests
- Forge integration tests
- packaging
- deployment
- rollback
- post-deployment validation

**Forge must never modify the collection database.**

---

## 3. Raspberry Pi target

Initial deployment is a **single Raspberry Pi 5 16 GB**.

```text
Raspberry Pi 5
├── Jenkins
│   ├── CI/CD
│   ├── Build
│   ├── Test
│   └── Deploy
├── MTG Collection Server
├── MTG Forge Service
│   ├── REST API
│   ├── Job Queue
│   ├── Deck Audit
│   └── Forge Worker
├── Forge / Java
└── forge_results.db
```

Do not introduce a second Pi until performance measurements justify it.

---

## 4. Forge deployment

Use Forge **headless/CLI**, not the GUI, for production simulation.

Expected commands:

```bash
java -jar forge.jar sim -d DeckA DeckB -n 100 -q
```

Commander:

```bash
java -jar forge.jar sim   -d DeckA DeckB   -f Commander   -n 100   -q
```

The implementation must detect the installed Forge and Java versions instead of hard-coding them.

Java must be >=17.

---

## 5. Jenkins is the standard deployment mechanism

Normal deployment:

```text
Developer
   |
   v
GitHub
   |
   v
Jenkins
   |
   +--> Checkout
   +--> Unit tests
   +--> Static validation
   +--> Forge integration test
   +--> Build/package
   +--> Deploy
   +--> Restart
   +--> Health check
   +--> Forge smoke test
   |
   v
Production Raspberry Pi
```

Manual production modification is an exception, not the standard deployment path.

---

## 6. Git workflow

Recommended:

```text
main
 |
 +-- feature/*
 +-- bugfix/*
```

Flow:

```text
feature branch
   |
   v
Pull Request
   |
   v
Jenkins CI
   |
   +--> unit tests
   +--> integration tests
   +--> Forge test
   |
   v
merge main
   |
   v
Jenkins deployment
```

Production must always correspond to a known Git commit.

---

## 7. Jenkins pipeline

Stages:

```text
1. Checkout
2. Unit Tests
3. Static Validation
4. Forge Compatibility Test
5. Build
6. Package
7. Deploy
8. Service Health Check
9. Forge Smoke Test
10. Deployment Result
```

A deployment must fail if any mandatory stage fails.

The Forge compatibility stage must run a **real Forge simulation**, not only a mock.

---

## 8. Versioned deployment and rollback

Use:

```text
/opt/mtg/
├── current -> /opt/mtg/releases/1.0.3
├── releases/
├── data/
├── logs/
├── benchmarks/
└── backups/
```

Deployment:

```text
build release
   |
   v
install versioned release
   |
   v
validate
   |
   v
switch current symlink
   |
   v
restart
   |
   v
health + smoke test
```

Rollback must simply restore the previous `current` target and restart the service.

---

## 9. Runtime vs CI/CD

Jenkins must **not** execute normal user simulations.

### Jenkins
- deployment
- CI
- regression validation
- rollback

### Forge Service
- runtime API
- simulation queue
- Forge execution
- results

Normal request:

```text
MTG API
  |
  v
Queue
  |
  v
Forge Worker
  |
  v
Forge
```

A 10,000-game request must not trigger a Jenkins build.

---

## 10. MTG Collection Server integration

The Collection Server remains authoritative.

Example:

```http
POST /api/simulations
Content-Type: application/json
```

```json
{
  "deck_a": "Abzan_Food",
  "deck_b": "Rakdos_Orcs",
  "format": "constructed",
  "games": 100
}
```

Commander:

```json
{
  "deck_a": "Frodo_Sam_Food_Ring",
  "deck_b": "Saruman_Spell_Amass",
  "format": "commander",
  "games": 100
}
```

The Forge service must not modify collection data.

---

## 11. Simulation queue

States:

```text
QUEUED
RUNNING
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

Example:

```json
{
  "job_id": "sim_20261003_001",
  "status": "queued"
}
```

Status:

```http
GET /api/simulations/sim_20261003_001
```

```json
{
  "job_id": "sim_20261003_001",
  "status": "running",
  "progress": {
    "games_completed": 43,
    "games_requested": 100
  }
}
```

Initial worker concurrency:

```yaml
forge:
  max_workers: 1
```

Increase only after measuring Pi CPU/RAM usage.

---

## 12. Deck conversion

Generate native Forge `.dck` files.

Constructed:

```text
[metadata]
Name=Abzan_Food

[Main]
1 Card Name|LTR|[123]
4 Card Name|HOB|[45]
```

Commander:

```text
[metadata]
Name=Frodo_Sam_Food_Ring

[Commander]
1 Frodo, Adventurous Hobbit|LTC|[2]
1 Sam, Loyal Attendant|LTC|[7]

[Main]
...
```

Preserve exact:

- card name
- set code
- collector number
- quantity
- commander section

Do not approximate-match cards when exact collection metadata exists.

---

## 13. Deck audit

Every generated deck must be audited before benchmarking.

```text
Generate .dck
    |
    v
Structural validation
    |
    v
Forge load test
    |
    v
Unsupported card scan
    |
    v
AI warning scan
    |
    v
PASS / FAIL
    |
    v
Benchmark
```

### Constructed
Expected total: 60 cards.

### Commander
Expected total: 100 cards including commander section.

Validate:

- section names
- quantities
- set codes
- collector numbers
- malformed lines
- missing cards
- commander definitions

---

## 14. Forge compatibility classification

### GREEN
Forge recognizes the card and no relevant AI warning was observed.

### YELLOW
Forge recognizes the card but an AI fallback warning was observed.

Example:

```text
Gollum's Bite
chooseSingleCard
```

This is **not** an unsupported-card error.

### RED
Forge cannot resolve the card.

Example previously observed:

```text
Gandalf, Goblins' Bane // Flameshape
HOB [96]
```

Store exact card/set/collector.

**Never classify an AI warning as an unsupported card.**

---

## 15. Results database

Create a dedicated:

```text
forge_results.db
```

Do not turn the collection database into the simulation database.

Suggested tables:

### simulation_jobs

```text
id
created_at
started_at
completed_at
status
format
games_requested
games_completed
```

### simulation_decks

```text
simulation_id
side
deck_name
deck_version
deck_hash
```

### games

```text
id
simulation_id
game_number
deck_a
deck_b
winner
turns
duration
```

### audit_runs

```text
id
deck_name
deck_version
forge_version
audit_time
structure_status
load_status
```

### audit_issues

```text
id
audit_id
severity
issue_type
card_name
set_code
collector_number
message
```

---

## 16. Raw Forge logs

Every Forge invocation must generate a raw log.

Example:

```text
/opt/mtg/logs/
└── 2026/
    └── 10/
        └── sim_20261003_001.log
```

Never discard raw Forge output.

The structured database is the parsed representation; the raw log is the forensic source.

---

## 17. Deck versioning

Every deck must have:

```text
human-readable version
SHA256 of exact .dck
timestamp
```

Example:

```text
Abzan_Food
version: 3
sha256: abc123...
```

Simulation results must reference the exact deck version/hash used.

---

## 18. Collection constraints

Personal decks must respect owned quantities.

Example:

```text
Card X
quantity = 2
```

must not become four copies in a `REAL_COLLECTION` deck.

Support:

```text
REAL_COLLECTION
PROXY
EXTERNAL_BENCHMARK
```

External benchmark decks may ignore personal collection quantities.

---

## 19. Simulation types

Support:

### Matchup
Deck A vs Deck B.

### Repeated matchup
1 / 10 / 100 / 500 / 1000 / configurable games.

### Round robin

For four decks:

```text
A vs B
A vs C
A vs D
B vs C
B vs D
C vs D
```

### Self-test

Deck A vs Deck A.

Self-test is diagnostic, not a competitive benchmark.

---

## 20. Benchmark groups

Support:

```text
MY_COLLECTION
STANDARD_BENCHMARK
COMMANDER_BENCHMARK
CEDH_BENCHMARK
TEST_DECKS
```

Example:

```json
{
  "benchmark": "MY_COLLECTION",
  "decks": [
    "Abzan_Food",
    "Rakdos_Orcs",
    "Frodo_Sam_Food_Ring",
    "Saruman_Spell_Amass"
  ]
}
```

---

## 21. Matchup results

Expose:

```http
GET /api/matchups
```

Calculate only from actual simulations.

Example:

```text
             Abzan   Rakdos   Frodo   Saruman
Abzan          -       42%      61%      55%
Rakdos        58%       -       48%      63%
Frodo         39%      52%       -       57%
Saruman       45%      37%      43%       -
```

These are observed Forge results, not predictions of real tournament performance.

---

## 22. Forge limitations

Reports must expose:

- Forge version
- Java version
- unsupported cards
- AI fallback warnings
- benchmark quality flags

Results with significant unsupported/fallback behaviour must be marked accordingly.

---

## 23. API

Recommended endpoints:

```text
GET  /api/forge/status
POST /api/forge/audit

POST /api/simulations
GET  /api/simulations
GET  /api/simulations/{id}
POST /api/simulations/{id}/cancel
GET  /api/simulations/{id}/results

GET  /api/decks/{name}/audit
GET  /api/decks/{name}/history

GET  /api/matchups
GET  /api/benchmarks
POST /api/benchmarks/run

GET /health
```

---

## 24. Forge status

```http
GET /api/forge/status
```

Example:

```json
{
  "status": "ready",
  "forge_version": "2.0.15",
  "java_version": "21.0.1",
  "platform": "linux-arm64",
  "worker": "idle"
}
```

---

## 25. Security

The Forge API is intended for LAN use.

Never expose arbitrary command execution.

Do not accept:

```json
{
  "command": "java -jar ..."
}
```

Accept structured parameters:

```json
{
  "deck_a": "Abzan_Food",
  "deck_b": "Rakdos_Orcs",
  "games": 100
}
```

Validate:

- deck names
- format
- game count
- benchmark names

Apply maximum game/time limits.

---

## 26. Resource limits

Example:

```yaml
limits:
  max_games_per_request: 10000
  max_concurrent_jobs: 1
  max_runtime_seconds: 3600
```

Timeout behaviour:

```text
terminate Forge
preserve log
mark TIMEOUT
```

---

## 27. Queue priority

Support:

```text
HIGH
NORMAL
LOW
```

Example:

```text
Deck audit             HIGH
User benchmark         NORMAL
Large overnight test   LOW
```

---

## 28. Scheduled jobs

Design the queue for future:

- nightly benchmarks
- weekly collection tournaments
- automatic regression after deck changes

Scheduling is not required for V1.

---

## 29. Regression testing

When a deck changes:

```text
Deck V1
  |
  v
benchmark
  |
  v
Deck V2
  |
  v
benchmark
```

Compare actual observed results.

Do not turn a difference such as +7 percentage points into a universal claim that a deck is stronger.

---

## 30. Future AI optimisation

Future workflow:

```text
Collection
    |
Candidate deck
    |
Forge audit
    |
Forge benchmark
    |
Weak matchup
    |
AI proposes changes
    |
Human approval
    |
New version
    |
Forge
```

The AI must never silently modify canonical collection/deck data.

---

## 31. Repository layout

```text
mtg-forge/
├── Jenkinsfile
├── src/
│   ├── api/
│   ├── worker/
│   ├── forge/
│   └── database/
├── tests/
├── decks/
│   └── test/
├── scripts/
│   ├── install.sh
│   ├── deploy.sh
│   ├── rollback.sh
│   └── healthcheck.sh
├── config/
│   └── production.yaml
└── README.md
```

Runtime:

```text
/opt/mtg/
├── current
├── releases/
├── data/
├── logs/
├── benchmarks/
└── backups/
```

---

## 32. Configuration

```yaml
forge:
  jar: /opt/mtg/forge/forge.jar
  java: java
  max_workers: 1
  timeout_seconds: 3600

server:
  host: 0.0.0.0
  port: 8787

database:
  results: /opt/mtg/data/forge_results.db

paths:
  decks: /opt/mtg/data/decks
  benchmarks: /opt/mtg/data/benchmarks
  logs: /opt/mtg/data/logs

limits:
  max_games: 10000
```

No Windows-specific paths may be hard-coded.

---

## 33. Installation

Create:

```text
scripts/install.sh
```

The installer must:

1. detect architecture
2. detect Linux distribution
3. verify Java
4. verify Java >=17
5. create directories
6. install/configure Forge
7. install application dependencies
8. configure services
9. configure systemd
10. run health check
11. run one-game Forge smoke test

---

## 34. Systemd

Use systemd for production runtime.

Recommended:

```text
mtg-collection.service
mtg-forge.service
```

Reuse the existing Collection Server service if it already exists.

Jenkins controls Forge deployment/restart.

---

## 35. Jenkinsfile requirements

Repository must contain `Jenkinsfile`.

Conceptual pipeline:

```groovy
pipeline {
    stages {
        stage('Checkout') {
            // checkout source
        }
        stage('Unit Tests') {
            // application tests
        }
        stage('Static Validation') {
            // syntax/config checks
        }
        stage('Forge Integration Test') {
            // real Forge CLI smoke test
        }
        stage('Build') {
            // package
        }
        stage('Deploy') {
            // versioned deployment
        }
        stage('Health Check') {
            // API + DB + worker
        }
        stage('Forge Smoke Test') {
            // real post-deployment game
        }
    }
}
```

Adapt the implementation to the actual Jenkins installation.

---

## 36. Jenkins deployment failure rules

Production deployment must fail if:

- unit tests fail
- Forge integration test fails
- package creation fails
- deployment fails
- service fails to start
- health endpoint fails
- post-deployment Forge smoke test fails

Never report deployment success merely because a process started.

---

## 37. Observability

Expose:

- current job
- queue length
- CPU usage
- RAM usage
- Forge process
- games completed
- games remaining
- last successful simulation
- last failed simulation

Example:

```json
{
  "worker": "running",
  "queue": 3,
  "current_job": "sim_001",
  "games": {
    "completed": 432,
    "total": 1000
  }
}
```

---

## 38. Backup

Back up:

```text
forge_results.db
configuration
deck definitions
benchmark definitions
```

Use daily SQLite backups initially.

Preserve raw logs according to configurable retention.

---

## 39. Power BI future integration

Design the results model for:

```text
FactGames
FactSimulations
FactAudits

DimDeck
DimCard
DimBenchmark
DimDate
DimForgeVersion
```

Potential dashboards:

- collection meta
- matchup matrix
- deck evolution
- Forge health
- unsupported cards
- AI warnings
- benchmark results

Not required for V1.

---

## 40. V1 scope

Implement:

```text
Forge on Raspberry Pi
+
Forge CLI worker
+
REST API
+
SQLite results DB
+
Deck conversion
+
Audit
+
Simulation
+
Jenkins CI/CD
+
Automated deployment
```

Do not initially implement:

- AI deck optimisation
- automatic card purchasing
- automatic collection modification
- full web UI
- Power BI dashboard
- multi-node cluster
- Kubernetes
- cloud deployment

---

## 41. V2

Add:

- web dashboard
- benchmark groups
- round robin
- scheduled tests
- Power BI integration
- deck history
- richer analytics

---

## 42. V3

Add AI-assisted deck optimisation:

```text
Collection
    |
Candidate deck
    |
Forge audit
    |
Forge benchmark
    |
Weak matchup
    |
AI proposes changes
    |
Human approval
    |
New version
    |
Forge
```

---

## 43. V4

Only if the Pi becomes a measured bottleneck:

```text
Simulation Queue
      |
      +---------+---------+
      |         |         |
      v         v         v
    Pi #1     Pi #2     Pi #3
    Forge     Forge     Forge
      |         |         |
      +---------+---------+
                |
                v
           Results DB
```

Do not build distributed execution until measurements justify it.

---

## 44. Acceptance criteria

### Infrastructure
- [ ] Forge runs on Raspberry Pi
- [ ] Java >=17 detected
- [ ] Forge CLI works
- [ ] production service starts after reboot
- [ ] Jenkins can deploy
- [ ] rollback works

### Decks
- [ ] 60-card decks export correctly
- [ ] 100-card Commander decks export correctly
- [ ] exact set/collector metadata preserved
- [ ] collection constraints preserved

### Audit
- [ ] malformed decks rejected
- [ ] unsupported cards detected
- [ ] AI warnings detected
- [ ] raw logs stored
- [ ] audit results stored

### Simulation
- [ ] one game works
- [ ] 100 games work
- [ ] 1000 games work
- [ ] Commander works
- [ ] results stored

### API
- [ ] submit simulation
- [ ] retrieve status
- [ ] retrieve results
- [ ] retrieve audit
- [ ] cancel job
- [ ] health endpoint

### Reliability
- [ ] Forge crash does not crash API
- [ ] timeout terminates Forge
- [ ] job state survives service restart
- [ ] results survive reboot
- [ ] logs survive failure

### CI/CD
- [ ] Jenkins runs unit tests
- [ ] Jenkins runs Forge integration test
- [ ] Jenkins builds package
- [ ] Jenkins deploys versioned release
- [ ] Jenkins performs health check
- [ ] Jenkins performs post-deployment Forge smoke test
- [ ] failed deployment does not become active
- [ ] previous release can be restored

---

## 45. Definition of done

The user can submit:

```http
POST http://raspberrypi:8787/api/simulations
Content-Type: application/json
```

```json
{
  "deck_a": "Abzan_Food",
  "deck_b": "Rakdos_Orcs",
  "format": "constructed",
  "games": 100
}
```

and receive:

```json
{
  "job_id": "sim_001",
  "status": "queued"
}
```

Then:

```http
GET http://raspberrypi:8787/api/simulations/sim_001
```

returns:

```json
{
  "status": "completed",
  "games_requested": 100,
  "games_completed": 100,
  "deck_a_wins": 43,
  "deck_b_wins": 57
}
```

with the complete raw Forge log stored on the Raspberry Pi.

The deployed service must have passed through Jenkins.

---

# Final architecture

```text
                         DEVELOPMENT
                              |
                              v
                           GitHub
                              |
                         webhook/poll
                              |
                              v
                       +--------------+
                       |   Jenkins    |
                       |              |
                       | CI/CD        |
                       | Build        |
                       | Test         |
                       | Forge Test   |
                       | Deploy       |
                       | Health       |
                       +------+-------+
                              |
                         deployment
                              |
                              v
=============================================================
                       RASPBERRY PI 5
=============================================================

   +----------------------+      +----------------------+
   | MTG Collection       |      | MTG Forge Service    |
   | Server               |      |                      |
   |                      |      | REST API             |
   | Collection DB        |----->| Queue                |
   | Cards                |      | Deck Export          |
   | Decks                |      | Audit                |
   | Collection           |      | Worker               |
   +----------------------+      +----------+-----------+
                                            |
                                            v
                                      +-----------+
                                      |   Forge   |
                                      |   Java    |
                                      |   CLI     |
                                      +-----+-----+
                                            |
                                            v
                                      Simulation
                                            |
                                            v
                                      Results DB
                                            |
                          +-----------------+----------------+
                          |                                  |
                          v                                  v
                    MTG Web/API                         Power BI
=============================================================
```

## Core principle

**The Collection Server decides what cards/decks exist.**

**Jenkins decides what software version is deployed.**

**Forge decides what happens when decks play.**

**The Results DB remembers what happened.**
