import React, { useState, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { CommandAction } from '../protocol/messages';
import { AlertTriangle, ShieldAlert, Bug, TestTube, Crosshair, Settings, Trash2, Download, Network } from 'lucide-react';

export default function AdvancedTestView() {
  const { drones, selectedDrones, testOverrides, setTestOverride, clearTestOverrides, injectFailure, testSessionLog, clearTestSessionLog, sendCommand, sendParamRequest, indoorMode, nowMs } = useDroneContext();
  const [selectedDroneId, setSelectedDroneId] = useState("");
  
  // Parameter Test State
  const [paramName, setParamName] = useState("");
  const [paramValue, setParamValue] = useState("");
  const [paramType, setParamType] = useState("float");

  useEffect(() => {
    if (!selectedDroneId && selectedDrones.size > 0) {
      setSelectedDroneId(Array.from(selectedDrones)[0]);
    } else if (!selectedDroneId && Object.keys(drones).length > 0) {
      setSelectedDroneId(Object.keys(drones)[0]);
    }
  }, [drones, selectedDrones, selectedDroneId]);

  const drone = drones[selectedDroneId];
  const overrides = testOverrides[selectedDroneId] || {};
  const activeCount = Object.values(overrides).filter(v => v === true).length;

  const handleOverrideToggle = (key) => {
    if (!selectedDroneId) return;
    setTestOverride(selectedDroneId, key, !overrides[key]);
  };

  const handleCommand = (action) => {
    if (!selectedDroneId) return;
    sendCommand(action, null, [selectedDroneId]);
  };
  
  const handleParamAction = (action) => {
    if (!selectedDroneId) return;
    sendParamRequest(action, paramName, paramValue, paramType, selectedDroneId);
  };

  const copyLog = () => {
    const text = testSessionLog.map(l => `[${new Date(l.time).toISOString()}] [${l.droneId}] ${l.message}`).join('\n');
    navigator.clipboard.writeText(text);
    alert("Copied to clipboard");
  };

  const renderToggle = (label, key) => {
    const active = !!overrides[key];
    return (
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0', borderBottom: '1px solid var(--border)'}}>
        <div style={{fontWeight: 500}}>{label}</div>
        <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
          <span style={{fontSize: '12px', fontWeight: 600, color: active ? 'var(--warning)' : 'var(--text-muted)'}}>
            {active ? 'TEST ON' : 'TEST OFF'}
          </span>
          <label className="switch">
            <input type="checkbox" checked={active} onChange={() => handleOverrideToggle(key)} />
            <span className="slider round"></span>
          </label>
        </div>
      </div>
    );
  };

  const renderInjectButton = (label, injectionType, color = 'var(--danger)') => {
    return (
      <button 
        className="btn" 
        style={{background: 'var(--bg-card)', border: `1px solid ${color}`, color, width: '100%', padding: '10px'}}
        onClick={() => {
            if (selectedDroneId) {
                injectFailure(selectedDroneId, injectionType, true);
                // The injection does not auto-timeout unless explicitly commanded for some tests,
                // but the prompt says: "Simulate: GPS LOST... The purpose is to verify recovery".
                // I'll leave it as a toggle or a one-shot. The prompt asks for "controlled software failure scenarios".
                // We'll let them click again to RESTORE, but for simplicity let's provide a RESTORE button below.
            }
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      
      {/* Banner */}
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px', background: 'var(--bg-main)', border: '2px dashed var(--warning)'}}>
        <div style={{display: 'flex', alignItems: 'center', gap: '12px'}}>
           <TestTube size={24} color="var(--warning)" />
           <div>
             <h2 style={{color: 'var(--warning)', margin: 0}}>ADVANCED TEST / ENGINEERING USE</h2>
             <div style={{fontSize: '12px', color: 'var(--text-muted)'}}>PX4 physical safety gates are preserved. UI and software tests only.</div>
           </div>
        </div>
        <div style={{display: 'flex', alignItems: 'center', gap: '12px'}}>
           <div style={{fontWeight: 'bold'}}>TARGET:</div>
           <select className="input-field" value={selectedDroneId} onChange={e => setSelectedDroneId(e.target.value)} style={{minWidth: '150px'}}>
             <option value="">-- Select Drone --</option>
             {Object.keys(drones).map(id => <option key={id} value={id}>{id}</option>)}
           </select>
        </div>
      </div>

      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px'}}>
        
        {/* Software / UI Overrides */}
        <div className="glass-panel">
          <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px'}}>
            <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', margin: 0}}><Settings size={18} /> UI Overrides</h3>
            {activeCount > 0 && <span className="status-badge badge-warning">{activeCount} ACTIVE</span>}
          </div>
          {renderToggle('Bypass GPS UI Requirement', 'bypass_gps')}
          {renderToggle('Bypass Sensor UI Requirement', 'bypass_sensors')}
          {renderToggle('Bypass Indoor Profile Gate', 'bypass_profile')}
          {renderToggle('Simulate Stale Telemetry UI', 'simulate_stale')}
          {renderToggle('Simulate Offline UI', 'simulate_offline')}
        </div>

        {/* Failure Injection */}
        <div className="glass-panel">
          <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px'}}>
             <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', margin: 0}}><AlertTriangle size={18} /> Failure Injection</h3>
             <button className="btn btn-secondary" style={{padding: '4px 8px', fontSize: '12px'}} onClick={() => {
                if (selectedDroneId) {
                   injectFailure(selectedDroneId, "RESTORE_ALL", false);
                   // Reset all injections on backend
                   ['GPS_LOST', 'BATTERY_LOW', 'BATTERY_CRITICAL', 'TELEMETRY_STALE', 'RELAY_DISCONNECT'].forEach(k => {
                      injectFailure(selectedDroneId, k, false);
                   });
                   // Clear all frontend overrides
                   clearTestOverrides(selectedDroneId);
                }
             }}>RESTORE ALL</button>
          </div>
          <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px'}}>
            {renderInjectButton('GPS LOST', 'GPS_LOST', '#F59E0B')}
            {renderInjectButton('BATT LOW (15%)', 'BATTERY_LOW', '#F59E0B')}
            {renderInjectButton('BATT CRITICAL (5%)', 'BATTERY_CRITICAL', '#EF4444')}
            {renderInjectButton('TELEMETRY STALE', 'TELEMETRY_STALE', '#6B7280')}
            {renderInjectButton('RELAY DISCONNECT', 'RELAY_DISCONNECT', '#EF4444')}
          </div>
        </div>

        {/* Swarm Testing */}
        <div className="glass-panel">
          <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', margin: 0}}><Network size={18} /> Swarm Simulation</h3>
          <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px'}}>
             {Object.keys(drones).map(peer => {
                 if (peer === selectedDroneId) return null;
                 return renderInjectButton(`${peer} LOST`, `${peer}_LOST`, '#8B5CF6');
             })}
          </div>
          <div style={{fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px'}}>Injects neighbor loss into the selected drone&apos;s swarm manager to test decentralized handling.</div>
        </div>

        {/* Command Testing */}
        <div className="glass-panel">
          <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', margin: 0}}><Crosshair size={18} /> Command Testing</h3>
          <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px'}}>
            <button className="btn btn-secondary" onClick={() => handleCommand(CommandAction.ARM)}>ARM REQ</button>
            <button className="btn btn-secondary" onClick={() => handleCommand(CommandAction.DISARM)}>DISARM REQ</button>
            <button className="btn btn-secondary" onClick={() => handleCommand(CommandAction.TAKEOFF)}>TAKEOFF REQ</button>
            <button className="btn btn-secondary" onClick={() => handleCommand(CommandAction.LAND)}>LAND REQ</button>
            <button className="btn btn-secondary" onClick={() => handleCommand(CommandAction.RTL)}>RTL REQ</button>
            <button className="btn btn-secondary" onClick={() => handleCommand(CommandAction.HOVER)}>HOLD REQ</button>
          </div>
          {drone?.commandState?.action && (
            <div style={{marginTop: '12px', padding: '12px', background: 'var(--bg-card)', borderRadius: '6px', fontSize: '12px', border: '1px solid var(--border)'}}>
               <div style={{fontWeight: 'bold', marginBottom: '4px'}}>LAST COMMAND: {drone.commandState.action.toUpperCase()}</div>
               <div style={{display: 'flex', justifyContent: 'space-between'}}>
                 <span>State: <span style={{color: drone.commandState.state === 'FAILED' ? 'var(--danger)' : 'var(--good)'}}>{drone.commandState.state}</span></span>
                 <span style={{color: 'var(--text-muted)'}}>{drone.commandState.cmd_id}</span>
               </div>
            </div>
          )}
        </div>
        
        {/* Parameter Testing */}
        <div className="glass-panel">
          <h3 style={{display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', margin: 0}}><Bug size={18} /> Parameter Transaction Test</h3>
          <div style={{display: 'flex', flexDirection: 'column', gap: '8px'}}>
             <div style={{display: 'flex', gap: '8px'}}>
               <input className="input-field" style={{flex: 2}} placeholder="Param Name (e.g. SYS_AUTOSTART)" value={paramName} onChange={e => setParamName(e.target.value)} />
               <select className="input-field" style={{flex: 1}} value={paramType} onChange={e => setParamType(e.target.value)}>
                 <option value="float">FLOAT</option>
                 <option value="int">INT</option>
               </select>
             </div>
             <input className="input-field" placeholder="Proposed Value" value={paramValue} onChange={e => setParamValue(e.target.value)} />
             
             <div style={{display: 'flex', gap: '8px', marginTop: '8px'}}>
               <button className="btn btn-secondary" style={{flex: 1}} onClick={() => handleParamAction("read")}>READ</button>
               <button className="btn btn-primary" style={{flex: 1}} onClick={() => handleParamAction("write")}>WRITE & VERIFY</button>
             </div>
          </div>
        </div>

      </div>
      
      {/* Test Event Log */}
      <div className="glass-panel" style={{display: 'flex', flexDirection: 'column'}}>
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px'}}>
          <h3 style={{margin: 0}}>Test Session Log</h3>
          <div style={{display: 'flex', gap: '8px'}}>
            <button className="btn btn-secondary" style={{padding: '4px 8px'}} onClick={copyLog}><Download size={14}/></button>
            <button className="btn btn-secondary" style={{padding: '4px 8px', color: 'var(--danger)'}} onClick={clearTestSessionLog}><Trash2 size={14}/></button>
          </div>
        </div>
        
        <div style={{
          background: '#111827', color: '#10B981', padding: '12px', borderRadius: '6px', 
          fontFamily: 'monospace', fontSize: '12px', height: '200px', overflowY: 'auto',
          display: 'flex', flexDirection: 'column', gap: '4px'
        }}>
          {testSessionLog.length === 0 && <div style={{color: '#6B7280'}}>No test events recorded.</div>}
          {testSessionLog.map((log, i) => (
            <div key={i} style={{display: 'flex', gap: '12px'}}>
              <span style={{color: '#6B7280'}}>{new Date(log.time).toISOString().substring(11, 23)}</span>
              <span style={{color: '#3B82F6', width: '90px'}}>{log.droneId}</span>
              <span>{log.message}</span>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}
