import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, 
    QTextEdit, QPushButton, QFormLayout
)
from PySide6.QtCore import QTimer

class DiagnosticsPanel(QWidget):
    """
    Provides a real-time, read-only diagnostic view of the entire 
    GroundStation and connected Swarm components.
    """
    def __init__(self, network_manager):
        super().__init__()
        self.network = network_manager
        self.init_ui()
        
        # Non-blocking diagnostic polling loop
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_diagnostics)
        self.timer.start(1000) # 1Hz refresh rate

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # 1. GS Status Box
        self.gs_box = QGroupBox("GroundStation Diagnostics")
        gs_layout = QFormLayout(self.gs_box)
        self.lbl_active_tasks = QLabel("0")
        self.lbl_known_drones = QLabel("0")
        gs_layout.addRow("Async Tasks (UDP):", self.lbl_active_tasks)
        gs_layout.addRow("Known Drones:", self.lbl_known_drones)
        layout.addWidget(self.gs_box)
        
        # 2. Network Activity Log
        self.log_box = QGroupBox("Network Activity")
        log_layout = QVBoxLayout(self.log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background-color: #1a1a1a; color: #00ff00; font-family: monospace;")
        log_layout.addWidget(self.log_view)
        layout.addWidget(self.log_box)
        
        # Refresh button
        btn_refresh = QPushButton("Force Refresh")
        btn_refresh.clicked.connect(self.update_diagnostics)
        layout.addWidget(btn_refresh)

    def update_diagnostics(self):
        # Gather network status
        adapter = getattr(self.network, "network", None)
        if adapter:
            active_tasks = len(getattr(adapter, "_active_tasks", []))
            self.lbl_active_tasks.setText(str(active_tasks))
            
            known_drones = len(getattr(adapter, "known_endpoints", {}))
            self.lbl_known_drones.setText(str(known_drones))
            
        pkts_in = getattr(self.network, "total_packets_received", 0)
        pkts_out = getattr(self.network, "total_packets_sent", 0)
        
        # OS Stats
        import threading
        import asyncio
        import os
        
        threads = threading.active_count()
        tasks = 0
        try:
            tasks = len(asyncio.all_tasks())
        except Exception:
            pass
            
        mem = 0
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemAvailable' in line:
                        mem = int(line.split()[1]) / 1024.0 # MB
                        break
        except Exception:
            pass
            
        cpu = 0.0
        try:
            with open('/proc/stat', 'r') as f:
                line = f.readline()
                parts = line.split()[1:]
                idle = float(parts[3])
                total = sum(float(x) for x in parts)
                cpu = 100.0 * (1.0 - idle / total)
        except Exception:
            pass
            
        reconnects = getattr(self.network, "reconnect_count", 0)
            
        status_text = f"RPC Status: Offline (UDP Only Mode)\n"
        status_text += f"UDP Status: Active\n"
        status_text += f"Threads: {threads} | Async Tasks: {tasks}\n"
        status_text += f"Available Mem: {mem:.1f} MB | CPU: {cpu:.1f}%\n"
        status_text += f"Packets Rx: {pkts_in} | Packets Tx: {pkts_out}\n"
        status_text += f"Reconnects: {reconnects}\n"
        
        for drone_id, info in self.network.drones.items():
            status_text += f"\n--- {drone_id} ---\n"
            status_text += f"Latency: {info.latency:.3f}s\n"
            status_text += f"Heartbeat Freq: {info.heartbeat_frequency:.1f} Hz\n"
            status_text += f"Packets: {info.packet_count}\n"
            
        self.log_view.setPlainText(status_text)

    def stop(self):
        self.timer.stop()
