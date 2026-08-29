#!/bin/bash
set -e

echo "======================================"
echo "PhoneOS Deploy & Autostart Installer"
echo "======================================"

# Determine base directory
BASE_DIR=$(pwd)
echo "Current directory: $BASE_DIR"

if [ ! -d "deploy" ]; then
  echo "Error: deploy directory not found. Please run this from the PhoneOS root directory."
  exit 1
fi

echo "Installing systemd services..."
# Copy the services
sudo cp deploy/phoneos-drone.service /etc/systemd/system/
sudo cp deploy/phoneos-gateway.service /etc/systemd/system/

# Make sure permissions are correct
sudo chmod 644 /etc/systemd/system/phoneos-drone.service
sudo chmod 644 /etc/systemd/system/phoneos-gateway.service

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling services to start on boot..."
sudo systemctl enable phoneos-drone.service
sudo systemctl enable phoneos-gateway.service

echo "Restarting services..."
sudo systemctl restart phoneos-drone.service
sudo systemctl restart phoneos-gateway.service

echo "======================================"
echo "Deployment successful."
echo "The laptop can now be disconnected."
echo "======================================"
echo "To check status run:"
echo "  systemctl status phoneos-drone"
echo "  systemctl status phoneos-gateway"
echo ""
echo "To view logs run:"
echo "  journalctl -u phoneos-drone -f"
echo "======================================"
