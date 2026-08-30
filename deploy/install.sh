#!/bin/bash
set -e

echo "======================================"
echo "PhoneOS Swarm Deploy & Autostart"
echo "======================================"

if [ -z "$1" ]; then
    echo "Usage: ./deploy/install.sh <DRONE_NUMBER>"
    echo "Example: ./deploy/install.sh 1"
    exit 1
fi

DRONE_NUM=$1
SERVICE_FILE="deploy/phoneos-drone${DRONE_NUM}.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "Error: $SERVICE_FILE not found."
    exit 1
fi

echo "Installing systemd service for Drone $DRONE_NUM..."
sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/phoneos-drone${DRONE_NUM}.service

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling service to start on boot..."
sudo systemctl enable phoneos-drone${DRONE_NUM}.service

echo "Starting service..."
sudo systemctl start phoneos-drone${DRONE_NUM}.service

echo "======================================"
echo "Deployment successful for Drone $DRONE_NUM."
echo "The drone will now automatically start on boot."
echo "======================================"
echo "To check status run:"
echo "  systemctl status phoneos-drone${DRONE_NUM}"
echo ""
echo "To view live logs run:"
echo "  journalctl -u phoneos-drone${DRONE_NUM} -f"
echo "======================================"
