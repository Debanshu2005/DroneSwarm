from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QGroupBox, QSlider, QGridLayout, QTextEdit, QComboBox
)
from PySide6.QtCore import Qt
from GroundStation.shared.protocol.messages import CommandAction
from GroundStation.ui.telemetry_panel import TelemetryPanel

class FlightControlPanel(QWidget):
    def __init__(self, network_manager, get_active_drone_cb, keyboard_controller):
        super().__init__()
        self.network = network_manager
        self.get_active_drone = get_active_drone_cb
        self.kb_ctrl = keyboard_controller
        self.cmd_history = []
        
        # REQUIRED for keyboard focus badge updates
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)

        # ----------------------------------------------------
        # Left Column: Manual Controls & Speed Sliders
        # ----------------------------------------------------
        left_layout = QVBoxLayout()
        
        # 1. Manual Flight Controls
        ctrl_box = QGroupBox("Manual Flight Controls")
        ctrl_layout = QGridLayout(ctrl_box)
        
        btn_arm = QPushButton("ARM")
        btn_disarm = QPushButton("DISARM")
        
        takeoff_widget = QWidget()
        takeoff_layout = QHBoxLayout(takeoff_widget)
        takeoff_layout.setContentsMargins(0, 0, 0, 0)
        btn_takeoff = QPushButton("TAKEOFF")
        self.combo_takeoff_alt = QComboBox()
        self.combo_takeoff_alt.addItems([f"{i} m" for i in range(1, 11)])
        self.combo_takeoff_alt.setCurrentText("5 m")
        self.combo_takeoff_alt.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        takeoff_layout.addWidget(btn_takeoff)
        takeoff_layout.addWidget(self.combo_takeoff_alt)
        
        btn_land = QPushButton("LAND")
        btn_rtl = QPushButton("RTL")
        btn_hover = QPushButton("HOVER")
        btn_estop = QPushButton("EMERGENCY STOP")
        
        btn_arm.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        btn_disarm.setStyleSheet("background-color: #c62828; color: white; font-weight: bold;")
        btn_estop.setStyleSheet("background-color: red; color: white; font-weight: bold; font-size: 14px;")
        
        # Make buttons not steal focus completely from the panel
        for btn in [btn_arm, btn_disarm, btn_takeoff, btn_land, btn_rtl, btn_hover, btn_estop]:
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        btn_arm.clicked.connect(lambda: self.send_action(CommandAction.ARM))
        btn_disarm.clicked.connect(lambda: self.send_action(CommandAction.DISARM))
        btn_takeoff.clicked.connect(self._on_takeoff)
        btn_land.clicked.connect(lambda: self.send_action(CommandAction.LAND))
        btn_rtl.clicked.connect(lambda: self.send_action(CommandAction.RTL))
        btn_hover.clicked.connect(lambda: self.send_action(CommandAction.HOVER))
        btn_estop.clicked.connect(self.send_emergency)

        ctrl_layout.addWidget(btn_arm, 0, 0)
        ctrl_layout.addWidget(btn_disarm, 0, 1)
        ctrl_layout.addWidget(takeoff_widget, 1, 0)
        ctrl_layout.addWidget(btn_land, 1, 1)
        ctrl_layout.addWidget(btn_rtl, 2, 0)
        ctrl_layout.addWidget(btn_hover, 2, 1)
        ctrl_layout.addWidget(btn_estop, 3, 0, 1, 2)
        
        # Command Feedback
        self.lbl_cmd_log = QLabel("Live Command Log:")
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(80)
        self.txt_log.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.txt_log.setStyleSheet("background-color: #111; color: #0f0; font-family: monospace;")
        
        # Flight Mode Selector
        mode_box = QGroupBox("Flight Mode")
        mode_layout = QGridLayout(mode_box)
        
        mode_layout.addWidget(QLabel("Mode:"), 0, 0)
        
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["STABILIZE", "GUIDED", "GUIDED_NOGPS", "LOITER", "RTL", "LAND"])
        self.combo_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        mode_layout.addWidget(self.combo_mode, 0, 1)
        
        btn_set_mode = QPushButton("SET MODE")
        btn_set_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_set_mode.clicked.connect(self._on_set_mode)
        mode_layout.addWidget(btn_set_mode, 0, 2)
        
        self.lbl_current_mode = QLabel("Current Mode: UNKNOWN")
        self.lbl_current_mode.setStyleSheet("font-weight: bold;")
        mode_layout.addWidget(self.lbl_current_mode, 1, 0, 1, 3)
        
        ctrl_layout.addWidget(mode_box, 4, 0, 1, 2)
        ctrl_layout.addWidget(self.lbl_cmd_log, 5, 0, 1, 2)
        ctrl_layout.addWidget(self.txt_log, 6, 0, 1, 2)
        
        left_layout.addWidget(ctrl_box)
        
        # 2. Movement Controls (D-Pad)
        move_box = QGroupBox("Movement (D-Pad)")
        move_grid = QGridLayout(move_box)
        
        def make_move_btn(text, k):
            btn = QPushButton(text)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.pressed.connect(lambda: self._sim_key_press(k))
            btn.released.connect(lambda: self._sim_key_release(k))
            return btn

        btn_fwd = make_move_btn("Forward (W)", Qt.Key.Key_W)
        btn_back = make_move_btn("Backward (S)", Qt.Key.Key_S)
        btn_left = make_move_btn("Left (A)", Qt.Key.Key_A)
        btn_right = make_move_btn("Right (D)", Qt.Key.Key_D)
        btn_yaw_l = make_move_btn("Yaw Left (Q)", Qt.Key.Key_Q)
        btn_yaw_r = make_move_btn("Yaw Right (E)", Qt.Key.Key_E)
        btn_up = make_move_btn("Up (R)", Qt.Key.Key_R)
        btn_down = make_move_btn("Down (F)", Qt.Key.Key_F)
        
        move_grid.addWidget(btn_yaw_l, 0, 0)
        move_grid.addWidget(btn_fwd, 0, 1)
        move_grid.addWidget(btn_yaw_r, 0, 2)
        move_grid.addWidget(btn_left, 1, 0)
        move_grid.addWidget(btn_back, 1, 1)
        move_grid.addWidget(btn_right, 1, 2)
        move_grid.addWidget(btn_up, 0, 3)
        move_grid.addWidget(btn_down, 1, 3)
        
        left_layout.addWidget(move_box)
        
        # 3. Speed Controls
        speed_box = QGroupBox("Speed Controls")
        speed_layout = QVBoxLayout(speed_box)
        
        self.move_speed = QSlider(Qt.Orientation.Horizontal)
        self.move_speed.setRange(1, 20)
        self.move_speed.setValue(int(self.kb_ctrl.speed))
        self.move_speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.move_speed.valueChanged.connect(self._update_kb_speed)
        speed_layout.addWidget(QLabel("Movement Speed (m/s)"))
        speed_layout.addWidget(self.move_speed)
        
        self.yaw_rate = QSlider(Qt.Orientation.Horizontal)
        self.yaw_rate.setRange(5, 90)
        self.yaw_rate.setValue(int(self.kb_ctrl.yaw_rate))
        self.yaw_rate.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.yaw_rate.valueChanged.connect(self._update_kb_yaw)
        speed_layout.addWidget(QLabel("Yaw Rate (deg/s)"))
        speed_layout.addWidget(self.yaw_rate)
        
        self.alt_step = QSlider(Qt.Orientation.Horizontal)
        self.alt_step.setRange(1, 10)
        self.alt_step.setValue(int(self.kb_ctrl.alt_step))
        self.alt_step.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.alt_step.valueChanged.connect(self._update_kb_alt)
        speed_layout.addWidget(QLabel("Altitude Step (m)"))
        speed_layout.addWidget(self.alt_step)
        
        left_layout.addWidget(speed_box)
        left_layout.addStretch()
        
        # ----------------------------------------------------
        # Right Column: Keyboard Help & Telemetry
        # ----------------------------------------------------
        right_layout = QVBoxLayout()
        
        kb_box = QGroupBox("Keyboard Status")
        kb_layout = QVBoxLayout(kb_box)
        
        self.lbl_kb_status = QLabel("STATUS: STANDBY (Click Here to Activate)")
        self.lbl_kb_status.setStyleSheet("color: #FFA500; font-weight: bold; font-size: 14px; padding: 5px;")
        self.lbl_kb_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kb_layout.addWidget(self.lbl_kb_status)
        
        kb_text = """
        <b>W / S</b> : Forward / Backward<br>
        <b>A / D</b> : Left / Right<br>
        <b>Q / E</b> : Yaw Left / Yaw Right<br>
        <b>R / F</b> : Up / Down<br>
        <b>Arrow Keys</b> : Fine Movement<br>
        <b>SPACE</b> : Hover<br>
        <b>ESC</b> : Emergency Stop
        """
        lbl_kb = QLabel(kb_text)
        lbl_kb.setTextFormat(Qt.TextFormat.RichText)
        kb_layout.addWidget(lbl_kb)
        right_layout.addWidget(kb_box)
        
        # Telemetry Panel
        tel_box = QGroupBox("Live Flight Status")
        tel_layout = QVBoxLayout(tel_box)
        self.telemetry_panel = TelemetryPanel()
        
        # Patch to read mode dynamically without modifying main_window
        orig_update = self.telemetry_panel.update_telemetry
        def intercept_telemetry(t_data):
            orig_update(t_data)
            mode_str = t_data.flight_mode if t_data.flight_mode else "UNKNOWN"
            self.lbl_current_mode.setText(f"Current Mode: {mode_str.upper()}")
        self.telemetry_panel.update_telemetry = intercept_telemetry
        
        tel_layout.addWidget(self.telemetry_panel)
        right_layout.addWidget(tel_box)
        
        right_layout.addStretch()
        
        main_layout.addLayout(left_layout, 1)
        main_layout.addLayout(right_layout, 1)

    def focusInEvent(self, event):
        self.lbl_kb_status.setText("STATUS: ACTIVE")
        self.lbl_kb_status.setStyleSheet("color: #FFFFFF; background-color: #28a745; font-weight: bold; font-size: 14px; padding: 5px;")
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.lbl_kb_status.setText("STATUS: STANDBY (Click Here to Activate)")
        self.lbl_kb_status.setStyleSheet("color: #FFA500; font-weight: bold; font-size: 14px; padding: 5px;")
        self.kb_ctrl.active_keys.clear()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def _sim_key_press(self, key):
        self.kb_ctrl.active_keys.add(key)
        self.kb_ctrl._was_moving = True
        
    def _sim_key_release(self, key):
        self.kb_ctrl.active_keys.discard(key)

    def _update_kb_speed(self, val):
        self.kb_ctrl.speed = float(val)
        self.setFocus()
        
    def _update_kb_yaw(self, val):
        self.kb_ctrl.yaw_rate = float(val)
        self.setFocus()
        
    def _update_kb_alt(self, val):
        self.kb_ctrl.alt_step = float(val)
        self.setFocus()

    def _append_log(self, text):
        import time
        ts = time.strftime("%H:%M:%S")
        self.cmd_history.insert(0, f"[{ts}] {text}")
        if len(self.cmd_history) > 10:
            self.cmd_history.pop()
        self.txt_log.setPlainText("\n".join(self.cmd_history))

    def _on_takeoff(self):
        alt_str = self.combo_takeoff_alt.currentText().replace(" m", "")
        params = {"altitude_m": float(alt_str)}
        self.send_action(CommandAction.TAKEOFF, params=params)

    def _on_set_mode(self):
        target = self.get_active_drone()
        if not target or target == "ALL":
            # The requirement is "send a targeted SET_MODE command to ONLY the selected drone. Do not send to all drones unless ALL is explicitly selected"
            if target != "ALL":
                self._append_log("Error: Select a specific drone to set mode.")
                return
                
        mode = self.combo_mode.currentText()
        t_str = target if target and target != "ALL" else "ALL"
        
        import asyncio
        async def _dispatch():
            await self.network.send_command(None if target=="ALL" else target, CommandAction.SET_MODE, params={"mode": mode})
            
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            loop = asyncio.get_event_loop()
            task = loop.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            
            # Log ONLY if dispatch scheduling succeeded
            self._append_log(f"COMMAND_DISPATCH_STARTED: SET_MODE {mode} to {t_str}")
        except Exception as e:
            self._append_log(f"COMMAND_DISPATCH_FAILED: SET_MODE - {e}")
            
        self.setFocus()

    def send_action(self, action: CommandAction, params=None):
        import asyncio
        target = self.get_active_drone()
        t_str = target if target and target != "ALL" else "ALL"
        
        if target == "ALL" or not target:
            target = None
            
        async def _dispatch():
            await self.network.send_command(target, action, params=params)
            
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            loop = asyncio.get_event_loop()
            task = loop.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            
            # Log ONLY if dispatch scheduling succeeded
            self._append_log(f"COMMAND_DISPATCH_STARTED: {action.name} to {t_str}")
        except Exception as e:
            self._append_log(f"COMMAND_DISPATCH_FAILED: {action.name} - {e}")
            
        self.setFocus()

    def send_emergency(self):
        import asyncio
        import time
        from GroundStation.shared.protocol.messages import EmergencyMessage
        
        target = self.get_active_drone()
        t_str = target if target and target != "ALL" else "ALL"
        
        msg = EmergencyMessage(sender_id=self.network.gs_id, timestamp=time.time())
        if target and target != "ALL":
            setattr(msg, 'target_id', target)
            
        async def _dispatch():
            await self.network.network.broadcast_message(msg)
            
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            loop = asyncio.get_event_loop()
            task = loop.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            
            # Log ONLY if dispatch scheduling succeeded
            self._append_log(f"COMMAND_DISPATCH_STARTED: EMERGENCY STOP to {t_str}")
        except Exception as e:
            self._append_log(f"COMMAND_DISPATCH_FAILED: EMERGENCY STOP - {e}")
            
        self.setFocus()
