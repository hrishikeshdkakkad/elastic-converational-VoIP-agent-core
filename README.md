# Voice AI System

Production-ready voice AI system using Twilio, Google Gemini Live API, and Temporal orchestration.

## Overview

Build intelligent voice agents that can make and receive phone calls with natural conversation capabilities. The system handles real-time audio streaming, speech recognition, and AI-powered responses with sub-second latency.

### Key Features

- **Real-time voice AI** - Bidirectional audio streaming with Google Gemini Live API
- **Durable orchestration** - Temporal workflows for reliable call lifecycle management
- **Sub-second latency** - Gemini session pre-warming reduces response time by 77%
- **Horizontal scaling** - Redis session state enables multi-instance deployment
- **Production-ready** - Connection pooling, health checks, metrics, and monitoring
- **AWS infrastructure** - Terraform modules for ECS Fargate deployment

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Voice AI System                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│   ┌─────────┐    ┌─────────────┐    ┌─────────────────────────┐ │
│   │ Twilio  │◄──►│  FastAPI    │◄──►│   Audio Bridge          │ │
│   │ (Phone) │    │  WebSocket  │    │   (u-law ↔ PCM)         │ │
│   └─────────┘    └─────────────┘    └───────────┬─────────────┘ │
│                         │                        │               │
│                         ▼                        ▼               │
│                  ┌─────────────┐         ┌─────────────┐        │
│                  │  Temporal   │         │   Gemini    │        │
│                  │  Workflow   │         │  Live API   │        │
│                  └──────┬──────┘         └─────────────┘        │
│                         │                                        │
│         ┌───────────────┼───────────────┐                       │
│         ▼               ▼               ▼                       │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐                   │
│   │PostgreSQL│   │  Redis   │   │Prometheus│                   │
│   │  (Data)  │   │(Sessions)│   │(Metrics) │                   │
│   └──────────┘   └──────────┘   └──────────┘                   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI, WebSockets |
| Orchestration | Temporal |
| Database | PostgreSQL + Alembic |
| Cache | Redis |
| Telephony | Twilio Media Streams |
| AI | Google Gemini Live API (2.0 Flash) |
| Audio | u-law 8kHz ↔ PCM 16/24kHz (SOXR) |
| Infrastructure | Docker Compose, Terraform (AWS) |

---

## Quick Start

### Prerequisites

- Python 3.13+, Docker, [uv](https://github.com/astral-sh/uv)
- Twilio account with phone number
- Google Gemini API key
- ngrok (for local webhook testing)

### 1. Setup

```bash
git clone <repo-url>
cd voice-ai
uv sync
```

### 2. Configure

Edit `.env` with your credentials:

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_PHONE_NUMBER=+15551234567
GEMINI_API_KEY=AIzaSyxxxxxxxx
BASE_URL=https://your-ngrok-url.ngrok-free.app
```

### 3. Start

```bash
docker compose up -d
```

### 4. Make a Call

```bash
curl -X POST http://localhost:8000/calls \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+15551234567",
    "greeting": "Hello! How can I help you today?",
    "system_prompt": "You are a helpful voice assistant."
  }'
```

### 5. Monitor

- **API Docs**: http://localhost:8000/docs
- **Temporal UI**: http://localhost:8080
- **Grafana**: http://localhost:3000

> **See [Quick Start Guide](docs/QUICKSTART.md) for detailed setup instructions.**

---

## Documentation

| Document | Description |
|----------|-------------|
| **[Quick Start](docs/QUICKSTART.md)** | Get up and running in minutes |
| **[API Reference](docs/API.md)** | Complete endpoint documentation |
| **[Development Guide](docs/DEVELOPMENT.md)** | Local development setup and workflows |
| **[Deployment Guide](docs/DEPLOYMENT.md)** | Production deployment with Docker/AWS |
| **[Troubleshooting](docs/TROUBLESHOOTING.md)** | Common issues and solutions |
| **[VAD Configuration](docs/VAD_CONFIGURATION.md)** | Voice Activity Detection tuning |
| **[Architecture Diagrams](docs/architecture-diagrams.md)** | System design diagrams |
| **[Terraform (AWS)](terraform/README.md)** | AWS infrastructure documentation |

---

## Project Structure

```
voice-ai/
├── src/voice_ai_system/
│   ├── workflows/          # Temporal workflows
│   ├── activities/         # Temporal activities
│   ├── api/                # FastAPI routes
│   ├── services/           # Audio bridge, database
│   ├── models/             # Pydantic & SQLAlchemy models
│   └── utils/              # Audio conversion, logging
├── migrations/             # Alembic database migrations
├── terraform/              # AWS infrastructure (ECS, RDS, etc.)
├── docker/                 # Dockerfiles and configs
├── scripts/                # Development scripts
├── tests/                  # Test suite
└── docs/                   # Documentation
```

---

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/calls` | Initiate outbound call |
| `GET` | `/calls/{id}` | Get call status |
| `POST` | `/calls/{id}/terminate` | End active call |
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Prometheus metrics |

> **Full API documentation: [docs/API.md](docs/API.md)**

---

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number |
| `GEMINI_API_KEY` | Google Gemini API key |
| `BASE_URL` | Public URL for Twilio webhooks |

### Key Optional Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | development | development/staging/production |
| `LOG_LEVEL` | INFO | Logging verbosity |
| `DB_POOL_SIZE` | 20 | Database connection pool size |
| `REDIS_SESSION_TTL` | 7200 | Session TTL in seconds |

---

## Voice Activity Detection

Configure how the AI detects speech and manages conversation turns:

```json
{
  "vad_config": {
    "start_sensitivity": "HIGH",
    "end_sensitivity": "LOW",
    "silence_duration_ms": 500
  }
}
```

> **Full VAD documentation: [docs/VAD_CONFIGURATION.md](docs/VAD_CONFIGURATION.md)**

---

## Deployment

### Local Development

```bash
docker compose up -d
```

### AWS Production

Terraform modules for multi-region ECS deployment (~$155/month):

```bash
cd terraform/environments/production/us-east-1
terraform init && terraform apply
```

> **Full deployment guide: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**

---

## Development

```bash
# Run locally with hot reload
uv run uvicorn src.voice_ai_system.api.main:app --reload

# Run tests
uv run pytest

# Code quality
uv run black src && uv run ruff check src && uv run mypy src
```

> **Full development guide: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**

---

## Troubleshooting

| Issue | Quick Fix |
|-------|-----------|
| PostgreSQL connection refused | Use port **5433** (not 5432) |
| Twilio webhooks failing | Update `BASE_URL` with ngrok URL |
| 3s delay before AI responds | Check Gemini pre-warming logs |
| Workflows not starting | Verify Temporal worker is connected |

> **Full troubleshooting guide: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**

---

## Service URLs (Local Development)

| Service | URL |
|---------|-----|
| FastAPI | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |
| Temporal UI | http://localhost:8080 |
| Grafana | http://localhost:3000 (admin/admin) |
| Prometheus | http://localhost:9090 |
| PostgreSQL | localhost:5433 (temporal/temporal) |

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes and add tests
4. Run: `uv run pytest && uv run black src && uv run ruff check src`
5. Submit a pull request

---

## License

MIT

---

**Built with [Temporal](https://temporal.io), [Twilio](https://twilio.com), and [Google Gemini](https://ai.google.dev)**
