import asyncio
from PySide6.QtCore import Qt, QObject, QTimer, QEvent
from GroundStation.shared.protocol.messages import CommandAction

class KeyboardController(QObject):
    """
    Handles global keyboard events for manual drone control.
    Acts as an event filter for the main window.
    """
    def __init__(self, network_manager, get_active_drone_cb, is_active_cb=None):
        super().__init__()
        self.network_manager = network_manager
        self.get_active_drone = get_active_drone_cb
        self.is_active_cb = is_active_cb or (lambda: True)
        self.on_command_dispatched = None
        
        self.active_keys = set()
        self._active_tasks = set()
        
        self.speed = 5.0
        self.yaw_rate = 45.0
        self.alt_step = 2.0
        self._was_moving = False
        
        self.movement_timer = QTimer(self)
        self.movement_timer.timeout.connect(self._send_movement_command)
        self.movement_timer.start(100)  # 10Hz command rate

    def eventFilter(self, obj, event):
        try:
            if not self.is_active_cb():
                return False
        except (AttributeError, RuntimeError, ReferenceError):
            return False
            
        if event.type() == QEvent.Type.WindowDeactivate or event.type() == QEvent.Type.FocusOut:
            if self.active_keys:
                self.active_keys.clear()
                params = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "duration": 0.1, "yaw_rate": 0.0}
                self._dispatch_command(CommandAction.MOVE, params)
                self._was_moving = False
            return False

        if event.type() == QEvent.Type.KeyPress:
            if event.isAutoRepeat():
                return False
            key_val = event.key()
            if hasattr(key_val, 'value'):
                key_val = key_val.value
            self.active_keys.add(key_val)
            
            # One-shot commands
            if key_val == Qt.Key.Key_Space.value:
                self._dispatch_command(CommandAction.HOVER)
                return True
            elif key_val == Qt.Key.Key_Escape.value:
                self._dispatch_command(CommandAction.DISARM)
                return True
                
        elif event.type() == QEvent.Type.KeyRelease:
            if event.isAutoRepeat():
                return False
            key_val = event.key()
            if hasattr(key_val, 'value'):
                key_val = key_val.value
            self.active_keys.discard(key_val)
            
        return False

    def _dispatch_command(self, action: CommandAction, params=None):
        drone_id = self.get_active_drone()
        if not drone_id: return
        
        if self.on_command_dispatched:
            self.on_command_dispatched(drone_id, action)
        
        task = asyncio.create_task(self.network_manager.send_command(drone_id, action, params))
        
        if not hasattr(self, '_active_tasks'):
            self._active_tasks = set()
            
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    def _send_movement_command(self):
        drone_id = self.get_active_drone()
        if not drone_id: return
        
        movement_keys = {
            Qt.Key.Key_W.value, Qt.Key.Key_S.value, Qt.Key.Key_A.value, Qt.Key.Key_D.value, 
            Qt.Key.Key_Q.value, Qt.Key.Key_E.value, Qt.Key.Key_R.value, Qt.Key.Key_F.value
        }
        if not self.active_keys.intersection(movement_keys):
            if self._was_moving:
                # Send a final zero-velocity command to arrest drift
                params = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "duration": 0.1, "yaw_rate": 0.0}
                self._dispatch_command(CommandAction.MOVE, params)
                self._was_moving = False
            return
            
        self._was_moving = True
        speed = self.speed
        if Qt.Key.Key_Shift.value in self.active_keys: speed *= 2.0
        if Qt.Key.Key_Control.value in self.active_keys: speed *= 0.5
        
        vx = vy = vz = yaw_rate = 0.0
        
        if Qt.Key.Key_W.value in self.active_keys: vx += speed
        if Qt.Key.Key_S.value in self.active_keys: vx -= speed
        if Qt.Key.Key_A.value in self.active_keys: vy -= speed
        if Qt.Key.Key_D.value in self.active_keys: vy += speed
        
        if Qt.Key.Key_R.value in self.active_keys: vz -= self.alt_step
        if Qt.Key.Key_F.value in self.active_keys: vz += self.alt_step
        
        if Qt.Key.Key_Q.value in self.active_keys: yaw_rate -= self.yaw_rate
        if Qt.Key.Key_E.value in self.active_keys: yaw_rate += self.yaw_rate
        
        params = {
            "vx": vx, "vy": vy, "vz": vz, 
            "duration": 0.2,
            "yaw_rate": yaw_rate
        }
        self._dispatch_command(CommandAction.MOVE, params)

    def stop(self):
        """Gracefully stop timers and pending tasks."""
        if self.movement_timer.isActive():
            self.movement_timer.stop()
        for task in self._active_tasks:
            task.cancel()
        self._active_tasks.clear()
