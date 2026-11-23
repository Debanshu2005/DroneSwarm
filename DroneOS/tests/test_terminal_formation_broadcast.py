import pytest
import time
from unittest.mock import MagicMock, AsyncMock

from DroneOS.core.terminal_controller import TerminalController
from DroneOS.core.command_handler import CommandHandler
from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.interfaces import IFlightController
from DroneOS.shared.protocol.messages import CommandAction, ControlMessage, MessageType, TelemetryData
from DroneOS.shared.config.models import FlightConfig, FormationConfig, CollisionAvoidanceConfig


class FakeDrone:
    def __init__(self, node_id: str, all_node_ids: list[str], network_bus: list):
        self.node_id = node_id
        self.network_bus = network_bus
        
        # Flight Config with safe spacing defaults
        self.flight_cfg = FlightConfig(
            adapter_type="px4_sitl",
            takeoff_altitude=5.0,
            max_velocity=10.0,
            px4_connection_string="udp:127.0.0.1:14540",
            airsim_host="127.0.0.1",
            airsim_port=41451,
            formation=FormationConfig(
                default_formation="V",
                spacing=10.0,
                min_formation_separation_m=8.0
            ),
            collision_avoidance=CollisionAvoidanceConfig(
                min_horizontal_distance=2.0
            )
        )
        
        # Flight controller
        self.fc = MagicMock(spec=IFlightController)
        telem = TelemetryData(
            flight_mode="GUIDED",
            armed_state="ARMED",
            gps_valid=True,
            latitude=22.3,
            longitude=87.3,
            altitude=10.0
        )
        self.fc.get_telemetry = AsyncMock(return_value=telem)

        # Swarm manager & Peer registry
        self.sm = MagicMock()
        self.sm.identity.drone_id = node_id
        self.sm.my_id = node_id
        self.sm.heartbeat_mgr.timeout_sec = 60.0

        now = time.time()
        peers = [pid for pid in all_node_ids if pid != node_id]
        self.sm.registry.get_all_peers.return_value = peers
        def get_peer(pid):
            if pid in peers:
                p = MagicMock()
                p.last_seen = now
                return p
            return None
        self.sm.registry.get_peer.side_effect = get_peer

        # Flight Manager
        store = MagicMock()
        self.fm = FlightManager(self.fc, store)
        self.fm.set_swarm_manager(self.sm)
        self.fm._flight_config = self.flight_cfg

        # Command Handler
        self.ch = CommandHandler(node_id=node_id, flight_controller=self.fc, swarm_manager=self.sm)
        self.ch.flight_manager = self.fm
        self.ch._handlers[CommandAction.FORMATION_UPDATE] = self.fm.formation_update

        # Terminal Controller
        self.tc = TerminalController(command_handler=self.ch, flight_controller=self.fc, node_id=node_id)
        self.tc.swarm_manager = self.sm

        # Network mock that appends messages to shared bus and routes remote commands
        self.sent_messages = []
        self.tc.network = MagicMock()
        async def fake_broadcast(msg):
            self.sent_messages.append(msg)
            self.network_bus.append((self.node_id, msg))
        self.tc.network.broadcast_message = AsyncMock(side_effect=fake_broadcast)


@pytest.mark.asyncio
async def test_terminal_formation_3drones_broadcast():
    bus = []
    node_ids = ["Drone1", "drone2", "Drone3"]
    drones = {nid: FakeDrone(nid, node_ids, bus) for nid in node_ids}

    # Simulate mobile app sending broadcast=True terminal command to all 3 drones
    cmd_text = "form v spacing 15m"
    for nid, drone in drones.items():
        await drone.tc.process_text(cmd_text, sender_id="gs-phone-1", broadcast=True)

    # Allow background broadcast tasks to flush
    import asyncio
    await asyncio.sleep(0.05)

    # 1. Exactly one drone (lowest ID: "Drone1" -> "drone1") initiated formation update
    formation_update_broadcasters = []
    for nid, drone in drones.items():
        has_formation_broadcast = any(
            isinstance(m, ControlMessage) and m.action == CommandAction.FORMATION_UPDATE
            for m in drone.sent_messages
        )
        if has_formation_broadcast:
            formation_update_broadcasters.append(nid)

    assert formation_update_broadcasters == ["Drone1"], f"Expected only Drone1 to broadcast, got {formation_update_broadcasters}"

    # 2. Check that the other drones skipped and sent "formation handled by drone1"
    for nid in ["drone2", "Drone3"]:
        status_msgs = [
            m for m in drones[nid].sent_messages
            if getattr(m, 'msg_type', None) == MessageType.STATUS
        ]
        assert any("formation handled by drone1" in m.status_text for m in status_msgs), (
            f"Drone {nid} did not report handled by coordinator"
        )

    # 3. Deliver remote FORMATION_UPDATE messages from Drone1 to the recipient drones
    for sender_id, msg in bus:
        if isinstance(msg, ControlMessage) and msg.action == CommandAction.FORMATION_UPDATE:
            target = getattr(msg, 'target_id', None)
            for nid, drone in drones.items():
                if target and target.lower() == nid.lower():
                    # Target drone executes the remote command
                    await drone.fm.formation_update(msg.params)

    # 4. Verify all 3 drones end with identical slot_assignments
    expected_slots = {"drone1": 0, "drone2": 1, "drone3": 2}
    for nid, drone in drones.items():
        assert drone.fm.formation_params is not None, f"Drone {nid} has no formation params"
        assert drone.fm.formation_params["slot_assignments"] == expected_slots, (
            f"Drone {nid} slot assignments {drone.fm.formation_params['slot_assignments']} != {expected_slots}"
        )
