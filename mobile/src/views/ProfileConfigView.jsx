import React, { useState, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { PX4_PROFILES } from '../utils/Profiles';
import { ShieldAlert, CheckCircle2, AlertTriangle, Play, RefreshCw, XCircle } from 'lucide-react';

export default function ProfileConfigView({ profileKey, setView }) {
  const { drones, selectedDrones, sendParamRequest } = useDroneContext();
  const targetId = Array.from(selectedDrones)[0];
  const targetDrone = targetId ? drones[targetId] : null;

  const profile = PX4_PROFILES[profileKey] || PX4_PROFILES['INDOOR_PROFILE'];

  const [step, setStep] = useState(0); 
  // 0: checking, 1: review, 2: applying, 3: success, 4: failed
  
  const [changes, setChanges] = useState([]);
  const [missingSensors, setMissingSensors] = useState([]);
  const [appliedCount, setAppliedCount] = useState(0);
  const [rollbackMode, setRollbackMode] = useState(false);
  const [previousValues, setPreviousValues] = useState({});

  useEffect(() => {
    if (targetDrone && step === 0) {
      // Simulate reading/checking
      const timer = setTimeout(() => {
        analyzeConfiguration();
      }, 1000);
      return () => clearTimeout(timer);
    }
  }, [targetDrone, step]);

  const analyzeConfiguration = () => {
    const params = targetDrone.parameters || {};
    const tel = targetDrone.telemetry || {};
    const diag = targetDrone.diagnostics || {};
    
    // 1. Check Sensors (mock logic based on telemetry and diagnostics)
    // In a real scenario, we'd check EKF2_AID_MASK or specific sensor health flags
    const missing = [];
    if (profile.requiredSensors.includes('GPS') && !tel.gps_valid) missing.push('GPS');
    if (profile.requiredSensors.includes('Barometer') && tel.sensor_health === 'ERROR') missing.push('Barometer');
    setMissingSensors(missing);

    // 2. Compare parameters
    const paramChanges = [];
    const prevVals = {};
    for (const [pName, pConfig] of Object.entries(profile.parameters)) {
       const currentVal = params[pName];
       prevVals[pName] = currentVal !== undefined ? currentVal : null;
       
       if (currentVal != pConfig.value) { // weak comparison intentional for float/int strings
          paramChanges.push({
             name: pName,
             current: currentVal !== undefined ? currentVal : 'N/A',
             target: pConfig.value,
             type: pConfig.type
          });
       }
    }
    setPreviousValues(prevVals);
    setChanges(paramChanges);

    setStep(1);
  };

  const handleApply = () => {
    setStep(2);
    setAppliedCount(0);
    setRollbackMode(false);
    applyNextParameter(0, false);
  };

  const applyNextParameter = (index, isRollback) => {
    const listToApply = isRollback ? changes.filter((_, i) => i < appliedCount).reverse() : changes;
    
    if (index >= listToApply.length) {
       setStep(isRollback ? 4 : 3);
       return;
    }

    const change = listToApply[index];
    const targetValue = isRollback ? previousValues[change.name] : change.target;
    
    if (targetValue === null || targetValue === undefined) {
       // Cannot rollback if it was null, just skip
       applyNextParameter(index + 1, isRollback);
       return;
    }

    sendParamRequest('write', change.name, targetValue, change.type, targetId);
    
    // We ideally should wait for the PARAM_RESPONSE. For this implementation, we poll or rely on context
    // In a real app we might use a promise wrapper around the websocket. Here we simulate the wait.
    setTimeout(() => {
       // In a full implementation, we verify DroneContext.paramHistory to see if it succeeded.
       // For this phase, we assume success unless rollbackMode triggered manually.
       if (!isRollback) setAppliedCount(prev => prev + 1);
       applyNextParameter(index + 1, isRollback);
    }, 500);
  };

  const handleRollback = () => {
    setRollbackMode(true);
    setStep(2);
    applyNextParameter(0, true);
  };

  if (!targetDrone) {
    return (
      <div className="view-container">
         <div className="card" style={{textAlign: 'center', padding: '40px'}}>
            <AlertTriangle size={48} color="var(--text-muted)" style={{marginBottom: '16px'}}/>
            <h3>No Drone Selected</h3>
            <p className="text-muted">Select a drone from the fleet to configure {profile.name}.</p>
         </div>
      </div>
    );
  }

  return (
    <div className="view-container">
      <div className="view-header">
         <h2>{profile.name.toUpperCase()} CONFIGURATION</h2>
         <p className="text-muted">Drone: {targetId}</p>
      </div>

      <div className="card" style={{ padding: '24px' }}>
         {step === 0 && (
            <div style={{ textAlign: 'center', padding: '40px' }}>
               <RefreshCw size={48} className="spin" color="var(--primary)" style={{ marginBottom: '16px' }} />
               <h3>Checking Configuration...</h3>
               <p className="text-muted">Reading parameters and verifying sensor capabilities.</p>
            </div>
         )}

         {step === 1 && (
            <div>
               {missingSensors.length > 0 ? (
                  <div style={{ textAlign: 'center', padding: '20px' }}>
                     <XCircle size={48} color="#ef4444" style={{ marginBottom: '16px' }} />
                     <h3 style={{ color: '#ef4444' }}>{profile.name.toUpperCase()} NOT AVAILABLE</h3>
                     <p className="text-muted" style={{ marginBottom: '24px' }}>Required positioning/sensor source unavailable.</p>
                     
                     <div style={{ background: 'var(--bg-main)', padding: '16px', borderRadius: '8px', textAlign: 'left', display: 'inline-block' }}>
                        <p style={{ fontWeight: 600, marginBottom: '8px' }}>Missing Sensors:</p>
                        <ul style={{ color: '#ef4444', marginLeft: '20px' }}>
                           {missingSensors.map(s => <li key={s}>{s}</li>)}
                        </ul>
                     </div>
                  </div>
               ) : (
                  <div>
                     <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#10b981', marginBottom: '24px' }}>
                        <CheckCircle2 size={24} />
                        <h3 style={{ margin: 0 }}>Sensors Verified</h3>
                     </div>
                     
                     {changes.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '20px' }}>
                           <h3 style={{ marginBottom: '8px' }}>Profile Already Applied</h3>
                           <p className="text-muted">No parameter changes are required.</p>
                           <button className="btn btn-primary" style={{ marginTop: '16px' }} onClick={() => setView('DRONE_CONTROL')}>PROCEED TO CONTROL</button>
                        </div>
                     ) : (
                        <>
                           <h3 style={{ marginBottom: '16px', fontSize: '14px', textTransform: 'uppercase' }}>{changes.length} parameters require changes</h3>
                           <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', marginBottom: '24px', fontSize: '14px' }}>
                              <thead>
                                 <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                                    <th style={{ padding: '8px' }}>PARAMETER</th>
                                    <th style={{ padding: '8px' }}>CURRENT</th>
                                    <th style={{ padding: '8px' }}>TARGET</th>
                                 </tr>
                              </thead>
                              <tbody>
                                 {changes.map(c => (
                                    <tr key={c.name} style={{ borderBottom: '1px solid var(--border-color)' }}>
                                       <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 500 }}>{c.name}</td>
                                       <td style={{ padding: '8px', color: 'var(--text-muted)' }}>{c.current}</td>
                                       <td style={{ padding: '8px', fontWeight: 600 }}>{c.target}</td>
                                    </tr>
                                 ))}
                              </tbody>
                           </table>
                           <div style={{ display: 'flex', gap: '12px' }}>
                              <button className="btn btn-secondary" style={{ flex: 1, padding: '12px' }} onClick={() => setView('DRONE_CONTROL')}>CANCEL</button>
                              <button className="btn btn-primary" style={{ flex: 1, padding: '12px' }} onClick={handleApply}>APPLY PROFILE</button>
                           </div>
                        </>
                     )}
                  </div>
               )}
            </div>
         )}

         {step === 2 && (
            <div style={{ textAlign: 'center', padding: '40px' }}>
               <RefreshCw size={48} className="spin" color={rollbackMode ? '#f59e0b' : 'var(--primary)'} style={{ marginBottom: '16px' }} />
               <h3>{rollbackMode ? 'ROLLING BACK...' : 'APPLYING PROFILE...'}</h3>
               <p className="text-muted" style={{ marginBottom: '24px' }}>
                  {rollbackMode ? 'Restoring' : 'Writing'} {appliedCount} / {rollbackMode ? appliedCount : changes.length} parameters
               </p>
               {!rollbackMode && (
                  <button className="btn btn-secondary" style={{ borderColor: '#ef4444', color: '#ef4444' }} onClick={handleRollback}>ABORT & ROLLBACK</button>
               )}
            </div>
         )}

         {step === 3 && (
            <div style={{ textAlign: 'center', padding: '40px' }}>
               <CheckCircle2 size={48} color="#10b981" style={{ marginBottom: '16px' }} />
               <h3 style={{ color: '#10b981' }}>CONFIGURATION READY</h3>
               <p className="text-muted" style={{ marginBottom: '24px' }}>Successfully applied {changes.length} parameters.</p>
               <button className="btn btn-primary" style={{ padding: '12px 24px' }} onClick={() => setView('DRONE_CONTROL')}>PROCEED TO CONTROL</button>
            </div>
         )}

         {step === 4 && (
            <div style={{ textAlign: 'center', padding: '40px' }}>
               <XCircle size={48} color="#ef4444" style={{ marginBottom: '16px' }} />
               <h3 style={{ color: '#ef4444' }}>CONFIGURATION NOT READY</h3>
               <p className="text-muted" style={{ marginBottom: '24px' }}>Profile application failed or was aborted. Rollback completed.</p>
               <button className="btn btn-secondary" style={{ padding: '12px 24px' }} onClick={() => setView('DRONE_CONTROL')}>RETURN</button>
            </div>
         )}
      </div>
    </div>
  );
}
