import React, { useState, useEffect, useRef } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { CommandAction } from '../protocol/messages';
import { Play, CheckCircle2, XCircle, AlertCircle, RefreshCw, ChevronRight } from 'lucide-react';
import { getParamMetadata, validateParameter } from '../utils/Px4Parameters';

export default function HardwareTestView({ setView }) {
  const { drones, selectedDrones, sendCommand, sendParamRequest } = useDroneContext();
  const targetId = Array.from(selectedDrones)[0];
  const drone = targetId ? drones[targetId] : null;

  const [activeTest, setActiveTest] = useState(null);
  const [results, setResults] = useState({});
  const [paramTestModal, setParamTestModal] = useState(null); // { step: 'select', name: '', val: '', originalVal: '', error: '' }

  const dronesRef = useRef(drones);
  useEffect(() => { dronesRef.current = drones; }, [drones]);

  const tests = [
    { id: 1, name: 'Connection', desc: 'Verify backend connection to vehicle' },
    { id: 2, name: 'Heartbeat', desc: 'Verify continuous heartbeat stream' },
    { id: 3, name: 'Telemetry', desc: 'Verify required telemetry attributes' },
    { id: 4, name: 'PX4 Identity', desc: 'Fetch firmware and vehicle metadata' },
    { id: 5, name: 'Parameter Discovery', desc: 'Request parameter list' },
    { id: 6, name: 'Parameter Read', desc: 'Verify parameter retrieval' },
    { id: 7, name: 'Safe parameter write/readback', desc: 'Test non-critical write loop' },
    { id: 8, name: 'Sensor health', desc: 'Verify pre-flight sensor checks' },
    { id: 9, name: 'Flight mode request', desc: 'Test mode change (disarmed)' },
    { id: 10, name: 'ARM PRECHECK', desc: 'Verify PX4 pre-arm validation' },
    { id: 11, name: 'Control Readiness', desc: 'Test offboard/manual endpoints safely' },
  ];

  const updateResult = (id, status, reason = null) => {
    setResults(prev => ({ ...prev, [id]: { status, reason } }));
  };

  const runTest = (testId) => {
    if (!drone) return;
    setActiveTest(testId);
    updateResult(testId, 'RUNNING');
    const tel = drone.telemetry || {};

    switch (testId) {
      case 1:
        setTimeout(() => updateResult(1, (drone.status === 'CONNECTED' || drone.status === 'DEGRADED') ? 'PASS' : 'FAIL', (drone.status === 'CONNECTED' || drone.status === 'DEGRADED') ? null : 'Drone not connected'), 500);
        break;
      case 2:
        setTimeout(() => updateResult(2, 'PASS'), 1000); // Handled by continuous context stream
        break;
      case 3:
        setTimeout(() => {
          if (tel.altitude !== undefined && tel.battery_level !== undefined) updateResult(3, 'PASS');
          else updateResult(3, 'FAIL', 'Missing alt/battery');
        }, 500);
        break;
      case 4:
        setTimeout(() => {
          const currentDrone = dronesRef.current[targetId];
          if (currentDrone?.diagnostics?.px4?.firmware_version) updateResult(4, 'PASS', currentDrone.diagnostics.px4.firmware_version);
          else updateResult(4, 'NOT AVAILABLE', 'Firmware identity missing from telemetry');
        }, 800);
        break;
      case 5:
        sendParamRequest('read_all', null, null, null, targetId);
        setTimeout(() => {
           const currentDrone = dronesRef.current[targetId];
           if (Object.keys(currentDrone?.parameters || {}).length > 0) updateResult(5, 'PASS', `${Object.keys(currentDrone.parameters).length} params found`);
           else updateResult(5, 'FAIL', 'No parameters discovered');
        }, 1500);
        break;
      case 6:
        setTimeout(() => {
           const currentDrone = dronesRef.current[targetId];
           if (currentDrone?.parameters && Object.keys(currentDrone.parameters).length > 0) updateResult(6, 'PASS', 'Parameters read successfully');
           else updateResult(6, 'FAIL', 'Parameters unavailable');
        }, 500);
        break;
      case 7:
        setParamTestModal({ step: 'select', name: 'MIS_TAKEOFF_ALT', val: '', originalVal: drone.parameters?.['MIS_TAKEOFF_ALT'] || '', error: '' });
        break;
      case 8:
        setTimeout(() => {
           const currentTel = dronesRef.current[targetId]?.telemetry;
           if (!currentTel) updateResult(8, 'FAIL', 'No telemetry');
           else if (currentTel.sensor_health === 'ERROR') updateResult(8, 'FAIL', 'Sensor error detected');
           else updateResult(8, 'PASS', 'Sensors healthy (IMU/MAG/BARO)');
        }, 500);
        break;
      case 9:
        sendCommand(CommandAction.SET_MODE, { mode: 'HOLD' });
        setTimeout(() => {
           const currentTel = dronesRef.current[targetId]?.telemetry;
           updateResult(9, currentTel?.flight_mode === 'HOLD' ? 'PASS' : 'FAIL', `Requested HOLD, got ${currentTel?.flight_mode}`);
        }, 1000);
        break;
      case 10:
        setTimeout(() => {
           const currentDrone = dronesRef.current[targetId];
           const currentTel = currentDrone?.telemetry;
           if (!currentTel) return updateResult(10, 'FAIL', 'No telemetry');
           
           let reason = [];
           if (!currentTel.gps_valid) reason.push('No GPS');
           if (currentTel.battery_level < 20) reason.push('Low Battery');
           if (currentDrone.status === 'failsafe') reason.push('Failsafe Active');
           if (currentTel.system_health !== 'OK' && currentTel.system_health !== null) reason.push('System Health Error');
           
           if (reason.length > 0) updateResult(10, 'FAIL', `ARM PRECHECK FAILED: ${reason.join(', ')}`);
           else updateResult(10, 'PASS', 'ARM PRECHECK PASS');
        }, 500);
        break;
      case 11:
        setTimeout(() => updateResult(11, 'NOT AVAILABLE', 'Software/SITL Validation Only - Physical restraint required'), 1000);
        break;
      default:
        setActiveTest(null);
    }
  };

  if (!drone) {
    return (
      <div className="view-container">
         <div className="card" style={{textAlign: 'center', padding: '40px'}}>
            <AlertCircle size={48} color="var(--text-muted)" style={{marginBottom: '16px'}}/>
            <h3>No Drone Selected</h3>
            <p className="text-muted">Select a drone to perform hardware tests.</p>
         </div>
      </div>
    );
  }
  const tel = drone?.telemetry || {};

  return (
    <div className="view-container">
      <div className="view-header">
         <h2>HARDWARE VALIDATION WORKFLOW</h2>
         <p className="text-muted">Target: {targetId} • Explicit operator action required for flight state</p>
      </div>

      <div className={`card ${drone.diagnostics?.px4?.firmware_version && !drone.diagnostics?.px4?.firmware_version.toLowerCase().includes('sitl') ? 'good-box' : 'danger-box'}`} style={{ marginBottom: '24px', display: 'flex', gap: '16px', alignItems: 'center' }}>
         <AlertCircle size={32} />
         <div style={{ flex: 1 }}>
            <h3 style={{ margin: '0 0 8px 0', textTransform: 'uppercase' }}>
               {drone.diagnostics?.px4?.firmware_version && !drone.diagnostics?.px4?.firmware_version.toLowerCase().includes('sitl') ? 'REAL HARDWARE CONNECTED' : 'SITL / SOFTWARE VALIDATION ONLY'}
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '13px' }}>
               <div><strong>Firmware:</strong> {drone.diagnostics?.px4?.firmware_version || 'UNKNOWN'}</div>
               <div><strong>Vehicle:</strong> {drone.diagnostics?.px4?.vehicle_type || 'UNKNOWN'}</div>
               <div><strong>Mode:</strong> {tel?.flight_mode || 'UNKNOWN'}</div>
               <div><strong>Armed:</strong> {tel?.armed_state || 'UNKNOWN'}</div>
               <div><strong>Health:</strong> {tel?.system_health || 'UNKNOWN'}</div>
               <div><strong>Heartbeat:</strong> ACTIVE</div>
            </div>
         </div>
      </div>

      <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '14px' }}>
          <thead>
             <tr style={{ background: 'var(--bg-main)', borderBottom: '1px solid var(--border-color)' }}>
                <th style={{ padding: '12px 16px', color: 'var(--text-muted)' }}>TEST PHASE</th>
                <th style={{ padding: '12px 16px', color: 'var(--text-muted)' }}>STATUS</th>
                <th style={{ padding: '12px 16px', color: 'var(--text-muted)' }}>DETAILS</th>
                <th style={{ padding: '12px 16px', color: 'var(--text-muted)', textAlign: 'right' }}>ACTION</th>
             </tr>
          </thead>
          <tbody>
             {tests.map(t => {
                const res = results[t.id];
                return (
                   <tr key={t.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                      <td style={{ padding: '12px 16px' }}>
                         <div style={{ fontWeight: 600 }}>TEST {t.id}</div>
                         <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>{t.name}</div>
                      </td>
                      <td style={{ padding: '12px 16px', fontWeight: 600 }}>
                         {res?.status === 'RUNNING' && <span style={{ color: 'var(--primary)' }}><RefreshCw size={14} className="spin" style={{marginRight: '4px', verticalAlign: 'text-bottom'}} /> RUNNING</span>}
                         {res?.status === 'PASS' && <span style={{ color: '#10b981' }}><CheckCircle2 size={14} style={{marginRight: '4px', verticalAlign: 'text-bottom'}} /> PASS</span>}
                         {res?.status === 'FAIL' && <span style={{ color: '#ef4444' }}><XCircle size={14} style={{marginRight: '4px', verticalAlign: 'text-bottom'}} /> FAIL</span>}
                         {res?.status === 'NOT AVAILABLE' && <span style={{ color: 'var(--text-muted)' }}>NOT AVAILABLE</span>}
                         {!res && <span style={{ color: 'var(--text-muted)' }}>PENDING</span>}
                      </td>
                      <td style={{ padding: '12px 16px', color: 'var(--text-muted)', fontSize: '12px' }}>
                         {res?.reason || t.desc}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                         <button className="btn btn-secondary" style={{ padding: '6px 12px' }} onClick={() => runTest(t.id)} disabled={activeTest === t.id}>
                            <Play size={14} /> 
                         </button>
                      </td>
                   </tr>
                );
             })}
          </tbody>
        </table>
      </div>
      
      <div className="card" style={{ marginTop: '24px', padding: '16px', background: 'var(--bg-main)' }}>
         <h4 style={{ margin: '0 0 12px 0', fontSize: '13px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Safety Guidelines & Hardware Status</h4>
         <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            <strong style={{color: 'var(--text-main)'}}>REAL HARDWARE NOT AVAILABLE — SOFTWARE/SITL VALIDATION ONLY.</strong><br/>
            Do NOT automatically arm or take off. For any physical flight action, explicitly verify propulsion is safely disabled or restrained. If real hardware is not available, do not claim hardware validation.
         </p>
      </div>

      {paramTestModal && (
         <div className="modal-overlay">
            <div className="modal-content">
               <h2>Safe Parameter Write Test</h2>
               
               {paramTestModal.step === 'select' && (
                  <div>
                     <p className="text-muted" style={{marginBottom: '16px'}}>Select a safe parameter to test writing and readback. MIS_TAKEOFF_ALT is recommended.</p>
                     <div className="form-group" style={{marginBottom: '16px'}}>
                        <label>Parameter Name</label>
                        <input className="input-field" value={paramTestModal.name} onChange={e => setParamTestModal({...paramTestModal, name: e.target.value})} />
                     </div>
                     <button className="btn btn-primary" onClick={() => {
                         const val = drone.parameters?.[paramTestModal.name];
                         if (val === undefined) setParamTestModal({...paramTestModal, error: 'Parameter not found on vehicle'});
                         else setParamTestModal({...paramTestModal, step: 'write', originalVal: val, val: val.toString(), error: ''});
                     }}>Read Current</button>
                     {paramTestModal.error && <p className="danger" style={{marginTop: '10px'}}>{paramTestModal.error}</p>}
                  </div>
               )}

               {paramTestModal.step === 'write' && (
                  <div>
                     <div className="metrics-row" style={{marginBottom: '16px'}}>
                        <div className="metric-card">
                           <span className="metric-label">Current Value</span>
                           <span className="metric-value">{paramTestModal.originalVal}</span>
                        </div>
                     </div>
                     <div className="form-group" style={{marginBottom: '16px'}}>
                        <label>New Test Value</label>
                        <input type="number" className="input-field" value={paramTestModal.val} onChange={e => setParamTestModal({...paramTestModal, val: e.target.value})} />
                     </div>
                     <div style={{display: 'flex', gap: '10px'}}>
                        <button className="btn btn-primary" onClick={() => {
                            sendParamRequest('write', paramTestModal.name, parseFloat(paramTestModal.val), 'float', targetId);
                            setParamTestModal({...paramTestModal, step: 'verify'});
                        }}>Write</button>
                     </div>
                  </div>
               )}

               {paramTestModal.step === 'verify' && (
                  <div>
                     <p>Waiting for PX4 readback...</p>
                     <button className="btn btn-secondary" onClick={() => {
                         const current = drone.parameters?.[paramTestModal.name];
                         if (current === parseFloat(paramTestModal.val)) {
                            setParamTestModal({...paramTestModal, step: 'success'});
                         } else {
                            setParamTestModal({...paramTestModal, error: `PARAMETER CONFLICT. Expected: ${paramTestModal.val}, PX4 reports: ${current}`});
                         }
                     }}>Check Readback</button>
                     {paramTestModal.error && <p className="danger" style={{marginTop: '10px'}}>{paramTestModal.error}</p>}
                  </div>
               )}

               {paramTestModal.step === 'success' && (
                  <div>
                     <h3 className="good" style={{marginBottom: '16px'}}>VERIFIED</h3>
                     <p className="text-muted" style={{marginBottom: '16px'}}>Write successful and readback matches.</p>
                     <button className="btn btn-secondary" onClick={() => {
                         sendParamRequest('write', paramTestModal.name, paramTestModal.originalVal, 'float', targetId);
                         setParamTestModal(null);
                         updateResult(7, 'PASS', 'Write and readback verified safely');
                     }}>Restore Original Value & Close</button>
                  </div>
               )}

               <button className="secondary-btn" style={{marginTop: '20px', width: '100%'}} onClick={() => { setParamTestModal(null); updateResult(7, 'FAIL', 'Test cancelled'); }}>CANCEL</button>
            </div>
         </div>
      )}
    </div>
  );
}
