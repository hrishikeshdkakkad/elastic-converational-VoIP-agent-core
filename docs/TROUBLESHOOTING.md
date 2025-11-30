# Troubleshooting Guide

Common issues and their solutions.

## Quick Diagnostics

```bash
# Check all services
docker compose ps

# Health check
curl http://localhost:8000/health

# View all logs
docker compose logs -f

# Check specific service
docker compose logs -f api worker temporal
```

---

## Service Issues

### Services Not Starting

**Symptoms:** Containers exit immediately or stay in "starting" state.

**Solutions:**

1. Check logs for errors:
```bash
docker compose logs temporal
docker compose logs postgresql
docker compose logs api
```

2. Verify dependencies are ready:
```bash
# PostgreSQL
docker compose exec postgresql pg_isready -U temporal

# Temporal
docker compose exec temporal tctl cluster health

# Redis
docker compose exec redis redis-cli ping
```

3. Clean restart:
```bash
docker compose down
docker compose up -d
```

4. Full reset (WARNING: data loss):
```bash
docker compose down -v
docker compose up -d
```

### API Not Responding

**Symptoms:** `curl http://localhost:8000/health` fails.

**Solutions:**

1. Check if container is running:
```bash
docker compose ps api
```

2. Check logs:
```bash
docker compose logs api --tail=50
```

3. Verify port binding:
```bash
docker compose port api 8000
```

4. Check Temporal connection:
```bash
docker compose logs api | grep -i temporal
```

5. Restart API:
```bash
docker compose restart api
```

---

## Temporal Issues

### Workflows Not Starting

**Symptoms:** POST /calls returns 500 or workflow doesn't appear in Temporal UI.

**Solutions:**

1. Check Temporal server health:
```bash
docker compose exec temporal tctl cluster health
```

2. Verify worker is connected:
```bash
docker compose logs worker | grep -i "worker started"
docker compose logs worker | grep -i "connected"
```

3. Check task queue:
```bash
docker compose exec temporal tctl task-queue describe \
  --task-queue voice-ai-task-queue
```

4. Verify namespace exists:
```bash
docker compose exec temporal tctl namespace list
```

5. View Temporal UI: http://localhost:8080

### Workflows Stuck

**Symptoms:** Workflow started but not progressing.

**Solutions:**

1. Check workflow in Temporal UI for pending activities
2. Verify activities are registered:
```bash
docker compose logs worker | grep -i "activities"
```

3. Check for activity errors:
```bash
docker compose logs worker | grep -i "error"
```

4. Signal workflow to end:
```bash
curl -X POST http://localhost:8000/calls/{workflow_id}/terminate
```

---

## Database Issues

### Connection Refused

**Symptoms:** `connection refused` errors to PostgreSQL.

**IMPORTANT:** PostgreSQL uses port **5433** (not 5432) to avoid conflicts.

**Solutions:**

1. Use correct port:
```bash
psql -h localhost -p 5433 -U temporal -d voice_ai
```

2. Check PostgreSQL is running:
```bash
docker compose ps postgresql
docker compose exec postgresql pg_isready -U temporal
```

3. Verify database exists:
```bash
docker compose exec postgresql psql -U temporal -c "\l"
```

### Migration Errors

**Symptoms:** Alembic migration fails.

**Solutions:**

1. Check current state:
```bash
docker compose exec api alembic current
docker compose exec api alembic history --indicate-current
```

2. View migration logs:
```bash
docker compose logs api | grep -i alembic
```

3. Rollback and retry:
```bash
docker compose exec api alembic downgrade -1
docker compose exec api alembic upgrade head
```

4. Manual database fix:
```bash
docker compose exec postgresql psql -U temporal -d voice_ai
# Fix schema issues manually
```

### Connection Pool Exhausted

**Symptoms:** `TimeoutError: QueuePool limit reached`

**Solutions:**

1. Increase pool size in `.env`:
```bash
DB_POOL_SIZE=50
DB_MAX_OVERFLOW=20
```

2. Check for connection leaks:
```bash
docker compose exec postgresql psql -U temporal -d voice_ai -c "
SELECT count(*) FROM pg_stat_activity WHERE datname = 'voice_ai';
"
```

3. Restart API:
```bash
docker compose restart api
```

---

## Redis Issues

### Connection Refused

**Symptoms:** Redis connection errors.

**Solutions:**

1. Check Redis is running:
```bash
docker compose exec redis redis-cli ping
```

2. Verify connection settings:
```bash
docker compose exec api env | grep REDIS
```

3. Test connection:
```bash
docker compose exec redis redis-cli
> INFO
```

### Session Not Found

**Symptoms:** `Session not found in Redis` errors.

**Solutions:**

1. Check session TTL:
```bash
docker compose exec redis redis-cli
> KEYS session:*
> TTL session:call-xxx
```

2. Increase TTL in `.env`:
```bash
REDIS_SESSION_TTL=14400  # 4 hours
```

3. Verify session was created:
```bash
docker compose logs api | grep -i "session.*created"
```

---

## Twilio Issues

### Webhooks Not Reaching Server

**Symptoms:** Twilio shows webhook failures, calls don't connect.

**Solutions:**

1. Ensure ngrok is running:
```bash
ngrok http 8000
```

2. Update `BASE_URL` in `.env` with ngrok URL

3. Restart API to pick up new URL:
```bash
docker compose restart api
```

4. Test webhook:
```bash
curl https://YOUR-NGROK-URL.ngrok-free.app/health
```

5. Check Twilio console for webhook logs

### Call Fails Immediately

**Symptoms:** Call starts but fails within seconds.

**Solutions:**

1. Verify Twilio credentials:
```bash
docker compose exec api env | grep TWILIO
```

2. Check Twilio number is valid and active

3. View Twilio call logs in console

4. Check API logs for Twilio errors:
```bash
docker compose logs api | grep -i twilio
```

### WebSocket Connection Fails

**Symptoms:** Call connects but no audio.

**Solutions:**

1. Check WebSocket logs:
```bash
docker compose logs api | grep -i websocket
```

2. Verify TwiML generation:
```bash
docker compose logs api | grep -i twiml
```

3. Check stream status:
```bash
docker compose logs api | grep -i stream
```

---

## Audio Issues

### No Audio / Silence

**Symptoms:** Call connects but user hears nothing.

**Solutions:**

1. Check Gemini session:
```bash
docker compose logs api | grep -i "gemini.*connected"
docker compose logs api | grep -i "pre-warming"
```

2. Verify audio bridge:
```bash
docker compose logs api | grep -i "audio bridge"
docker compose logs api | grep -i "audio chunk"
```

3. Check first audio frame:
```bash
docker compose logs api | grep -i "first audio frame"
```

### Choppy Audio

**Symptoms:** Audio cuts in and out, garbled speech.

**Solutions:**

1. Check for dropped frames:
```bash
docker compose logs api | grep -i "dropped"
```

2. Monitor queue depth:
```bash
docker compose logs api | grep -i "queue"
```

3. Check network latency to Gemini

4. Reduce concurrent calls if overloaded

### Audio Conversion Errors

**Symptoms:** `Failed to convert audio` errors.

**Solutions:**

1. Verify audio format:
```bash
docker compose logs api | grep -i "audio diagnostics"
```

2. Check audio utility:
```bash
uv run pytest tests/test_audio_conversion.py -v
```

---

## Gemini Issues

### Pre-warming Not Working

**Symptoms:** 3+ second delay before AI responds.

**Solutions:**

1. Check pre-warming triggered:
```bash
docker compose logs api | grep -i "pre-warming initiated"
```

2. Verify pre-warmed session used:
```bash
docker compose logs api | grep -i "using pre-warmed"
```

3. Check for errors:
```bash
docker compose logs api | grep -i "failed to pre-warm"
```

4. Verify Gemini API key:
```bash
docker compose exec api env | grep GEMINI
```

### Rate Limit Exceeded

**Symptoms:** `429 Too Many Requests` from Gemini.

**Solutions:**

1. Reduce concurrent calls temporarily
2. Check Gemini API quota in Google Cloud Console
3. Implement exponential backoff (already configured)
4. Consider upgrading API quota

### Session Timeout

**Symptoms:** Gemini session closes unexpectedly.

**Solutions:**

1. Check for `go_away` signals:
```bash
docker compose logs api | grep -i "go_away"
```

2. Verify session duration (max ~15 minutes)
3. Implement session reconnection if needed

---

## VAD Issues

### AI Responds to Background Noise

**Symptoms:** AI speaks when nobody is talking.

**Solutions:**

Adjust VAD configuration:
```json
{
  "vad_config": {
    "start_sensitivity": "LOW",
    "prefix_padding_ms": 300
  }
}
```

### User Gets Cut Off

**Symptoms:** AI interrupts before user finishes.

**Solutions:**

```json
{
  "vad_config": {
    "end_sensitivity": "LOW",
    "silence_duration_ms": 800
  }
}
```

### Can't Interrupt AI

**Symptoms:** User can't barge in.

**Solutions:**

1. Ensure VAD is enabled:
```json
{
  "vad_config": {
    "disabled": false
  }
}
```

2. Check activity handling is set correctly

See [VAD_CONFIGURATION.md](./VAD_CONFIGURATION.md) for detailed tuning.

---

## Performance Issues

### High Latency

**Symptoms:** Slow response times, delayed audio.

**Diagnostics:**
```bash
# Check API latency
curl -w "@curl-format.txt" http://localhost:8000/health

# Check database
docker compose exec postgresql psql -U temporal -d voice_ai -c "
SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;
"
```

**Solutions:**

1. Scale workers:
```yaml
worker:
  deploy:
    replicas: 5
```

2. Increase database connections:
```bash
DB_POOL_SIZE=50
```

3. Check network latency to external APIs

### Memory Issues

**Symptoms:** OOM kills, increasing memory usage.

**Solutions:**

1. Check container memory:
```bash
docker stats
```

2. Increase limits:
```yaml
services:
  api:
    deploy:
      resources:
        limits:
          memory: 4G
```

3. Check for memory leaks in logs

---

## Common Error Messages

### "Temporal client not initialized"

API started before Temporal was ready.

```bash
docker compose restart api
```

### "Session expired"

Call took too long, Redis TTL exceeded.

```bash
# Increase TTL
REDIS_SESSION_TTL=14400
```

### "Workflow not found"

Workflow completed or never started.

```bash
# Check Temporal UI
open http://localhost:8080
```

### "Call failed to connect"

Twilio couldn't reach phone or webhooks failed.

1. Verify phone number is valid
2. Check ngrok is running
3. Verify BASE_URL is correct

---

## Debug Logging

Enable verbose logging:

```bash
# .env
LOG_LEVEL=DEBUG
```

Restart services:
```bash
docker compose restart api worker
```

View debug logs:
```bash
docker compose logs -f api | grep -i debug
```

---

## Getting Help

1. **Search logs thoroughly** before escalating
2. **Check Temporal UI** for workflow/activity details
3. **Review this guide** for common solutions
4. **GitHub Issues**: Report bugs with logs and reproduction steps
5. **Temporal Community**: https://community.temporal.io/
