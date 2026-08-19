from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsTextItem, QVBoxLayout, QWidget, QGroupBox
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPen, QBrush, QColor, QFont, QPainter
import math
import random

class NetworkGraphWidget(QWidget):
    """
    Renders a live peer-to-peer network graph showing active communication links,
    signal strengths, and node statuses using QGraphicsScene.
    """
    def __init__(self, network_manager):
        super().__init__()
        
        self.network = network_manager
        self.nodes = {}  # drone_id -> { 'item': QGraphicsEllipseItem, 'label': QGraphicsTextItem }
        self.links = {}  # (source_id, dest_id) -> QGraphicsLineItem
        
        self.init_ui()
        
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_graph_data)
        self.timer.start(1000)

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        graph_group = QGroupBox("Live Peer-to-Peer Topology")
        group_layout = QVBoxLayout(graph_group)
        
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setBackgroundBrush(QBrush(QColor(20, 20, 20)))
        
        group_layout.addWidget(self.view)
        layout.addWidget(graph_group)

    def add_node(self, drone_id: str, x: float = None, y: float = None):
        """Adds or updates a drone node in the graph."""
        if drone_id in self.nodes:
            return
            
        # If no coords provided, place randomly in a circle
        if x is None or y is None:
            angle = random.uniform(0, 2 * math.pi)
            radius = random.uniform(50, 150)
            x = radius * math.cos(angle)
            y = radius * math.sin(angle)
            
        # Draw node
        ellipse = QGraphicsEllipseItem(x - 15, y - 15, 30, 30)
        ellipse.setBrush(QBrush(QColor(40, 167, 69)))  # Green base
        ellipse.setPen(QPen(Qt.white, 2))
        ellipse.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsMovable)
        
        # Draw label
        label_text = f"{drone_id}\nFreq: 0.0Hz\nPkts: 0\nLat: 0.0s\nState: Connecting"
        label = QGraphicsTextItem(label_text)
        label.setDefaultTextColor(Qt.white)
        label.setFont(QFont("Arial", 8))
        label.setPos(x - 30, y + 15)
        
        self.scene.addItem(ellipse)
        self.scene.addItem(label)
        
        self.nodes[drone_id] = {
            'item': ellipse,
            'label': label,
            'x': x,
            'y': y
        }

    def update_node_stats(self, drone_id: str, freq: float, pkts: int, lat: float, state: str):
        if drone_id in self.nodes:
            label = self.nodes[drone_id]['label']
            label.setPlainText(f"{drone_id}\nFreq: {freq:.1f}Hz\nPkts: {pkts}\nLat: {lat:.3f}s\nState: {state}")

    def remove_node(self, drone_id: str):
        if drone_id not in self.nodes:
            return
            
        node_data = self.nodes.pop(drone_id)
        self.scene.removeItem(node_data['item'])
        self.scene.removeItem(node_data['label'])
        
        # Remove associated links
        to_remove = []
        for (src, dst) in self.links.keys():
            if src == drone_id or dst == drone_id:
                to_remove.append((src, dst))
                
        for pair in to_remove:
            self.scene.removeItem(self.links[pair])
            del self.links[pair]

    def update_link(self, source_id: str, dest_id: str, quality: float):
        """
        Updates the communication link line between two nodes.
        Quality (0.0 to 1.0) dictates color and thickness.
        """
        if source_id not in self.nodes or dest_id not in self.nodes:
            return
            
        pair = (source_id, dest_id)
        reverse_pair = (dest_id, source_id)
        
        # Use a single undirected edge for simplicity
        if reverse_pair in self.links:
            pair = reverse_pair
            
        src_item = self.nodes[source_id]['item']
        dst_item = self.nodes[dest_id]['item']
        
        # Calculate center of items
        src_rect = src_item.sceneBoundingRect()
        dst_rect = dst_item.sceneBoundingRect()
        
        src_center = src_rect.center()
        dst_center = dst_rect.center()
        
        if pair not in self.links:
            line = QGraphicsLineItem(src_center.x(), src_center.y(), dst_center.x(), dst_center.y())
            # Put lines under nodes
            line.setZValue(-1)
            self.scene.addItem(line)
            self.links[pair] = line
        else:
            line = self.links[pair]
            line.setLine(src_center.x(), src_center.y(), dst_center.x(), dst_center.y())
            
        # Update color based on quality
        if quality > 0.8:
            color = QColor(40, 167, 69)  # Green
        elif quality > 0.4:
            color = QColor(255, 193, 7)  # Yellow
        else:
            color = QColor(220, 53, 69)  # Red
            
        pen = QPen(color, 2 + (quality * 3))
        line.setPen(pen)

    def redraw_links(self):
        """Called automatically if items are moved manually."""
        for (src, dst), line in self.links.items():
            if src in self.nodes and dst in self.nodes:
                src_c = self.nodes[src]['item'].sceneBoundingRect().center()
                dst_c = self.nodes[dst]['item'].sceneBoundingRect().center()
                line.setLine(src_c.x(), src_c.y(), dst_c.x(), dst_c.y())

    def update_graph_data(self):
        if not self.network:
            return
            
        for drone_id, info in self.network.drones.items():
            if drone_id not in self.nodes:
                self.add_node(drone_id)
                self.update_link(self.network.gs_id, drone_id, 1.0)
                
            state = "Connected" if info.latency < 2.0 else "Disconnected"
            self.update_node_stats(
                drone_id, 
                freq=info.heartbeat_frequency, 
                pkts=info.packet_count, 
                lat=info.latency, 
                state=state
            )
