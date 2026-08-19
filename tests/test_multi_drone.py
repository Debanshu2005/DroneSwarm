import asyncio
import json
import logging
import random
import time
import unittest

import websockets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TestMultiDrone(unittest.IsolatedAsyncioTestCase):
    async def simulate_drone(self, drone_id: str, ws_uri: str, duration: int = 5):
        try:
            async with websockets.connect(ws_uri) as websocket:
                logging.info(f"{drone_id} connected")
                start_time = time.time()
                
                while time.time() - start_time < duration:
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
                            "armed_state": random.choice(["ARMED", "DISARMED"]),
                            "flight_mode": random.choice(["HOLD", "LOITER", "RTL"]),
                            "battery_level": random.randint(15, 100),
                            "gps_valid": True,
                            "altitude": random.uniform(0.0, 10.0),
                            "ground_speed": random.uniform(0.0, 5.0),
                            "satellites": random.randint(6, 14),
                            "hdop": random.uniform(0.5, 2.0)
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
        duration = 3
        
        logging.info(f"Starting {num_drones} virtual drones for {duration} seconds")
        tasks = []
        for i in range(1, num_drones + 1):
            drone_id = f"drone_{i:02d}"
            tasks.append(self.simulate_drone(drone_id, ws_uri, duration))
            
        await asyncio.gather(*tasks)
        logging.info("Multi-drone test complete")

if __name__ == "__main__":
    unittest.main()
