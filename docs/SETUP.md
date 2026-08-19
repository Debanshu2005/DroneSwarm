# PhoneOS Setup & Deployment

## 1. Prerequisites
- A Raspberry Pi running SwarmOS (DroneOS).
- A mobile phone or laptop with a modern web browser, or an Android phone to install the APK.
- Python 3.10+ on the Raspberry Pi.
- Node.js (v18+) for building the mobile web app or APK.

## 2. Raspberry Pi Setup (Relay & Networking)

The Relay acts as a bridge between your phone's web browser/app and the DroneOS UDP network.

1. **Install Dependencies**:
   ```bash
   cd PhoneOS/relay
   pip install -r requirements.txt
   ```

2. **Start Raspberry Pi Relay**:
   Run the relay on the Pi in the background:
   ```bash
   python3 PhoneOS/relay/relay.py
   ```

### Network Configuration (Wi-Fi / Hotspot)
To allow the phone and laptop to easily connect:
1. Configure `wpa_supplicant.conf` or NetworkManager on the Raspberry Pi to connect to known networks.
2. Add your Phone's Hotspot credentials to the Pi. 
3. Add your Home Wi-Fi credentials to the Pi.
4. When the Pi boots, it will automatically connect to whichever network is available. The phone can then access the GroundStation by connecting to the same network and typing the Pi's IP address.

## 3. Mobile App Deployment

### Option A: Install Android APK (Recommended)
You can install PhoneOS natively on any Android device.

1. **Build APK**:
   ```bash
   cd PhoneOS/mobile
   npm run build
   npm run android:debug
   ```
2. **Install APK**:
   Connect your Android phone via USB and run:
   ```bash
   adb install -r android/app/build/outputs/apk/debug/app-debug.apk
   ```

### Option B: Serve via Web Browser (PWA)
1. **Build the Mobile App**:
   ```bash
   cd PhoneOS/mobile
   npm install
   npm run build
   ```
2. **Serve the App**:
   Serve the `dist` directory via any web server (e.g. Nginx, Python http.server, or Vite preview).
   ```bash
   npm run preview -- --host 0.0.0.0 --port 3000
   ```
3. **Accessing from Phone**:
   - Open Safari or Chrome on your mobile phone.
   - Navigate to `http://<RASPBERRY_PI_IP>:3000`

## 4. Usage

1. **Connect phone to Raspberry Pi hotspot/Wi-Fi**.
2. **Configure Raspberry Pi IP in app**: Click the settings icon in the top right. Enter the WebSocket URL (e.g. `ws://192.168.1.100:8080`) and hit 'Save & Reconnect'.
3. **Test connection**: In the settings menu, click `Test Connection` to verify the phone can reach the Relay.
4. **Discover drone**: Wait for the drone's heartbeat. It will appear in the "Target Drone" dropdown.
5. **View telemetry**: Observe GPS status, Battery, Altitude, and Flight mode in the Drone Status widget.
6. **ARM / TAKEOFF**: Use the ARM slider/button (requires confirmation) to arm the drone, then hit TAKEOFF.
7. **Manual Control**: Use the manual joystick pad to navigate, and hit LAND or RTL to return.
8. **Emergency**: Double-tap the EMERGENCY STOP button in case of failure.
