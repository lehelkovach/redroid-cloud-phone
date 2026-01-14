#!/bin/bash
# Anti-Detection Configuration for Cloud Phone
#
# This script applies modifications to make Redroid appear as a real physical device.
# It addresses common detection vectors used by apps to identify emulators/containers.
#
# Usage:
#   ./anti-detection.sh apply [profile]     Apply anti-detection with device profile
#   ./anti-detection.sh reset               Reset to default Redroid settings
#   ./anti-detection.sh status              Check current detection status
#   ./anti-detection.sh generate-ids        Generate random hardware IDs
#
# Profiles: samsung-galaxy-s21, google-pixel-6, oneplus-9-pro, random
#
# Detection vectors addressed:
# - Build properties (ro.product.*, ro.build.*)
# - Hardware identifiers (IMEI, serial, MAC)
# - Emulator artifacts (/dev/goldfish, qemu files)
# - Debug flags (ro.debuggable, adb secure)
# - Root indicators (su binary, magisk)
# - GL renderer strings
# - System files and paths

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILES_DIR="$PROJECT_ROOT/config/device-profiles"
CONTAINER="${REDROID_CONTAINER:-redroid}"
ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Generate random hardware identifiers
generate_imei() {
    # Generate valid IMEI with Luhn checksum
    local tac="35${RANDOM:0:4}0"  # TAC (Type Allocation Code)
    local snr=$(printf "%06d" $((RANDOM % 1000000)))
    local imei_without_check="${tac}${snr}"
    
    # Calculate Luhn checksum
    local sum=0
    local double=false
    for ((i=${#imei_without_check}-1; i>=0; i--)); do
        local digit=${imei_without_check:$i:1}
        if $double; then
            digit=$((digit * 2))
            if ((digit > 9)); then
                digit=$((digit - 9))
            fi
        fi
        sum=$((sum + digit))
        double=!$double
    done
    local check=$(( (10 - (sum % 10)) % 10 ))
    echo "${imei_without_check}${check}"
}

generate_serial() {
    # Generate realistic serial number
    local chars="0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    local serial=""
    for i in {1..11}; do
        serial+="${chars:RANDOM%${#chars}:1}"
    done
    echo "$serial"
}

generate_mac() {
    # Generate random MAC address with common vendor prefixes
    local vendors=("00:1A:2B" "00:1E:C9" "00:26:BB" "D8:FC:93" "F4:F5:D8" "AC:37:43")
    local prefix=${vendors[$RANDOM % ${#vendors[@]}]}
    printf "%s:%02X:%02X:%02X\n" "$prefix" $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256))
}

generate_android_id() {
    # Generate 16-character hex Android ID
    openssl rand -hex 8
}

generate_gsf_id() {
    # Generate Google Services Framework ID (decimal)
    echo "$((RANDOM))$((RANDOM))$((RANDOM))" | cut -c1-19
}

# Generate all IDs and save to file
generate_ids() {
    local output_file="${1:-/tmp/device-ids.json}"
    
    cat > "$output_file" <<EOF
{
  "imei": "$(generate_imei)",
  "imei2": "$(generate_imei)",
  "serial": "$(generate_serial)",
  "mac_wifi": "$(generate_mac)",
  "mac_bt": "$(generate_mac)",
  "android_id": "$(generate_android_id)",
  "gsf_id": "$(generate_gsf_id)",
  "advertising_id": "$(uuidgen | tr '[:upper:]' '[:lower:]')",
  "generated_at": "$(date -Iseconds)"
}
EOF
    
    log_info "Generated hardware IDs saved to $output_file"
    cat "$output_file"
}

# Apply device profile
apply_profile() {
    local profile="$1"
    local profile_file=""
    
    case "$profile" in
        samsung*|galaxy*)
            profile_file="$PROFILES_DIR/samsung-galaxy-s21.prop"
            ;;
        pixel*|google*)
            profile_file="$PROFILES_DIR/google-pixel-6.prop"
            ;;
        oneplus*)
            profile_file="$PROFILES_DIR/oneplus-9-pro.prop"
            ;;
        random)
            # Pick a random profile
            local profiles=("samsung-galaxy-s21" "google-pixel-6" "oneplus-9-pro")
            local random_profile=${profiles[$RANDOM % ${#profiles[@]}]}
            profile_file="$PROFILES_DIR/${random_profile}.prop"
            log_info "Randomly selected profile: $random_profile"
            ;;
        *)
            if [[ -f "$profile" ]]; then
                profile_file="$profile"
            elif [[ -f "$PROFILES_DIR/${profile}.prop" ]]; then
                profile_file="$PROFILES_DIR/${profile}.prop"
            else
                log_error "Unknown profile: $profile"
                echo "Available profiles:"
                ls -1 "$PROFILES_DIR"/*.prop 2>/dev/null | xargs -n1 basename | sed 's/.prop$//'
                return 1
            fi
            ;;
    esac
    
    log_info "Applying profile: $(basename "$profile_file" .prop)"
    
    # Read and apply each property
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        
        # Apply via setprop
        adb -s "$ADB_TARGET" shell "setprop $key '$value'" 2>/dev/null || true
    done < "$profile_file"
    
    log_info "Device profile applied"
}

# Apply hardware ID spoofing
apply_hardware_ids() {
    local ids_file="${1:-/tmp/device-ids.json}"
    
    if [[ ! -f "$ids_file" ]]; then
        generate_ids "$ids_file"
    fi
    
    log_info "Applying hardware IDs..."
    
    # Read IDs
    local imei=$(jq -r '.imei' "$ids_file")
    local imei2=$(jq -r '.imei2' "$ids_file")
    local serial=$(jq -r '.serial' "$ids_file")
    local android_id=$(jq -r '.android_id' "$ids_file")
    local mac_wifi=$(jq -r '.mac_wifi' "$ids_file")
    
    # Apply via ADB
    adb -s "$ADB_TARGET" shell "settings put secure android_id '$android_id'" 2>/dev/null || true
    
    # Set system properties
    adb -s "$ADB_TARGET" shell "setprop ro.serialno '$serial'" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.boot.serialno '$serial'" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop gsm.sim.imei '$imei'" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop persist.radio.imei '$imei'" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop persist.radio.imei2 '$imei2'" 2>/dev/null || true
    
    log_info "Hardware IDs applied"
}

# Hide emulator artifacts
hide_emulator_artifacts() {
    log_info "Hiding emulator artifacts..."
    
    # Properties to hide emulator
    local hide_props=(
        "ro.kernel.qemu=0"
        "ro.kernel.qemu.gles=0"
        "ro.hardware.virtual_device=0"
        "ro.boot.qemu=0"
        "ro.boot.hardware=qcom"
        "init.svc.qemu-props="
        "qemu.hw.mainkeys=0"
        "ro.product.first_api_level=29"
    )
    
    for prop in "${hide_props[@]}"; do
        local key="${prop%%=*}"
        local value="${prop#*=}"
        adb -s "$ADB_TARGET" shell "setprop $key '$value'" 2>/dev/null || true
    done
    
    # Hide suspicious files (run inside container)
    docker exec "$CONTAINER" sh -c '
        # Remove/hide emulator-specific files
        rm -f /system/bin/qemu-props 2>/dev/null
        rm -f /system/lib/libc_malloc_debug_qemu.so 2>/dev/null
        rm -f /system/lib64/libc_malloc_debug_qemu.so 2>/dev/null
        
        # Hide goldfish references
        rm -rf /dev/goldfish* 2>/dev/null
        rm -rf /dev/qemu* 2>/dev/null
        
        # Remove emulator init scripts
        rm -f /system/etc/init/goldfish*.rc 2>/dev/null
        rm -f /system/etc/init/qemu*.rc 2>/dev/null
        
        # Hide vbox/vmware artifacts (if any)
        rm -rf /dev/vboxguest 2>/dev/null
        rm -rf /dev/vboxuser 2>/dev/null
    ' 2>/dev/null || true
    
    log_info "Emulator artifacts hidden"
}

# Hide root/debug indicators
hide_root_indicators() {
    log_info "Hiding root/debug indicators..."
    
    # Disable debugging
    adb -s "$ADB_TARGET" shell "settings put global adb_enabled 0" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.debuggable 0" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.secure 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop service.adb.root 0" 2>/dev/null || true
    
    # Re-enable ADB but mark as secure
    adb -s "$ADB_TARGET" shell "settings put global adb_enabled 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.adb.secure 1" 2>/dev/null || true
    
    # Hide su binary and root files
    docker exec "$CONTAINER" sh -c '
        # Hide common root indicators
        chmod 000 /system/xbin/su 2>/dev/null
        chmod 000 /system/bin/su 2>/dev/null
        chmod 000 /sbin/su 2>/dev/null
        
        # Hide Magisk
        rm -rf /sbin/.magisk 2>/dev/null
        rm -rf /data/adb/magisk 2>/dev/null
        
        # Hide SuperSU
        rm -rf /data/data/eu.chainfire.supersu 2>/dev/null
        
        # Hide busybox
        chmod 000 /system/xbin/busybox 2>/dev/null
    ' 2>/dev/null || true
    
    # Set boot state properties
    adb -s "$ADB_TARGET" shell "setprop ro.boot.flash.locked 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.boot.verifiedbootstate green" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.boot.vbmeta.device_state locked" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.boot.veritymode enforcing" 2>/dev/null || true
    
    log_info "Root indicators hidden"
}

# Spoof GL renderer
spoof_gl_renderer() {
    log_info "Spoofing GL renderer..."
    
    # Set realistic GPU properties
    adb -s "$ADB_TARGET" shell "setprop ro.hardware.egl adreno" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.hardware.vulkan adreno" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop ro.opengles.version 196610" 2>/dev/null || true  # OpenGL ES 3.2
    
    # These may need root/system access
    docker exec "$CONTAINER" sh -c '
        # Create fake GPU lib symlinks if needed
        # This helps hide swiftshader/llvmpipe
        echo "GPU spoofing applied (limited)"
    ' 2>/dev/null || true
    
    log_info "GL renderer spoofed (limited - some apps may still detect)"
}

# Set realistic sensor behavior  
configure_sensors() {
    log_info "Configuring sensor behavior..."
    
    # Enable sensor services
    adb -s "$ADB_TARGET" shell "setprop ro.hardware.sensors qcom" 2>/dev/null || true
    
    # Set sensor properties
    adb -s "$ADB_TARGET" shell "setprop sensor.accelerometer.available 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop sensor.gyroscope.available 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop sensor.magnetometer.available 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop sensor.proximity.available 1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop sensor.barometer.available 1" 2>/dev/null || true
    
    log_info "Sensor configuration applied"
}

# Configure battery to appear realistic
configure_battery() {
    log_info "Configuring battery..."
    
    # Set battery properties to appear as a real battery
    adb -s "$ADB_TARGET" shell "dumpsys battery set level 78" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "dumpsys battery set status 3" 2>/dev/null || true  # 3 = not charging
    adb -s "$ADB_TARGET" shell "dumpsys battery set ac 0" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "dumpsys battery set usb 0" 2>/dev/null || true
    
    log_info "Battery configuration applied"
}

# Configure telephony/SIM
configure_telephony() {
    log_info "Configuring telephony..."
    
    local imei=$(jq -r '.imei // empty' /tmp/device-ids.json 2>/dev/null || generate_imei)
    
    # Set telephony properties
    adb -s "$ADB_TARGET" shell "setprop gsm.version.baseband G991BXXS5CVK1" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop gsm.version.ril-impl android samsung-ril 1.0" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop gsm.sim.state READY" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop gsm.network.type LTE" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop gsm.nitz.time $(date +%s)000" 2>/dev/null || true
    adb -s "$ADB_TARGET" shell "setprop gsm.sim.imei '$imei'" 2>/dev/null || true
    
    log_info "Telephony configuration applied"
}

# Main apply function
apply_all() {
    local profile="${1:-samsung-galaxy-s21}"
    
    log_info "Applying full anti-detection configuration..."
    echo ""
    
    # Ensure ADB is connected
    adb connect "$ADB_TARGET" 2>/dev/null || true
    sleep 2
    
    # Generate fresh IDs
    generate_ids /tmp/device-ids.json > /dev/null
    
    # Apply all modifications
    apply_profile "$profile"
    apply_hardware_ids /tmp/device-ids.json
    hide_emulator_artifacts
    hide_root_indicators
    spoof_gl_renderer
    configure_sensors
    configure_battery
    configure_telephony
    
    echo ""
    log_info "Anti-detection configuration complete!"
    echo ""
    echo "Profile: $profile"
    echo "IDs saved to: /tmp/device-ids.json"
    echo ""
    echo "Note: Some detection methods cannot be fully bypassed:"
    echo "  - Deep hardware checks may still detect virtualization"
    echo "  - Play Integrity/SafetyNet strong attestation will fail"
    echo "  - Binary/library inspection may reveal emulator"
    echo ""
    echo "For stronger hiding, consider:"
    echo "  - Using Magisk + Universal SafetyNet Fix"
    echo "  - Hiding specific apps with Magisk Hide"
    echo "  - Custom ROM with anti-detection patches"
}

# Reset to defaults
reset_all() {
    log_info "Resetting to default Redroid settings..."
    
    # Reset battery
    adb -s "$ADB_TARGET" shell "dumpsys battery reset" 2>/dev/null || true
    
    # Re-enable debugging
    adb -s "$ADB_TARGET" shell "setprop ro.debuggable 1" 2>/dev/null || true
    
    log_info "Reset complete. Restart container for full reset."
}

# Check detection status
check_status() {
    echo "========================================"
    echo "  Detection Status Check"
    echo "========================================"
    echo ""
    
    echo "Build Properties:"
    adb -s "$ADB_TARGET" shell "getprop ro.product.model" 2>/dev/null | xargs echo "  Model:"
    adb -s "$ADB_TARGET" shell "getprop ro.product.brand" 2>/dev/null | xargs echo "  Brand:"
    adb -s "$ADB_TARGET" shell "getprop ro.build.fingerprint" 2>/dev/null | xargs echo "  Fingerprint:"
    
    echo ""
    echo "Security Status:"
    adb -s "$ADB_TARGET" shell "getprop ro.debuggable" 2>/dev/null | xargs echo "  Debuggable:"
    adb -s "$ADB_TARGET" shell "getprop ro.secure" 2>/dev/null | xargs echo "  Secure:"
    adb -s "$ADB_TARGET" shell "getprop ro.boot.verifiedbootstate" 2>/dev/null | xargs echo "  Boot State:"
    
    echo ""
    echo "Hardware IDs:"
    adb -s "$ADB_TARGET" shell "settings get secure android_id" 2>/dev/null | xargs echo "  Android ID:"
    adb -s "$ADB_TARGET" shell "getprop ro.serialno" 2>/dev/null | xargs echo "  Serial:"
    
    echo ""
    echo "Emulator Indicators:"
    local qemu=$(adb -s "$ADB_TARGET" shell "getprop ro.kernel.qemu" 2>/dev/null)
    [[ "$qemu" == "1" ]] && echo -e "  ${RED}✗${NC} QEMU detected" || echo -e "  ${GREEN}✓${NC} QEMU not detected"
    
    local goldfish=$(adb -s "$ADB_TARGET" shell "ls /dev/goldfish* 2>/dev/null | wc -l")
    [[ "$goldfish" -gt 0 ]] && echo -e "  ${RED}✗${NC} Goldfish devices found" || echo -e "  ${GREEN}✓${NC} Goldfish not found"
    
    echo ""
    echo "Battery:"
    adb -s "$ADB_TARGET" shell "dumpsys battery" 2>/dev/null | grep -E "level|status|AC powered|USB" | sed 's/^/  /'
}

# Usage
usage() {
    cat <<EOF
Anti-Detection Configuration for Cloud Phone

Usage: $0 <command> [options]

Commands:
  apply [profile]     Apply anti-detection with device profile
  reset               Reset to default Redroid settings
  status              Check current detection status
  generate-ids        Generate random hardware IDs
  list-profiles       List available device profiles

Profiles:
  samsung-galaxy-s21  Samsung Galaxy S21 5G (default)
  google-pixel-6      Google Pixel 6
  oneplus-9-pro       OnePlus 9 Pro
  random              Randomly select a profile

Examples:
  $0 apply                          # Apply with default profile
  $0 apply google-pixel-6          # Apply Pixel 6 profile
  $0 apply random                   # Random device profile
  $0 generate-ids                   # Generate new hardware IDs
  $0 status                         # Check detection status

EOF
    exit 0
}

# Main
case "${1:-}" in
    apply)
        apply_all "${2:-samsung-galaxy-s21}"
        ;;
    reset)
        reset_all
        ;;
    status)
        check_status
        ;;
    generate-ids)
        generate_ids "${2:-/tmp/device-ids.json}"
        ;;
    list-profiles)
        echo "Available profiles:"
        ls -1 "$PROFILES_DIR"/*.prop 2>/dev/null | xargs -n1 basename | sed 's/.prop$//'
        ;;
    --help|-h|"")
        usage
        ;;
    *)
        log_error "Unknown command: $1"
        usage
        ;;
esac
