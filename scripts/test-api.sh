#!/bin/bash
# test-api.sh
# Tests the Control API endpoints
#
# Usage: ./test-api.sh [api_url]

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

API_URL="${1:-http://127.0.0.1:8080}"
PASSED=0
FAILED=0

test_endpoint() {
    local method=$1
    local endpoint=$2
    local data="${3:-}"
    local expected="${4:-}"
    
    local url="${API_URL}${endpoint}"
    local response
    
    if [ "$method" = "GET" ]; then
        response=$(curl -s --max-time 10 "$url" 2>/dev/null || echo "ERROR")
    elif [ "$method" = "POST" ]; then
        response=$(curl -s --max-time 10 -X POST \
            -H "Content-Type: application/json" \
            -d "$data" \
            "$url" 2>/dev/null || echo "ERROR")
    fi
    
    if [ "$response" = "ERROR" ]; then
        echo -e "  ${RED}✗${NC} $method $endpoint (connection failed)"
        ((FAILED++))
        return 1
    fi
    
    if [ -n "$expected" ]; then
        if echo "$response" | grep -q "$expected"; then
            echo -e "  ${GREEN}✓${NC} $method $endpoint"
            ((PASSED++))
            return 0
        else
            echo -e "  ${RED}✗${NC} $method $endpoint (unexpected response)"
            echo "    Expected: $expected"
            echo "    Got: ${response:0:100}..."
            ((FAILED++))
            return 1
        fi
    else
        # Just check for non-empty response
        if [ -n "$response" ]; then
            echo -e "  ${GREEN}✓${NC} $method $endpoint"
            ((PASSED++))
            return 0
        else
            echo -e "  ${RED}✗${NC} $method $endpoint (empty response)"
            ((FAILED++))
            return 1
        fi
    fi
}

echo -e "${BLUE}=========================================="
echo "Control API Tests"
echo "==========================================${NC}"
echo ""
echo "API URL: $API_URL"
echo ""

# Test health endpoint
echo -e "${BLUE}[1/8] Testing Health Endpoint${NC}"
test_endpoint "GET" "/health" "" "healthy"
echo ""

# Test device info
echo -e "${BLUE}[2/8] Testing Device Info${NC}"
test_endpoint "GET" "/device/info" "" "device\|screen\|android"
echo ""

# Test screenshot
echo -e "${BLUE}[3/8] Testing Screenshot${NC}"
SCREENSHOT=$(curl -s --max-time 10 "${API_URL}/device/screenshot" 2>/dev/null || echo "")
if [[ -n "$SCREENSHOT" ]] && [[ "${SCREENSHOT:0:4}" =~ ^(PNG|GIF|JFIF|RIFF) ]]; then
    echo -e "  ${GREEN}✓${NC} GET /device/screenshot (valid image)"
    ((PASSED++))
else
    echo -e "  ${RED}✗${NC} GET /device/screenshot (invalid or empty)"
    ((FAILED++))
fi
echo ""

# Test tap (normalized)
echo -e "${BLUE}[4/8] Testing Tap (Normalized)${NC}"
test_endpoint "POST" "/device/tap" '{"x":0.5,"y":0.5,"mode":"norm"}' ""
echo ""

# Test tap (pixel)
echo -e "${BLUE}[5/8] Testing Tap (Pixel)${NC}"
test_endpoint "POST" "/device/tap" '{"x":540,"y":960}' ""
echo ""

# Test swipe
echo -e "${BLUE}[6/8] Testing Swipe${NC}"
test_endpoint "POST" "/device/swipe" '{"x1":540,"y1":1500,"x2":540,"y2":500,"duration_ms":300}' ""
echo ""

# Test text input
echo -e "${BLUE}[7/8] Testing Text Input${NC}"
test_endpoint "POST" "/device/text" '{"text":"test"}' ""
echo ""

# Test key press
echo -e "${BLUE}[8/8] Testing Key Press${NC}"
test_endpoint "POST" "/device/key" '{"keycode":"KEYCODE_HOME"}' ""
echo ""

# Summary
echo -e "${BLUE}=========================================="
echo "Test Summary"
echo "==========================================${NC}"
echo ""
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All API tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some API tests failed.${NC}"
    exit 1
fi

