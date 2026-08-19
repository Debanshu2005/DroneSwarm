import asyncio
import json
import logging
import random
import time
import unittest
import math

import websockets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TestMultiDrone(unittest.IsolatedAsyncioTestCase):
    async def simulate_drone(self, drone_id: str, ws_uri: str, duration: int = 15, index: int = 0):
        try:
            async with websockets.connect(ws_uri) as websocket:
                logging.info(f"{drone_id} connected")
                start_time = time.time()
                
                # Base location: San Francisco
                base_lat = 37.7749
                base_lon = -122.4194
                
                while time.time() - start_time < duration:
                    elapsed = time.time() - start_time
                    
                    # Circular orbit for GPS test
                    lat = base_lat + (math.sin(elapsed / 5.0 + index) * 0.005)
                    lon = base_lon + (math.cos(elapsed / 5.0 + index) * 0.005)
                    heading = (elapsed * 10) % 360
                    
                    hb = {
                        "msg_type": "heartbeat",
                        "sender_id": drone_id,
                        "timestamp": time.time(),
                        "status": "active"
                    }
                    await websocket.send(json.dumps(hb))
                    
                    tel = {
                        "msg_type": "telemetry",
                        "sender_id": drone_id,
                        "timestamp": time.time(),
                        "telemetry": {
                            "armed_state": "ARMED",
                            "flight_mode": "MISSION",
                            "battery_level": 85 - int(elapsed),
                            "gps_valid": True,
                            "altitude": 10.0 + math.sin(elapsed),
                            "ground_speed": 5.0,
                            "satellites": 12,
                            "hdop": 0.8,
                            "latitude": lat,
                            "longitude": lon,
                            "heading": heading
                        }
                    }
                    await websocket.send(json.dumps(tel))
                    await asyncio.sleep(1.0)
                logging.info(f"{drone_id} stopping simulation")
        except ConnectionRefusedError:
            self.skipTest("Relay not running, skipping multi-drone simulation")
            
    async def test_multi_drone_connections(self):
        ws_uri = "ws://localhost:8080"
        num_drones = 5
        duration = 5
        
        logging.info(f"Starting {num_drones} virtual drones for {duration} seconds")
        tasks = []
        for i in range(1, num_drones + 1):
            drone_id = f"drone_{i:02d}"
            tasks.append(self.simulate_drone(drone_id, ws_uri, duration, i))
            
        await asyncio.gather(*tasks)
        logging.info("Multi-drone test complete")

if __name__ == "__main__":
    unittest.main()
