# Oil Intelligence Platform — Operating Reference

This is the Oil Intelligence Platform. Every decision, every file, every system follows the architecture defined in the knowledge base below. Read the relevant document before building, modifying, or extending any system.

## Knowledge Base

The `docs/` directory is the architectural bible. It is organized into four layers:

### Foundation
| Document | Governs | Source Directory |
|----------|---------|-----------------|
| [001 Project Vision](docs/001_Project_Vision.md) | Platform mission, principles, architectural invariants | — |
| [003 Repository Architecture](docs/003_Repository_Architecture.md) | File organization, naming, versioning, asset management | All directories |

### Data & Intelligence Layer
| Document | Governs | Source Directory |
|----------|---------|-----------------|
| [004 Data Layer](docs/004_Data_Layer.md) | Data acquisition, storage, processing, quality, lineage | `src/data/` |
| [017 Forecasting System](docs/017_Forecasting_System.md) | Price prediction, model types, forecast horizons, validation | `src/engines/forecast/` |
| [019 Geopolitical Intelligence](docs/019_Geopolitical_Intelligence.md) | Political events, sanctions, OPEC, conflict monitoring | `src/intelligence/geopolitical/` |
| [020 Supply Chain Intelligence](docs/020_Supply_Chain_Intelligence.md) | Pipelines, tankers, ports, refineries, storage, logistics | `src/intelligence/supply_chain/` |
| [021 Market Sentiment Intelligence](docs/021_Market_Sentiment_Intelligence.md) | News, analyst, futures, options, institutional positioning | `src/intelligence/market_sentiment/` |

### Analytical Engine Layer
| Document | Governs | Source Directory |
|----------|---------|-----------------|
| [005 Engine Architecture](docs/005_Engine_Architecture.md) | Engine principles, communication, execution lifecycle | `src/engines/` |
| [006 Scoring System](docs/006_Scoring_System.md) | Score categories, weighting, normalization, explainability | `src/engines/scoring/` |
| [007 Risk System](docs/007_Risk_System.md) | Risk categories, assessment, probability, exposure, mitigation | `src/engines/risk/` |
| [008 Confidence System](docs/008_Confidence_System.md) | Confidence factors, reliability, uncertainty, classification | `src/engines/confidence/` |
| [009 Strategy System](docs/009_Strategy_System.md) | Strategic planning, optimization, decision frameworks | `src/engines/strategy/` |
| [010 Backtesting](docs/010_Backtesting.md) | Historical validation, metrics, drift detection, model comparison | `src/engines/backtesting/` |
| [011 Simulation](docs/011_Simulation.md) | Scenarios, Monte Carlo, stress testing, sensitivity analysis | `src/engines/simulation/` |

### Platform Operations Layer
| Document | Governs | Source Directory |
|----------|---------|-----------------|
| [002 Monitoring](docs/002_Monitoring.md) | System health, data quality, model performance, alerts | `src/monitoring/` |
| [012 Observability System](docs/012_Observability_System.md) | Metrics, logs, traces, events, diagnostics, dashboards | `src/observability/` |
| [013 Automation](docs/013_Automation.md) | Scheduling, triggers, workflows, failure recovery | `src/engines/automation/` |
| [014 Security](docs/014_Security.md) | Auth, encryption, secrets, audit, threat detection, incident response | `src/security/` |
| [015 Deployment](docs/015_Deployment.md) | CI/CD, environments, IaC, migrations, rollback | `config/`, `src/deployment/` |
| [016 CLI](docs/016_CLI.md) | Command structure (`oil <module> <command>`), scripting, diagnostics | `src/cli/` |
| [022 API System](docs/022_API_System.md) | Endpoints, gateway, versioning, rate limiting, response standards | `src/api/` |

### Strategic
| Document | Governs |
|----------|---------|
| [018 Future Ideas](docs/018_Future_Ideas.md) | Roadmap, expansion, research topics, long-term vision |

## Architectural Invariants (Always True)

These are non-negotiable. Taken from [001 Project Vision](docs/001_Project_Vision.md):

1. Data quality comes before analysis.
2. Every analytical output must have supporting evidence.
3. Models must remain explainable.
4. Risk must always be considered.
5. Uncertainty must always be measured.
6. Historical information must remain preserved.
7. Security must exist throughout the architecture.
8. Human oversight must remain available for critical decisions.

## Directory Map

```
Kippa-oil-/
├── CLAUDE.md              ← You are here. Operating reference.
├── README.md              ← Project overview and document index.
├── docs/                  ← Architecture knowledge base (22 documents).
├── src/
│   ├── data/              ← Data acquisition, processing, storage, quality.
│   ├── engines/
│   │   ├── forecast/      ← Price forecasting models and inference.
│   │   ├── scoring/       ← Score calculation, weighting, normalization.
│   │   ├── risk/          ← Risk identification, assessment, mitigation.
│   │   ├── confidence/    ← Confidence measurement, reliability scoring.
│   │   ├── strategy/      ← Strategic evaluation, optimization.
│   │   ├── backtesting/   ← Historical validation, performance metrics.
│   │   ├── simulation/    ← Scenario analysis, Monte Carlo, stress testing.
│   │   ├── analytics/     ← Statistical analysis, trend detection, patterns.
│   │   ├── automation/    ← Workflow orchestration, scheduling, triggers.
│   │   └── data_processing/ ← Transformation, feature generation, normalization.
│   ├── intelligence/
│   │   ├── geopolitical/  ← Political events, sanctions, OPEC monitoring.
│   │   ├── supply_chain/  ← Infrastructure, logistics, shipping, inventory.
│   │   ├── market_sentiment/ ← News, futures, options, institutional activity.
│   │   └── macroeconomic/ ← GDP, inflation, interest rates, employment.
│   ├── api/               ← API gateway, endpoints, versioning, rate limiting.
│   ├── cli/               ← Command-line interface (oil <module> <command>).
│   ├── monitoring/        ← Health checks, alerts, data quality monitoring.
│   ├── observability/     ← Metrics, logs, traces, events, dashboards.
│   └── security/          ← Auth, encryption, secrets, audit, threat detection.
├── config/                ← Environment configs, deployment params, automation rules.
├── tests/                 ← Test suites for all systems.
├── research/              ← Market research, experimental analysis, concepts.
├── simulations/           ← Scenario definitions, results, historical experiments.
├── reports/               ← Analytical reports, generated intelligence products.
└── archive/               ← Deprecated systems, historical versions, legacy docs.
```

## How To Use This

- **Before building anything**: Read the governing document for that system.
- **Before adding a file**: Check the directory map. Every asset has a defined location.
- **Before making a decision**: Check the architectural invariants.
- **When uncertain**: The document for that system defines responsibilities, anti-patterns, and compliance criteria. Follow them.
- **Engine work**: Every engine follows the same lifecycle — Receive Input → Validate → Execute → Verify Output → Log Results → Publish Output. See [005 Engine Architecture](docs/005_Engine_Architecture.md).
- **New intelligence source**: Follow the pattern in `src/intelligence/`. Each source has its own classification, impact scoring, and historical repository. See docs 019–021.
- **CLI commands**: Follow `oil <module> <command> [options]` syntax. See [016 CLI](docs/016_CLI.md).
- **API endpoints**: Follow `/api/v1/` versioning. Every endpoint needs auth, validation, standardized responses. See [022 API System](docs/022_API_System.md).

## Anti-Patterns (Never Do These)

- Black-box decisions or hidden scoring
- Unverified data or missing lineage
- Undocumented models or untracked changes
- Automation without monitoring
- Security as an afterthought
- Treating scores or forecasts as absolute truth
- Single-model or single-source dependence
