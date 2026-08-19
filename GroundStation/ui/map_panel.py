from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGraphicsView, QGraphicsScene, QGraphicsEllipseItem,
    QGraphicsLineItem, QGraphicsTextItem, QGroupBox, QHBoxLayout, QPushButton
)
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QPen, QBrush, QColor, QFont, QPainter
import math
import json

class MapPanel(QWidget):
    """
    Provides a 2D radar-style display mapping GPS coordinates (Lat/Lon) 
    to local cartesian coordinates. Allows clicking to set a destination.
    """
    destination_set = Signal(float, float)

    def __init__(self, network_manager):
        super().__init__()
        self.network = network_manager
        
        self.home_lat = None
        self.home_lon = None
        self.scale_factor = 100000.0  # Approx scaling to make lat/lon differences visible
        
        self.drone_items = {} # drone_id -> item
        self.mission_items = []
        self.destination_item = None
        
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        box = QGroupBox("Mission Map & Radar")
        box_layout = QVBoxLayout(box)
        
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setBackgroundBrush(QBrush(QColor(10, 10, 20)))
        
        # Grid
        self._draw_grid()
        
        # Click handler for destination
        self.scene.mousePressEvent = self._on_scene_click
        
        box_layout.addWidget(self.view)
        layout.addWidget(box)
        
        btn_layout = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Destination")
        self.btn_clear.clicked.connect(self._clear_dest)
        btn_layout.addWidget(self.btn_clear)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _draw_grid(self):
        pen = QPen(QColor(40, 40, 50), 1)
        for i in range(-500, 500, 50):
            self.scene.addLine(i, -500, i, 500, pen)
            self.scene.addLine(-500, i, 500, i, pen)
            
        # Draw Home
        home_marker = QGraphicsEllipseItem(-5, -5, 10, 10)
        home_marker.setBrush(QBrush(Qt.blue))
        home_marker.setPen(QPen(Qt.white))
        self.scene.addItem(home_marker)
        lbl = QGraphicsTextItem("HOME")
        lbl.setDefaultTextColor(Qt.white)
        lbl.setPos(5, 5)
        self.scene.addItem(lbl)

    def _on_scene_click(self, event):
        pos = event.scenePos()
        self._set_destination(pos.x(), pos.y())
        super(QGraphicsScene, self.scene).mousePressEvent(event)

    def _set_destination(self, x, y):
        if self.destination_item:
            self.scene.removeItem(self.destination_item)
            
        self.destination_item = QGraphicsEllipseItem(x - 6, y - 6, 12, 12)
        self.destination_item.setBrush(QBrush(Qt.red))
        self.destination_item.setPen(QPen(Qt.white, 2))
        self.scene.addItem(self.destination_item)
        
        # Save lat/lon representation for planner
        if self.home_lat is not None and self.home_lon is not None:
            dest_lat = self.home_lat - (y / self.scale_factor)
            dest_lon = self.home_lon + (x / self.scale_factor)
            self.last_clicked_lat = dest_lat
            self.last_clicked_lon = dest_lon
            self.destination_set.emit(dest_lat, dest_lon)

    def _clear_dest(self):
        if self.destination_item:
            self.scene.removeItem(self.destination_item)
            self.destination_item = None
            self.last_clicked_lat = None
            self.last_clicked_lon = None

    def get_destination(self):
        if hasattr(self, 'last_clicked_lat'):
            return self.last_clicked_lat, self.last_clicked_lon
        return None, None

    def update_drone_position(self, drone_id: str, lat: float, lon: float):
        if lat is None or lon is None:
            return
            
        if self.home_lat is None:
            self.home_lat = lat
            self.home_lon = lon
            
        x = (lon - self.home_lon) * self.scale_factor
        y = (self.home_lat - lat) * self.scale_factor
        
        if drone_id not in self.drone_items:
            marker = QGraphicsEllipseItem(-8, -8, 16, 16)
            marker.setBrush(QBrush(QColor(40, 167, 69)))
            marker.setPen(QPen(Qt.white, 2))
            
            lbl = QGraphicsTextItem(drone_id)
            lbl.setDefaultTextColor(Qt.white)
            
            self.scene.addItem(marker)
            self.scene.addItem(lbl)
            self.drone_items[drone_id] = {'marker': marker, 'label': lbl}
            
        item = self.drone_items[drone_id]
        item['marker'].setPos(x, y)
        item['label'].setPos(x + 10, y + 10)

    def plot_mission(self, mission_json: str):
        for item in self.mission_items:
            self.scene.removeItem(item)
        self.mission_items.clear()
        
        if self.home_lat is None:
            return
            
        try:
            data = json.loads(mission_json)
            waypoints = data.get("waypoints", [])
            
            prev_x, prev_y = None, None
            for i, wp in enumerate(waypoints):
                lat = wp.get("latitude", 0)
                lon = wp.get("longitude", 0)
                
                x = (lon - self.home_lon) * self.scale_factor
                y = (self.home_lat - lat) * self.scale_factor
                
                # Plot Waypoint
                marker = QGraphicsEllipseItem(x - 4, y - 4, 8, 8)
                marker.setBrush(QBrush(Qt.yellow))
                self.scene.addItem(marker)
                self.mission_items.append(marker)
                
                lbl = QGraphicsTextItem(str(i+1))
                lbl.setDefaultTextColor(Qt.yellow)
                lbl.setPos(x+5, y-15)
                self.scene.addItem(lbl)
                self.mission_items.append(lbl)
                
                if prev_x is not None:
                    line = QGraphicsLineItem(prev_x, prev_y, x, y)
                    line.setPen(QPen(Qt.yellow, 1, Qt.DashLine))
                    self.scene.addItem(line)
                    self.mission_items.append(line)
                    
                prev_x, prev_y = x, y
        except Exception:
            pass
