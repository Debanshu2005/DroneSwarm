# Raspberry Pi Wi-Fi Multi-Network Setup

To ensure the Raspberry Pi can connect seamlessly to your Phone Hotspot out in the field, and fallback to your Home/Lab Wi-Fi when indoors, use the following `nmcli` (NetworkManager) commands on the Raspberry Pi terminal.

## 1. List Available Networks
First, scan for available Wi-Fi networks:
```bash
sudo nmcli device wifi list
```

## 2. Add New Wi-Fi Networks
Do **NOT** commit your Wi-Fi passwords to source control. Run these commands directly in your terminal.

Add your Phone Hotspot (set priority to a high number, e.g., 100):
```bash
sudo nmcli connection add type wifi con-name "PhoneHotspot" ifname wlan0 ssid "<YOUR_PHONE_SSID>" autoconnect yes
sudo nmcli connection modify "PhoneHotspot" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<YOUR_PHONE_PASSWORD>" ipv4.method auto connection.autoconnect-priority 100
```

Add your Home Wi-Fi (set priority lower than the hotspot, e.g., 50):
```bash
sudo nmcli connection add type wifi con-name "HomeWiFi" ifname wlan0 ssid "<YOUR_HOME_SSID>" autoconnect yes
sudo nmcli connection modify "HomeWiFi" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<YOUR_HOME_PASSWORD>" ipv4.method auto connection.autoconnect-priority 50
```

Add Lab Wi-Fi (priority even lower, e.g., 25):
```bash
sudo nmcli connection add type wifi con-name "LabWiFi" ifname wlan0 ssid "<YOUR_LAB_SSID>" autoconnect yes
sudo nmcli connection modify "LabWiFi" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<YOUR_LAB_PASSWORD>" ipv4.method auto connection.autoconnect-priority 25
```

## 3. Verify Connection Priorities
List your saved connections to ensure the priorities are correct:
```bash
sudo nmcli -f NAME,UUID,AUTOCONNECT,AUTOCONNECT-PRIORITY connection
```

## 4. Check Current Connection
To see which network the Pi is currently connected to:
```bash
sudo nmcli connection show --active
```

## 5. Remove or Forget a Network
If you need to forget a network:
```bash
sudo nmcli connection delete "LabWiFi"
```

## How It Works
When the Raspberry Pi boots or loses connection, NetworkManager evaluates all saved networks with `autoconnect yes`. It will automatically attempt to connect to the network with the **highest priority number** first. If that network (e.g., your Phone Hotspot) is unavailable, it will step down to the next highest priority (e.g., Home Wi-Fi).

## Troubleshooting
If the Pi does not automatically switch networks, you can restart NetworkManager:
```bash
sudo systemctl restart NetworkManager
```
Or view the network logs:
```bash
sudo journalctl -u NetworkManager -f
```
