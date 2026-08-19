import asyncio
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QListWidget, QProgressBar, QLabel, QComboBox, QGroupBox, QGridLayout
)
from PySide6.QtCore import Qt
from GroundStation.shared.protocol.messages import CommandAction

class MissionControllerPanel(QWidget):
    """
    Mission Controller manages discrete commands, formation structures,
    and visualizes mission progress and queues.
    """
    def __init__(self, network_manager, get_active_drone_cb):
        super().__init__()
        self.network_manager = network_manager
        self.get_active_drone = get_active_drone_cb
        self._active_tasks = set()
        
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # 1. Action Buttons Grid
        action_group = QGroupBox("Immediate Actions")
        grid = QGridLayout(action_group)
        
        btn_arm = QPushButton("ARM")
        btn_arm.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        btn_arm.clicked.connect(lambda: self._dispatch(CommandAction.ARM))
        
        btn_disarm = QPushButton("DISARM")
        btn_disarm.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold;")
        btn_disarm.clicked.connect(lambda: self._dispatch(CommandAction.DISARM))
        
        btn_takeoff = QPushButton("TAKEOFF")
        btn_takeoff.clicked.connect(lambda: self._dispatch(CommandAction.TAKEOFF, {"altitude": 5.0}))
        
        btn_land = QPushButton("LAND")
        btn_land.clicked.connect(lambda: self._dispatch(CommandAction.LAND))
        
        btn_rtl = QPushButton("RTL")
        btn_rtl.clicked.connect(lambda: self._dispatch(CommandAction.RTL))
        
        btn_hover = QPushButton("HOVER (PAUSE)")
        btn_hover.setStyleSheet("background-color: #ffc107; color: black; font-weight: bold;")
        btn_hover.clicked.connect(lambda: self._dispatch(CommandAction.HOVER))
        
        btn_estop = QPushButton("EMERGENCY STOP")
        btn_estop.setStyleSheet("background-color: #8b0000; color: white; font-weight: bold;")
        btn_estop.clicked.connect(self._trigger_emergency)
        
        grid.addWidget(btn_arm, 0, 0)
        grid.addWidget(btn_disarm, 0, 1)
        grid.addWidget(btn_takeoff, 1, 0)
        grid.addWidget(btn_land, 1, 1)
        grid.addWidget(btn_rtl, 2, 0)
        grid.addWidget(btn_hover, 2, 1)
        grid.addWidget(btn_estop, 3, 0, 1, 2)
        
        main_layout.addWidget(action_group)
        
        # 2. Formation Control
        formation_group = QGroupBox("Formation Control")
        form_layout = QHBoxLayout(formation_group)
        
        form_layout.addWidget(QLabel("Shape:"))
        self.formation_combo = QComboBox()
        self.formation_combo.addItems(["V-Shape", "Line", "Circle", "Grid"])
        form_layout.addWidget(self.formation_combo)
        
        btn_apply_form = QPushButton("Apply Formation")
        btn_apply_form.clicked.connect(self._apply_formation)
        form_layout.addWidget(btn_apply_form)
        
        main_layout.addWidget(formation_group)
        
        # 3. Mission Queue
        queue_group = QGroupBox("Mission Queue")
        queue_layout = QVBoxLayout(queue_group)
        
        self.mission_list = QListWidget()
        queue_layout.addWidget(self.mission_list)
        
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("0/0 Waypoints")
        
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        queue_layout.addLayout(progress_layout)
        
        main_layout.addWidget(queue_group)
        main_layout.addStretch()

    def _trigger_emergency(self):
        drone_id = self.get_active_drone()
        if not drone_id: return
        from GroundStation.shared.protocol.messages import EmergencyMessage
        import time
        msg = EmergencyMessage(sender_id="gs1", timestamp=time.time())
        task = asyncio.create_task(self.network_manager.network.broadcast_message(msg))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        

    def _dispatch(self, action: CommandAction, params=None):
        drone_id = self.get_active_drone()
        if not drone_id: return
        task = asyncio.create_task(self.network_manager.send_command(drone_id, action, params))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    def _apply_formation(self):
        # Implementation depends on formation parameters structure
        shape = self.formation_combo.currentText()
        self._dispatch(CommandAction.FORMATION_UPDATE, {"shape": shape})

    def set_mission_queue(self, tasks: list):
        self.mission_list.clear()
        for t in tasks:
            self.mission_list.addItem(str(t))

    def update_progress(self, percent: int, current_task: str):
        self.progress_bar.setValue(percent)
        self.progress_label.setText(current_task)

    def stop(self):
        for task in self._active_tasks:
            task.cancel()
        self._active_tasks.clear()
