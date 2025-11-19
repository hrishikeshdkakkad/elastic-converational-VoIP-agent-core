#!/bin/bash
# Comprehensive health check script for Voice AI System

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═════════════════════════════════════════════${NC}"
echo -e "${BLUE}     Voice AI System Health Check            ${NC}"
echo -e "${BLUE}═════════════════════════════════════════════${NC}"
echo ""

# Function to check service health
check_health() {
    local name=$1
    local url=$2
    local expected=$3

    printf "%-30s" "$name:"

    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)

    if [ "$response" == "$expected" ]; then
        echo -e "${GREEN}✅ Healthy (HTTP $response)${NC}"
        return 0
    elif [ "$response" == "000" ]; then
        echo -e "${RED}❌ Not Reachable${NC}"
        return 1
    else
        echo -e "${YELLOW}⚠️  Unhealthy (HTTP $response)${NC}"
        return 1
    fi
}

# Check container status
echo -e "${YELLOW}Container Status:${NC}"
echo "─────────────────────────────────────────────"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "voice-ai|NAMES"
echo ""

# Check service health endpoints
echo -e "${YELLOW}Service Health Checks:${NC}"
echo "─────────────────────────────────────────────"

# PostgreSQL
printf "%-30s" "PostgreSQL:"
if docker exec voice-ai-postgresql pg_isready -U temporal >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Ready${NC}"
else
    echo -e "${RED}❌ Not Ready${NC}"
fi

# Elasticsearch
check_health "Elasticsearch" "http://localhost:9200/_cluster/health" "200"

# Prometheus
check_health "Prometheus" "http://localhost:9090/-/healthy" "200"

# Grafana
check_health "Grafana" "http://localhost:3000/api/health" "200"

# Temporal (when running)
check_health "Temporal UI" "http://localhost:8080" "200"

# FastAPI (when running)
check_health "FastAPI Health" "http://localhost:8000/health" "200"
check_health "FastAPI Docs" "http://localhost:8000/docs" "200"

echo ""
echo -e "${YELLOW}Port Availability:${NC}"
echo "─────────────────────────────────────────────"

# Function to check port
check_port() {
    local name=$1
    local port=$2

    printf "%-30s" "$name (port $port):"

    if nc -z localhost $port 2>/dev/null; then
        echo -e "${GREEN}✅ Open${NC}"
    else
        echo -e "${RED}❌ Closed${NC}"
    fi
}

check_port "PostgreSQL" "5432"
check_port "Temporal gRPC" "7233"
check_port "Temporal UI" "8080"
check_port "FastAPI" "8000"
check_port "Elasticsearch" "9200"
check_port "Prometheus" "9090"
check_port "Grafana" "3000"

echo ""
echo -e "${YELLOW}Database Check:${NC}"
echo "─────────────────────────────────────────────"

# Check if voice_ai database exists
if docker exec voice-ai-postgresql psql -U temporal -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw voice_ai; then
    echo -e "voice_ai database:            ${GREEN}✅ Exists${NC}"

    # Check tables
    tables=$(docker exec voice-ai-postgresql psql -U temporal -d voice_ai -c "\dt" 2>/dev/null | grep -E "calls|transcripts|call_events" | wc -l)
    if [ "$tables" -eq "3" ]; then
        echo -e "Database tables:              ${GREEN}✅ Created (3/3)${NC}"
    else
        echo -e "Database tables:              ${YELLOW}⚠️  Incomplete ($tables/3)${NC}"
    fi
else
    echo -e "voice_ai database:            ${RED}❌ Not Found${NC}"
fi

echo ""
echo -e "${BLUE}═════════════════════════════════════════════${NC}"
echo -e "${YELLOW}Summary:${NC}"

# Count running containers
running=$(docker ps --format "{{.Names}}" | grep -c "voice-ai" || echo "0")
total=9  # Expected: postgresql, elasticsearch, temporal, temporal-ui, api, worker-1, worker-2, prometheus, grafana

echo -e "Containers Running:           $running/$total"

if [ "$running" -eq "$total" ]; then
    echo -e "System Status:                ${GREEN}✅ Fully Operational${NC}"
elif [ "$running" -ge 4 ]; then
    echo -e "System Status:                ${YELLOW}⚠️  Partially Operational${NC}"
else
    echo -e "System Status:                ${RED}❌ Critical Services Missing${NC}"
fi

echo -e "${BLUE}═════════════════════════════════════════════${NC}"