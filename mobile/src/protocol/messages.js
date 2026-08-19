// Protocol Definitions mapping SwarmOS Pydantic Models to JS objects

export const MessageType = {
    HEARTBEAT: "heartbeat",
    TELEMETRY: "telemetry",
    CONTROL: "control",
    STATUS: "status",
    ERROR: "error",
    EMERGENCY: "emergency",
    MISSION: "mission",
    MISSION_PROGRESS: "mission_progress",
    MISSION_STATUS: "mission_status",
    MISSION_COMPLETE: "mission_complete",
    MISSION_ABORT: "mission_abort",
    MISSION_PAUSE: "mission_pause",
    MISSION_RESUME: "mission_resume",
    MISSION_UPLOAD: "mission_upload",
    MISSION_START: "mission_start",
    MISSION_STOP: "mission_stop",
    MISSION_DELETE: "mission_delete",
    MISSION_DUPLICATE: "mission_duplicate",
    MISSION_CLEAR: "mission_clear",
    DRONE_JOIN: "drone_join",
    DRONE_LEAVE: "drone_leave",
    SWARM_STATE: "swarm_state",
    PEER_STATE: "peer_state",
    DRONE_IDENTITY: "drone_identity",
    SWARM_HEARTBEAT: "swarm_heartbeat",
};

export const CommandAction = {
    ARM: "arm",
    DISARM: "disarm",
    TAKEOFF: "takeoff",
    LAND: "land",
    RTL: "rtl",
    HOVER: "hover",
    MOVE: "move",
    FORMATION_UPDATE: "formation_update",
    SET_MODE: "set_mode",
};

export class BaseMessage {
    constructor(msg_type, sender_id, target_id = null) {
        this.msg_type = msg_type;
        this.sender_id = sender_id;
        this.timestamp = Date.now() / 1000.0;
        this.target_id = target_id;
    }
}

export class HeartbeatMessage extends BaseMessage {
    constructor(sender_id, target_id = null, status = "active") {
        super(MessageType.HEARTBEAT, sender_id, target_id);
        this.status = status;
    }
}

export class ControlMessage extends BaseMessage {
    constructor(sender_id, action, params = null, target_id = null, command_id = null) {
        super(MessageType.CONTROL, sender_id, target_id);
        this.action = action;
        this.params = params;
        this.command_id = command_id || `cmd_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    }
}

export class EmergencyMessage extends BaseMessage {
    constructor(sender_id, action = "stop_and_land", target_id = null) {
        super(MessageType.EMERGENCY, sender_id, target_id);
        this.action = action;
    }
}
