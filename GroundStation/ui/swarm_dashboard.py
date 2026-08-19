from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
    QTableWidgetItem, QHeaderView, QGroupBox, QLabel, QProgressBar
)
from PySide6.QtCore import Qt
from GroundStation.shared.protocol.messages import TelemetryData

class SwarmDashboardPanel(QWidget):
    """
    Provides a comprehensive overview of the entire swarm,
    including individual health, distributed consensus state,
    and peer-to-peer network metrics.
    """
    def __init__(self):
        super().__init__()
        self.drone_data = {}
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # 1. Swarm Overview Table
        list_group = QGroupBox("Swarm Asset Overview")
        list_layout = QVBoxLayout(list_group)
        
        self.drone_table = QTableWidget(0, 5)
        self.drone_table.setHorizontalHeaderLabels(["Drone ID", "Status", "Battery", "Altitude", "Task"])
        header = self.drone_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.drone_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        list_layout.addWidget(self.drone_table)
        main_layout.addWidget(list_group)
        
        # 2. Health & Consensus Metrics
        metrics_layout = QHBoxLayout()
        
        # Network Health
        net_group = QGroupBox("Network Health")
        net_vbox = QVBoxLayout(net_group)
        
        self.latency_label = QLabel("Avg Latency: -- ms")
        self.packet_loss_label = QLabel("Packet Loss: 0%")
        
        self.network_health_bar = QProgressBar()
        self.network_health_bar.setValue(100)
        self.network_health_bar.setStyleSheet("QProgressBar::chunk {background-color: #28a745;}")
        
        net_vbox.addWidget(self.latency_label)
        net_vbox.addWidget(self.packet_loss_label)
        net_vbox.addWidget(self.network_health_bar)
        metrics_layout.addWidget(net_group)
        
        # Consensus Status
        consensus_group = QGroupBox("Distributed Consensus")
        cons_vbox = QVBoxLayout(consensus_group)
        
        self.consensus_state_label = QLabel("State: SYNCHRONIZED")
        self.consensus_state_label.setStyleSheet("color: #00FF00; font-weight: bold;")
        self.active_leader_label = QLabel("Leader: None (Decentralized)")
        
        cons_vbox.addWidget(self.consensus_state_label)
        cons_vbox.addWidget(self.active_leader_label)
        cons_vbox.addStretch()
        metrics_layout.addWidget(consensus_group)
        
        main_layout.addLayout(metrics_layout)



    def update_consensus_state(self, is_synced: bool, leader_id: str = None):
        if is_synced:
            self.consensus_state_label.setText("State: SYNCHRONIZED")
            self.consensus_state_label.setStyleSheet("color: #00FF00; font-weight: bold;")
        else:
            self.consensus_state_label.setText("State: RESOLVING...")
            self.consensus_state_label.setStyleSheet("color: #FFA500; font-weight: bold;")
            
        if leader_id:
            self.active_leader_label.setText(f"Leader: {leader_id}")
        else:
            self.active_leader_label.setText("Leader: None (Decentralized)")

    def update_network_health(self, latency_ms: float, loss_percent: float):
        self.latency_label.setText(f"Avg Latency: {latency_ms:.1f} ms")
        self.packet_loss_label.setText(f"Packet Loss: {loss_percent:.1f}%")
        
        health_score = max(0, int(100 - loss_percent - (latency_ms/10)))
        self.network_health_bar.setValue(health_score)
        
        if health_score > 80:
            self.network_health_bar.setStyleSheet("QProgressBar::chunk {background-color: #28a745;}")
        elif health_score > 40:
            self.network_health_bar.setStyleSheet("QProgressBar::chunk {background-color: #ffc107;}")
        else:
            self.network_health_bar.setStyleSheet("QProgressBar::chunk {background-color: #dc3545;}")
