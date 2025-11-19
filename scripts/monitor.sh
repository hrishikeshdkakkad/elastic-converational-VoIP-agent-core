#!/bin/bash
# Real-time monitoring dashboard for Voice AI System

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

# Function to check if service is running
check_service() {
    local container_name=$1
    if docker ps | grep -q $container_name; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${RED}✗ Stopped${NC}"
    fi
}

# Function to check port
check_port() {
    local port=$1
    if nc -z localhost $port 2>/dev/null; then
        echo -e "${GREEN}✓ Open${NC}"
    else
        echo -e "${RED}✗ Closed${NC}"
    fi
}

# Function to get container stats
get_container_stats() {
    local container=$1
    if docker ps | grep -q $container; then
        docker stats --no-stream --format "table {{.CPUPerc}}\t{{.MemUsage}}" $container | tail -1
    else
        echo "N/A"
    fi
}

# Main monitoring loop
while true; do
    clear
    echo -e "${PURPLE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${PURPLE}║         🔍 Voice AI System Monitor Dashboard 🔍            ║${NC}"
    echo -e "${PURPLE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}Timestamp: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
    echo ""

    # Services Status
    echo -e "${YELLOW}═══ Service Status ═══${NC}"
    echo ""
    printf "%-20s %-15s %-15s\n" "Service" "Container" "Port"
    printf "%-20s %-15s %-15s\n" "-------" "---------" "----"

    printf "%-20s %-15s %-15s\n" "PostgreSQL" "$(check_service voice-ai-postgresql)" "$(check_port 5432)"
    printf "%-20s %-15s %-15s\n" "Elasticsearch" "$(check_service voice-ai-elasticsearch)" "$(check_port 9200)"
    printf "%-20s %-15s %-15s\n" "Temporal" "$(check_service voice-ai-temporal)" "$(check_port 7233)"
    printf "%-20s %-15s %-15s\n" "Temporal UI" "$(check_service voice-ai-temporal-ui)" "$(check_port 8080)"
    printf "%-20s %-15s %-15s\n" "FastAPI" "$(check_service voice-ai-api)" "$(check_port 8000)"
    printf "%-20s %-15s %-15s\n" "Worker" "$(check_service voice-ai-worker)" "N/A"
    printf "%-20s %-15s %-15s\n" "Prometheus" "$(check_service voice-ai-prometheus)" "$(check_port 9090)"
    printf "%-20s %-15s %-15s\n" "Grafana" "$(check_service voice-ai-grafana)" "$(check_port 3000)"

    echo ""

    # Resource Usage
    echo -e "${YELLOW}═══ Resource Usage ═══${NC}"
    echo ""
    printf "%-20s %-15s %-20s\n" "Container" "CPU Usage" "Memory Usage"
    printf "%-20s %-15s %-20s\n" "---------" "---------" "------------"

    for container in voice-ai-postgresql voice-ai-temporal voice-ai-api voice-ai-worker; do
        if docker ps | grep -q $container; then
            stats=$(docker stats --no-stream --format "{{.CPUPerc}}\t{{.MemUsage}}" $container 2>/dev/null | tail -1)
            if [ ! -z "$stats" ]; then
                printf "%-20s %s\n" "$container" "$stats"
            fi
        fi
    done

    echo ""

    # Quick Actions
    echo -e "${YELLOW}═══ Quick Actions ═══${NC}"
    echo ""
    echo -e "${CYAN}[L]${NC} View Logs  ${CYAN}[R]${NC} Restart All  ${CYAN}[S]${NC} Stop All  ${CYAN}[Q]${NC} Quit"
    echo ""

    # Read user input with timeout
    read -t 5 -n 1 -r key

    case $key in
        l|L)
            docker compose logs -f --tail=50
            ;;
        r|R)
            echo -e "${YELLOW}Restarting all services...${NC}"
            docker compose restart
            sleep 5
            ;;
        s|S)
            echo -e "${RED}Stopping all services...${NC}"
            docker compose down
            exit 0
            ;;
        q|Q)
            echo -e "${GREEN}Exiting monitor...${NC}"
            exit 0
            ;;
    esac
done