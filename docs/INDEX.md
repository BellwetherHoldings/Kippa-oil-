# Oil Intelligence Platform — Knowledge Base Index

This index organizes the 22 architecture documents by system layer. Each document is the authoritative reference for its domain. Read the governing document before building, modifying, or extending any system.

---

## Layer 1: Foundation

The principles, vision, and organizational standards that govern everything.

| # | Document | Purpose |
|---|----------|---------|
| 001 | [Project Vision](001_Project_Vision.md) | Platform mission, core principles, architectural invariants, definition of success |
| 003 | [Repository Architecture](003_Repository_Architecture.md) | File organization, naming standards, version control, asset lifecycle, data lineage |

---

## Layer 2: Data & Intelligence

Where information enters the platform — collection, processing, and domain-specific intelligence systems.

| # | Document | Purpose |
|---|----------|---------|
| 004 | [Data Layer](004_Data_Layer.md) | Data acquisition, pipeline (Collection → Storage → Distribution), quality framework, metadata |
| 017 | [Forecasting System](017_Forecasting_System.md) | Price prediction, forecast horizons, model types, validation, continuous learning |
| 019 | [Geopolitical Intelligence](019_Geopolitical_Intelligence.md) | Conflicts, sanctions, OPEC, elections, policy — event classification and impact scoring |
| 020 | [Supply Chain Intelligence](020_Supply_Chain_Intelligence.md) | Production, pipelines, tankers, ports, refineries, storage — logistics monitoring |
| 021 | [Market Sentiment Intelligence](021_Market_Sentiment_Intelligence.md) | News, analysts, futures, options, institutional activity — sentiment scoring |

---

## Layer 3: Analytical Engines

Where intelligence is produced — the computational systems that transform data into decisions.

| # | Document | Purpose |
|---|----------|---------|
| 005 | [Engine Architecture](005_Engine_Architecture.md) | Engine principles, standardized I/O, execution lifecycle, communication standards |
| 006 | [Scoring System](006_Scoring_System.md) | Score categories, weighting, normalization, explainability, historical tracking |
| 007 | [Risk System](007_Risk_System.md) | Risk categories (9), lifecycle (7 stages), assessment framework, exposure analysis |
| 008 | [Confidence System](008_Confidence_System.md) | Confidence factors, classification (Very High → Very Low), uncertainty quantification |
| 009 | [Strategy System](009_Strategy_System.md) | Strategic planning, decision framework (8 stages), evaluation criteria, optimization |
| 010 | [Backtesting](010_Backtesting.md) | Historical validation, performance metrics (MAE, RMSE, Sharpe), drift detection |
| 011 | [Simulation](011_Simulation.md) | Scenario analysis, Monte Carlo, stress testing, sensitivity analysis, variable management |

---

## Layer 4: Platform Operations

The systems that keep the platform running — monitoring, security, deployment, and interfaces.

| # | Document | Purpose |
|---|----------|---------|
| 002 | [Monitoring](002_Monitoring.md) | System health, data quality, model performance, alert classification (4 tiers) |
| 012 | [Observability System](012_Observability_System.md) | Metrics, logs, distributed tracing, events, root cause analysis, capacity planning |
| 013 | [Automation](013_Automation.md) | Workflow types (4), scheduling, triggers, failure recovery, audit logging |
| 014 | [Security](014_Security.md) | Identity, auth (7 methods), RBAC (8 roles), encryption, threat detection, incident response |
| 015 | [Deployment](015_Deployment.md) | CI/CD pipeline, environments (4), IaC, database migration, rollback, release management |
| 016 | [CLI](016_CLI.md) | Command structure (`oil <module> <command>`), 15 modules, scripting, diagnostics |
| 022 | [API System](022_API_System.md) | API categories (6), gateway, versioning, rate limiting, response standards, documentation |

---

## Layer 5: Strategic

The long-term vision for platform evolution.

| # | Document | Purpose |
|---|----------|---------|
| 018 | [Future Ideas](018_Future_Ideas.md) | Expansion roadmap, AI initiatives, new markets, research topics, business opportunities |

---

## Cross-Reference: Which Document Governs What

| If you are working on... | Read... |
|--------------------------|---------|
| Data ingestion, pipelines, storage | 004 Data Layer |
| Any analytical engine | 005 Engine Architecture first, then the specific engine doc |
| Price forecasting or models | 017 Forecasting System |
| Score calculations or rankings | 006 Scoring System |
| Risk identification or assessment | 007 Risk System |
| Confidence or reliability scoring | 008 Confidence System |
| Strategic planning or optimization | 009 Strategy System |
| Historical validation or testing | 010 Backtesting |
| Scenario analysis or Monte Carlo | 011 Simulation |
| Political events or OPEC monitoring | 019 Geopolitical Intelligence |
| Shipping, pipelines, inventory | 020 Supply Chain Intelligence |
| News, futures, sentiment analysis | 021 Market Sentiment Intelligence |
| Health checks or alerts | 002 Monitoring |
| Logs, traces, diagnostics | 012 Observability System |
| Scheduled jobs or workflows | 013 Automation |
| Auth, encryption, secrets | 014 Security |
| Releases or infrastructure | 015 Deployment |
| CLI commands or scripting | 016 CLI |
| API endpoints or integrations | 022 API System |
| File organization or naming | 003 Repository Architecture |
| Future planning or research | 018 Future Ideas |
| Anything architectural | 001 Project Vision |
