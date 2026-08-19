from PySide6.QtWidgets import QWidget, QFormLayout, QLabel
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

class TelemetryPanel(QWidget):
    def __init__(self):
        super().__init__()
        
        self.layout = QFormLayout(self)
        self.layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        
        font = QFont()
        font.setBold(True)
        
        self.lbl_battery = QLabel("-- % ( -- V)")
        self.lbl_altitude = QLabel("-- m")
        self.lbl_gps = QLabel("--, --")
        self.lbl_velocity = QLabel("--, --, -- m/s")
        self.lbl_heading = QLabel("-- °")
        self.lbl_attitude = QLabel("R: --° P: --° Y: --°")
        self.lbl_mode = QLabel("UNKNOWN")
        self.lbl_armed = QLabel("UNKNOWN")
        self.lbl_mission = QLabel("IDLE")
        self.lbl_heartbeat = QLabel("UNKNOWN")
        
        self.labels = [
            self.lbl_battery, self.lbl_altitude, self.lbl_gps, self.lbl_velocity, 
            self.lbl_heading, self.lbl_attitude, self.lbl_mode, self.lbl_armed, self.lbl_mission, self.lbl_heartbeat
        ]
        
        for lbl in self.labels:
            lbl.setFont(font)
            lbl.setStyleSheet("color: #00FF00;") # Green text for telemetry
            
        self.layout.addRow("Battery:", self.lbl_battery)
        self.layout.addRow("Altitude:", self.lbl_altitude)
        self.layout.addRow("GPS (Lat, Lon):", self.lbl_gps)
        self.layout.addRow("Velocity (X, Y, Z):", self.lbl_velocity)
        self.layout.addRow("Heading:", self.lbl_heading)
        self.layout.addRow("Attitude (R/P/Y):", self.lbl_attitude)
        self.layout.addRow("Flight Mode:", self.lbl_mode)
        self.layout.addRow("Armed State:", self.lbl_armed)
        self.layout.addRow("Mission State:", self.lbl_mission)
        self.layout.addRow("Heartbeat:", self.lbl_heartbeat)

    def update_heartbeat(self, status: str):
        self.lbl_heartbeat.setText(status.upper())
        if "LOST" in status.upper():
            self.lbl_heartbeat.setStyleSheet("color: red;")
        else:
            self.lbl_heartbeat.setStyleSheet("color: #00FF00;")

    def update_telemetry(self, telemetry_data):
        try:
            def fmt(val, template="{:.2f}"):
                return template.format(val) if val is not None else "--"
            
            batt = fmt(telemetry_data.battery_level, '{:.1f}')
            volt = fmt(getattr(telemetry_data, 'voltage', None), '{:.1f}')
            self.lbl_battery.setText(f"{batt} % ({volt} V)")
            
            if telemetry_data.battery_level is not None and telemetry_data.battery_level < 20:
                self.lbl_battery.setStyleSheet("color: red;")
            else:
                self.lbl_battery.setStyleSheet("color: #00FF00;")
                
            self.lbl_altitude.setText(f"{fmt(telemetry_data.altitude)} m")
            
            lat = fmt(telemetry_data.latitude, '{:.6f}')
            lon = fmt(telemetry_data.longitude, '{:.6f}')
            self.lbl_gps.setText(f"{lat}, {lon}")
            
            vx = fmt(telemetry_data.velocity_x)
            vy = fmt(telemetry_data.velocity_y)
            vz = fmt(telemetry_data.velocity_z)
            self.lbl_velocity.setText(f"{vx}, {vy}, {vz} m/s")
            
            # Pitch/Roll/Yaw and Heading integration
            if getattr(telemetry_data, 'heading', None) is not None:
                self.lbl_heading.setText(f"{fmt(telemetry_data.heading, '{:.1f}')}°")
            else:
                self.lbl_heading.setText("N/A")
                
            r = getattr(telemetry_data, 'roll', None)
            p = getattr(telemetry_data, 'pitch', None)
            y = getattr(telemetry_data, 'yaw', None)
            
            if r is not None and p is not None and y is not None:
                self.lbl_attitude.setText(f"R: {r:.1f}° P: {p:.1f}° Y: {y:.1f}°")
            else:
                self.lbl_attitude.setText("R: N/A P: N/A Y: N/A")
            
            mode = telemetry_data.flight_mode.upper() if telemetry_data.flight_mode else "UNKNOWN"
            self.lbl_mode.setText(mode)
            
            # Use Actual Armed State from Telemetry
            actual_armed = getattr(telemetry_data, "armed_state", None)
            if actual_armed:
                self.lbl_armed.setText(actual_armed)
            else:
                self.lbl_armed.setText("UNKNOWN")
            
            mission_st = getattr(telemetry_data, "mission_state", "IDLE")
            self.lbl_mission.setText(mission_st)
            
            # Stale Indication
            import time
            is_stale = False
            if getattr(telemetry_data, 'timestamp', None) is not None:
                if (time.time() - telemetry_data.timestamp) > 2.0:
                    is_stale = True
            else:
                is_stale = True
                
            if is_stale:
                for lbl in self.labels:
                    lbl.setStyleSheet("color: #777777;")
                    if "(STALE)" not in lbl.text():
                        lbl.setText(lbl.text() + " (STALE)")
            else:
                for lbl in self.labels:
                    if lbl == self.lbl_battery and telemetry_data.battery_level is not None and telemetry_data.battery_level < 20:
                        lbl.setStyleSheet("color: red;")
                    else:
                        lbl.setStyleSheet("color: #00FF00;")
        except Exception as e:
            # Prevent UI crashes from affecting the rest of the application
            import logging, traceback
            logging.error(f"Error updating telemetry UI: {e}\n{traceback.format_exc()}")
