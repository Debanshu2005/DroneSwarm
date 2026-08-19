import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { WebSocketManager } from '../networking/WebSocketManager';
import { MessageType, CommandAction, ControlMessage, HeartbeatMessage } from '../protocol/messages';

const DroneContext = createContext();

export const useDroneContext = () => useContext(DroneContext);

const GS_ID = "gs_mobile_01";

export const DroneProvider = ({ children }) => {
  const [wsManager, setWsManager] = useState(null);
  const [isConnected, setIsConnected] = useState("DISCONNECTED");
  
  const [drones, setDrones] = useState({});
  const [selectedDrones, setSelectedDrones] = useState(new Set());
  
  const [wsUrl, setWsUrl] = useState(() => localStorage.getItem("PhoneOS_WsUrl") || "ws://swarmos-pi.local:8080");
  const [testMode, setTestMode] = useState(() => localStorage.getItem("PhoneOS_TestMode") === "true");
  const [eventLog, setEventLog] = useState([]);

  // Time tracker for stale checks
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 100);
    return () => clearInterval(timer);
  }, []);

  const addLog = (msg) => {
    setEventLog(prev => [{ time: Date.now(), msg }, ...prev].slice(0, 100));
  };

  useEffect(() => {
    localStorage.setItem("PhoneOS_WsUrl", wsUrl);
    localStorage.setItem("PhoneOS_TestMode", testMode);
  }, [wsUrl, testMode]);
  
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
          if (now - drone.lastSeen > 5000 && drone.status !== "OFFLINE" && !id.startsWith("drone_test")) {
            newDrones[id] = { ...drone, status: "OFFLINE" };
            changed = true;
            addLog(`${id} went OFFLINE (timeout)`);
          }
        }
        return changed ? newDrones : prev;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, [testMode, isConnected]);

  // Network Initialization
  useEffect(() => {
    const manager = new WebSocketManager(wsUrl);
    
    manager.onConnectionChange = (status) => {
      setIsConnected(status);
      addLog(`Relay connection: ${status}`);
    };
    
    manager.subscribe(MessageType.HEARTBEAT, (msg) => {
      if (msg.sender_id && msg.sender_id.startsWith("drone")) {
        setDrones(prev => {
          const isNew = !prev[msg.sender_id];
          if (isNew) addLog(`${msg.sender_id} CONNECTED`);
          return {
            ...prev,
            [msg.sender_id]: {
              ...(prev[msg.sender_id] || {}),
              id: msg.sender_id,
              status: msg.status,
              lastSeen: Date.now(),
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
          // Maintain a bounded path history for the map
          let path = existing.path || [];
          if (msg.telemetry?.latitude && msg.telemetry?.longitude) {
            // Only add if it moved significantly or time passed, for simplicity just keep last 100
            path = [...path, [msg.telemetry.latitude, msg.telemetry.longitude]].slice(-100);
          }
          
          return {
            ...prev,
            [msg.sender_id]: {
              ...existing,
              id: msg.sender_id,
              telemetry: msg.telemetry,
              lastSeen: Date.now(),
              path,
              commandState: existing.commandState || { action: null, state: 'IDLE', cmd_id: null }
            }
          };
        });
      }
    });

    manager.subscribe(MessageType.STATUS, (msg) => {
       addLog(`ACK from ${msg.sender_id}: ${msg.status_text}`);
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

    manager.subscribe(MessageType.ERROR, (msg) => {
       addLog(`ERROR from ${msg.sender_id}: ${msg.error_msg}`);
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                commandState: { ...drone.commandState, state: 'REJECTED' }
             }
          };
       });
    });

    manager.connect();
    setWsManager(manager);

    const hbInterval = setInterval(() => {
      if (manager.connected) manager.send(new HeartbeatMessage(GS_ID, null, "active"));
    }, 1000);

    return () => {
      clearInterval(hbInterval);
      if (manager.ws) manager.ws.close();
    };
  }, [wsUrl]);

  const sendCommand = (action, params = null, targetIds = null, isEmergency = false) => {
    if (!wsManager || isConnected !== "CONNECTED") return;
    
    const targets = targetIds || Array.from(selectedDrones);
    if (targets.length === 0) return;
    
    targets.forEach(id => {
       const cmd_id = `cmd_${Date.now()}_${id}`;
       setDrones(prev => ({
          ...prev,
          [id]: {
             ...prev[id],
             commandState: { action, state: 'SENDING', cmd_id }
          }
       }));
       
       if (isEmergency) {
          wsManager.send(new ControlMessage(GS_ID, CommandAction.EMERGENCY, params, id, cmd_id));
       } else {
          wsManager.send(new ControlMessage(GS_ID, action, params, id, cmd_id));
       }
       addLog(`Sent ${action} to ${id}`);
    });
  };

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
    wsUrl, setWsUrl, testMode, setTestMode, eventLog, nowMs,
    sendCommand, toggleSelect, selectAll, selectNone, addLog
  };

  return <DroneContext.Provider value={value}>{children}</DroneContext.Provider>;
};
