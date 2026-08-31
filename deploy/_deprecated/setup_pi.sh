#!/bin/bash

# Ensure script is run as root or with sudo
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (use sudo)"
  exit
fi

DRONEOS_DIR="/home/pi/PhoneOS/DroneOS"

if [ ! -d "$DRONEOS_DIR" ]; then
    echo "Could not find DroneOS directory at $DRONEOS_DIR"
    echo "Please copy the PhoneOS folder to /home/pi/ so the path matches."
    exit 1
fi

echo "Installing DroneOS Systemd Service..."

cat << 'SERVICE' > /etc/systemd/system/droneos.service
[Unit]
Description=DroneOS Background Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/PhoneOS
ExecStart=/usr/bin/python3 DroneOS/main.py --config DroneOS/config.json
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling droneos.service to start on boot..."
systemctl enable droneos.service

echo "Starting droneos.service..."
systemctl start droneos.service

echo "Done! DroneOS will now run automatically on boot."
echo "You can view logs with: sudo journalctl -fu droneos.service"
