#!/bin/bash
set -e

echo "======================================"
echo "PhoneOS Swarm RPi 5 Deploy & Autostart"
echo "======================================"

if [ -z "$1" ]; then
    echo "Usage: ./deploy/install.sh <DRONE_NUMBER>"
    echo "Example: ./deploy/install.sh 1"
    exit 1
fi

DRONE_NUM=$1
CURRENT_USER=$(whoami)
WORK_DIR=$(pwd)
TEMPLATE_FILE="deploy/phoneos-drone.service.template"
TARGET_SERVICE="/etc/systemd/system/phoneos-drone${DRONE_NUM}.service"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Error: $TEMPLATE_FILE not found."
    exit 1
fi

# Locate Python in venv
if [ -f "$WORK_DIR/venv/bin/python" ]; then
    VENV_PYTHON="$WORK_DIR/venv/bin/python"
elif [ -f "$WORK_DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$WORK_DIR/.venv/bin/python"
else
    VENV_PYTHON=$(which python3)
fi

START_SCRIPT="start_drone${DRONE_NUM}.py"
if [ ! -f "$WORK_DIR/$START_SCRIPT" ]; then
    echo "Warning: $START_SCRIPT not found in $WORK_DIR. Checking start_drone.py..."
    if [ -f "$WORK_DIR/start_drone.py" ]; then
        START_SCRIPT="start_drone.py"
    else
        echo "Error: Neither $START_SCRIPT nor start_drone.py exists in $WORK_DIR."
        exit 1
    fi
fi

echo "Configuration:"
echo "  Drone Number:       $DRONE_NUM"
echo "  User:               $CURRENT_USER"
echo "  Working Directory:  $WORK_DIR"
echo "  Python Interpreter: $VENV_PYTHON"
echo "  Start Script:       $START_SCRIPT"
echo "  Target Unit File:   $TARGET_SERVICE"
echo ""

# Ensure user is in dialout (serial/MAVLink) and video (camera/V4L2) groups
echo "Configuring hardware device permissions (dialout, video)..."
sudo usermod -a -G dialout,video "$CURRENT_USER" || true

# Install dependencies if Python virtual environment exists
echo "Installing and updating Python dependencies..."
if [ -f "$WORK_DIR/requirements.txt" ]; then
    echo "Installing requirements from $WORK_DIR/requirements.txt..."
    "$VENV_PYTHON" -m pip install --upgrade -r "$WORK_DIR/requirements.txt" || echo "Warning: pip install encountered non-fatal issues."
elif [ -f "$WORK_DIR/DroneOS/requirements.txt" ]; then
    echo "Installing requirements from $WORK_DIR/DroneOS/requirements.txt..."
    "$VENV_PYTHON" -m pip install --upgrade -r "$WORK_DIR/DroneOS/requirements.txt" || echo "Warning: pip install encountered non-fatal issues."
fi

# Generate service file from template
echo "Generating systemd service from template..."
sed -e "s|__DRONE_NUM__|$DRONE_NUM|g" \
    -e "s|__USER__|$CURRENT_USER|g" \
    -e "s|__WORK_DIR__|$WORK_DIR|g" \
    -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
    -e "s|__START_SCRIPT__|$START_SCRIPT|g" \
    "$TEMPLATE_FILE" | sudo tee "$TARGET_SERVICE" > /dev/null

sudo chmod 644 "$TARGET_SERVICE"

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling service on boot..."
sudo systemctl enable "phoneos-drone${DRONE_NUM}.service"

echo "Restarting service..."
sudo systemctl restart "phoneos-drone${DRONE_NUM}.service"

echo "======================================"
echo "Deployment successful for Drone $DRONE_NUM (RPi 5)."
echo "PhoneOS + ML Perception will start automatically on boot."
echo "======================================"
echo "To check live status:"
echo "  sudo systemctl status phoneos-drone${DRONE_NUM}"
echo ""
echo "To view live streaming logs:"
echo "  sudo journalctl -u phoneos-drone${DRONE_NUM} -f"
echo ""
echo "To stop the background service:"
echo "  sudo systemctl stop phoneos-drone${DRONE_NUM}"
echo "======================================"
