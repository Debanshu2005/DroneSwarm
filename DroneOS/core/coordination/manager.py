import asyncio
import time
import copy
from typing import Dict, Any, Optional, Set
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.swarm_manager import SwarmMembership
from DroneOS.core.coordination.membership import MembershipView, PeerState

logger = setup_logger("CoordinationManager")

class CoordinationManager:
    def __init__(self, swarm_manager: SwarmMembership, flight_cfg: Any, heartbeat_interval: float = 1.0, formation_provider=None, self_status_provider=None, clock=None, formation_update_sender=None):
        self.clock = clock or time.monotonic
        self.swarm = swarm_manager
        self.hb_interval = heartbeat_interval
        self.flight_cfg = flight_cfg
        self.config_dict = getattr(flight_cfg, "coordination", {}) if flight_cfg else {}
        if not isinstance(self.config_dict, dict):
            try:
                self.config_dict = dict(self.config_dict)
            except:
                self.config_dict = {}
                
        self.enabled = str(self.config_dict.get("enabled", "false")).lower() == "true"
        self.mode = self.config_dict.get("mode", "advisory")
        self.armed = str(self.config_dict.get("armed", "false")).lower() == "true"
        self.kill_switch_file = self.config_dict.get("kill_switch_file", "COORD_DISABLE")
        self.formation_update_sender = formation_update_sender
        self.max_heals_per_session = int(self.config_dict.get("max_heals_per_session", 3))
        self.resend_count = int(self.config_dict.get("resend_count", 2))
        
        self.membership = MembershipView(self.config_dict, clock=self.clock)
        self.is_running = False
        self.formation_provider = formation_provider
        self.self_status_provider = self_status_provider
        
        self.last_quorum_state = None
        self.last_proposed_anchor = None
        self.last_anchor = None
        self.last_log_time = 0.0
        self.my_id = self.swarm.identity.drone_id

        # ── Settle gate (req 4) ─────────────────────────────────────────────
        # Clock resets whenever we observe a *change* in slot_assignments from
        # the formation_provider, regardless of its source (operator, prior heal).
        self._last_slot_change_time: float = 0.0       # monotonic
        self._last_slot_assignments: Optional[dict] = None  # last observed value

        # Per-session heal cap
        self.max_heals_per_session: int = int(self.config_dict.get("max_heals_per_session", 3))
        self._heal_count: int = 0
        self._heals_disabled: bool = False  # runtime disable flag

        # HOLD memoization: set of dead pids already reported as HOLD this formation
        self._hold_logged: Set[str] = set()
        self._transient_logged: Dict[str, str] = {}
        self._settle_logged: Dict[str, float] = {}

        # Legacy: kept for tests that directly read _last_heal_time
        self._last_heal_time: float = 0.0

        is_advisory = self.config_dict.get("mode", "advisory").lower() == "advisory"
        dead_drone_obstacle = str(self.config_dict.get("dead_drone_obstacle", "true")).lower() == "true"
        if not is_advisory and not dead_drone_obstacle:
            logger.warning("[coord] WARNING: dead_drone_obstacle=false not allowed in active mode.")

        if self.mode == "active":
            if not self.armed:
                logger.warning("[coord] active mode NOT armed (set coordination.armed: true)")
            else:
                logger.info("[coord] ACTIVE and ARMED: formation updates may be sent")
        else:
            logger.info("[coord] advisory only")

        self._memoized_plans: Dict[str, Any] = {}

    async def run(self):
        from DroneOS.core.coordination.quorum import compute_quorum
        from DroneOS.core.coordination.anchor import is_healthy, propose_anchor

        try:
            if not self.enabled:
                logger.info("[coord] Coordination module disabled via config.")
                return
    
            logger.info(f"[coord] Coordination module starting in {self.mode} mode.")
            self.is_running = True
            
            poll_interval = max(0.25, min(0.5, self.hb_interval / 2.0))
            
            while self.is_running:
                try:
                    import os
                    if os.path.exists(self.kill_switch_file):
                        if not getattr(self, "_kill_switch_logged", False):
                            logger.warning(f"[coord] kill switch file {self.kill_switch_file} detected, coordination is now passive.")
                            self._kill_switch_logged = True
                        self._kill_switch_active = True
                    else:
                        self._kill_switch_active = False
                    await asyncio.sleep(poll_interval)
                    
                    for peer_id in self.swarm.registry.get_all_peers():
                        peer_state = self.swarm.registry.get_peer(peer_id)
                        if peer_state:
                            self.membership.update_from_peer(
                                peer_id=peer_id,
                                last_seen=peer_state.last_seen,
                                battery=peer_state.battery_level,
                                lat=peer_state.lat,
                                lon=peer_state.lon,
                                alt=peer_state.alt,
                                peer_position_stamp=peer_state.last_position_time
                            )
                            
                    self.membership.evaluate_tick(self.hb_interval)
                    
                    # Compute quorum & anchor
                    now = self.clock()
                    fp = self.formation_provider() if self.formation_provider else None
                    my_status = self.self_status_provider() if self.self_status_provider else {"battery_level": 0.0, "gps_valid": False, "last_position_time": None}
                    
                    slot_assignments = fp.get("slot_assignments", {}) if fp else {}

                    # ── Settle gate: track when slot_assignments last changed ──
                    if slot_assignments != self._last_slot_assignments:
                        self._last_slot_assignments = dict(slot_assignments)
                        self._last_slot_change_time = now
                        # Reset HOLD memos when the formation itself changes
                        self._hold_logged.clear()

                    quorum_req = self.config_dict.get("quorum_required", True)
                    q_state = compute_quorum(self.my_id, slot_assignments, self.membership, quorum_req)
                    
                    anchor_min_battery = float(self.config_dict.get("anchor_min_battery", 30))
                    anchor_min_dwell_s = float(self.config_dict.get("anchor_min_dwell_s", 10))
                    heal_max_position_age_s = float(self.config_dict.get("heal_max_position_age_s", 5.0))
                    
                    # Build healthy members
                    healthy_members = set()
                    for pid in slot_assignments:
                        if pid == self.my_id:
                            # self check
                            if my_status.get("battery_level", 0.0) >= anchor_min_battery:
                                age = my_status.get("position_age")
                                if age is not None and (heal_max_position_age_s <= 0 or age <= heal_max_position_age_s):
                                    healthy_members.add(pid)
                                
                            if pid not in slot_assignments and pid not in getattr(self, '_excluded_warned', set()) and hasattr(self, '_excluded_members') and pid in self._excluded_members:
                                if not hasattr(self, '_excluded_warned'): self._excluded_warned = set()
                                logger.warning(f"[coord] drone {pid} returned after heal; operator must re-issue the formation to re-add it")
                                self._excluded_warned.add(pid)
                        else:
                            if is_healthy(pid, self.my_id, self.membership, self.swarm, anchor_min_battery, now, heal_max_position_age_s):
                                healthy_members.add(pid)
                                
                    current_anchor = None
                    for pid, slot in slot_assignments.items():
                        if int(slot) == 0:
                            current_anchor = pid
                            break
                            
                    prop_anchor, prop_time = None, None
                    if q_state != q_state.ISOLATED:
                        prop_anchor, prop_time = propose_anchor(
                            slot_assignments, healthy_members, self.last_proposed_anchor, getattr(self, '_prop_time', None), anchor_min_dwell_s, now
                        )
                        self._prop_time = prop_time
                        
                    changed = False
                    if q_state != self.last_quorum_state:
                        changed = True
                        self.last_quorum_state = q_state
                    if prop_anchor != self.last_proposed_anchor:
                        changed = True
                        self.last_proposed_anchor = prop_anchor
                    if current_anchor != self.last_anchor:
                        changed = True
                        self.last_anchor = current_anchor
                        
                    if changed or (now - self.last_log_time >= 30.0):
                        self.last_log_time = now
                        logger.info(f"[coord] state={q_state.name} anchor={current_anchor} proposed={prop_anchor} healthy={len(healthy_members)}/{len(slot_assignments)}")
                        
                    # ── Healing (Phase 3 Advisory) ──────────────────────────
                    await self._handle_healing(now, q_state, fp, healthy_members, current_anchor, prop_anchor, my_status)

                    
                except asyncio.CancelledError:
                    logger.info("[coord] Coordination manager task cancelled.")
                    break
                except Exception as e:
                    logger.error(f"[coord] Coordination manager failed: {e}")
                    self.enabled = False
                    self.is_running = False
                    break
        except Exception as e:
            logger.error(f"[coord] Unhandled exception in coordination loop: {e}")
            self.is_running = False
            self.enabled = False
            logger.error("[coord] Coordination module has entered FATAL FAULT state and disabled itself.")

    def stop(self):
        self.is_running = False


    async def _handle_healing(self, now, q_state, fp, healthy_members, current_anchor, prop_anchor, my_status=None):
        try:
            await self._do_handle_healing(now, q_state, fp, healthy_members, current_anchor, prop_anchor, my_status)
        except Exception as e:
            logger.error(f"[coord] Unexpected exception in heal path: {e}")
            self._heals_disabled = True

    async def _do_handle_healing(self, now, q_state, fp, healthy_members, current_anchor, prop_anchor, my_status=None):
        if self.mode not in ("advisory", "active") or q_state.name != "QUORUM" or not fp:
            return

        slot_assignments = fp.get("slot_assignments", {})
        heal_after_dead_s = float(self.config_dict.get("heal_after_dead_s", 10.0))
        
        true_dead = set()
        from DroneOS.core.coordination.quorum import PeerState
        for pid in slot_assignments:
            if pid not in healthy_members:
                node = self.membership.nodes.get(pid)
                if node and node.state == PeerState.DEAD:
                    if getattr(node, 'dead_since', None):
                        if now - node.dead_since >= heal_after_dead_s:
                            true_dead.add(pid)
                    else:
                        node.dead_since = now
                        
        if not true_dead or self._heals_disabled:
            return

        # Safe memo key excluding mutable/unpredictable elements
        safe_slots = tuple(sorted(slot_assignments.items()))
        fp_safe = (fp.get('type'), fp.get('spacing'), safe_slots)
        memo_key = (tuple(sorted(true_dead)), fp_safe)
        if memo_key in self._memoized_plans:
            return

        reslot_cooldown = float(self.config_dict.get("reslot_cooldown_s", 5.0))
        time_since_change = now - self._last_slot_change_time
        if time_since_change < reslot_cooldown:
            return

        if self._heal_count >= self.max_heals_per_session:
            if not self._heals_disabled:
                logger.warning(f"[coord] max_heals_per_session={self.max_heals_per_session} reached; healing disabled for this session.")
                self._heals_disabled = True
            return

        reshape_on_follower = str(self.config_dict.get("reshape_on_follower_loss", "false")).lower() == "true"
        dead_pid_str = ",".join(sorted(true_dead))
        is_anchor_dead = (current_anchor in true_dead)

        if not is_anchor_dead and reshape_on_follower:
            if self.mode == "active":
                if dead_pid_str not in self._hold_logged:
                    self._hold_logged.add(dead_pid_str)
                    logger.warning("[coord] follower reshape is not safe under partial delivery in active mode")
                self._memoized_plans[memo_key] = True
                return
            elif self.mode == "advisory":
                if dead_pid_str not in self._hold_logged:
                    self._hold_logged.add(dead_pid_str)
                    logger.info("[coord] follower reshape requested in advisory mode (not safe under partial delivery)")

        if not is_anchor_dead and not reshape_on_follower:
            if dead_pid_str not in self._hold_logged:
                self._hold_logged.add(dead_pid_str)
                logger.info(f"[coord] HOLD: hole left at slot(s) {[slot_assignments[d] for d in true_dead]} (follower died, reshape_on_follower_loss=false)")
            self._memoized_plans[memo_key] = True
            return
            
        from DroneOS.core.coordination.healing import plan_healing, RejectKind
        plan = plan_healing(fp, healthy_members, current_anchor, prop_anchor, self.my_id, self.flight_cfg, self.membership, my_status)
        if not plan:
            return
            
        if plan.is_hold:
            if dead_pid_str not in self._hold_logged:
                self._hold_logged.add(dead_pid_str)
                logger.info(f"[coord] HOLD: hole left at slot {plan.hold_slot} (no safe move for {dead_pid_str})")
            self._memoized_plans[memo_key] = True
        elif not plan.accepted:
            if plan.reject_kind == RejectKind.TRANSIENT:
                if plan.unsettled:
                    pid_part = plan.unsettled["pid"]
                    last_log = self._settle_logged.get(pid_part, None)
                    if last_log is None or now - last_log >= 5.0:
                        self._settle_logged[pid_part] = now
                        dist_str = f'{plan.unsettled["dist"]:.2f}'
                        rad_str = f'{plan.unsettled["radius"]:.2f}'
                        logger.info(f"[coord] settle: {pid_part} dist={dist_str}m radius={rad_str}m")

                if self._transient_logged.get(dead_pid_str) != plan.reject_reason:
                    self._transient_logged[dead_pid_str] = plan.reject_reason
                    logger.info(f"[coord] TRANSIENT NO_PLAN: {plan.reject_reason} for {dead_pid_str}")
                # Do NOT memoize transient so it keeps retrying
            elif plan.reject_kind == RejectKind.CONFIG:
                if dead_pid_str not in self._hold_logged:
                    self._hold_logged.add(dead_pid_str)
                    logger.warning(f"[coord] CONFIG REJECTION: {plan.reject_reason}")
                self._memoized_plans[memo_key] = True
            else:
                if dead_pid_str not in self._hold_logged:
                    self._hold_logged.add(dead_pid_str)
                    if is_anchor_dead:
                        logger.warning(f"[coord] WARNING: anchor lost, no safe re-form plan; formation will hold until operator intervenes (reason: {plan.reject_reason})")
                    else:
                        logger.info(f"[coord] NO_PLAN: {plan.reject_reason} for {dead_pid_str}")
                self._memoized_plans[memo_key] = True
        else:
            await self._send_formation_update(memo_key, plan, fp, prop_anchor, healthy_members, true_dead)

    async def _send_formation_update(self, memo_key, plan, fp, prop_anchor, healthy_members, true_dead):
        if not self.enabled or self.mode != "active" or not self.armed or getattr(self, "_kill_switch_active", False) or not getattr(self, "formation_update_sender", None):
            new_fp = copy.deepcopy(fp)
            new_fp["slot_assignments"] = plan.slot_assignments
            logger.info(f"[coord] would broadcast FORMATION_UPDATE: {fp} -> {new_fp}")
            logger.info(f"[coord] HealPlan: method={plan.reason} travel={plan.total_travel:.1f} min_sep={plan.min_separation:.1f} moves={plan.moves}")
            self._memoized_plans[memo_key] = plan
            return

        # B3 Pre-send checks
        live_fp = self.formation_provider()
        if not live_fp or live_fp != fp:
            logger.warning("[coord] PRE-SEND ABORT: live formation params do not match planning snapshot")
            return
            
        quorum_req = self.config_dict.get("quorum_required", True)
        from DroneOS.core.coordination.quorum import compute_quorum
        q_state = compute_quorum(self.my_id, live_fp.get("slot_assignments", {}), self.membership, quorum_req)
        if q_state.name != "QUORUM":
            logger.warning("[coord] PRE-SEND ABORT: quorum lost")
            return
            
        from DroneOS.core.coordination.anchor import propose_anchor
        live_prop_anchor, _ = propose_anchor(
            live_fp.get("slot_assignments", {}), 
            healthy_members, 
            self.last_proposed_anchor, 
            getattr(self, '_prop_time', None), 
            float(self.config_dict.get("anchor_min_dwell_s", 10)), 
            self.clock()
        )
        if prop_anchor != live_prop_anchor:
            logger.warning("[coord] PRE-SEND ABORT: proposed anchor changed")
            return
            
        for d in true_dead:
            node = self.membership.nodes.get(d)
            if not node or node.state.name != "DEAD":
                logger.warning("[coord] PRE-SEND ABORT: dead set changed")
                return

        if not plan.accepted or plan.is_hold or plan.reject_kind is not None:
            logger.warning("[coord] PRE-SEND ABORT: plan not accepted")
            return
            
        new_params = copy.deepcopy(live_fp)
        new_params["slot_assignments"] = plan.slot_assignments
        new_params["members"] = sorted([pid for pid in plan.slot_assignments.keys() if pid != self.my_id])
        
        healthy_survivors = set(live_fp.get("slot_assignments", {}).keys()) - true_dead
        if set(new_params["slot_assignments"].keys()) != healthy_survivors:
            logger.warning("[coord] PRE-SEND ABORT: slots do not contain exactly healthy survivors")
            return
            
        anchor_slots = [p for p, s in new_params["slot_assignments"].items() if s == 0]
        if not anchor_slots or anchor_slots[0] != prop_anchor:
            logger.warning("[coord] PRE-SEND ABORT: slot 0 is not promoted anchor")
            return
            
        from DroneOS.core.coordination.healing import validate_formation_params
        if not validate_formation_params(new_params, self.flight_cfg):
            logger.warning("[coord] PRE-SEND ABORT: validator failed")
            return
            
        if self._heal_count >= self.max_heals_per_session:
            logger.warning("[coord] PRE-SEND ABORT: max heals reached")
            return
            
        reslot_cooldown = float(self.config_dict.get("reslot_cooldown_s", 5.0))
        if self.clock() - self._last_slot_change_time < reslot_cooldown:
            logger.warning("[coord] PRE-SEND ABORT: cooldown not elapsed")
            return

        targets = [pid for pid in plan.slot_assignments if pid != self.my_id]
        
        try:
            success = await self.formation_update_sender(new_params, targets)
        except Exception as e:
            logger.error(f"[coord] Sender raised: {e}")
            success = False
            
        if success:
            self._heal_count += 1
            self._last_heal_time = self.clock()
            self._consecutive_failures = 0
            self._memoized_plans[memo_key] = plan
            if not hasattr(self, '_excluded_members'):
                self._excluded_members = set()
            self._excluded_members.update(true_dead)
            logger.info(f"[coord] FORMATION_UPDATE SENT: {fp} -> {new_params} targets={targets}")
            
            asyncio.create_task(self._verify_application(plan.slot_assignments, fp))
            
        else:
            self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
            logger.error(f"[coord] SENDER RETURNED FALSE (failures: {self._consecutive_failures})")
            if self._consecutive_failures >= 2:
                self._heals_disabled = True
                logger.error("[coord] SENDER FAILURES >= 2, healing disabled for session.")

    async def _verify_application(self, new_slots, fp):
        await asyncio.sleep(3.0)
        if not self.formation_provider:
            return
        live = self.formation_provider()
        if live and live.get("slot_assignments") != new_slots:
            live_copy = dict(live)
            live_copy.pop("slot_assignments", None)
            fp_copy = dict(fp)
            fp_copy.pop("slot_assignments", None)
            if live_copy == fp_copy:
                logger.error("[coord] OWN APPLICATION VERIFICATION FAILED: healing disabled for session.")
                self._heals_disabled = True
            else:
                logger.info("[coord] OWN APPLICATION VERIFICATION FAILED: live params changed by operator.")
