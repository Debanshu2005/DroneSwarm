// Protocol Definitions mapping SwarmOS Pydantic Models to JS objects

export const MessageType = {
    HEARTBEAT: "heartbeat",
    TELEMETRY: "telemetry",
    CONTROL: "control",
    STATUS: "status",
    ERROR: "error",
    COMMAND_LIFECYCLE: "command_lifecycle",
    EMERGENCY: "emergency",
    EMERGENCY_RESET: "emergency_reset",
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
    PARAM_REQUEST: "param_request",
    PARAM_RESPONSE: "param_response",
    DIAGNOSTICS: "diagnostics",
    TEST_INJECT: "test_inject",
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
    GOTO: "goto",
    STOP: "stop",
    EMERGENCY: "emergency",
    EMERGENCY_RESET: "emergency_reset",
};

export class CommandLifecycleMessage {
  constructor(sender_id, target_id, action, stage, reason = null, cmd_id = null) {
    this.msg_type = MessageType.COMMAND_LIFECYCLE;
    this.sender_id = sender_id;
    this.target_id = target_id;
    this.timestamp = Date.now() / 1000;
    this.action = action;
    this.stage = stage;
    this.reason = reason;
    this.cmd_id = cmd_id;
  }
}

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

export class ParamRequestMessage extends BaseMessage {
    constructor(sender_id, target_id, action, param_name = null, param_value = null, param_type = null) {
        super(MessageType.PARAM_REQUEST, sender_id, target_id);
        this.action = action;
        this.param_name = param_name;
        this.param_value = param_value;
        this.param_type = param_type;
    }
}

export class TestInjectMessage extends BaseMessage {
    constructor(sender_id, target_id, injection_type, active = true) {
        super(MessageType.TEST_INJECT, sender_id, target_id);
        this.injection_type = injection_type;
        this.active = active;
    }
}
