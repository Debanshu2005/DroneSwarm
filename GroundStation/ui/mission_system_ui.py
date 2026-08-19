import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QTextEdit, QFileDialog, QListWidget, QProgressBar, QGroupBox, QComboBox
)
from GroundStation.shared.protocol.messages import (
    MissionUploadMessage, MissionPauseMessage, MissionResumeMessage, MissionAbortMessage,
    MissionStartMessage, MissionStopMessage, MissionDeleteMessage, MissionDuplicateMessage,
    MissionClearMessage
)
from GroundStation.shared.utils.logger import setup_logger

logger = setup_logger("GroundStationMissionUI")

class MissionValidatorUI:
    @staticmethod
    def validate(mission_json: str) -> bool:
        try:
            data = json.loads(mission_json)
            if "waypoints" not in data or not isinstance(data["waypoints"], list):
                return False
            return True
        except Exception:
            return False

class MissionSerialization:
    @staticmethod
    def serialize(waypoints_list: list) -> str:
        return json.dumps({"waypoints": waypoints_list}, indent=4)
        
    @staticmethod
    def deserialize(json_str: str) -> dict:
        return json.loads(json_str)

class MissionEditor(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        
        # Template selection
        template_layout = QHBoxLayout()
        self.template_combo = QComboBox()
        templates = [
            "Takeoff", "Land", "Hover", "Square", "Circle", "Triangle", 
            "Rectangle", "Figure Eight", "Spiral", "Patrol", "Grid Search", 
            "Lawn Mower", "Orbit", "Return Home", "Waypoint Mission", 
            "Area Survey", "Inspection", "Custom Mission"
        ]
        self.template_combo.addItems(templates)
        self.btn_generate = QPushButton("Generate JSON from template")
        self.btn_generate.clicked.connect(self.generate_template)
        template_layout.addWidget(QLabel("Select Template:"))
        template_layout.addWidget(self.template_combo)
        template_layout.addWidget(self.btn_generate)
        
        layout.addLayout(template_layout)
        
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Enter Mission JSON here...")
        from PySide6.QtWidgets import QSizePolicy
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.editor.setMinimumHeight(150)
        layout.addWidget(QLabel("Mission Editor"))
        layout.addWidget(self.editor)
        self.setLayout(layout)

    def generate_template(self):
        template = self.template_combo.currentText()
        waypoints = []
        if template == "Takeoff":
            waypoints = [{"latitude": 0.0, "longitude": 0.0, "altitude": 10.0, "speed": 5.0, "delay": 0.0}]
        elif template == "Land":
            waypoints = [{"latitude": 0.0, "longitude": 0.0, "altitude": 0.0, "speed": 2.0, "delay": 0.0}]
        elif template == "Hover":
            waypoints = [{"latitude": 0.0, "longitude": 0.0, "altitude": 10.0, "speed": 0.0, "delay": 10.0}]
        elif template == "Square":
            waypoints = [
                {"latitude": 0.0001, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0001, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0001, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0001, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0001, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0}
            ]
        elif template == "Circle":
            # Rough hexagon for circle
            waypoints = [
                {"latitude": 0.0002, "longitude": 0.0, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0001, "longitude": 0.00017, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0001, "longitude": 0.00017, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0002, "longitude": 0.0, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0001, "longitude": -0.00017, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0001, "longitude": -0.00017, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0002, "longitude": 0.0, "altitude": 10.0, "speed": 5.0, "delay": 0.0}
            ]
        elif template == "Figure 8":
            waypoints = [
                {"latitude": 0.0002, "longitude": 0.0002, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0002, "longitude": -0.0002, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0002, "longitude": 0.0002, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0002, "longitude": -0.0002, "altitude": 10.0, "speed": 5.0, "delay": 0.0}
            ]
        elif template == "Triangle":
            waypoints = [
                {"latitude": 0.0001, "longitude": 0.0, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0001, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0001, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0}
            ]
        elif template == "Rectangle":
            waypoints = [
                {"latitude": 0.0002, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0002, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0002, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": -0.0002, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0}
            ]
        elif template in ["Spiral", "Orbit"]:
            waypoints = [{"latitude": 0.0001, "longitude": 0.0, "altitude": 15.0, "speed": 4.0, "delay": 0.0}]
        elif template in ["Lawn Mower", "Area Survey", "Grid Search", "Area Scan"]:
            waypoints = [
                {"latitude": 0.0001, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0001, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0002, "longitude": -0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0},
                {"latitude": 0.0002, "longitude": 0.0001, "altitude": 10.0, "speed": 5.0, "delay": 0.0}
            ]
        elif template == "Patrol":
            waypoints = [
                {"latitude": 0.0005, "longitude": 0.0000, "altitude": 12.0, "speed": 6.0, "delay": 1.0},
                {"latitude": 0.0000, "longitude": 0.0005, "altitude": 12.0, "speed": 6.0, "delay": 1.0},
                {"latitude": -0.0005, "longitude": 0.0000, "altitude": 12.0, "speed": 6.0, "delay": 1.0},
                {"latitude": 0.0000, "longitude": -0.0005, "altitude": 12.0, "speed": 6.0, "delay": 1.0}
            ]
        elif template == "Return Home":
            waypoints = [{"latitude": 0.0, "longitude": 0.0, "altitude": 10.0, "speed": 10.0, "delay": 0.0}]
        else:
            waypoints = [{"latitude": 0.0, "longitude": 0.0, "altitude": 10.0, "speed": 5.0, "delay": 0.0}]
            
        sample_json = json.dumps({
            "name": template,
            "waypoints": waypoints
        }, indent=4)
        self.editor.setPlainText(sample_json)

    def get_mission_json(self) -> str:
        return self.editor.toPlainText()

    def set_mission_json(self, data: str):
        self.editor.setPlainText(data)
        
    def clear(self):
        self.editor.clear()

class MissionFileBrowser(QWidget):
    def __init__(self, editor: MissionEditor):
        super().__init__()
        self.editor = editor
        layout = QHBoxLayout()
        self.btn_load = QPushButton("Load Mission File")
        self.btn_load.clicked.connect(self.load_file)
        self.btn_save = QPushButton("Save Mission File")
        self.btn_save.clicked.connect(self.save_file)
        layout.addWidget(self.btn_load)
        layout.addWidget(self.btn_save)
        self.setLayout(layout)

    def load_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Mission JSON", "", "JSON Files (*.json)")
        if file_path:
            with open(file_path, 'r') as f:
                self.editor.set_mission_json(f.read())

    def save_file(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Mission JSON", "", "JSON Files (*.json)")
        if file_path:
            with open(file_path, 'w') as f:
                f.write(self.editor.get_mission_json())

class MissionProgressPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.lbl_status = QLabel("Progress: 0/0 Waypoints | 0%")
        self.lbl_metrics = QLabel("Remaining Distance: 0m | ETA: 0s")
        self.lbl_metrics.setStyleSheet("color: #00ffff; font-weight: bold;")
        
        self.lbl_eta = QLabel("ETA: --:--")
        self.lbl_dist = QLabel("Active Wp: --")
        self.lbl_remaining = QLabel("Remaining: --")
        self.lbl_completed = QLabel("Completed: --")
        
        for lbl in (self.lbl_eta, self.lbl_dist, self.lbl_remaining, self.lbl_completed):
            lbl.setStyleSheet("font-weight: bold; color: yellow;")
        
        info_layout = QHBoxLayout()
        info_layout.addWidget(self.lbl_dist)
        info_layout.addWidget(self.lbl_completed)
        info_layout.addWidget(self.lbl_remaining)
        info_layout.addWidget(self.lbl_eta)
        
        layout.addWidget(QLabel("Mission Progress"))
        layout.addWidget(self.progress_bar)
        layout.addLayout(info_layout)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.lbl_metrics)
        self.setLayout(layout)

    def update_progress(self, current: int, total: int, percent: float):
        self.progress_bar.setValue(int(percent))
        
        if total > 0 and current > 0:
            rem = max(0, total - current)
            self.lbl_dist.setText(f"Active Wp: {current}")
            self.lbl_completed.setText(f"Completed: {current-1 if current>0 else 0}")
            self.lbl_remaining.setText(f"Remaining: {rem}")
            self.lbl_status.setText(f"Progress: {current}/{total} Waypoints | {percent:.1f}%")
            
            # Simple estimation: 10m per waypoint, 5m/s avg
            dist = rem * 10.0
            eta = dist / 5.0
            self.lbl_eta.setText(f"ETA: ~{eta:.1f}s")
            self.lbl_metrics.setText(f"Remaining Distance: ~{dist:.1f}m | ETA: ~{eta:.1f}s")

class MissionStatusPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.lbl_state = QLabel("State: IDLE")
        self.lbl_mission_id = QLabel("Active Mission: None")
        layout.addWidget(QLabel("Mission Status"))
        layout.addWidget(self.lbl_state)
        layout.addWidget(self.lbl_mission_id)
        self.setLayout(layout)

    def update_status(self, state: str, mission_id: str):
        self.lbl_state.setText(f"State: {state}")
        self.lbl_mission_id.setText(f"Active Mission: {mission_id}")

class MissionHistory(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.history_list = QListWidget()
        from PySide6.QtWidgets import QSizePolicy
        self.history_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.history_list.setMinimumHeight(100)
        layout.addWidget(QLabel("Mission History"))
        layout.addWidget(self.history_list)
        self.setLayout(layout)

    def add_log(self, log_msg: str):
        self.history_list.addItem(log_msg)

class MissionTransferProtocol:
    def __init__(self, network_manager):
        self.network = network_manager

    def upload_mission(self, target_id: str, mission_id: str, mission_json: str):
        msg = MissionUploadMessage(
            sender_id="GroundStation",
            timestamp=0.0,
            mission_id=mission_id,
            mission_json=mission_json
        )
        target = target_id if target_id and target_id != "ALL" else None
        
        import asyncio
        async def _dispatch():
            await self.network.send_mission_message(target, msg)
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            task = asyncio.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
        except RuntimeError:
            pass
        logger.info(f"Uploaded mission {mission_id} to {target_id or 'ALL'} via MissionTransferProtocol")
        
    def control_mission(self, target_id: str, mission_id: str, action: str):
        if action == "START":
            msg = MissionStartMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "PAUSE":
            msg = MissionPauseMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "RESUME":
            msg = MissionResumeMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "STOP":
            msg = MissionStopMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "ABORT":
            msg = MissionAbortMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "DELETE":
            msg = MissionDeleteMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "DUPLICATE":
            msg = MissionDuplicateMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        elif action == "CLEAR":
            msg = MissionClearMessage(sender_id="GroundStation", timestamp=0.0, mission_id=mission_id)
        else:
            return
            
        target = target_id if target_id and target_id != "ALL" else None
        
        import asyncio
        async def _dispatch():
            await self.network.send_mission_message(target, msg)
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            task = asyncio.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
        except RuntimeError:
            pass
        logger.info(f"Sent {action} for mission {mission_id} to {target_id or 'ALL'}")

class MissionUploadUI(QWidget):
    def __init__(self, transfer_protocol: MissionTransferProtocol, editor: MissionEditor, history: MissionHistory, active_drone_cb=None):
        super().__init__()
        self.transfer = transfer_protocol
        self.editor = editor
        self.history = history
        self.active_drone_cb = active_drone_cb
        self.active_mission_id = "MISSION_001"
        
        layout = QVBoxLayout()
        
        btn_layout1 = QHBoxLayout()
        self.btn_upload = QPushButton("Upload Mission")
        self.btn_upload.clicked.connect(self.handle_upload)
        
        self.btn_start = QPushButton("Start")
        self.btn_pause = QPushButton("Pause")
        self.btn_resume = QPushButton("Resume")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet("background-color: #dc3545; color: white;")
        self.btn_stop.clicked.connect(lambda: self.handle_control("ABORT"))
        self.btn_abort = QPushButton("Abort")
        self.btn_abort.setStyleSheet("background-color: #dc3545; color: white;")
        
        self.btn_duplicate = QPushButton("Duplicate")
        self.btn_delete = QPushButton("Delete")
        self.btn_clear = QPushButton("Clear")
        
        btn_layout1.addWidget(self.btn_upload)
        btn_layout1.addWidget(self.btn_start)
        btn_layout1.addWidget(self.btn_pause)
        btn_layout1.addWidget(self.btn_resume)
        
        btn_layout2 = QHBoxLayout()
        btn_layout2.addWidget(self.btn_stop)
        btn_layout2.addWidget(self.btn_abort)
        btn_layout2.addWidget(self.btn_duplicate)
        btn_layout2.addWidget(self.btn_delete)
        btn_layout2.addWidget(self.btn_clear)
        
        self.btn_start.clicked.connect(lambda: self.handle_control("START"))
        self.btn_pause.clicked.connect(lambda: self.handle_control("PAUSE"))
        self.btn_resume.clicked.connect(lambda: self.handle_control("RESUME"))
        self.btn_stop.clicked.connect(lambda: self.handle_control("STOP"))
        self.btn_abort.clicked.connect(lambda: self.handle_control("ABORT"))
        self.btn_clear.clicked.connect(lambda: self.handle_control("CLEAR"))
        self.btn_delete.clicked.connect(lambda: self.handle_control("DELETE"))
        
        # Duplicate triggers local edit clear/duplicate AND backend message
        def handle_duplicate():
            self.editor.set_mission_json(self.editor.get_mission_json())
            self.handle_control("DUPLICATE")
        self.btn_duplicate.clicked.connect(handle_duplicate)
        
        # Local UI clear helper for clear button
        self.btn_clear.clicked.connect(self.editor.clear)
        
        # ARM Test feature
        btn_layout3 = QHBoxLayout()
        self.btn_arm_test = QPushButton("ARM Test (Pre-Mission)")
        self.btn_arm_test.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        self.btn_arm_test.clicked.connect(self.handle_arm_test)
        btn_layout3.addWidget(self.btn_arm_test)
        
        layout.addLayout(btn_layout1)
        layout.addLayout(btn_layout2)
        layout.addLayout(btn_layout3)
        self.setLayout(layout)
        
        self.arm_test_in_progress = False
        self.arm_test_target = None
        self.arm_test_timestamp = 0.0

    def handle_arm_test(self):
        target = self.active_drone_cb() if self.active_drone_cb else None
        self.arm_test_target = target
        self.arm_test_in_progress = True
        import time
        self.arm_test_timestamp = time.time()
        
        tgt_label = target if target and target != "ALL" else "ALL"
        self.history.add_log(f"Mission ARM test SENT to {tgt_label}.")
        
        import asyncio
        from GroundStation.shared.protocol.messages import CommandAction
        
        async def _dispatch():
            await self.transfer.network.send_command(target, CommandAction.ARM)
        
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            task = asyncio.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
        except RuntimeError:
            pass

    def on_telemetry(self, sender_id: str, telemetry):
        if not self.arm_test_in_progress:
            return
            
        active = self.arm_test_target if self.arm_test_target else "ALL"
        if active == "ALL" or active == sender_id:
            import time
            if getattr(telemetry, 'armed_state', None) == "ARMED":
                self.history.add_log(f"Mission ARM test successful: Pixhawk confirmed ARMED on {sender_id}.")
                self.arm_test_in_progress = False
            elif time.time() - self.arm_test_timestamp > 5.0:
                self.history.add_log(f"Mission ARM test FAILED: Telemetry timeout waiting for ARMED on {sender_id}.")
                self.arm_test_in_progress = False

    def on_error(self, sender_id: str, error_msg: str):
        if not self.arm_test_in_progress:
            return
            
        active = self.arm_test_target if self.arm_test_target else "ALL"
        if active == "ALL" or active == sender_id:
            self.history.add_log(f"Mission ARM test failed: {error_msg}")
            self.arm_test_in_progress = False

    def handle_control(self, action: str):
        target = self.active_drone_cb() if self.active_drone_cb else None
        self.transfer.control_mission(target, self.active_mission_id, action)
        tgt_label = target if target and target != "ALL" else "ALL"
        self.history.add_log(f"Mission {action} requested for {tgt_label}.")

    def handle_upload(self):
        mission_json = self.editor.get_mission_json()
        target = self.active_drone_cb() if self.active_drone_cb else None
        tgt_label = target if target and target != "ALL" else "ALL"
        
        if MissionValidatorUI.validate(mission_json):
            self.transfer.upload_mission(target, self.active_mission_id, mission_json)
            self.history.add_log(f"Uploaded {self.active_mission_id} successfully to {tgt_label}.")
        else:
            self.history.add_log("Upload failed: Invalid JSON format.")
            logger.error("Invalid mission JSON. Upload aborted.")

class CompleteMissionSystemUI(QWidget):
    def __init__(self, network_manager=None, active_drone_cb=None):
        super().__init__()
        layout = QVBoxLayout()
        self.active_drone_cb = active_drone_cb
        
        # Initialize sub-components
        self.editor = MissionEditor()
        self.browser = MissionFileBrowser(self.editor)
        self.history = MissionHistory()
        self.progress = MissionProgressPanel()
        self.status = MissionStatusPanel()
        
        self.transfer_protocol = MissionTransferProtocol(network_manager)
        self.upload_ui = MissionUploadUI(self.transfer_protocol, self.editor, self.history, self.active_drone_cb)
        
        # Connect to network updates if available
        if network_manager:
            network_manager.on_mission_progress = self._handle_progress
            network_manager.on_mission_status = self._handle_status
            
            # Hook into the existing broadcasted telemetry and errors safely
            # Since main_window also maps to these, we should ideally chain them, 
            # but GSNetworkManager overwrites them if reassigned directly.
            # To avoid breaking main_window, CompleteMissionSystemUI does NOT reassign the callbacks on GSNetworkManager.
            # Instead, we will be fed by main_window, or we inject a proxy.
            
            # Easiest safe injection without breaking GSMainWindow:
            original_on_tel = network_manager.on_telemetry_updated
            original_on_err = network_manager.on_error_received
            
            def hooked_telemetry(drone_id, tel):
                if original_on_tel: original_on_tel(drone_id, tel)
                self.upload_ui.on_telemetry(drone_id, tel)
                
            def hooked_error(drone_id, err):
                if original_on_err: original_on_err(drone_id, err)
                self.upload_ui.on_error(drone_id, err)
                
            network_manager.on_telemetry_updated = hooked_telemetry
            network_manager.on_error_received = hooked_error
            
        # Assemble UI
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.browser)
        layout.addWidget(self.editor)
        layout.addWidget(self.upload_ui)
        layout.addWidget(self.history)
        layout.addStretch()
        
        self.setLayout(layout)
        
    def _handle_progress(self, sender_id: str, current: int, total: int, percent: float):
        # We only update UI if the telemetry is for the currently selected drone, or ALL.
        active = self.active_drone_cb() if self.active_drone_cb else "ALL"
        if active == "ALL" or active == sender_id:
            self.progress.update_progress(current, total, percent)
        
    def _handle_status(self, state: str, sender_id: str):
        active = self.active_drone_cb() if self.active_drone_cb else "ALL"
        if active == "ALL" or active == sender_id:
            self.status.update_status(state, sender_id)

    def refresh_drone(self, drone_id: str, network_manager):
        if not drone_id or drone_id == "ALL":
            self.status.update_status("IDLE", "None")
            self.progress.update_progress(0, 0, 0.0)
            return
            
        drone_info = network_manager.drones.get(drone_id)
        if drone_info and drone_info.telemetry:
            # We assume mission status might be bundled in telemetry or we just use default
            state = getattr(drone_info.telemetry, 'mission_state', 'IDLE')
            self.status.update_status(state, drone_id)
            # Without cached progress, reset to 0 to avoid stale data
            self.progress.update_progress(0, 0, 0.0)
        else:
            self.status.update_status("IDLE", "None")
            self.progress.update_progress(0, 0, 0.0)
