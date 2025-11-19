# Voice AI System

Production-ready voice AI system using Twilio, Google Gemini Live API, and Temporal orchestration.

## Architecture

- **FastAPI**: REST API and WebSocket endpoints
- **Temporal**: Durable workflow orchestration for call lifecycle management
- **PostgreSQL**: Database for Temporal and application data with Alembic migrations
- **Redis**: Session state management for horizontal scalability
- **Twilio**: Telephony and real-time audio streaming
- **Google Gemini Live API**: Real-time conversational AI with bidirectional audio
- **Docker Compose**: Local development and deployment

## Features

✅ **Durable phone call orchestration** with Temporal workflows
✅ **Real-time audio streaming** via WebSocket (Twilio Media Streams)
✅ **AI-powered conversations** with Google Gemini Live (2.5 Flash Native Audio)
✅ **Optimized latency** with Gemini session pre-warming (~77% faster first response)
✅ **Audio bridge architecture** - streaming outside Temporal's hot path
✅ **Audio format conversion** (μ-law ↔ PCM) with SOXR resampling
✅ **Call transcripts** and metadata storage with PostgreSQL
✅ **Fault-tolerant execution** with automatic retries
✅ **Session state management** with Redis for horizontal scaling
✅ **Database migrations** with Alembic
✅ **Monitoring** with Temporal UI, Prometheus, and Grafana
✅ **Production-ready** with connection pooling and resource management

## Prerequisites

- **Python 3.13+**
- **Docker and Docker Compose**
- **[uv](https://github.com/astral-sh/uv)** package manager
- **Twilio account** with phone number ([Sign up](https://www.twilio.com/try-twilio))
- **Google Cloud account** with Gemini API access ([Get API key](https://aistudio.google.com/app/apikey))
- **ngrok** (for local Twilio webhook testing) - [Install](https://ngrok.com/)

## Quick Start

### 1. Clone and Setup

```bash
# Clone repository
git clone <your-repo-url>
cd voice-ai

# Install dependencies
uv sync

# Environment file is already created with defaults
# Edit .env to add your API credentials (see below)
```

### 2. Configure Environment Variables

The `.env` file is already created with sensible defaults. **You only need to add your API credentials:**

```bash
# Edit .env and replace these values:
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx    # From https://console.twilio.com/
TWILIO_AUTH_TOKEN=your_auth_token_here                   # From https://console.twilio.com/
TWILIO_PHONE_NUMBER=+15551234567                         # Your Twilio number (E.164 format)
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # From https://aistudio.google.com/app/apikey
```

**For local testing with Twilio (required for actual calls):**
```bash
# Start ngrok to expose your local server
ngrok http 8000

# Copy the HTTPS URL and update .env:
BASE_URL=https://abc123.ngrok-free.app
```

### 3. Start All Services

```bash
# Start entire stack (Temporal, PostgreSQL, Redis, API, Workers)
docker compose up -d

# View logs
docker compose logs -f

# Check all services are healthy
docker compose ps
```

**Wait for all services to be healthy** (usually ~60 seconds for first startup).

### 4. Run Database Migrations

```bash
# Initialize database schema
docker compose exec api alembic upgrade head

# Verify migrations
docker compose exec api alembic current
```

### 5. Verify System Health

```bash
# Check all services are healthy
./scripts/test-health.sh

# Or manually:
curl http://localhost:8000/health
```

### 6. Access Services

| Service | URL | Credentials |
|---------|-----|-------------|
| **FastAPI** | http://localhost:8000 | - |
| **API Docs (Swagger)** | http://localhost:8000/docs | - |
| **Temporal UI** | http://localhost:8080 | - |
| **Grafana** | http://localhost:3000 | admin / admin |
| **Prometheus** | http://localhost:9090 | - |

### 7. Make a Test Call

```bash
# Using curl
curl -X POST http://localhost:8000/calls \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+15551234567",
    "greeting": "Hello! How can I help you today?",
    "system_prompt": "You are a helpful voice assistant. Be concise and natural.",
    "max_duration_seconds": 1800
  }'

# Response:
# {
#   "workflow_id": "call-abc123...",
#   "run_id": "def456...",
#   "phone_number": "+15551234567",
#   "status": "initiated"
# }
```

**Monitor the call:**
```bash
# Check call status
curl http://localhost:8000/calls/{workflow_id}

# View in Temporal UI
open http://localhost:8080
```

## Project Structure

```
voice-ai/
├── src/voice_ai_system/
│   ├── workflows/          # Temporal workflow definitions
│   │   └── call_workflow.py
│   ├── activities/         # Temporal activities (Twilio, Gemini, DB)
│   │   ├── twilio_activities.py
│   │   ├── gemini_activities.py
│   │   └── database_activities.py
│   ├── api/               # FastAPI endpoints and WebSocket handlers
│   │   ├── routes/
│   │   │   ├── calls.py       # Call management endpoints
│   │   │   └── twilio.py      # Twilio webhooks & WebSocket
│   │   └── main.py
│   ├── services/          # Business logic layer
│   │   ├── audio_bridge.py    # Real-time Twilio-Gemini audio bridge
│   │   └── call_service.py
│   ├── models/            # Pydantic models and schemas
│   │   └── call.py
│   ├── utils/             # Utilities
│   │   ├── audio.py           # Format conversion (μ-law ↔ PCM)
│   │   ├── redis_client.py    # Redis session management
│   │   └── logging.py
│   ├── config.py          # Application configuration
│   └── worker.py          # Temporal worker entry point
├── migrations/            # Alembic database migrations
├── docker/
│   ├── Dockerfile.api     # FastAPI service
│   ├── Dockerfile.worker  # Temporal worker
│   ├── prometheus.yml     # Metrics configuration
│   └── temporal-config/   # Temporal dynamic config
├── scripts/
│   ├── start-full.sh      # Start all services
│   └── test-health.sh     # Health check script
├── tests/                 # Test suite
├── docs/                  # Documentation
│   ├── architecture-diagrams.md           # Mermaid architecture diagrams
│   └── gemini-preinitialization-analysis.md
├── docker-compose.yml     # Multi-service orchestration
├── pyproject.toml        # Project dependencies (uv)
├── alembic.ini           # Alembic configuration
├── .env                  # Environment variables (created)
└── .env.example          # Environment template
```

## Architecture Deep Dive

### Call Flow Overview

```
1. Client → POST /calls → API Server
2. API → Start Temporal Workflow + Pre-warm Gemini session (parallel)
3. Workflow → Execute Twilio activity → Make outbound call
4. Phone rings → User answers
5. Twilio → GET /twiml/{workflow_id} → Returns TwiML with WebSocket URL
6. Twilio → WebSocket /ws/media/{workflow_id} → Connects
7. WebSocket → Attach to pre-warmed Gemini session (instant!)
8. User speaks → Audio flows: Twilio → Audio Bridge → Gemini → Audio Bridge → Twilio
9. Transcripts sync to Workflow every 2 seconds (not every frame)
10. Call ends → Workflow completes → Transcripts saved to DB
```

### Audio Bridge Architecture

**Key Optimization: Real-time audio processing happens OUTSIDE Temporal's hot path.**

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI WebSocket                       │
│  (Receives Twilio μ-law audio, sends back μ-law audio)     │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                   Audio Bridge Manager                       │
│  • Pre-warms Gemini during ring time (~2s savings)         │
│  • Direct audio streaming (bypasses Temporal)               │
│  • Buffers transcripts for periodic sync                    │
└────────────────────┬────────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐  ┌──────────────────┐
│  Gemini Live API │  │ Temporal Workflow│
│  In: 16kHz PCM   │  │ (Coarse events)  │
│  Out: 24kHz PCM  │  │                  │
└──────────────────┘  └──────────────────┘
     Real-time             Every 2 seconds
    (every 20ms)          (transcript sync)
```

**Benefits:**
- ✅ Reduced Temporal activity load by 100x (from every 20ms to every 2s)
- ✅ Lower latency (no Temporal round-trip for audio frames)
- ✅ Gemini pre-warming reduces first response from 3s → 0.7s

### Gemini Session Pre-Warming

**Problem:** After user answers, there's a 3-second delay while Gemini initializes.

**Solution:** Pre-initialize Gemini session during ring time (in parallel with call setup).

**Timeline:**
```
Before optimization:
0s    → Call initiated
5-30s → User answers "Hello?"
30s   → WebSocket connects
32s   → Gemini initialized (SLOW)
33s   → User hears response
      = 3s user waiting in silence ❌

After optimization:
0s    → Call initiated + Gemini pre-warming starts (parallel)
2s    → Gemini session ready
5-30s → User answers "Hello?"
30s   → WebSocket connects → Uses pre-warmed session (instant!)
30.7s → User hears response
      = 0.7s user waiting ✅ (77% improvement)
```

### Redis Session Management

Redis stores session state for horizontal API scaling:

```python
Session key: session:{workflow_id}
Data: {
    "call_id": "uuid",
    "workflow_id": "call-abc123...",
    "phone_number": "+15551234567",
    "greeting": "Hello!",
    "system_prompt": "...",
    "status": "in_progress",
    "stream_sid": "MZxxx...",
    "created_at": "2024-01-15T10:30:00Z"
}
TTL: 2 hours (configurable via REDIS_SESSION_TTL)
```

## Development

### Local Development (without Docker)

Run infrastructure in Docker, but API/Worker locally for faster iteration:

```bash
# 1. Start only infrastructure services
docker compose up -d postgresql temporal temporal-ui redis elasticsearch

# 2. Wait for services to be healthy
docker compose ps

# 3. Set environment variables for local connection
export TEMPORAL_HOST=localhost
export REDIS_HOST=localhost
export DATABASE_URL=postgresql://temporal:temporal@localhost:5432/voice_ai

# 4. Run database migrations
uv run alembic upgrade head

# 5. Run API locally (hot reload enabled)
uv run uvicorn src.voice_ai_system.api.main:app --reload --port 8000

# 6. Run worker locally (in another terminal)
uv run python -m src.voice_ai_system.worker
```

### Database Migrations

```bash
# Create a new migration
uv run alembic revision --autogenerate -m "description"

# Apply migrations
uv run alembic upgrade head

# Rollback one migration
uv run alembic downgrade -1

# View migration history
uv run alembic history

# View current version
uv run alembic current
```

### Install Development Dependencies

```bash
uv sync --dev
```

### Run Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=html

# Run specific test
uv run pytest tests/test_audio_bridge.py -v

# Run with logs
uv run pytest -s
```

### Code Quality

```bash
# Format code
uv run black src tests

# Lint code
uv run ruff check src tests

# Type checking
uv run mypy src

# Run all checks
uv run black src tests && uv run ruff check src tests && uv run mypy src
```

## API Endpoints

### Call Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/calls` | Initiate outbound call |
| `GET` | `/calls/{workflow_id}` | Get call status |
| `POST` | `/calls/{workflow_id}/terminate` | Terminate active call |
| `GET` | `/calls/{workflow_id}/result` | Get final call result |

### Twilio Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/twilio/twiml/{workflow_id}` | Generate TwiML with WebSocket |
| `WebSocket` | `/twilio/ws/media/{workflow_id}` | Twilio Media Stream handler |
| `POST` | `/twilio/status/{workflow_id}` | Call status callbacks |

### Example: Initiate Call

**Request:**
```bash
POST /calls
Content-Type: application/json

{
  "phone_number": "+15551234567",
  "greeting": "Hello! How can I help you today?",
  "system_prompt": "You are a helpful assistant. Be concise.",
  "max_duration_seconds": 1800
}
```

**Response:**
```json
{
  "workflow_id": "call-abc123-def456-ghi789",
  "run_id": "abc123...",
  "phone_number": "+15551234567",
  "status": "initiated"
}
```

**Monitor:**
```bash
# Get status
GET /calls/call-abc123-def456-ghi789

# Response:
{
  "workflow_id": "call-abc123-def456-ghi789",
  "status": "in_progress",
  "transcript_count": 15,
  "call_config": {
    "phone_number": "+15551234567",
    "greeting": "Hello! How can I help you today?"
  }
}
```

## Monitoring

### Temporal UI

View workflow executions, query history, and debug issues:
- **Workflow list**: http://localhost:8080/namespaces/default/workflows
- **Event history**: Click on any workflow to see detailed execution
- **Stack traces**: View failures and retry attempts
- **Queries & Signals**: See real-time workflow state

### Prometheus Metrics

Custom metrics exposed at `/metrics`:
- Call volumes and durations
- Workflow success/failure rates
- Audio processing latency
- API request rates

### Grafana Dashboards

Pre-configured dashboards for:
- System overview
- Call analytics
- Temporal performance
- Resource utilization

Access: http://localhost:3000 (admin / admin)

### Logs

```bash
# View all logs
docker compose logs -f

# View specific service
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f temporal

# View recent logs
docker compose logs --tail=100 api
```

## Production Considerations

### Environment Configuration

**For production, update `.env`:**

```bash
# Production environment
ENVIRONMENT=production
LOG_LEVEL=WARNING

# Your production domain
BASE_URL=https://voice-ai.yourdomain.com

# Strong database password
POSTGRES_PASSWORD=<strong-password>

# Enable Redis authentication
REDIS_PASSWORD=<redis-password>

# Production API keys
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1234567890
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxx
```

### Scaling

**Horizontal Scaling:**
```yaml
# docker-compose.yml
worker:
  deploy:
    replicas: 5  # Scale workers based on call volume
```

**Database:**
- PostgreSQL works well for moderate loads (hundreds of concurrent calls)
- Consider Cassandra for high volume (thousands of concurrent calls)
- Use managed PostgreSQL (AWS RDS, Cloud SQL) for HA

**Temporal:**
- `NUM_HISTORY_SHARDS=512` (already configured, immutable after deployment)
- Run Temporal in HA mode with multiple replicas

### Resource Requirements

**Minimum (moderate load - hundreds of concurrent calls):**
- **Temporal**: 2 CPU, 4GB RAM
- **PostgreSQL**: 2 CPU, 4GB RAM
- **Redis**: 1 CPU, 1GB RAM
- **Workers**: 1 CPU, 2GB RAM per replica
- **API**: 1 CPU, 2GB RAM

**Recommended (production):**
- **Temporal**: 4 CPU, 8GB RAM
- **PostgreSQL**: 4 CPU, 8GB RAM
- **Redis**: 2 CPU, 2GB RAM
- **Workers**: 2 CPU, 4GB RAM per replica (3-5 replicas)
- **API**: 2 CPU, 4GB RAM (2-3 replicas)

### Security

- ✅ Enable TLS for Temporal gRPC
- ✅ Use secrets management (AWS Secrets Manager, HashiCorp Vault)
- ✅ Implement API authentication (JWT)
- ✅ Enable PostgreSQL SSL
- ✅ Rotate API keys regularly
- ✅ Use Redis authentication (set `REDIS_PASSWORD`)
- ✅ Configure firewall rules (only expose necessary ports)
- ✅ Enable rate limiting on API endpoints

### High Availability

- ✅ Run multiple worker replicas
- ✅ Run multiple API replicas with load balancer
- ✅ Use managed PostgreSQL (AWS RDS, Cloud SQL)
- ✅ Deploy Temporal server in HA mode
- ✅ Implement health checks and auto-restart
- ✅ Use Redis Sentinel or Cluster for HA
- ✅ Set up monitoring and alerting

### Multi-Instance API Deployment

**For Gemini pre-warming to work with multiple API instances**, configure sticky sessions in your load balancer:

**Nginx example:**
```nginx
upstream api_backend {
    hash $request_uri consistent;  # Route by URL
    server api-1:8000;
    server api-2:8000;
    server api-3:8000;
}
```

This ensures the same workflow always hits the same API instance, allowing pre-warmed sessions to be found.

## Troubleshooting

### Services Not Starting

```bash
# Check service status
docker compose ps

# Check logs for specific service
docker compose logs temporal
docker compose logs postgresql

# Restart services
docker compose restart

# Clean restart (removes volumes - CAUTION: data loss)
docker compose down -v
docker compose up -d
```

### Workflows Not Starting

```bash
# 1. Check Temporal server is healthy
docker compose logs temporal | grep -i error

# 2. Verify worker is connected
docker compose logs worker | grep -i "worker started"

# 3. Check task queue
docker compose exec temporal tctl task-queue describe --task-queue voice-ai-task-queue

# 4. View Temporal UI
open http://localhost:8080
```

### Database Connection Issues

```bash
# Check PostgreSQL health
docker compose exec postgresql pg_isready -U temporal

# Test connection
docker compose exec postgresql psql -U temporal -d voice_ai -c "SELECT 1;"

# View connection pool stats
docker compose logs api | grep -i "pool"

# Check if database exists
docker compose exec postgresql psql -U temporal -c "\l"

# Run migrations if needed
docker compose exec api alembic upgrade head
```

### Audio Quality Issues

**Symptoms:** Choppy audio, garbled speech, silence

**Solutions:**
- ✅ Check WebSocket connection stability (logs show disconnects)
- ✅ Verify audio format conversion (μ-law ↔ PCM)
- ✅ Monitor audio processing latency (<100ms target)
- ✅ Check Gemini API rate limits
- ✅ Verify network bandwidth (24kHz PCM = ~384 kbps)

```bash
# Check WebSocket logs
docker compose logs api | grep -i "websocket"

# Monitor audio bridge
docker compose logs api | grep -i "audio bridge"
```

### Gemini Pre-warming Not Working

```bash
# Check if pre-warming is triggered
docker compose logs api | grep -i "pre-warming initiated"

# Check if pre-warmed session is used
docker compose logs api | grep -i "using pre-warmed"

# If not found, check for errors
docker compose logs api | grep -i "failed to pre-warm"
```

### Redis Connection Issues

```bash
# Check Redis health
docker compose exec redis redis-cli ping

# Test connection
docker compose exec redis redis-cli
> KEYS session:*
> GET session:call-abc123...

# Check API can connect
docker compose logs api | grep -i redis
```

### Twilio Webhook Issues

**Problem:** Twilio can't reach your webhooks

**Solution:**
1. Ensure ngrok is running: `ngrok http 8000`
2. Update `.env` with ngrok URL: `BASE_URL=https://abc123.ngrok-free.app`
3. Restart API: `docker compose restart api`
4. Test webhook: `curl https://abc123.ngrok-free.app/health`

```bash
# Verify BASE_URL is correct
docker compose exec api env | grep BASE_URL

# Check Twilio webhook logs
docker compose logs api | grep -i twiml
```

### Migration Issues

```bash
# Check current migration version
docker compose exec api alembic current

# View migration history
docker compose exec api alembic history

# If stuck, check logs
docker compose logs api | grep -i alembic

# Rollback and retry
docker compose exec api alembic downgrade -1
docker compose exec api alembic upgrade head
```

## Common Issues

### "Session not found in Redis"

**Cause:** Redis session expired or not created

**Fix:**
```bash
# Check Redis TTL setting
grep REDIS_SESSION_TTL .env

# Increase TTL if needed (default: 7200 = 2 hours)
REDIS_SESSION_TTL=14400  # 4 hours
```

### "Temporal workflow not found"

**Cause:** Workflow completed or never started

**Fix:**
```bash
# Check Temporal UI for workflow status
open http://localhost:8080

# View worker logs
docker compose logs worker | grep -i "workflow.*started"
```

### "Gemini API rate limit exceeded"

**Cause:** Too many concurrent requests to Gemini

**Fix:**
- Reduce worker replicas temporarily
- Implement exponential backoff in activities
- Check your Gemini API quota

## Performance Optimization Tips

1. **Gemini Pre-warming**: Already enabled (saves ~2s per call)
2. **Database Connection Pooling**: Already configured (`DB_POOL_SIZE=20`)
3. **Redis Session Caching**: Already implemented
4. **Worker Scaling**: Adjust `deploy.replicas` based on load
5. **Temporal History Shards**: Already set to 512 (good for moderate load)

## Documentation

- **Architecture Diagrams**: `docs/architecture-diagrams.md` (12 Mermaid diagrams)
- **Gemini Pre-warming Analysis**: `docs/gemini-preinitialization-analysis.md`
- **API Documentation**: http://localhost:8000/docs (Swagger UI)
- **Temporal Documentation**: https://docs.temporal.io/

## License

MIT

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `uv run pytest`
5. Submit a pull request

## Support

- **Issues**: [GitHub Issues](https://github.com/your-repo/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-repo/discussions)
- **Temporal Community**: https://community.temporal.io/

---

**Made with ❤️ using Temporal, Twilio, and Google Gemini**
