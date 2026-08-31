import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { MultiWebSocketManager } from '../networking/MultiWebSocketManager';
import { MessageType, CommandAction, ControlMessage, EmergencyMessage, HeartbeatMessage, TestInjectMessage, ParamRequestMessage, TerminalCommandMessage } from '../protocol/messages';
import { evaluateDroneHealth, evaluateTelemetryFreshness } from '../utils/DroneHealth';

const DroneContext = createContext();

export const useDroneContext = () => useContext(DroneContext);

const GS_ID = "gs_mobile_01";

const safeStorageGet = (key, fallback) => {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : fallback;
  } catch (e) {
    console.warn(`Storage access failed for ${key}`, e);
    return fallback;
  }
};

const safeStorageSet = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn(`Storage set failed for ${key}`, e);
  }
};


export const DroneProvider = ({ children }) => {
  const [wsManager, setWsManager] = useState(null);
  const [isConnected, setIsConnected] = useState("DISCONNECTED");
  
  const [drones, setDrones] = useState({});
  const [selectedDrones, setSelectedDrones] = useState(new Set());
  
  const [wsUrl, setWsUrl] = useState(() => safeStorageGet("PhoneOS_WsUrl", "ws://swarmos-pi.local:8080"));
  const [relayAuthToken, setRelayAuthToken] = useState(() => safeStorageGet("PhoneOS_RelayAuthToken", ""));
  const [testMode, setTestMode] = useState(() => safeStorageGet("PhoneOS_TestMode", "false") === "true");
  const [indoorMode, setIndoorMode] = useState(() => safeStorageGet("PhoneOS_IndoorMode", "false") === "true");
  const [eventLog, setEventLog] = useState([]);
  
  // Advanced Test Overrides
  const [testOverrides, setTestOverrides] = useState({});
  const [testSessionLog, setTestSessionLog] = useState([]);
  
  const setTestOverride = (droneId, key, value) => {
    setTestOverrides(prev => ({
      ...prev,
      [droneId]: {
        ...(prev[droneId] || {}),
        [key]: value
      }
    }));
    logTestEvent(droneId, `Override ${key} = ${value}`);
  };
  
  const injectFailure = (droneId, injectionType, active = true) => {
    if (!wsManager || isConnected !== "CONNECTED") return;
    wsManager.send(new TestInjectMessage(GS_ID, droneId, injectionType, active));
    logTestEvent(droneId, `Inject Failure: ${injectionType} = ${active}`);
  };
  
  const clearTestOverrides = (droneId) => {
    setTestOverrides(prev => {
      const next = { ...prev };
      delete next[droneId];
      return next;
    });
    logTestEvent(droneId, `Cleared all test overrides`);
  };
  
  const logTestEvent = (droneId, message) => {
    setTestSessionLog(prev => [{ time: Date.now(), droneId, message }, ...prev].slice(0, 500));
  };
  
  const clearTestSessionLog = () => setTestSessionLog([]);

  // Time tracker for stale checks
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 100);
    return () => clearInterval(timer);
  }, []);

  const addLog = (msg, severity = 'INFO', source = 'PHONEOS', droneId = null, event = null) => {
    setEventLog(prev => [{ time: Date.now(), msg, severity, source, droneId, event }, ...prev].slice(0, 100));
  };

  useEffect(() => {
    safeStorageSet("PhoneOS_WsUrl", wsUrl);
    safeStorageSet("PhoneOS_RelayAuthToken", relayAuthToken);
    safeStorageSet("PhoneOS_TestMode", testMode);
    safeStorageSet("PhoneOS_IndoorMode", indoorMode);
  }, [wsUrl, relayAuthToken, testMode, indoorMode]);
  
  // Drone cleanup task & demo mode
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      
      if (testMode) {
        setDrones(prev => ({
          ...prev,
          "drone_test_01": {
            id: "drone_test_01", status: "active", lastSeen: now,
            telemetry: { armed_state: "DISARMED", flight_mode: "HOLD", battery_level: 85, gps_valid: true, altitude: 0.0, ground_speed: 0.0, satellites: 12, hdop: 0.8, latitude: 37.7749, longitude: -122.4194, heading: 90 },
            commandState: prev["drone_test_01"]?.commandState || { action: null, state: 'IDLE', cmd_id: null },
            missionState: prev["drone_test_01"]?.missionState || { status: 'none', count: 0 }
          },
          "drone_test_02": {
            id: "drone_test_02", status: "active", lastSeen: now,
            telemetry: { armed_state: "ARMED", flight_mode: "LOITER", battery_level: 45, gps_valid: true, altitude: 2.5, ground_speed: 1.2, satellites: 14, hdop: 0.7, latitude: 37.7750, longitude: -122.4180, heading: 45 },
            commandState: prev["drone_test_02"]?.commandState || { action: null, state: 'IDLE', cmd_id: null },
            missionState: prev["drone_test_02"]?.missionState || { status: 'running', count: 5 }
          },
          "drone_test_03": {
            id: "drone_test_03", status: "failsafe", lastSeen: now - 3000,
            telemetry: { armed_state: "ARMED", flight_mode: "RTL", battery_level: 15, gps_valid: true, altitude: 15.0, ground_speed: 5.0, satellites: 3, hdop: 3.5, latitude: 37.7740, longitude: -122.4200, heading: 270 },
            commandState: prev["drone_test_03"]?.commandState || { action: null, state: 'IDLE', cmd_id: null },
            missionState: prev["drone_test_03"]?.missionState || { status: 'aborted', count: 3 }
          }
        }));
        if (isConnected !== "CONNECTED") setIsConnected("CONNECTED");
      }
      
      setDrones(prev => {
        let changed = false;
        const newDrones = { ...prev };
        for (const [id, drone] of Object.entries(newDrones)) {
          if (id.startsWith("drone_test") && testMode) continue;
          
          const freshness = evaluateTelemetryFreshness(drone);
          const health = evaluateDroneHealth(drone);
          
          let newStatus = drone.status;
          if (freshness === 'OFFLINE') newStatus = 'OFFLINE';
          else if (freshness === 'STALE') newStatus = 'DEGRADED';
          else if (drone.status === 'active' || drone.status === 'standby' || drone.status === 'OFFLINE' || drone.status === 'DEGRADED') newStatus = 'CONNECTED';
          
          if (newStatus !== drone.status || health !== drone.healthScore || freshness !== drone.freshness) {
            newDrones[id] = { ...drone, status: newStatus, healthScore: health, freshness };
            changed = true;
            if (newStatus === 'OFFLINE' && drone.status !== 'OFFLINE') addLog(`${id} went OFFLINE (timeout)`, 'WARNING', 'PHONEOS', id, 'CONNECTION_LOST');
          }
        }
        return changed ? newDrones : prev;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [testMode, isConnected]);

  // Network Initialization
  useEffect(() => {
    const manager = new MultiWebSocketManager(relayAuthToken);

    // Load any previously saved connections
    manager.loadSavedConnections();
    
    // Add default configured connection for backward compatibility or dev (if no others exist)
    if (wsUrl && Object.keys(manager.connections).length === 0) {
      const [ip, portStr] = wsUrl.replace('ws://', '').split(':');
      manager.addConnection(ip, portStr ? parseInt(portStr) : 8080);
    }

    manager.onConnectionChange = (url, status) => {
      // Could track individual statuses, for now just global "CONNECTED" if any are connected
      const anyConnected = Object.values(manager.connections).some(ws => ws.connected);
      setIsConnected(anyConnected ? "CONNECTED" : "DISCONNECTED");
      addLog(`Connection ${url}: ${status}`);
    };

    manager.subscribe(MessageType.HEARTBEAT, (msg) => {
      if (msg.sender_id && msg.sender_id.startsWith("drone")) {
        setDrones(prev => {
          const isNew = !prev[msg.sender_id];
          if (isNew) addLog(`${msg.sender_id} CONNECTED`, 'INFO', 'PHONEOS', msg.sender_id, 'DRONE_CONNECTED');
          const now = Date.now();
          return {
            ...prev,
            [msg.sender_id]: {
              ...(prev[msg.sender_id] || {}),
              id: msg.sender_id,
              status: msg.status === "active" ? "active" : "standby",
              lastSeen: now,
              lastHeartbeat: now,
              connectTime: isNew ? now : (prev[msg.sender_id]?.connectTime || now),
              reconnects: isNew ? 0 : (prev[msg.sender_id]?.reconnects || 0),
              commandState: prev[msg.sender_id]?.commandState || { action: null, state: 'IDLE', cmd_id: null }
            }
          };
        });
      }
    });

    manager.subscribe(MessageType.TELEMETRY, (msg) => {
      if (msg.sender_id && msg.sender_id.startsWith("drone")) {
        setDrones(prev => {
          const existing = prev[msg.sender_id] || {};
          let path = existing.path || [];
          if (msg.telemetry?.latitude && msg.telemetry?.longitude) {
            path = [...path, [msg.telemetry.latitude, msg.telemetry.longitude]].slice(-100);
          }
          
          let sanitizedTelemetry = { ...(existing.telemetry || {}) };
          for (const [k, v] of Object.entries(msg.telemetry || {})) {
              if (v !== null && v !== undefined) {
                  sanitizedTelemetry[k] = v;
              }
          }
          if (sanitizedTelemetry.battery_level !== undefined && sanitizedTelemetry.battery_level !== null) {
              if (isNaN(sanitizedTelemetry.battery_level) || !isFinite(sanitizedTelemetry.battery_level)) {
                  sanitizedTelemetry.battery_level = null;
              } else {
                  sanitizedTelemetry.battery_level = Math.max(0, Math.min(100, Math.round(sanitizedTelemetry.battery_level)));
              }
          }
          
          const now = Date.now();
          return {
            ...prev,
            [msg.sender_id]: {
              ...existing,
              id: msg.sender_id,
              telemetry: sanitizedTelemetry,
              lastSeen: now,
              lastTelemetry: now,
              path,
              commandState: existing.commandState || { action: null, state: 'IDLE', cmd_id: null }
            }
          };
        });
      }
    });

    manager.subscribe(MessageType.STATUS, (msg) => {
       addLog(`ACK from ${msg.sender_id}: ${msg.status_text}`, 'INFO', 'PX4', msg.sender_id, 'STATUS_TEXT');
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                commandState: { ...drone.commandState, state: 'ACCEPTED' }
             }
          };
       });
    });

    manager.subscribe(MessageType.COMMAND_LIFECYCLE, (msg) => {
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                commandState: {
                   ...drone.commandState,
                   action: msg.action,
                   state: msg.stage,
                   reason: msg.reason,
                   cmd_id: msg.cmd_id || (drone.commandState ? drone.commandState.cmd_id : undefined)
                }
             }
          };
       });
        
        let severity = 'INFO';
        if (msg.stage === 'REJECTED' || msg.stage === 'FAILED' || msg.stage === 'TIMEOUT') severity = 'ERROR';
        addLog(`COMMAND ${msg.action.toUpperCase()}: ${msg.stage}${msg.reason ? ` (${msg.reason})` : ''}`, severity, 'DRONEOS', msg.sender_id, `COMMAND_${msg.stage}`);
    });

    manager.subscribe(MessageType.ERROR, (msg) => {
       addLog(`COMMAND FAILED: ${msg.error_msg}`, 'ERROR', 'MAVSDK', msg.sender_id, 'ERROR');
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                commandState: { ...drone.commandState, state: 'REJECTED' },
                diagnostics: { ...(drone.diagnostics || {}), last_error: msg.error_msg }
             }
          };
       });
    });

    manager.subscribe(MessageType.PARAM_RESPONSE, (msg) => {
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          
          let updatedParams = { ...(drone.parameters || {}) };
          let updatedHistory = [ ...(drone.paramHistory || []) ];
          
          if (msg.action === "read_all" && msg.parameters) {
             updatedParams = msg.parameters;
          } else if (msg.success && msg.param_name !== null) {
             const oldVal = updatedParams[msg.param_name];
             updatedParams[msg.param_name] = msg.param_value;
             
             if (msg.action === "write") {
                updatedHistory.unshift({
                   time: Date.now(),
                   name: msg.param_name,
                   old_value: oldVal,
                   new_value: msg.param_value,
                   status: 'SUCCESS'
                });
             }
          }
          
          if (msg.message && !msg.success) {
             addLog(`Param Error from ${msg.sender_id}: ${msg.message}`);
             if (msg.action === "write" && msg.param_name) {
                updatedHistory.unshift({
                   time: Date.now(),
                   name: msg.param_name,
                   old_value: updatedParams[msg.param_name],
                   new_value: msg.param_value,
                   status: 'FAILED',
                   error: msg.message
                });
             }
          }

          // Keep bounded history
          if (updatedHistory.length > 50) updatedHistory = updatedHistory.slice(0, 50);

          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                parameters: updatedParams,
                paramHistory: updatedHistory,
                paramSyncState: { pending: false, lastSync: Date.now(), lastAction: msg.action, lastParam: msg.param_name }
             }
          };
       });
    });

    manager.subscribe(MessageType.DIAGNOSTICS, (msg) => {
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                diagnostics: msg.diagnostics
             }
          };
       });
    });

    setWsManager(manager);

    const hbInterval = setInterval(() => {
      manager.send(new HeartbeatMessage(GS_ID, null, "active"));
    }, 1000);

    return () => {
      clearInterval(hbInterval);
      manager.disconnectAll();
    };
  }, [wsUrl, relayAuthToken]);

  const sendCommand = (action, params = null, targetIds = null, isEmergency = false) => {
    if (!wsManager || isConnected !== "CONNECTED") return;
    
    const targets = targetIds || Array.from(selectedDrones);
    if (targets.length === 0) return;
    
    targets.forEach(id => {
       // Prevent duplicate pending commands (unless MOVE which is streamable)
       const currentState = drones[id]?.commandState;
       if (currentState && currentState.state === 'SENDING' && currentState.action === action && action !== CommandAction.MOVE) {
           console.warn(`Command ${action} is already pending for ${id}. Deduplicating.`);
           return;
       }

       // Strict Command Gating
       const tel = drones[id]?.telemetry || {};

       if (action === CommandAction.TAKEOFF && tel.armed_state !== 'ARMED') {
           addLog(`TAKEOFF rejected by UI: not armed`, 'WARNING', 'PHONEOS', id, 'TAKEOFF_BLOCKED');
           return;
       }
       if (action === CommandAction.RTL && tel.home_valid === false) {
           addLog(`RTL rejected by UI: home invalid`, 'WARNING', 'PHONEOS', id, 'RTL_BLOCKED');
           return;
       }

       const cmd_id = `cmd_${Date.now()}_${id}`;
       setDrones(prev => ({
          ...prev,
          [id]: {
             ...prev[id],
             commandState: { action, state: 'SENDING', cmd_id, timestamp: Date.now() }
          }
       }));
       
       if (isEmergency) {
          wsManager.send(new EmergencyMessage(GS_ID, id));
       } else {
          wsManager.send(new ControlMessage(GS_ID, action, params, id, cmd_id));
       }
       addLog(`Sent ${action} to ${id}`);
    });
  };

  const sendTerminalCommand = (text, targetIds = null) => {
    if (!wsManager || isConnected !== "CONNECTED") return;
    
    const targets = targetIds || Array.from(selectedDrones);
    if (targets.length === 0) return;
    
    targets.forEach(id => {
       wsManager.send(new TerminalCommandMessage(GS_ID, text, id));
    });
  };

  const sendParamRequest = (action, param_name = null, param_value = null, param_type = null, targetId = null) => {
    if (!wsManager || isConnected !== "CONNECTED") return;
    
    const targets = targetId ? [targetId] : Array.from(selectedDrones);
    if (targets.length === 0) return;
    
    targets.forEach(id => {
       wsManager.send(new ParamRequestMessage(GS_ID, id, action, param_name, param_value, param_type));
       
       setDrones(prev => ({
          ...prev,
          [id]: {
             ...(prev[id] || {}),
             paramSyncState: { pending: true, lastSync: prev[id]?.paramSyncState?.lastSync || 0 }
          }
       }));
    });
  };

  // Command Timeout Monitor
  useEffect(() => {
    const timeoutMonitor = setInterval(() => {
       const now = Date.now();
       setDrones(prev => {
          let changed = false;
          const nextDrones = { ...prev };
          for (const [id, drone] of Object.entries(nextDrones)) {
             if (drone.commandState && drone.commandState.state === 'SENDING') {
                if (now - drone.commandState.timestamp > 5000) { // 5s timeout
                   nextDrones[id] = {
                      ...drone,
                      commandState: { ...drone.commandState, state: 'TIMEOUT' }
                   };
                   changed = true;
                   addLog(`Command timeout for ${id}`);
                }
             }
          }
          return changed ? nextDrones : prev;
       });
    }, 1000);
    return () => clearInterval(timeoutMonitor);
  }, []);

  const toggleSelect = (id) => {
     const newSet = new Set(selectedDrones);
     if (newSet.has(id)) newSet.delete(id);
     else newSet.add(id);
     setSelectedDrones(newSet);
  };
  
  const selectAll = () => setSelectedDrones(new Set(Object.keys(drones)));
  const selectNone = () => setSelectedDrones(new Set());

  const value = {
    wsManager, isConnected, drones, selectedDrones,
    wsUrl, setWsUrl, relayAuthToken, setRelayAuthToken, testMode, setTestMode, indoorMode, setIndoorMode, eventLog, nowMs,
    sendCommand, sendTerminalCommand, sendParamRequest, toggleSelect, selectAll, selectNone, addLog,
    testOverrides, setTestOverride, clearTestOverrides, injectFailure, testSessionLog, clearTestSessionLog
  };

  return <DroneContext.Provider value={value}>{children}</DroneContext.Provider>;
};
