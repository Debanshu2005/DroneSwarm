import json
from typing import Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QTableWidget, QTableWidgetItem, QListWidget, QGroupBox, QAbstractItemView
)
from GroundStation.shared.protocol.messages import (
    ControlMessage, CommandAction, TelemetryMessage, 
    DroneJoinMessage, DroneLeaveMessage
)
from GroundStation.shared.utils.logger import setup_logger

logger = setup_logger("GroundStationMultiDroneUI")

class DynamicDroneList(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.drone_list = QListWidget()
        layout.addWidget(QLabel("Active Drones"))
        layout.addWidget(self.drone_list)
        self.setLayout(layout)
        self._drones = set()

    def add_drone(self, drone_id: str):
        if drone_id not in self._drones:
            self._drones.add(drone_id)
            self.drone_list.addItem(drone_id)

    def remove_drone(self, drone_id: str):
        if drone_id in self._drones:
            self._drones.remove(drone_id)
            items = self.drone_list.findItems(drone_id, Qt.MatchFlag.MatchExactly)
            for item in items:
                self.drone_list.takeItem(self.drone_list.row(item))

class MultiDroneStatusTable(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Drone ID", "Status", "Battery", "Altitude", "Mode"])
        layout.addWidget(QLabel("Swarm Status Table"))
        layout.addWidget(self.table)
        self.setLayout(layout)
        self._row_map: Dict[str, int] = {}

    def update_drone(self, drone_id: str, status: str, batt: str, alt: str, mode: str):
        if drone_id not in self._row_map:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._row_map[drone_id] = row
            self.table.setItem(row, 0, QTableWidgetItem(drone_id))
        else:
            row = self._row_map[drone_id]

        self.table.setItem(row, 1, QTableWidgetItem(status))
        self.table.setItem(row, 2, QTableWidgetItem(batt))
        self.table.setItem(row, 3, QTableWidgetItem(alt))
        self.table.setItem(row, 4, QTableWidgetItem(mode))

class DroneSelectionManager(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.list_widget.addItem("ALL DRONES (Broadcast)")
        layout.addWidget(QLabel("Target Drones (Ctrl+Click for multiple):"))
        layout.addWidget(self.list_widget)
        self.setLayout(layout)
        self._drones = set()

    def update_list(self, active_drones: List[str]):
        # Maintain selection
        selected = self.get_selected_targets()
        self.list_widget.clear()
        self.list_widget.addItem("ALL DRONES (Broadcast)")
        for d in active_drones:
            self.list_widget.addItem(d)
            if d in selected:
                items = self.list_widget.findItems(d, Qt.MatchFlag.MatchExactly)
                for item in items:
                    item.setSelected(True)

    def get_selected_targets(self) -> List[str]:
        items = self.list_widget.selectedItems()
        targets = [item.text() for item in items]
        if "ALL DRONES (Broadcast)" in targets or len(targets) == 0:
            return [] # Empty list means broadcast
        return targets

class BroadcastCommandDispatcher:
    def __init__(self, network_manager):
        self.network = network_manager

    def dispatch(self, action: CommandAction, params: dict = None):
        msg = ControlMessage(
            sender_id="GroundStation",
            timestamp=0.0,
            action=action,
            params=params,
            target_id=None # None means broadcast to all
        )
        import asyncio
        async def _dispatch():
            await self.network.send_command(None, action, params)
            
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            task = asyncio.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
        except RuntimeError:
            pass
        logger.info(f"Broadcasted command {action.value} to entire swarm.")

class IndividualCommandDispatcher:
    def __init__(self, network_manager):
        self.network = network_manager

    def dispatch(self, target_id: str, action: CommandAction, params: dict = None):
        msg = ControlMessage(
            sender_id="GroundStation",
            timestamp=0.0,
            action=action,
            params=params,
            target_id=target_id
        )
        # In single drone setup, network.send_command broadcast is enough.
        # But we use the provided target_id.
        import asyncio
        async def _dispatch():
            await self.network.send_command(target_id, action, params)
            
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        try:
            task = asyncio.create_task(_dispatch())
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
        except RuntimeError:
            pass
        logger.info(f"Sent command {action.value} to specific drone: {target_id}")

class MultiDroneTelemetryManager:
    """
    Consolidates incoming telemetry from multiple peers and routes it to the UI.
    """
    def __init__(self, drone_list: DynamicDroneList, status_table: MultiDroneStatusTable):
        self.drone_list = drone_list
        self.status_table = status_table

    def handle_join(self, msg: DroneJoinMessage):
        self.drone_list.add_drone(msg.sender_id)

    def handle_leave(self, msg: DroneLeaveMessage):
        self.drone_list.remove_drone(msg.sender_id)

    def handle_telemetry(self, msg: TelemetryMessage):
        t = msg.telemetry
        alt_str = f"{t.altitude:.1f}m" if t.altitude is not None else "N/A"
        batt_str = f"{t.battery_level:.0f}%" if t.battery_level is not None else "N/A"
        
        self.drone_list.add_drone(msg.sender_id)
        self.status_table.update_drone(
            drone_id=msg.sender_id,
            status=t.mission_state,
            batt=batt_str,
            alt=alt_str,
            mode=t.flight_mode
        )

class SwarmControlPanel(QWidget):
    def __init__(self, network_manager=None):
        super().__init__()
        layout = QVBoxLayout()
        
        # Sub-components
        self.drone_list = DynamicDroneList()
        self.status_table = MultiDroneStatusTable()
        self.selection_mgr = DroneSelectionManager()
        
        self.broadcast_dispatcher = BroadcastCommandDispatcher(network_manager)
        self.individual_dispatcher = IndividualCommandDispatcher(network_manager)
        self.telemetry_mgr = MultiDroneTelemetryManager(self.drone_list, self.status_table)
        
        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_takeoff = QPushButton("Takeoff")
        btn_land = QPushButton("Land")
        btn_takeoff.clicked.connect(lambda: self._execute_command(CommandAction.TAKEOFF))
        btn_land.clicked.connect(lambda: self._execute_command(CommandAction.LAND))
        
        btn_layout.addWidget(btn_takeoff)
        btn_layout.addWidget(btn_land)
        
        # Flight Mode Selector
        from PySide6.QtWidgets import QComboBox
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Flight Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "STABILIZE",
            "GUIDED",
            "GUIDED_NOGPS",
            "LOITER",
            "RTL",
            "LAND"
        ])
        btn_set_mode = QPushButton("SET MODE")
        btn_set_mode.clicked.connect(self._on_set_mode_clicked)
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addWidget(btn_set_mode)
        
        # Assembly
        layout.addWidget(self.selection_mgr)
        layout.addLayout(btn_layout)
        layout.addLayout(mode_layout)
        layout.addWidget(self.drone_list)
        layout.addWidget(self.status_table)
        self.setLayout(layout)

    def _on_set_mode_clicked(self):
        selected_mode = self.mode_combo.currentText()
        targets = self.selection_mgr.get_selected_targets()
        
        if not targets:
            self.broadcast_dispatcher.dispatch(CommandAction.SET_MODE, params={"mode": selected_mode})
        else:
            for t in targets:
                self.individual_dispatcher.dispatch(t, CommandAction.SET_MODE, params={"mode": selected_mode})

    def _execute_command(self, action: CommandAction):
        targets = self.selection_mgr.get_selected_targets()
        if not targets:
            self.broadcast_dispatcher.dispatch(action)
        else:
            for t in targets:
                self.individual_dispatcher.dispatch(t, action)
