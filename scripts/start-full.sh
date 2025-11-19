#!/bin/bash
# Full system startup script with browser automation and log display

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    color=$1
    message=$2
    echo -e "${color}${message}${NC}"
}

# Function to check if a port is open
wait_for_port() {
    local host=$1
    local port=$2
    local service=$3
    local max_attempts=60
    local attempt=0

    print_color "$YELLOW" "⏳ Waiting for $service on $host:$port..."

    while ! nc -z $host $port 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            print_color "$RED" "❌ Timeout waiting for $service"
            return 1
        fi
        sleep 2
        echo -n "."
    done
    echo ""
    print_color "$GREEN" "✅ $service is ready!"
    return 0
}

# Function to check service health
check_service_health() {
    local service=$1
    local url=$2

    if curl -f -s "$url" > /dev/null; then
        print_color "$GREEN" "✅ $service is healthy"
        return 0
    else
        print_color "$YELLOW" "⚠️  $service is not responding yet"
        return 1
    fi
}

# Function to open URLs in browser
open_urls_in_browser() {
    local urls=("$@")

    # Detect OS and browser command
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        browser_cmd="open"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        if command -v xdg-open &> /dev/null; then
            browser_cmd="xdg-open"
        elif command -v gnome-open &> /dev/null; then
            browser_cmd="gnome-open"
        else
            print_color "$YELLOW" "⚠️  Could not find browser command"
            return 1
        fi
    else
        print_color "$YELLOW" "⚠️  Unsupported OS for browser automation"
        return 1
    fi

    # Open each URL
    for url in "${urls[@]}"; do
        print_color "$CYAN" "🌐 Opening $url in browser..."
        $browser_cmd "$url" 2>/dev/null || true
        sleep 0.5  # Small delay between opening tabs
    done
}

# Main script starts here
clear
print_color "$PURPLE" "╔════════════════════════════════════════════╗"
print_color "$PURPLE" "║     🚀 Voice AI System Full Startup 🚀      ║"
print_color "$PURPLE" "╚════════════════════════════════════════════╝"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    print_color "$RED" "❌ No .env file found!"
    print_color "$YELLOW" "Creating .env from .env.example..."
    cp .env.example .env
    print_color "$YELLOW" "⚠️  Please edit .env with your API credentials before continuing!"
    print_color "$YELLOW" "Required: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, GEMINI_API_KEY"
    exit 1
fi

# Stop any existing containers
print_color "$BLUE" "🧹 Cleaning up existing containers..."
docker compose down 2>/dev/null || true

# Start services
print_color "$BLUE" "🏗️  Building and starting all services..."
docker compose up -d --build

# Wait for services to be ready
print_color "$CYAN" "⏳ Waiting for services to initialize..."
sleep 5

# Check PostgreSQL
wait_for_port localhost 5432 "PostgreSQL"

# Check Elasticsearch
wait_for_port localhost 9200 "Elasticsearch"

# Check Temporal
wait_for_port localhost 7233 "Temporal gRPC"
wait_for_port localhost 8080 "Temporal UI"

# Wait a bit more for Temporal to fully initialize
print_color "$YELLOW" "⏳ Waiting for Temporal to fully initialize..."
sleep 10

# Check FastAPI
wait_for_port localhost 8000 "FastAPI"

# Check Prometheus and Grafana
wait_for_port localhost 9090 "Prometheus"
wait_for_port localhost 3000 "Grafana"

# Health checks
print_color "$CYAN" "🔍 Running health checks..."
echo ""

check_service_health "FastAPI" "http://localhost:8000/health"
check_service_health "Temporal UI" "http://localhost:8080"
check_service_health "Prometheus" "http://localhost:9090/-/healthy"
check_service_health "Grafana" "http://localhost:3000/api/health"

echo ""
print_color "$GREEN" "════════════════════════════════════════════"
print_color "$GREEN" "✅ All services are up and running!"
print_color "$GREEN" "════════════════════════════════════════════"
echo ""

# Display service URLs
print_color "$CYAN" "📊 Service URLs:"
echo ""
print_color "$WHITE" "  • FastAPI:      http://localhost:8000"
print_color "$WHITE" "  • API Docs:     http://localhost:8000/docs"
print_color "$WHITE" "  • Temporal UI:  http://localhost:8080"
print_color "$WHITE" "  • Prometheus:   http://localhost:9090"
print_color "$WHITE" "  • Grafana:      http://localhost:3000 (admin/admin)"
echo ""

# Ask if user wants to open URLs in browser
read -p "$(echo -e ${CYAN}Would you like to open all UIs in your browser? [Y/n]: ${NC})" -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    urls=(
        "http://localhost:8000/docs"
        "http://localhost:8080"
        "http://localhost:9090"
        "http://localhost:3000"
    )
    open_urls_in_browser "${urls[@]}"
fi

# Show logs option
echo ""
print_color "$CYAN" "📋 Log Management:"
echo ""
print_color "$WHITE" "  • View all logs:        docker compose logs -f"
print_color "$WHITE" "  • View API logs:        docker compose logs -f api"
print_color "$WHITE" "  • View Worker logs:     docker compose logs -f worker"
print_color "$WHITE" "  • View Temporal logs:   docker compose logs -f temporal"
echo ""

# Ask if user wants to see logs
read -p "$(echo -e ${CYAN}Would you like to see live logs? [Y/n]: ${NC})" -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    print_color "$GREEN" "📺 Showing live logs (Press Ctrl+C to exit)..."
    echo ""
    docker compose logs -f --tail=100
else
    print_color "$GREEN" "✨ System is ready! Use 'docker compose logs -f' to view logs anytime."
fi