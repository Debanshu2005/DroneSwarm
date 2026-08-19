import math
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, 
    QLabel, QLineEdit, QPushButton, QFormLayout, QMessageBox
)

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # radius of Earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def compute_heading(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)
    
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - \
        math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    
    theta = math.atan2(y, x)
    return (math.degrees(theta) + 360) % 360

class GPSMissionPlanner(QWidget):
    def __init__(self, map_panel, mission_system_ui, network_manager):
        super().__init__()
        self.map = map_panel
        self.mission_system = mission_system_ui
        self.network = network_manager
        self.init_ui()
        
        # Connect to map clicks
        self.map.destination_set.connect(self._fill_map_dest)

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        grp = QGroupBox("GPS Mission Planner")
        form = QFormLayout(grp)
        
        self.in_start_lat = QLineEdit()
        self.in_start_lon = QLineEdit()
        self.btn_use_current = QPushButton("Use Current Drone GPS")
        self.btn_use_current.clicked.connect(self._fill_current_gps)
        
        start_box = QHBoxLayout()
        start_box.addWidget(self.in_start_lat)
        start_box.addWidget(self.in_start_lon)
        start_box.addWidget(self.btn_use_current)
        
        self.in_dest_lat = QLineEdit()
        self.in_dest_lon = QLineEdit()
        self.btn_use_map = QPushButton("Use Map Destination")
        self.btn_use_map.clicked.connect(self._fill_map_dest)
        
        dest_box = QHBoxLayout()
        dest_box.addWidget(self.in_dest_lat)
        dest_box.addWidget(self.in_dest_lon)
        dest_box.addWidget(self.btn_use_map)
        
        self.in_alt = QLineEdit("10.0")
        self.in_speed = QLineEdit("5.0")
        self.in_hover = QLineEdit("2.0")
        self.in_yaw = QLineEdit("Auto")
        
        form.addRow("Start (Lat, Lon):", start_box)
        form.addRow("Destination (Lat, Lon):", dest_box)
        form.addRow("Altitude (m):", self.in_alt)
        form.addRow("Speed (m/s):", self.in_speed)
        form.addRow("Hover Time at Dest (s):", self.in_hover)
        form.addRow("Yaw (deg):", self.in_yaw)
        
        self.lbl_stats = QLabel("Distance: 0m | Heading: 0°")
        self.lbl_stats.setStyleSheet("font-weight: bold; color: yellow;")
        form.addRow("Stats:", self.lbl_stats)
        
        layout.addWidget(grp)
        
        btn_layout = QHBoxLayout()
        self.btn_gen = QPushButton("Generate Mission & Send to Editor")
        self.btn_gen.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        self.btn_gen.clicked.connect(self._generate_mission)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_gen)
        
        layout.addLayout(btn_layout)

    def _fill_current_gps(self):
        # Pick the first drone's GPS from network manager if available
        drones = self.network.drones
        if not drones:
            return
        for drone_id, info in drones.items():
            if info.telemetry and info.telemetry.latitude is not None:
                self.in_start_lat.setText(str(info.telemetry.latitude))
                self.in_start_lon.setText(str(info.telemetry.longitude))
                return
        
        # Fallback to map panel home
        if self.map.home_lat is not None:
            self.in_start_lat.setText(str(self.map.home_lat))
            self.in_start_lon.setText(str(self.map.home_lon))

    def _fill_map_dest(self):
        lat, lon = self.map.get_destination()
        if lat is not None and lon is not None:
            self.in_dest_lat.setText(f"{lat:.7f}")
            self.in_dest_lon.setText(f"{lon:.7f}")

    def _generate_mission(self):
        try:
            slat = float(self.in_start_lat.text())
            slon = float(self.in_start_lon.text())
            dlat = float(self.in_dest_lat.text())
            dlon = float(self.in_dest_lon.text())
            alt = float(self.in_alt.text())
            spd = float(self.in_speed.text())
            hover = float(self.in_hover.text())
            
            dist = haversine_distance(slat, slon, dlat, dlon)
            heading = compute_heading(slat, slon, dlat, dlon)
            
            self.lbl_stats.setText(f"Distance: {dist:.1f}m | Heading: {heading:.1f}°")
            
            yaw = self.in_yaw.text()
            yaw_val = heading if yaw.lower() == "auto" else float(yaw)
            
            # Simple 3 point mission: Takeoff, Fly to Dest, Hover
            waypoints = [
                {"latitude": slat, "longitude": slon, "altitude": alt, "speed": spd, "delay": 0.0},
                {"latitude": dlat, "longitude": dlon, "altitude": alt, "speed": spd, "delay": hover}
            ]
            
            mission = {
                "name": "Generated GPS Mission",
                "waypoints": waypoints
            }
            
            self.mission_system.editor.set_mission_json(json.dumps(mission, indent=4))
            QMessageBox.information(self, "Success", "Mission generated and sent to editor.")
            
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please ensure all coordinates and numeric fields are filled properly.")
