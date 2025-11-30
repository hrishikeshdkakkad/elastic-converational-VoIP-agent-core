# Quick Start Guide

Get up and running with Voice AI in minutes.

## Prerequisites

- **Python 3.13+**
- **Docker and Docker Compose**
- **[uv](https://github.com/astral-sh/uv)** package manager
- **Twilio account** with phone number ([Sign up](https://www.twilio.com/try-twilio))
- **Google Gemini API key** ([Get key](https://aistudio.google.com/app/apikey))
- **ngrok** for local webhook testing ([Install](https://ngrok.com/))

## Step 1: Clone and Install

```bash
git clone <your-repo-url>
cd voice-ai
uv sync
```

## Step 2: Configure Environment

Edit `.env` with your API credentials:

```bash
# Required credentials
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+15551234567
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

For local development with Twilio webhooks:

```bash
# Start ngrok
ngrok http 8000

# Update .env with the HTTPS URL
BASE_URL=https://abc123.ngrok-free.app
```

## Step 3: Start Services

```bash
# Start the entire stack
docker compose up -d

# Wait for services to be healthy (~60 seconds)
docker compose ps

# Verify health
curl http://localhost:8000/health
```

## Step 4: Make Your First Call

```bash
curl -X POST http://localhost:8000/calls \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+15551234567",
    "greeting": "Hello! How can I help you today?",
    "system_prompt": "You are a helpful voice assistant."
  }'
```

## Step 5: Monitor the Call

- **API Status**: `curl http://localhost:8000/calls/{workflow_id}`
- **Temporal UI**: http://localhost:8080
- **Logs**: `docker compose logs -f api worker`

## Service URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| FastAPI | http://localhost:8000 | - |
| API Docs | http://localhost:8000/docs | - |
| Temporal UI | http://localhost:8080 | - |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | - |
| PostgreSQL | localhost:5433 | temporal / temporal |

## Next Steps

- [API Reference](./API.md) - Full endpoint documentation
- [Development Guide](./DEVELOPMENT.md) - Local development setup
- [VAD Configuration](./VAD_CONFIGURATION.md) - Voice Activity Detection tuning
- [Deployment Guide](./DEPLOYMENT.md) - Production deployment with Terraform
- [Troubleshooting](./TROUBLESHOOTING.md) - Common issues and solutions
