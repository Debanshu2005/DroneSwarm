from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QComboBox, QGroupBox, QFrame, QTextEdit, QTabWidget
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QColor, QPalette

from GroundStation.ui.telemetry_panel import TelemetryPanel
from GroundStation.ui.mission_system_ui import CompleteMissionSystemUI
from GroundStation.ui.multi_drone_ui import SwarmControlPanel
from GroundStation.ui.diagnostics_ui import DiagnosticsPanel
from GroundStation.ui.network_graph import NetworkGraphWidget
from GroundStation.ui.settings_panel import SettingsPanel
from GroundStation.ui.keyboard_controls import KeyboardController
from GroundStation.ui.flight_control_panel import FlightControlPanel
from GroundStation.ui.map_panel import MapPanel
from GroundStation.ui.gps_mission_planner import GPSMissionPlanner

# A signal emitter bridge because we are running with asyncio (qasync)
class UIEvents(QObject):
    drone_discovered = Signal(str)
    drone_disconnected = Signal(str)
    heartbeat_updated = Signal(str, str)
    telemetry_updated = Signal(str, object)  # id, TelemetryData
    status_updated = Signal(str, str)
    error_received = Signal(str, str)

class GSMainWindow(QMainWindow):
    def __init__(self, network_manager):
        super().__init__()
        self.network_manager = network_manager
        self.events = UIEvents()
        self.active_drone_id = None
        
        # Defer Keyboard Controller initialization until after UI is built
        self.keyboard_controller = None
        
        self.init_ui()
        self.apply_dark_theme()
        
        # Connect signals
        self.events.drone_discovered.connect(self._on_drone_discovered_ui)
        self.events.drone_disconnected.connect(self._on_drone_disconnected_ui)
        self.events.heartbeat_updated.connect(self._on_heartbeat_updated_ui)
        self.events.telemetry_updated.connect(self._on_telemetry_updated_ui)
        self.events.status_updated.connect(self._on_status_updated_ui)
        self.events.error_received.connect(self._on_error_received_ui)
        
        # Hook network manager to signals
        self.network_manager.on_drone_discovered = self.events.drone_discovered.emit
        self.network_manager.on_drone_disconnected = self.events.drone_disconnected.emit
        self.network_manager.on_heartbeat_updated = self.events.heartbeat_updated.emit
        self.network_manager.on_telemetry_updated = self.events.telemetry_updated.emit
        self.network_manager.on_status_updated = self.events.status_updated.emit
        self.network_manager.on_error_received = self.events.error_received.emit

    def init_ui(self):
        self.setWindowTitle("SwarmOS GroundStation")
        self.resize(1600, 900)
        self.setMinimumSize(1280, 720)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        main_layout = QVBoxLayout(main_widget)
        
        # Top Bar: Connection & Selection
        top_bar = QHBoxLayout()
        
        self.status_label = QLabel("Status: IDLE")
        self.status_label.setStyleSheet("color: blue; font-weight: bold;")
        
        self.conn_label = QLabel("Connection: DISCONNECTED")
        self.conn_label.setStyleSheet("color: red; font-weight: bold;")
        
        self.telemetry_status_label = QLabel("Telemetry: UNKNOWN")
        self.telemetry_status_label.setStyleSheet("color: gray; font-weight: bold;")
        
        top_bar.addWidget(QLabel("Select Drone:"))
        self.drone_combo = QComboBox()
        self.drone_combo.setMinimumWidth(200)
        self.drone_combo.addItem("ALL")
        self.drone_combo.currentTextChanged.connect(self._on_drone_selected)
        top_bar.addWidget(self.drone_combo)
        
        top_bar.addStretch()
        top_bar.addWidget(self.conn_label)
        top_bar.addSpacing(20)
        top_bar.addWidget(self.telemetry_status_label)
        top_bar.addSpacing(20)
        top_bar.addWidget(self.status_label)
        
        main_layout.addLayout(top_bar)
        
        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)
        
        # Main Content Area (Tabbed)
        self.tabs = QTabWidget()
        
        # 0. Flight Control Tab
        self.keyboard_controller = KeyboardController(
            self.network_manager, 
            lambda: self.active_drone_id,
            self._is_keyboard_active
        )
        
        # Wire keyboard to log exactly the same as mouse clicks
        self.keyboard_controller.on_command_dispatched = self._on_kb_command
        
        self.flight_control_panel = FlightControlPanel(
            self.network_manager, 
            lambda: self.active_drone_id,
            self.keyboard_controller
        )
        self.tabs.addTab(self.flight_control_panel, "Flight Control")
        
        # 1. Mission & Telemetry Tab
        mission_tab = QWidget()
        mission_layout = QHBoxLayout(mission_tab)
        
        self.telemetry_panel_mission = TelemetryPanel()
        self.mission_controller = CompleteMissionSystemUI(self.network_manager, lambda: self.active_drone_id)
        
        mission_layout.addWidget(self.mission_controller, stretch=2)
        mission_layout.addWidget(self.telemetry_panel_mission, stretch=1)
        self.tabs.addTab(mission_tab, "Mission Controller")
        
        # 1.5 GPS Mission Planner & Map Tab
        planner_tab = QWidget()
        planner_layout = QHBoxLayout(planner_tab)
        
        self.map_panel = MapPanel(self.network_manager)
        self.gps_planner = GPSMissionPlanner(self.map_panel, self.mission_controller, self.network_manager)
        
        # Connect mission editor to map panel for live plotting
        self.mission_controller.editor.editor.textChanged.connect(
            lambda: self.map_panel.plot_mission(self.mission_controller.editor.get_mission_json())
        )
        
        planner_layout.addWidget(self.gps_planner, stretch=1)
        planner_layout.addWidget(self.map_panel, stretch=2)
        self.tabs.addTab(planner_tab, "GPS Mission & Map")
        
        # 2. Swarm Dashboard Tab
        self.swarm_dashboard = SwarmControlPanel(self.network_manager)
        self.tabs.addTab(self.swarm_dashboard, "Swarm Dashboard")
        
        # 3. Network Graph Tab
        self.network_graph = NetworkGraphWidget(self.network_manager)
        self.tabs.addTab(self.network_graph, "Network Graph")
        
        # 4. Settings Tab
        self.settings_panel = SettingsPanel(self.network_manager)
        self.tabs.addTab(self.settings_panel, "Settings")
        
        # 5. Diagnostics Tab
        self.diagnostics_panel = DiagnosticsPanel(self.network_manager)
        self.tabs.addTab(self.diagnostics_panel, "Diagnostics")
        
        main_layout.addWidget(self.tabs)
        
        # Switch focus logic for keyboard
        self.tabs.currentChanged.connect(self._on_tab_changed)
        
        # Log Viewer
        self.log_box = QGroupBox("System Logs")
        log_layout = QVBoxLayout(self.log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background-color: #111111; color: #CCCCCC; font-family: monospace;")
        self.log_view.setMaximumHeight(150)
        log_layout.addWidget(self.log_view)
        main_layout.addWidget(self.log_box)
        
        self._setup_ui_logging()
        
        # Install event filter globally ONLY after all UI is built
        QApplication.instance().installEventFilter(self.keyboard_controller)

    def _is_keyboard_active(self):
        if not hasattr(self, 'tabs') or self.tabs is None:
            return False
        if not hasattr(self, 'flight_control_panel') or self.flight_control_panel is None:
            return False
        if self.tabs.currentWidget() != self.flight_control_panel:
            return False
            
        # Keyboard lockout safety gate
        if self.active_drone_id and self.active_drone_id != "ALL":
            if self.active_drone_id in self.network_manager.drones:
                status = self.network_manager.drones[self.active_drone_id].status
                if status and "LOST" in status.upper():
                    return False
        return True

    def _on_tab_changed(self, index):
        if self.tabs.tabText(index) == "Flight Control":
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.flight_control_panel.setFocus)
            
    def showEvent(self, event):
        super().showEvent(event)
        if self.tabs.currentWidget() == self.flight_control_panel:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.flight_control_panel.setFocus)

    def _setup_ui_logging(self):
        import logging
        class UIHandler(logging.Handler):
            def __init__(self, log_view):
                super().__init__()
                self.log_view = log_view
            def emit(self, record):
                msg = self.format(record)
                self.log_view.append(msg)
                
        ui_handler = UIHandler(self.log_view)
        ui_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(ui_handler)

    def _append_system_log(self, msg: str):
        import logging
        logging.info(msg)

    def apply_dark_theme(self):
        app = QApplication.instance()
        
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(230, 230, 230))
        palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
        palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(230, 230, 230))
        palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        
        app.setPalette(palette)
        app.setStyleSheet("QToolTip { color: #ffffff; background-color: #2a82da; border: 1px solid white; }")

    def _on_drone_selected(self, drone_id: str):
        self.active_drone_id = drone_id
        if drone_id and drone_id != "ALL":
            self.status_label.setText(f"Status: WAITING for {drone_id} Heartbeat...")
        else:
            self.status_label.setText("Status: BROADCAST (ALL)")
            
        # Guarantee keyboard focus shifts back to the active panel after combobox click
        if hasattr(self, 'tabs') and self.tabs.currentWidget() == self.flight_control_panel:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.flight_control_panel.setFocus)
            
        # Instantly refresh UI with cached data
        self.mission_controller.refresh_drone(drone_id, self.network_manager)
        if drone_id and drone_id != "ALL" and drone_id in self.network_manager.drones:
            drone_info = self.network_manager.drones[drone_id]
            if drone_info.telemetry:
                self._on_telemetry_updated_ui(drone_id, drone_info.telemetry)
            if drone_info.status:
                self._on_heartbeat_updated_ui(drone_id, drone_info.status)

    def _on_drone_discovered_ui(self, drone_id: str):
        for i in range(self.drone_combo.count()):
            if self.drone_combo.itemText(i) == drone_id:
                return
        
        self.drone_combo.addItem(drone_id)
        if self.drone_combo.count() == 1:
            self.drone_combo.setCurrentIndex(0)
            
    def _on_drone_disconnected_ui(self, drone_id: str):
        for i in range(self.drone_combo.count()):
            if self.drone_combo.itemText(i) == drone_id:
                self.drone_combo.removeItem(i)
                break
                
        if self.active_drone_id == drone_id:
            self.status_label.setText("Status: DISCONNECTED")
            self.drone_combo.setCurrentIndex(0) 
            
        self.swarm_dashboard.drone_list.remove_drone(drone_id)
        
        items = self.swarm_dashboard.selection_mgr.list_widget.findItems(drone_id, Qt.MatchFlag.MatchExactly)
        for item in items:
            self.swarm_dashboard.selection_mgr.list_widget.takeItem(self.swarm_dashboard.selection_mgr.list_widget.row(item))

    def _on_heartbeat_updated_ui(self, drone_id: str, status: str):
        if self.active_drone_id == drone_id or self.active_drone_id == "ALL":
            self.conn_label.setText(f"Connection: {status.upper()} ({drone_id})")
            if "LOST" in status.upper():
                self.conn_label.setStyleSheet("color: red; font-weight: bold;")
            else:
                self.conn_label.setStyleSheet("color: green; font-weight: bold;")
                
            try:
                self.telemetry_panel_mission.update_heartbeat(status)
                self.flight_control_panel.telemetry_panel.update_heartbeat(status)
            except Exception as e:
                import logging
                logging.error(f"UI Error updating heartbeat panels: {e}")
                
    def _on_telemetry_updated_ui(self, drone_id: str, telemetry_data):
        import time
        if self.active_drone_id == drone_id or self.active_drone_id == "ALL":
            if getattr(telemetry_data, 'timestamp', None) is not None:
                if (time.time() - telemetry_data.timestamp) > 2.0:
                    self.telemetry_status_label.setText("Telemetry: STALE")
                    self.telemetry_status_label.setStyleSheet("color: red; font-weight: bold;")
                else:
                    self.telemetry_status_label.setText("Telemetry: LIVE")
                    self.telemetry_status_label.setStyleSheet("color: green; font-weight: bold;")
            else:
                self.telemetry_status_label.setText("Telemetry: STALE (No TS)")
                self.telemetry_status_label.setStyleSheet("color: red; font-weight: bold;")

        import logging
        if self.active_drone_id == "ALL" or drone_id == self.active_drone_id:
            try:
                self.telemetry_panel_mission.update_telemetry(telemetry_data)
            except Exception as e:
                logging.error(f"UI Error in telemetry_panel_mission: {e}")
                
            try:
                self.flight_control_panel.telemetry_panel.update_telemetry(telemetry_data)
            except Exception as e:
                logging.error(f"UI Error in flight_control_panel telemetry: {e}")
            
            try:
                if telemetry_data.latitude is not None and telemetry_data.longitude is not None:
                    self.map_panel.update_drone_position(drone_id, telemetry_data.latitude, telemetry_data.longitude)
            except Exception as e:
                logging.error(f"UI Error in map_panel: {e}")
        
        alt_str = f"{telemetry_data.altitude:.1f}m" if telemetry_data.altitude is not None else "N/A"
        batt_str = f"{telemetry_data.battery_level:.0f}%" if telemetry_data.battery_level is not None else "N/A"
        
        try:
            existing_items = self.swarm_dashboard.selection_mgr.list_widget.findItems(drone_id, Qt.MatchFlag.MatchExactly)
            if not existing_items:
                self.swarm_dashboard.selection_mgr.list_widget.addItem(drone_id)
        except Exception as e:
            logging.error(f"UI Error in swarm_dashboard selection_mgr: {e}")
        
        try:
            self.swarm_dashboard.status_table.update_drone(
                drone_id=drone_id,
                status=telemetry_data.mission_state or "unknown",
                batt=batt_str,
                alt=alt_str,
                mode=telemetry_data.flight_mode or "unknown"
            )
        except Exception as e:
            logging.error(f"UI Error in swarm_dashboard status_table: {e}")
        
    def _on_kb_command(self, drone_id: str, action):
        t_str = drone_id if drone_id and drone_id != "ALL" else "ALL"
        msg = f"{t_str} -> {action.name}"
        self.flight_control_panel._append_log(msg)
        self._append_system_log(f"INFO: Sending command {action.name} to {t_str}")

    def _on_status_updated_ui(self, drone_id: str, msg: str):
        self.flight_control_panel._append_log(f"✓ {msg}")
        self._append_system_log(f"STATUS ({drone_id}): {msg}")
        self.status_label.setText(f"Status: {msg.upper()} ({drone_id})")

    def _on_error_received_ui(self, drone_id: str, error_msg: str):
        self.flight_control_panel._append_log(f"Command rejected: {error_msg}")
        self._append_system_log(f"ERROR ({drone_id}): {error_msg}")
        self.status_label.setText(f"Status: FAILED - {error_msg} ({drone_id})")

    def closeEvent(self, event):
        self.keyboard_controller.stop()
        self.diagnostics_panel.stop()
        super().closeEvent(event)
