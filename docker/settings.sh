#!/system/bin/sh
# Cloud Phone Initial Settings
# This script runs on first boot to configure Android settings

# Wait for system to be ready
sleep 30

# Enable developer options
settings put global development_settings_enabled 1

# Enable USB debugging
settings put global adb_enabled 1

# Allow mock locations
settings put secure mock_location 1

# Disable lock screen
settings put secure lockscreen.disabled 1

# Set screen timeout (10 minutes)
settings put system screen_off_timeout 600000

# Enable stay awake while charging
settings put global stay_on_while_plugged_in 7

# Disable animations (faster for automation)
settings put global window_animation_scale 0.5
settings put global transition_animation_scale 0.5
settings put global animator_duration_scale 0.5

# Set default input method
# settings put secure default_input_method com.android.inputmethod.latin/.LatinIME

# Enable auto-time (NTP)
settings put global auto_time 1
settings put global auto_time_zone 1

# Disable notification sounds
settings put system notification_sound ""

# Set volume levels
settings put system volume_music 10
settings put system volume_ring 5
settings put system volume_alarm 5

echo "Cloud Phone settings applied"
