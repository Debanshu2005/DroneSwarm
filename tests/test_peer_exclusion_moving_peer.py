"""
Tests for moving peer dead-reckoning and unpositioned peer bounded deferral in peer exclusion.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from DroneOS.perception.geolocate import meters_to_lat_lon
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Detection
from DroneOS.shared.config.models import PerceptionConfig


class MockPeer:
    def __init__(self, drone_id, lat=None, lon=None, alt=10.0, vx=0.0, vy=0.0, vz=0.0, is_active=True, last_pos_time=100.0):
        self.drone_id = drone_id
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.vx = vx
        self.vy = vy
        self.vz = vz
        self.is_active = is_active
        self.last_position_time = last_pos_time


def test_moving_peer_dead_reckoned_to_obs_time():
    """Moving peer position is dead-reckoned to observation timestamp, matching target."""
    base_lat = 47.641468
    base_lon = -122.140165

    # Peer reports at t = 100.0 moving North at 5.0 m/s (vx = 5.0)
    peer = MockPeer(
        drone_id="drone2",
        lat=base_lat,
        lon=base_lon,
        alt=10.0,
        vx=5.0,
        vy=0.0,
        is_active=True,
        last_pos_time=100.0,
    )

    swarm_mgr = MagicMock()
    swarm_mgr.registry.peers = {"drone2": peer}

    cfg = PerceptionConfig(
        enabled=True,
        peer_exclusion_radius_m=4.0,
        peer_position_max_age_s=5.0,
        ground_elevation_m=0.0,
    )

    pipeline = PerceptionPipeline(
        drone_id="drone1",
        config=cfg,
        frame_source=AsyncMock(),
        detector=AsyncMock(),
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        swarm_manager=swarm_mgr,
    )

    # Observation was taken at t = 101.0s (1.0s after peer telemetry).
    # In 1.0s, peer has traveled 5.0m North.
    obs_time = 101.0
    target_lat, target_lon = meters_to_lat_lon(5.0, 0.0, base_lat, base_lon)

    # Drone1 is hovering above at 20m altitude
    # Evaluating candidate target directly under where peer was at obs_time
    is_peer, decision, reason, matched_id, dist, _, _ = pipeline._evaluate_peer_exclusion(
        glat=target_lat,
        glon=target_lon,
        agl=20.0,
        u=640.0,
        v=360.0,
        w=1280,
        h=720,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        drone_lat=target_lat,
        drone_lon=target_lon,
        alt=20.0,
        now_curr=101.5,
        obs_time=obs_time,
    )

    assert is_peer is True
    assert decision == "REJECTED"
    assert matched_id == "drone2"
    assert "REJECTED_PEER_EXCLUSION_MATCH_3D" in reason


def test_unpositioned_peer_bounded_deferral_releases_detection():
    """An active peer that never reported coordinates defers initially, then releases detection upon timeout."""
    base_lat = 47.641468
    base_lon = -122.140165

    # Peer is active but lat/lon are None (never reported GPS)
    unpositioned_peer = MockPeer(
        drone_id="drone2",
        lat=None,
        lon=None,
        alt=None,
        is_active=True,
        last_pos_time=None,
    )

    swarm_mgr = MagicMock()
    swarm_mgr.registry.peers = {"drone2": unpositioned_peer}

    cfg = PerceptionConfig(
        enabled=True,
        peer_exclusion_radius_m=4.0,
        deferred_buffer_window_s=2.0,
        unknown_peer_deferral_max_s=2.0,
    )

    pipeline = PerceptionPipeline(
        drone_id="drone1",
        config=cfg,
        frame_source=AsyncMock(),
        detector=AsyncMock(),
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        swarm_manager=swarm_mgr,
    )

    # Initial evaluation: should defer due to unpositioned peer
    is_peer, decision, reason, _, _, _, _ = pipeline._evaluate_peer_exclusion(
        glat=base_lat,
        glon=base_lon,
        agl=15.0,
        u=640.0,
        v=360.0,
        w=1280,
        h=720,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        drone_lat=base_lat,
        drone_lon=base_lon,
        alt=15.0,
        now_curr=100.0,
        obs_time=100.0,
    )

    assert is_peer is False
    assert decision == "DEFERRED"

    # Add detection to buffer with deadline = 100.0 + 2.0 = 102.0
    det = Detection(bbox_xyxy=(100.0, 100.0, 200.0, 200.0), confidence=0.85, label="rubbish")
    pipeline._deferred_buffer.append({
        "det": det,
        "glat": base_lat,
        "glon": base_lon,
        "agl": 15.0,
        "u": 640.0,
        "v": 360.0,
        "w": 1280,
        "h": 720,
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
        "drone_lat": base_lat,
        "drone_lon": base_lon,
        "alt": 15.0,
        "obs_time": 100.0,
        "deadline": 102.0,
    })

    # At t = 101.0, deadline not expired -> still deferred
    recovered = pipeline._process_deferred_buffer(now_curr=101.0)
    assert len(recovered) == 0
    assert len(pipeline._deferred_buffer) == 1

    # At t = 102.5, bounded deferral has elapsed! Detection must be released and accepted.
    recovered = pipeline._process_deferred_buffer(now_curr=102.5)
    assert len(recovered) == 1
    assert recovered[0].label == "rubbish"
    assert recovered[0].lat == base_lat
    assert recovered[0].lon == base_lon
    assert len(pipeline._deferred_buffer) == 0


def test_stale_peer_plus_expired_window_must_not_produce_confirmed_detection():
    """
    On deadline expiry, a detection deferred due to a stale peer (known position but
    outdated telemetry) must be dropped and must NEVER produce a confirmed detection.
    """
    base_lat = 47.641468
    base_lon = -122.140165

    # Peer has known position, but last reported at t = 90.0s (> 5.0s max age at t = 100.0s)
    stale_peer = MockPeer(
        drone_id="drone2",
        lat=base_lat,
        lon=base_lon,
        alt=10.0,
        is_active=True,
        last_pos_time=90.0,
    )

    swarm_mgr = MagicMock()
    swarm_mgr.registry.peers = {"drone2": stale_peer}

    cfg = PerceptionConfig(
        enabled=True,
        peer_exclusion_radius_m=4.0,
        peer_position_max_age_s=5.0,
        deferred_buffer_window_s=3.0,
    )

    pipeline = PerceptionPipeline(
        drone_id="drone1",
        config=cfg,
        frame_source=AsyncMock(),
        detector=AsyncMock(),
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        swarm_manager=swarm_mgr,
    )

    # Det deferred at t = 100.0s because peer2 is stale
    det = Detection(bbox_xyxy=(100.0, 100.0, 200.0, 200.0), confidence=0.85, label="rubbish")
    pipeline._deferred_buffer.append({
        "det": det,
        "glat": base_lat,
        "glon": base_lon,
        "agl": 15.0,
        "u": 640.0,
        "v": 360.0,
        "w": 1280,
        "h": 720,
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
        "drone_lat": base_lat,
        "drone_lon": base_lon,
        "alt": 15.0,
        "obs_time": 100.0,
        "deadline": 103.0,
        "reason_deferred": "DEFERRED_UNCERTAIN_PEER_TELEMETRY",
    })

    # At t = 101.0s, deadline not yet expired -> remains buffered
    recovered = pipeline._process_deferred_buffer(now_curr=101.0)
    assert len(recovered) == 0
    assert len(pipeline._deferred_buffer) == 1

    # At t = 104.0s (> deadline 103.0s), peer telemetry remains stale.
    # Stale peer plus expired window must NOT produce accepted/confirmed detection!
    recovered = pipeline._process_deferred_buffer(now_curr=104.0)
    assert len(recovered) == 0, "Stale peer with expired window must NEVER produce an accepted detection"
    assert len(pipeline._deferred_buffer) == 0, "Expired stale item must be purged from buffer"

    # Aggregator must have zero tracks
    assert len(pipeline.aggregator.tracks) == 0


def test_real_registry_and_telemetry_handler_dead_reckoning_changes_exclusion():
    """
    Go through the real SwarmMembership and telemetry handler (not MockPeer),
    storing velocity_x/y/z on the registry peer, and prove that dead-reckoning
    changes the peer exclusion decision.
    """
    from DroneOS.core.swarm_manager import SwarmMembership
    from DroneOS.shared.protocol.messages import TelemetryMessage, TelemetryData

    base_lat = 47.641468
    base_lon = -122.140165

    # Real SwarmMembership instance for drone1
    swarm = SwarmMembership("drone1")

    # Target ground coordinate located 7.5m North of base_lat
    target_lat, target_lon = meters_to_lat_lon(7.5, 0.0, base_lat, base_lon)

    # Peer reports telemetry at t = 100.0s at base_lat, moving North at 5.0 m/s (velocity_x = 5.0)
    telem_msg = TelemetryMessage(
        sender_id="drone2",
        timestamp=100.0,
        telemetry=TelemetryData(
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
            latitude=base_lat,
            longitude=base_lon,
            altitude=10.0,
            velocity_x=5.0,
            velocity_y=0.0,
            velocity_z=0.0,
            timestamp=100.0,
        )
    )

    # Process through real telemetry handler at local receipt time 100.0
    with patch("time.time", return_value=100.0):
        swarm.sync.handle_telemetry(telem_msg)

    # Verify peer stored velocity_x on registry peer
    peer = swarm.registry.get_peer("drone2")
    assert peer is not None
    assert peer.velocity_x == 5.0
    assert peer.lat == base_lat

    cfg = PerceptionConfig(
        enabled=True,
        peer_exclusion_radius_m=4.0,
        peer_position_max_age_s=5.0,
        ground_elevation_m=0.0,
    )

    pipeline = PerceptionPipeline(
        drone_id="drone1",
        config=cfg,
        frame_source=AsyncMock(),
        detector=AsyncMock(),
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        swarm_manager=swarm,
    )

    # Scenario A: Observation at t = 100.0 (no dead reckoning offset)
    # Peer is at base_lat, target is 7.5m North -> distance is 7.5m > 4.0m exclusion radius -> ACCEPTED
    is_peer_a, decision_a, _, _, dist_a, _, peers_eval_a = pipeline._evaluate_peer_exclusion(
        glat=target_lat,
        glon=target_lon,
        agl=20.0,
        u=640.0,
        v=360.0,
        w=1280,
        h=720,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        drone_lat=target_lat,
        drone_lon=target_lon,
        alt=20.0,
        now_curr=100.0,
        obs_time=100.0,
    )
    assert is_peer_a is False
    assert decision_a == "ACCEPTED"
    assert peers_eval_a[0]["distance_m"] > 4.0

    # Scenario B: Observation at t = 101.5s (1.5s after peer telemetry)
    # Peer has traveled 5.0 m/s * 1.5s = 7.5m North, reaching exactly target_lat!
    # Dead reckoning moves peer directly onto target ray -> distance < 4.0m -> REJECTED!
    is_peer_b, decision_b, reason_b, matched_id_b, dist_b, _, _ = pipeline._evaluate_peer_exclusion(
        glat=target_lat,
        glon=target_lon,
        agl=20.0,
        u=640.0,
        v=360.0,
        w=1280,
        h=720,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        drone_lat=target_lat,
        drone_lon=target_lon,
        alt=20.0,
        now_curr=101.8,
        obs_time=101.5,
    )
    assert is_peer_b is True
    assert decision_b == "REJECTED"
    assert matched_id_b == "drone2"
    assert dist_b < 1.0, f"Dead-reckoned peer should be within 1.0m of target, got {dist_b}"
    assert "REJECTED_PEER_EXCLUSION_MATCH_3D" in reason_b


def test_clock_skew_sender_ahead_and_behind():
    """Sender timestamp 10s ahead or behind must not cause premature purge, false staleness, or failure to age out."""
    from DroneOS.core.swarm_manager import SwarmHeartbeatManager, SwarmRegistry, PeerSynchronization
    from DroneOS.shared.protocol.messages import TelemetryData, TelemetryMessage

    registry = SwarmRegistry(my_id="drone1")
    sync = PeerSynchronization(registry=registry, my_id="drone1")
    heartbeat_mgr = SwarmHeartbeatManager(registry=registry, timeout_sec=5.0, my_id="drone1")

    # 1. Sender timestamp 10s behind
    t_now = time.time()
    telem_behind = TelemetryMessage(
        sender_id="drone2",
        timestamp=t_now - 10.0,
        telemetry=TelemetryData(
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
            latitude=47.641468,
            longitude=-122.140165,
            altitude=10.0,
            timestamp=t_now - 10.0,
        )
    )
    sync.handle_telemetry(telem_behind)
    peer2 = registry.get_peer("drone2")
    assert peer2 is not None
    assert abs(peer2.last_seen - t_now) < 0.5, "last_seen must be local receipt time"
    assert abs(peer2.last_position_time - t_now) < 0.5, "last_position_time must be local receipt time"
    assert peer2.position_msg_time == t_now - 10.0, "position_msg_time must store sender message timestamp"

    # Must NOT be purged on timeout check
    heartbeat_mgr.purge_stale_peers()
    assert registry.get_peer("drone2") is not None, "Peer 10s behind must NOT be prematurely purged"

    # Must NOT be marked stale in pipeline
    swarm_mgr = MagicMock()
    swarm_mgr.registry = registry
    pipe = PerceptionPipeline(
        drone_id="drone1",
        frame_source=AsyncMock(),
        detector=AsyncMock(),
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        swarm_manager=swarm_mgr,
        config=PerceptionConfig(),
    )
    is_peer, decision, reason, _, _, _, _ = pipe._evaluate_peer_exclusion(
        glat=47.641468, glon=-122.140165, agl=10.0,
        u=640, v=360, w=1280, h=720, roll=0, pitch=0, yaw=0,
        drone_lat=47.641468, drone_lon=-122.140165, alt=20.0,
        now_curr=time.time(), obs_time=time.time()
    )
    assert "DEFERRED_STALE_PEER" not in reason, "Peer 10s behind must NOT be marked stale"

    # 2. Sender timestamp 10s ahead
    telem_ahead = TelemetryMessage(
        sender_id="drone3",
        timestamp=t_now + 10.0,
        telemetry=TelemetryData(
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
            latitude=47.641468,
            longitude=-122.140165,
            altitude=10.0,
            timestamp=t_now + 10.0,
        )
    )
    sync.handle_telemetry(telem_ahead)
    peer3 = registry.get_peer("drone3")
    assert peer3 is not None
    assert abs(peer3.last_seen - t_now) < 0.5, "last_seen must be local receipt time"
    assert abs(peer3.last_position_time - t_now) < 0.5, "last_position_time must be local receipt time"
    assert peer3.position_msg_time == t_now + 10.0, "position_msg_time must store sender message timestamp"

    # Advance local time by 6s (> timeout_sec 5.0s): peer MUST age out!
    future_time = t_now + 6.0
    with patch("time.time", return_value=future_time):
        heartbeat_mgr.purge_stale_peers()
        assert registry.get_peer("drone3") is None, "Peer 10s ahead MUST age out when local time exceeds timeout"

    # 3. Moving peer at 5 m/s: dead-reckoned position with +10s and -10s sender skew
    # must equal the one from a zero-skew run.
    reg_skew = SwarmRegistry(my_id="drone1")
    sync_skew = PeerSynchronization(registry=reg_skew, my_id="drone1")
    base_lat, base_lon = 47.641468, -122.140165
    vx = 5.0  # 5 m/s North

    with patch("time.time", return_value=t_now):
        # Baseline: zero skew
        sync_skew.handle_telemetry(TelemetryMessage(
            sender_id="peer_zero",
            timestamp=t_now,
            telemetry=TelemetryData(
                flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
                latitude=base_lat, longitude=base_lon, altitude=10.0,
                velocity_x=vx, velocity_y=0.0, velocity_z=0.0,
                timestamp=t_now,
            )
        ))
        # Sender 10s ahead (+10s skew)
        sync_skew.handle_telemetry(TelemetryMessage(
            sender_id="peer_ahead",
            timestamp=t_now + 10.0,
            telemetry=TelemetryData(
                flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
                latitude=base_lat, longitude=base_lon, altitude=10.0,
                velocity_x=vx, velocity_y=0.0, velocity_z=0.0,
                timestamp=t_now + 10.0,
            )
        ))
        # Sender 10s behind (-10s skew)
        sync_skew.handle_telemetry(TelemetryMessage(
            sender_id="peer_behind",
            timestamp=t_now - 10.0,
            telemetry=TelemetryData(
                flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
                latitude=base_lat, longitude=base_lon, altitude=10.0,
                velocity_x=vx, velocity_y=0.0, velocity_z=0.0,
                timestamp=t_now - 10.0,
            )
        ))

    swarm_mgr_skew = MagicMock()
    swarm_mgr_skew.registry = reg_skew
    pipe_skew = PerceptionPipeline(
        drone_id="drone1",
        frame_source=AsyncMock(),
        detector=AsyncMock(),
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        swarm_manager=swarm_mgr_skew,
        config=PerceptionConfig(),
    )

    eval_time = t_now + 1.0  # 1.0s elapsed on local clock
    _, _, _, _, _, _, peers_eval = pipe_skew._evaluate_peer_exclusion(
        glat=base_lat, glon=base_lon, agl=10.0,
        u=640, v=360, w=1280, h=720, roll=0, pitch=0, yaw=0,
        drone_lat=base_lat, drone_lon=base_lon, alt=20.0,
        now_curr=eval_time, obs_time=eval_time,
    )
    eval_by_id = {p["peer_id"]: p for p in peers_eval}

    pos_zero = (eval_by_id["peer_zero"]["lat"], eval_by_id["peer_zero"]["lon"])
    pos_ahead = (eval_by_id["peer_ahead"]["lat"], eval_by_id["peer_ahead"]["lon"])
    pos_behind = (eval_by_id["peer_behind"]["lat"], eval_by_id["peer_behind"]["lon"])

    assert pos_ahead[0] == pytest.approx(pos_zero[0], abs=1e-9), "Dead-reckoned latitude with +10s skew must equal zero-skew"
    assert pos_ahead[1] == pytest.approx(pos_zero[1], abs=1e-9), "Dead-reckoned longitude with +10s skew must equal zero-skew"
    assert pos_behind[0] == pytest.approx(pos_zero[0], abs=1e-9), "Dead-reckoned latitude with -10s skew must equal zero-skew"
    assert pos_behind[1] == pytest.approx(pos_zero[1], abs=1e-9), "Dead-reckoned longitude with -10s skew must equal zero-skew"
