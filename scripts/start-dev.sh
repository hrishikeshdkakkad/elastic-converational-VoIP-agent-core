#!/bin/bash
# Development startup script

set -e

echo "🚀 Starting Voice AI System (Development Mode)"
echo "=============================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Copying .env.example to .env"
    cp .env.example .env
    echo "⚠️  Please edit .env with your API credentials before continuing!"
    exit 1
fi

# Start infrastructure services
echo ""
echo "📦 Starting infrastructure services (PostgreSQL, Temporal, Elasticsearch)..."
docker compose up -d postgresql temporal temporal-ui elasticsearch prometheus grafana

# Wait for services to be healthy
echo ""
echo "⏳ Waiting for services to be ready..."
sleep 15

# Check service health
echo ""
echo "🔍 Checking service health..."
docker compose ps

echo ""
echo "✅ Infrastructure services are running!"
echo ""
echo "📊 Service URLs:"
echo "   - Temporal UI:  http://localhost:8080"
echo "   - Prometheus:   http://localhost:9090"
echo "   - Grafana:      http://localhost:3000 (admin/admin)"
echo ""
echo "🎯 Next steps:"
echo "   1. Run API:    make dev-api    (or: uv run uvicorn src.voice_ai_system.api.main:app --reload)"
echo "   2. Run Worker: make dev-worker (or: uv run python -m src.voice_ai_system.worker)"
echo ""
echo "💡 Or start all services with docker compose:"
echo "   docker compose up -d"
echo ""
