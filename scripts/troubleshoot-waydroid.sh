#!/bin/bash
# troubleshoot-waydroid.sh
# Comprehensive Waydroid troubleshooting script
# Run this from the Weston console: bash ~/troubleshoot-waydroid.sh
#
# Output is saved to: ~/waydroid-troubleshoot-YYYYMMDD-HHMMSS.log
# You can also view it: cat ~/waydroid-troubleshoot-*.log | tail -1

set -e

# Create log file with timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="$HOME/waydroid-troubleshoot-${TIMESTAMP}.log"
LATEST_LOG="$HOME/waydroid-troubleshoot-latest.log"

# Function to log and echo
log_echo() {
    echo "$@" | tee -a "$LOG_FILE"
}

# Start logging
log_echo "=========================================="
log_echo "  Waydroid Troubleshooting Script"
log_echo "  Started: $(date)"
log_echo "  Log file: $LOG_FILE"
log_echo "=========================================="
log_echo ""

# Also create symlink to latest
ln -sf "$LOG_FILE" "$LATEST_LOG" 2>/dev/null || true

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print section headers
print_section() {
    log_echo ""
    log_echo "=========================================="
    log_echo "  $1"
    log_echo "=========================================="
}

# Function to check status
check_status() {
    if [ $? -eq 0 ]; then
        log_echo -e "${GREEN}✓${NC} $1"
    else
        log_echo -e "${RED}✗${NC} $1"
    fi
}

print_section "System Information"
log_echo "User: $(whoami)"
log_echo "Date: $(date)"
log_echo "Uptime: $(uptime -p)"
log_echo ""

print_section "1. Waydroid Status"
waydroid status | tee -a "$LOG_FILE"
check_status "Waydroid status check"
log_echo ""

print_section "2. ADB Devices (Android)"
adb devices | tee -a "$LOG_FILE"
if adb devices | grep -q "device$"; then
    log_echo -e "${GREEN}✓ Android device detected${NC}"
else
    log_echo -e "${RED}✗ No Android devices found${NC}"
fi
log_echo ""

print_section "3. Container Service Status"
sudo systemctl status waydroid-container --no-pager | head -15 | tee -a "$LOG_FILE"
check_status "Container service check"
log_echo ""

print_section "4. Session Service Status"
sudo systemctl status waydroid-session --no-pager | head -15 | tee -a "$LOG_FILE"
check_status "Session service check"
log_echo ""

print_section "5. Recent Container Logs"
sudo journalctl -u waydroid-container -n 30 --no-pager | tail -20 | tee -a "$LOG_FILE"
log_echo ""

print_section "6. LXC Container Status"
sudo lxc-ls -f | tee -a "$LOG_FILE"
if sudo lxc-ls -f | grep -q "waydroid.*RUNNING"; then
    log_echo -e "${GREEN}✓ Container is running${NC}"
else
    log_echo -e "${RED}✗ Container is not running${NC}"
fi
log_echo ""

print_section "7. Waydroid Images"
sudo ls -lh /var/lib/waydroid/images/ | tee -a "$LOG_FILE"
if [ -f /var/lib/waydroid/images/system.img ] && [ -f /var/lib/waydroid/images/vendor.img ]; then
    log_echo -e "${GREEN}✓ Images exist${NC}"
else
    log_echo -e "${RED}✗ Images missing${NC}"
fi
log_echo ""

print_section "8. Binder Modules"
log_echo "Loaded modules:"
lsmod | grep binder | tee -a "$LOG_FILE" || log_echo "No binder modules loaded"
log_echo ""
log_echo "Binderfs mount:"
mount | grep binder | tee -a "$LOG_FILE" || log_echo "Binderfs not mounted"
log_echo ""
if mount | grep -q binder; then
    log_echo -e "${GREEN}✓ Binderfs mounted${NC}"
    log_echo "Binderfs devices:"
    ls -la /dev/binderfs/ 2>/dev/null | tee -a "$LOG_FILE" || log_echo "No devices found"
else
    log_echo -e "${RED}✗ Binderfs not mounted${NC}"
fi
log_echo ""

print_section "9. Weston/Wayland Status"
log_echo "Weston process:"
ps aux | grep weston | grep -v grep | tee -a "$LOG_FILE" || log_echo "Weston not running"
log_echo ""
log_echo "Wayland socket:"
ls -la /run/user/$(id -u)/wayland* 2>/dev/null | tee -a "$LOG_FILE" || log_echo "Wayland socket not found"
if [ -S /run/user/$(id -u)/wayland-0 ]; then
    log_echo -e "${GREEN}✓ Wayland socket exists${NC}"
else
    log_echo -e "${RED}✗ Wayland socket missing${NC}"
fi
log_echo ""

print_section "10. Environment Variables"
log_echo "DISPLAY: ${DISPLAY:-not set}"
log_echo "XDG_RUNTIME_DIR: ${XDG_RUNTIME_DIR:-not set}"
log_echo "WAYLAND_DISPLAY: ${WAYLAND_DISPLAY:-not set}"
log_echo ""

print_section "11. Network Interfaces"
ip addr show | grep -A 2 waydroid | tee -a "$LOG_FILE" || log_echo "No waydroid network interface"
log_echo ""

print_section "12. Waydroid Configuration"
sudo cat /var/lib/waydroid/waydroid.cfg 2>/dev/null | head -20 | tee -a "$LOG_FILE" || log_echo "Config file not found"
log_echo ""

print_section "13. Container Directory"
sudo ls -la /var/lib/waydroid/lxc/waydroid/ 2>/dev/null | head -10 | tee -a "$LOG_FILE" || log_echo "Container directory not found"
log_echo ""

print_section "14. System Logs (Recent Errors)"
sudo journalctl -k -n 50 --no-pager | grep -i "lxc\|waydroid\|binder\|error" | tail -10 | tee -a "$LOG_FILE" || log_echo "No relevant errors found"
log_echo ""

print_section "15. Process Check"
log_echo "Waydroid processes:"
ps aux | grep -E "[w]aydroid|[p]ython.*waydroid" | head -10 | tee -a "$LOG_FILE" || log_echo "No waydroid processes"
log_echo ""

log_echo ""
log_echo "=========================================="
log_echo "  Log file saved to: $LOG_FILE"
log_echo "  Latest log symlink: $LATEST_LOG"
log_echo "=========================================="
log_echo ""

print_section "Summary"
log_echo "Checking critical components..."
log_echo ""

# Summary checks
CHECKS_PASSED=0
CHECKS_FAILED=0

# Check 1: Images exist
if [ -f /var/lib/waydroid/images/system.img ] && [ -f /var/lib/waydroid/images/vendor.img ]; then
    log_echo -e "${GREEN}✓ Images exist${NC}"
    ((CHECKS_PASSED++))
else
    log_echo -e "${RED}✗ Images missing${NC}"
    ((CHECKS_FAILED++))
fi

# Check 2: Binderfs mounted
if mount | grep -q binder; then
    log_echo -e "${GREEN}✓ Binderfs mounted${NC}"
    ((CHECKS_PASSED++))
else
    log_echo -e "${RED}✗ Binderfs not mounted${NC}"
    ((CHECKS_FAILED++))
fi

# Check 3: Container service running
if sudo systemctl is-active --quiet waydroid-container; then
    log_echo -e "${GREEN}✓ Container service active${NC}"
    ((CHECKS_PASSED++))
else
    log_echo -e "${RED}✗ Container service not active${NC}"
    ((CHECKS_FAILED++))
fi

# Check 4: LXC container exists
if sudo lxc-ls -f | grep -q waydroid; then
    log_echo -e "${GREEN}✓ LXC container exists${NC}"
    ((CHECKS_PASSED++))
else
    log_echo -e "${RED}✗ LXC container missing${NC}"
    ((CHECKS_FAILED++))
fi

# Check 5: Wayland socket
if [ -S /run/user/$(id -u)/wayland-0 ]; then
    log_echo -e "${GREEN}✓ Wayland socket exists${NC}"
    ((CHECKS_PASSED++))
else
    log_echo -e "${RED}✗ Wayland socket missing${NC}"
    ((CHECKS_FAILED++))
fi

# Check 6: ADB device
if adb devices | grep -q "device$"; then
    log_echo -e "${GREEN}✓ Android device connected${NC}"
    ((CHECKS_PASSED++))
else
    log_echo -e "${RED}✗ No Android device${NC}"
    ((CHECKS_FAILED++))
fi

log_echo ""
log_echo "=========================================="
log_echo "  Results: $CHECKS_PASSED passed, $CHECKS_FAILED failed"
log_echo "=========================================="
log_echo ""

print_section "Quick Fixes"
log_echo "To try fixing common issues, run:"
log_echo ""
log_echo "1. Restart container service:"
log_echo "   sudo systemctl restart waydroid-container"
log_echo ""
log_echo "2. Start Waydroid session manually:"
log_echo "   export DISPLAY=:1"
log_echo "   export XDG_RUNTIME_DIR=/run/user/$(id -u)"
log_echo "   export WAYLAND_DISPLAY=wayland-0"
log_echo "   waydroid session start"
log_echo ""
log_echo "3. Check container logs:"
log_echo "   sudo journalctl -u waydroid-container -n 50 --no-pager"
log_echo ""
log_echo "4. Try starting container manually:"
log_echo "   sudo waydroid container stop"
log_echo "   sudo waydroid container start"
log_echo ""

# Final message
log_echo ""
log_echo "=========================================="
log_echo "  Troubleshooting Complete"
log_echo "  Log saved to: $LOG_FILE"
log_echo "  Latest log: $LATEST_LOG"
log_echo "=========================================="

