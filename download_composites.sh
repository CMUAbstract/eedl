#!/bin/bash
# download_composites.sh
# Script to download 10 composite images with exact pixel dimensions

# Configuration
MGRS_REGION="09V"              # MGRS grid region
START_DATE="2017"        # Start date for imagery
END_DATE="2024"          # End date for imagery
NUM_IMAGES=10                   # Number of composite images to generate
SENSOR="l8"                     # Sensor: l8 (Landsat 8) or l9 (Landsat 9)
GSD=175.0                       # Ground Sample Distance in meters
WIDTH_PIXELS=4608               # Image width in pixels
HEIGHT_PIXELS=2592              # Image height in pixels
OUTPUT_DIR="09V_composite_images"   # Output directory on Google Drive

# Run the composite downloader
python composite-eedl-mgrs.py \
    -g "$MGRS_REGION" \
    -i "$START_DATE" \
    -f "$END_DATE" \
    -np "$NUM_IMAGES" \
    -se "$SENSOR" \
    -s "$GSD" \
    -wp "$WIDTH_PIXELS" \
    -hp "$HEIGHT_PIXELS" \
    -o "$OUTPUT_DIR" \
    -ba B4 B3 B2

echo ""
echo "Tasks submitted to Google Earth Engine!"
echo "Check status at: https://code.earthengine.google.com/tasks"
echo "Once complete, images will be in Google Drive folder: $OUTPUT_DIR"
