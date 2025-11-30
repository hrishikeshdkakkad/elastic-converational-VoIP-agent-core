# Development Guide

Local development setup, workflows, and best practices.

## Development Setup

### Option 1: Full Docker (Recommended for Quick Start)

Run everything in Docker:

```bash
docker compose up -d
docker compose logs -f
```

### Option 2: Hybrid (Recommended for Development)

Run infrastructure in Docker, but API/Worker locally for hot reload:

```bash
# 1. Start infrastructure only
docker compose up -d postgresql temporal temporal-ui redis elasticsearch

# 2. Wait for services
docker compose ps

# 3. Set environment for local connection
export TEMPORAL_HOST=localhost
export REDIS_HOST=localhost
export DATABASE_URL=postgresql://temporal:temporal@localhost:5433/voice_ai

# 4. Run migrations
uv run alembic upgrade head

# 5. Run API with hot reload (terminal 1)
uv run uvicorn src.voice_ai_system.api.main:app --reload --port 8000

# 6. Run worker (terminal 2)
uv run python -m src.voice_ai_system.worker
```

### Option 3: Fully Local

For advanced users who want to run Temporal locally without Docker.

---

## Project Structure

```
src/voice_ai_system/
├── workflows/              # Temporal workflow definitions
│   └── call_workflow.py    # Main VoiceCallWorkflow
├── activities/             # Temporal activities (side effects)
│   ├── twilio_activities.py    # Twilio API calls
│   ├── database_activities.py  # Database operations
│   ├── session_activities.py   # Redis session management
│   └── metrics_activities.py   # Call metrics tracking
├── api/                    # FastAPI application
│   ├── routes/
│   │   ├── calls.py            # Call management endpoints
│   │   ├── twilio.py           # Twilio webhooks & WebSocket
│   │   └── health.py           # Health check endpoints
│   └── main.py                 # FastAPI app factory
├── services/               # Business logic
│   ├── audio_bridge.py         # Twilio-Gemini audio bridge
│   ├── database.py             # Database connection pool
│   └── temporal_client.py      # Temporal client wrapper
├── models/                 # Data models
│   ├── call.py                 # Pydantic models
│   └── database.py             # SQLAlchemy ORM models
├── utils/                  # Utilities
│   ├── audio.py                # Audio format conversion
│   ├── redis_client.py         # Redis helpers
│   └── logging.py              # Structured logging
├── config.py               # Configuration (pydantic-settings)
└── worker.py               # Temporal worker entry point
```

---

## Database Migrations

We use Alembic for database migrations.

### Create a New Migration

```bash
# Auto-generate from model changes
uv run alembic revision --autogenerate -m "Add new field to calls"

# Create empty migration for manual SQL
uv run alembic revision -m "Custom migration"
```

### Apply Migrations

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Apply to specific revision
uv run alembic upgrade abc123

# Rollback one migration
uv run alembic downgrade -1

# Rollback to specific revision
uv run alembic downgrade abc123
```

### View Migration Status

```bash
# Current version
uv run alembic current

# Migration history
uv run alembic history

# Show pending migrations
uv run alembic history --indicate-current
```

### Migration Best Practices

1. **Always review auto-generated migrations** before applying
2. **Test migrations** on a copy of production data
3. **Make migrations reversible** when possible
4. **Keep migrations small** and focused

---

## Testing

### Run Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov=src --cov-report=html

# Specific test file
uv run pytest tests/test_audio_conversion.py -v

# With output
uv run pytest -s

# Parallel execution
uv run pytest -n auto
```

### Test Structure

```
tests/
├── test_audio_conversion.py    # Audio format tests
├── test_audio_bridge_session.py # Audio bridge tests
├── test_api_calls.py           # API endpoint tests
└── test_imports.py             # Import validation
```

### Writing Tests

```python
import pytest
from src.voice_ai_system.utils.audio import twilio_to_gemini

def test_twilio_to_gemini_conversion():
    """Test u-law to PCM conversion."""
    # Arrange
    mulaw_base64 = "..." # base64 u-law audio

    # Act
    pcm_audio = twilio_to_gemini(mulaw_base64)

    # Assert
    assert len(pcm_audio) > 0
    assert isinstance(pcm_audio, bytes)
```

---

## Code Quality

### Formatting

```bash
# Format with Black
uv run black src tests

# Check only (CI mode)
uv run black --check src tests
```

### Linting

```bash
# Lint with Ruff
uv run ruff check src tests

# Auto-fix issues
uv run ruff check --fix src tests
```

### Type Checking

```bash
# Type check with mypy
uv run mypy src

# Strict mode
uv run mypy --strict src
```

### Run All Checks

```bash
uv run black src tests && uv run ruff check src tests && uv run mypy src
```

---

## Temporal Development

### Workflow Development

Workflows are deterministic and must follow Temporal rules:

```python
from temporalio import workflow

@workflow.defn(name="VoiceCallWorkflow")
class VoiceCallWorkflow:
    @workflow.run
    async def run(self, input_data: CallWorkflowInput) -> CallWorkflowResult:
        # Use workflow.now() instead of datetime.now()
        started_at = workflow.now()

        # Execute activities for side effects
        result = await workflow.execute_activity(
            "some_activity",
            args=[params],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Use workflow.wait_condition for waiting
        await workflow.wait_condition(
            lambda: self.some_condition,
            timeout=timedelta(seconds=60),
        )
```

### Activity Development

Activities handle all side effects (I/O, external APIs):

```python
from temporalio import activity

@activity.defn(name="some_activity")
async def some_activity(params: dict) -> dict:
    # Safe to do I/O, API calls, etc.
    activity.logger.info("Doing something", extra={"params": params})

    # Heartbeat for long-running activities
    activity.heartbeat()

    return {"result": "success"}
```

### Testing Workflows

```python
import pytest
from temporalio.testing import WorkflowEnvironment

@pytest.mark.asyncio
async def test_voice_call_workflow():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[VoiceCallWorkflow],
            activities=[mock_activity],
        ):
            result = await env.client.execute_workflow(
                VoiceCallWorkflow.run,
                input_data,
                id="test-workflow",
                task_queue="test-queue",
            )
            assert result.status == CallStatus.COMPLETED
```

---

## Audio Development

### Understanding Audio Formats

| Format | Sample Rate | Bits | Use Case |
|--------|-------------|------|----------|
| u-law | 8kHz | 8-bit | Twilio Media Streams |
| PCM16 | 16kHz | 16-bit | Gemini Live API input |
| PCM16 | 24kHz | 16-bit | Gemini Live API output |

### Audio Conversion Flow

```
Twilio (u-law 8kHz)
    → twilio_to_gemini()
    → PCM 16kHz
    → Gemini Live API
    → PCM 24kHz
    → gemini_to_twilio()
    → u-law 8kHz
    → Twilio
```

### Testing Audio Conversion

```bash
# Run audio tests
uv run pytest tests/test_audio_conversion.py -v

# Check audio levels
uv run python -c "
from src.voice_ai_system.utils.audio import twilio_to_gemini
import base64
# Your test code here
"
```

---

## Debugging

### Logging

The system uses structlog for structured logging:

```python
import structlog
logger = structlog.get_logger(__name__)

logger.info("Processing call", call_id=call_id, phone=phone_number)
logger.error("Call failed", error=str(e), exc_info=True)
```

### View Logs

```bash
# All logs
docker compose logs -f

# Specific service
docker compose logs -f api
docker compose logs -f worker

# Filter by pattern
docker compose logs api | grep -i "websocket"
docker compose logs api | grep -i "gemini"
```

### Temporal UI Debugging

1. Open http://localhost:8080
2. Navigate to workflow
3. View event history
4. Check activity inputs/outputs
5. Review errors and stack traces

### Database Debugging

```bash
# Connect to PostgreSQL
docker compose exec postgresql psql -U temporal -d voice_ai

# Common queries
SELECT * FROM calls ORDER BY started_at DESC LIMIT 10;
SELECT * FROM transcripts WHERE call_id = 'uuid';
SELECT * FROM call_metrics WHERE workflow_id = 'call-xxx';
```

### Redis Debugging

```bash
# Connect to Redis
docker compose exec redis redis-cli

# View sessions
KEYS session:*
GET session:call-xxx
TTL session:call-xxx
```

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Your Twilio phone number |
| `GEMINI_API_KEY` | Google Gemini API key |
| `BASE_URL` | Public URL for webhooks |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | development | development/staging/production |
| `LOG_LEVEL` | INFO | Logging level |
| `TEMPORAL_HOST` | localhost | Temporal server host |
| `TEMPORAL_PORT` | 7233 | Temporal server port |
| `REDIS_HOST` | localhost | Redis host |
| `DB_POOL_SIZE` | 20 | Database connection pool size |

See `.env.example` for all options.

---

## IDE Setup

### VS Code

Recommended extensions:
- Python
- Pylance
- Black Formatter
- Ruff
- Docker

Settings (`.vscode/settings.json`):

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "python.formatting.provider": "black",
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter"
  }
}
```

### PyCharm

1. Set Python interpreter to `.venv/bin/python`
2. Enable Black as formatter
3. Configure Ruff as external tool

---

## Common Development Tasks

### Add a New Activity

1. Create function in `activities/`:
```python
@activity.defn(name="new_activity")
async def new_activity(params: dict) -> dict:
    ...
```

2. Register in `worker.py`:
```python
activities = [
    ...
    new_activities.new_activity,
]
```

3. Call from workflow:
```python
result = await workflow.execute_activity(
    "new_activity",
    args=[params],
    start_to_close_timeout=timedelta(seconds=30),
)
```

### Add a New API Endpoint

1. Create route in `api/routes/`:
```python
@router.post("/new-endpoint")
async def new_endpoint(request: Request):
    ...
```

2. Register router in `api/main.py`:
```python
app.include_router(new_router.router, prefix="/new", tags=["new"])
```

### Add a Database Model

1. Create model in `models/database.py`
2. Create migration: `uv run alembic revision --autogenerate -m "Add new table"`
3. Apply: `uv run alembic upgrade head`
