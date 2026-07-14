# Oil Intelligence Platform

A comprehensive intelligence, analytics, forecasting, simulation, strategy, and decision-support platform for global oil markets.

## Overview

The Oil Intelligence Platform transforms complex energy market information into actionable intelligence through a unified ecosystem of data engineering, artificial intelligence, forecasting, simulation, risk management, automation, and strategic analysis.

## Quick Reference

- **Architecture Knowledge Base**: [`docs/INDEX.md`](docs/INDEX.md) — categorized index of all 22 architecture documents
- **Operating Reference**: [`CLAUDE.md`](CLAUDE.md) — directory map, invariants, and development guide
- **CLI Pattern**: `oil <module> <command> [options]`
- **API Pattern**: `/api/v1/<resource>`

## Running the Platform

```bash
pip install -r requirements.txt
# .env in the repo root needs: EIA_API_KEY, FRED_API_KEY, PLATFORM_API_KEYS

python src/cli/oil.py signal run       # full pipeline: data → signals → strategy
python src/cli/oil.py monitor status   # platform health
python src/cli/oil.py deploy check     # release gate (audit + tests + env)
python src/cli/oil.py api serve        # REST API on 127.0.0.1:8000
```

`python src/cli/oil.py --help` lists all 19 commands. The weekly refresh
workflow (`oil auto run weekly_full_run`) is designed to be scheduled
after the Wednesday EIA release.

**Intelligence stack (all engines live):** six freshness-weighted signal
components — geopolitical events, inventory surprise, price momentum,
supply chain stress, CFTC positioning, macro conditions — aggregated into
an explainable composite, then graded by the risk, confidence, forecast,
simulation, and strategy engines. Every output carries its evidence,
timestamps, and warnings.

## Architecture Documents

### Foundation
| Document | System |
|----------|--------|
| [001 — Project Vision](docs/001_Project_Vision.md) | Master architecture and platform vision |
| [003 — Repository Architecture](docs/003_Repository_Architecture.md) | Repository architecture and asset management |

### Data & Intelligence
| Document | System |
|----------|--------|
| [004 — Data Layer](docs/004_Data_Layer.md) | Data layer architecture and pipeline |
| [017 — Forecasting System](docs/017_Forecasting_System.md) | Forecasting engine and price prediction |
| [019 — Geopolitical Intelligence](docs/019_Geopolitical_Intelligence.md) | Geopolitical intelligence and event scoring |
| [020 — Supply Chain Intelligence](docs/020_Supply_Chain_Intelligence.md) | Supply chain monitoring and logistics intelligence |
| [021 — Market Sentiment Intelligence](docs/021_Market_Sentiment_Intelligence.md) | Market sentiment and behavioral intelligence |

### Analytical Engines
| Document | System |
|----------|--------|
| [005 — Engine Architecture](docs/005_Engine_Architecture.md) | Engine architecture and computational core |
| [006 — Scoring System](docs/006_Scoring_System.md) | Scoring system and evaluation framework |
| [007 — Risk System](docs/007_Risk_System.md) | Risk system and uncertainty intelligence |
| [008 — Confidence System](docs/008_Confidence_System.md) | Confidence system and reliability assessment |
| [009 — Strategy System](docs/009_Strategy_System.md) | Strategy system and decision intelligence |
| [010 — Backtesting](docs/010_Backtesting.md) | Backtesting and historical validation |
| [011 — Simulation](docs/011_Simulation.md) | Simulation and scenario analysis |

### Platform Operations
| Document | System |
|----------|--------|
| [002 — Monitoring](docs/002_Monitoring.md) | Monitoring system architecture |
| [012 — Observability System](docs/012_Observability_System.md) | Observability, tracing, and diagnostics |
| [013 — Automation](docs/013_Automation.md) | Automation and workflow orchestration |
| [014 — Security](docs/014_Security.md) | Security system and platform protection |
| [015 — Deployment](docs/015_Deployment.md) | Deployment system and release management |
| [016 — CLI](docs/016_CLI.md) | Command line interface and operational control |
| [022 — API System](docs/022_API_System.md) | API system and communication backbone |

### Strategic
| Document | System |
|----------|--------|
| [018 — Future Ideas](docs/018_Future_Ideas.md) | Long-term roadmap and strategic expansion |

## Project Structure

```
src/
├── data/                  Data acquisition, processing, storage, quality
├── engines/
│   ├── forecast/          Price forecasting models and inference
│   ├── scoring/           Score calculation, weighting, normalization
│   ├── risk/              Risk identification, assessment, mitigation
│   ├── confidence/        Confidence measurement, reliability scoring
│   ├── strategy/          Strategic evaluation, optimization
│   ├── backtesting/       Historical validation, performance metrics
│   ├── simulation/        Scenario analysis, Monte Carlo, stress testing
│   ├── analytics/         Statistical analysis, trend detection
│   ├── automation/        Workflow orchestration, scheduling
│   └── data_processing/   Transformation, feature generation
├── intelligence/
│   ├── geopolitical/      Political events, sanctions, OPEC
│   ├── supply_chain/      Infrastructure, logistics, shipping
│   ├── market_sentiment/  News, futures, options, institutional
│   └── macroeconomic/     GDP, inflation, interest rates
├── api/                   API gateway, endpoints, versioning
├── cli/                   Command-line interface
├── monitoring/            Health checks, alerts, data quality
├── observability/         Metrics, logs, traces, events
└── security/              Auth, encryption, secrets, audit
```

## Core Principles

- **Intelligence First** — Information quality, accuracy, and analytical depth above all
- **Explainability** — Every output must be understandable and traceable
- **Continuous Improvement** — The platform learns and improves over time
- **Modular Architecture** — Independent but connected systems that remain expandable

## License

Proprietary
