# API Reference

Complete API documentation for the Voice AI System.

## Base URL

- **Local**: `http://localhost:8000`
- **Production**: Your configured `BASE_URL`

## Authentication

Currently, the API does not require authentication. For production, implement JWT or API key authentication.

---

## Call Management

### Initiate Call

Start a new outbound voice call.

```
POST /calls
```

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `phone_number` | string | Yes | - | Phone number in E.164 format (+15551234567) |
| `greeting` | string | No | "Hello! How can I help you today?" | Initial greeting message |
| `system_prompt` | string | No | null | Custom AI behavior instructions |
| `max_duration_seconds` | integer | No | 1800 | Maximum call duration (30 min default) |
| `vad_config` | object | No | null | Voice Activity Detection settings |

**VAD Config Options:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `disabled` | boolean | false | Disable automatic VAD |
| `start_sensitivity` | string | "HIGH" | "HIGH" or "LOW" - speech start detection |
| `end_sensitivity` | string | "LOW" | "HIGH" or "LOW" - speech end detection |
| `prefix_padding_ms` | integer | 200 | Buffer before speech detection (ms) |
| `silence_duration_ms` | integer | 500 | Silence to trigger end of speech (ms) |

**Example Request:**

```bash
curl -X POST http://localhost:8000/calls \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+15551234567",
    "greeting": "Hello! How can I help you today?",
    "system_prompt": "You are a helpful customer service agent.",
    "max_duration_seconds": 1800,
    "vad_config": {
      "start_sensitivity": "HIGH",
      "end_sensitivity": "LOW",
      "silence_duration_ms": 500
    }
  }'
```

**Response (201 Created):**

```json
{
  "workflow_id": "call-abc123-def456-ghi789",
  "run_id": "abc123def456...",
  "phone_number": "+15551234567",
  "status": "initiated"
}
```

---

### Get Call Status

Get the current status of an active or completed call.

```
GET /calls/{workflow_id}
```

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `workflow_id` | string | The workflow ID returned from initiate call |

**Example Request:**

```bash
curl http://localhost:8000/calls/call-abc123-def456-ghi789
```

**Response (200 OK):**

```json
{
  "workflow_id": "call-abc123-def456-ghi789",
  "status": "in_progress",
  "transcript_count": 15,
  "call_config": {
    "call_id": "uuid-here",
    "greeting": "Hello! How can I help you today?",
    "system_prompt": "You are a helpful customer service agent.",
    "vad_config": {
      "start_sensitivity": "HIGH",
      "end_sensitivity": "LOW"
    }
  }
}
```

**Call Status Values:**

| Status | Description |
|--------|-------------|
| `initiated` | Call workflow started, Twilio call being placed |
| `ringing` | Phone is ringing |
| `in_progress` | Call connected and active |
| `completed` | Call ended normally |
| `failed` | Call failed (error occurred) |
| `no_answer` | No answer after timeout |
| `busy` | Line was busy |
| `canceled` | Call was canceled |

---

### Terminate Call

End an active call gracefully.

```
POST /calls/{workflow_id}/terminate
```

**Example Request:**

```bash
curl -X POST http://localhost:8000/calls/call-abc123-def456-ghi789/terminate
```

**Response (204 No Content):**

No body returned.

---

### Get Call Result

Get the final result of a completed call (waits for completion).

```
GET /calls/{workflow_id}/result
```

**Example Request:**

```bash
curl http://localhost:8000/calls/call-abc123-def456-ghi789/result
```

**Response (200 OK):**

```json
{
  "call_id": "uuid-here",
  "workflow_id": "call-abc123-def456-ghi789",
  "run_id": "abc123...",
  "status": "completed",
  "phone_number": "+15551234567",
  "started_at": "2024-01-15T10:30:00Z",
  "ended_at": "2024-01-15T10:35:30Z",
  "duration_seconds": 330,
  "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "total_transcript_segments": 24,
  "metadata": {}
}
```

---

## Health Checks

### Basic Health Check

```
GET /health
```

**Response:**

```json
{
  "status": "healthy",
  "service": "voice-ai-system",
  "version": "0.1.0"
}
```

### Readiness Check

Check if the service is ready to accept requests (Temporal connected).

```
GET /health/ready
```

**Response (200 OK):**

```json
{
  "status": "ready"
}
```

**Response (503 Service Unavailable):**

```json
{
  "status": "not_ready",
  "reason": "Temporal client not initialized"
}
```

### Liveness Check

Check if the service is alive.

```
GET /health/live
```

**Response:**

```json
{
  "status": "alive"
}
```

---

## Twilio Webhooks

These endpoints are called by Twilio during call lifecycle.

### TwiML Generation

Generate TwiML for WebSocket streaming (called by Twilio).

```
POST /twilio/twiml/{workflow_id}
```

### Media Stream WebSocket

WebSocket endpoint for Twilio Media Streams.

```
WebSocket /twilio/ws/media/{workflow_id}
```

### Status Callback

Receive call status updates from Twilio.

```
POST /twilio/status/{workflow_id}
```

### Stream Status Callback

Receive stream status updates from Twilio.

```
POST /twilio/stream-status/{workflow_id}
```

---

## Metrics

### Prometheus Metrics

Prometheus-compatible metrics endpoint.

```
GET /metrics
```

**Available Metrics:**

- Request counts and latency
- Call volumes and durations
- Workflow success/failure rates
- Audio processing metrics

---

## Root Endpoint

Get service information.

```
GET /
```

**Response:**

```json
{
  "service": "voice-ai-system",
  "version": "0.1.0",
  "environment": "development",
  "temporal": {
    "address": "temporal:7233",
    "namespace": "default"
  }
}
```

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message description"
}
```

**Common HTTP Status Codes:**

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created (new call initiated) |
| 204 | No Content (successful termination) |
| 400 | Bad Request (invalid input) |
| 404 | Not Found (workflow not found) |
| 500 | Internal Server Error |
| 503 | Service Unavailable (Temporal not connected) |

---

## Rate Limits

No rate limits are enforced by default. For production:

- Implement rate limiting at the API gateway level
- Consider Gemini API quotas (~60 requests/minute by default)
- Monitor Twilio concurrent call limits

---

## SDKs and Examples

### Python

```python
import httpx

async def make_call(phone_number: str, greeting: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/calls",
            json={
                "phone_number": phone_number,
                "greeting": greeting,
                "system_prompt": "You are a helpful assistant."
            }
        )
        return response.json()
```

### JavaScript/TypeScript

```typescript
async function makeCall(phoneNumber: string, greeting: string) {
  const response = await fetch("http://localhost:8000/calls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      phone_number: phoneNumber,
      greeting: greeting,
      system_prompt: "You are a helpful assistant."
    })
  });
  return response.json();
}
```

### cURL

```bash
# Initiate call
curl -X POST http://localhost:8000/calls \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+15551234567", "greeting": "Hello!"}'

# Check status
curl http://localhost:8000/calls/{workflow_id}

# Terminate call
curl -X POST http://localhost:8000/calls/{workflow_id}/terminate
```

---

## Interactive Documentation

Access the interactive Swagger UI at:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json
