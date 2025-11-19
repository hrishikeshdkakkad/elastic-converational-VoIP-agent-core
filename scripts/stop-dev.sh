#!/bin/bash
# Stop development services

echo "🛑 Stopping Voice AI System services..."

docker compose down

echo "✅ All services stopped"
echo ""
echo "💡 To remove volumes and data, run: docker compose down -v"
