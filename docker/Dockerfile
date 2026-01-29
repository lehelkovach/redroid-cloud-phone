# Custom Redroid Cloud Phone Image
#
# Build a customized Redroid image with:
# - Pre-installed apps
# - Custom system settings
# - GApps (optional)
# - Proxy configuration (optional)
#
# Build:
#   docker build -t my-cloud-phone:latest .
#   docker build --build-arg BASE_IMAGE=redroid/redroid:11.0.0-latest -t my-cloud-phone:android11 .
#   docker build --build-arg INCLUDE_GAPPS=true -t my-cloud-phone:gapps .
#
# Push to registry:
#   docker tag my-cloud-phone:latest myregistry.com/cloud-phone:latest
#   docker push myregistry.com/cloud-phone:latest

ARG BASE_IMAGE=redroid/redroid:11.0.0-latest
FROM ${BASE_IMAGE}

# Build arguments
ARG INCLUDE_GAPPS=false
ARG DEFAULT_TIMEZONE=UTC
ARG DEFAULT_LOCALE=en_US

# Labels
LABEL maintainer="cloud-phone"
LABEL description="Custom Redroid Cloud Phone Image"
LABEL version="1.0"

# Set system properties
# These are applied at build time and persist in the image
RUN echo "# Cloud Phone Custom Properties" >> /system/build.prop && \
    echo "ro.setupwizard.mode=OPTIONAL" >> /system/build.prop && \
    echo "ro.config.nocheckin=1" >> /system/build.prop && \
    echo "persist.sys.timezone=${DEFAULT_TIMEZONE}" >> /system/build.prop && \
    echo "persist.sys.language=en" >> /system/build.prop && \
    echo "persist.sys.country=US" >> /system/build.prop && \
    echo "ro.com.google.clientidbase=android-google" >> /system/build.prop && \
    echo "ro.url.legal=http://www.google.com/intl/%s/mobile/android/basic/phone-legal.html" >> /system/build.prop && \
    echo "ro.url.legal.android_privacy=http://www.google.com/intl/%s/mobile/android/basic/privacy.html" >> /system/build.prop

# Enable ADB by default
RUN echo "persist.adb.tcp.port=5555" >> /system/build.prop && \
    echo "service.adb.tcp.port=5555" >> /system/build.prop && \
    echo "ro.adb.secure=0" >> /system/build.prop && \
    echo "ro.debuggable=1" >> /system/build.prop

# Configure default settings database
# These settings will be applied on first boot
COPY settings.sh /system/etc/init/cloud-phone-settings.sh
RUN chmod 755 /system/etc/init/cloud-phone-settings.sh 2>/dev/null || true

# Copy custom apps (if any exist in context)
# Place APKs in apps/ directory
COPY apps/*.apk /system/priv-app/ 2>/dev/null || true

# Copy GApps if enabled
# You'll need to download GApps separately and place in gapps/ directory
COPY gapps/ /system/ 2>/dev/null || true

# Set default boot parameters via system properties
ENV REDROID_GPU_MODE=guest
ENV REDROID_WIDTH=1280
ENV REDROID_HEIGHT=720
ENV REDROID_FPS=30
ENV REDROID_DPI=240

# Expose ports
EXPOSE 5555
EXPOSE 5900

# Default command inherits from base image
