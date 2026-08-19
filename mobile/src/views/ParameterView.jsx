import React, { useState, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Settings, RefreshCw, Search, Save, AlertCircle, X, Edit2 } from 'lucide-react';
import { getParamMetadata, validateParameter } from '../utils/Px4Parameters';

export default function ParameterView() {
  const { drones, selectedDrones, sendParamRequest } = useDroneContext();
  
  const [searchTerm, setSearchTerm] = useState('');
  const [filterCategory, setFilterCategory] = useState('All');
  const [editState, setEditState] = useState({}); // { param_name: current_edited_value }
  const [validationErrors, setValidationErrors] = useState({});
  
  // If multiple drones are selected, just show the first one's params for now, 
  // or prompt to select exactly one.
  const targetId = Array.from(selectedDrones)[0];
  const targetDrone = targetId ? drones[targetId] : null;

  const handleRefreshAll = () => {
    if (targetId) sendParamRequest('read_all', null, null, null, targetId);
  };

  const handleParamChange = (name, val) => {
    setEditState(prev => ({ ...prev, [name]: val }));
    setValidationErrors(prev => ({ ...prev, [name]: null }));
  };

  const handleSave = (name, type) => {
     const value = editState[name];
     const validation = validateParameter(name, value, type);
     if (!validation.valid) {
        setValidationErrors(prev => ({ ...prev, [name]: validation.error }));
        return;
     }
     
     const actualType = getParamMetadata(name)?.type === 'FLOAT' ? 'float' : 'int';
     sendParamRequest('write', name, value, actualType, targetId);
     handleCancel(name);
  };

  const handleCancel = (name) => {
     setEditState(prev => {
        const next = { ...prev };
        delete next[name];
        return next;
     });
     setValidationErrors(prev => {
        const next = { ...prev };
        delete next[name];
        return next;
     });
  };

  if (!targetDrone) {
    return (
      <div className="view-container">
         <div className="card" style={{textAlign: 'center', padding: '40px'}}>
            <AlertCircle size={48} color="var(--text-muted)" style={{marginBottom: '16px'}}/>
            <h3>No Drone Selected</h3>
            <p className="text-muted">Select a drone from the Drones tab to view and edit its parameters.</p>
         </div>
      </div>
    );
  }

  const parameters = targetDrone.parameters || {};
  const isSyncing = targetDrone.paramSyncState?.pending;
  
  const getCategory = (name) => {
     if (name.startsWith('MPC_')) return 'Flight';
     if (name.startsWith('NAV_')) return 'Navigation';
     if (name.startsWith('COM_') || name.startsWith('CBRK_')) return 'Safety';
     if (name.startsWith('SENS_') || name.startsWith('CAL_')) return 'Sensors';
     if (name.startsWith('BAT_')) return 'Battery';
     if (name.startsWith('GPS_')) return 'GPS';
     if (name.startsWith('EKF2_')) return 'EKF';
     if (name.startsWith('MIS_')) return 'Mission';
     return 'Other';
  };

  const filteredParams = Object.keys(parameters)
    .filter(k => k.toLowerCase().includes(searchTerm.toLowerCase()))
    .filter(k => {
       if (filterCategory === 'All') return true;
       if (filterCategory === 'Modified') return targetDrone.paramHistory?.some(h => h.name === k);
       if (filterCategory === 'Read Only') return false; // mock implementation
       return getCategory(k) === filterCategory || (filterCategory === 'Offboard' && k.startsWith('COM_OBL'));
    })
    .sort();
    
  const paramHistory = targetDrone.paramHistory || [];

  return (
    <div className="view-container">
      <div className="view-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h2>PX4 Parameters</h2>
          <p className="text-muted text-sm">{targetId} • {Object.keys(parameters).length} parameters loaded</p>
        </div>
        <button className="btn btn-primary" onClick={handleRefreshAll} disabled={isSyncing}>
          <RefreshCw size={18} className={isSyncing ? "spin" : ""} /> {isSyncing ? "Syncing..." : "Refresh All"}
        </button>
      </div>

      <div className="card" style={{ padding: '16px', marginBottom: '24px', display: 'flex', gap: '12px' }}>
        <div className="search-bar" style={{ display: 'flex', flex: 1, alignItems: 'center', background: 'var(--bg-main)', padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
          <Search size={18} color="var(--text-muted)" style={{ marginRight: '8px' }} />
          <input 
            type="text" 
            placeholder="Search parameters (e.g., MPC_XY_P)" 
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{ border: 'none', background: 'transparent', flex: 1, outline: 'none', color: 'var(--text-main)' }}
          />
        </div>
        <select 
           className="input-field" 
           value={filterCategory} 
           onChange={(e) => setFilterCategory(e.target.value)}
           style={{ padding: '8px 12px', borderRadius: '8px', width: '180px' }}
        >
           <option value="All">All Categories</option>
           <option value="Modified">Modified</option>
           <option value="Read Only">Read Only</option>
           <option value="Flight">Flight (MPC)</option>
           <option value="Navigation">Navigation (NAV)</option>
           <option value="Safety">Safety (COM/CBRK)</option>
           <option value="Sensors">Sensors (SENS/CAL)</option>
           <option value="Battery">Battery (BAT)</option>
           <option value="GPS">GPS</option>
           <option value="EKF">EKF</option>
           <option value="Offboard">Offboard</option>
           <option value="Mission">Mission (MIS)</option>
        </select>
      </div>

      <div className="card" style={{ overflow: 'hidden', padding: 0 }}>
        {filteredParams.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
             {Object.keys(parameters).length === 0 ? "Click 'Refresh All' to load parameters from PX4." : "No parameters match your search."}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-main)' }}>
                  <th style={{ padding: '12px 16px', fontWeight: 600, fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Name</th>
                  <th style={{ padding: '12px 16px', fontWeight: 600, fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Value</th>
                  <th style={{ padding: '12px 16px', fontWeight: 600, fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', width: '100px' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredParams.map(k => {
                  const val = parameters[k];
                  const isEditing = editState[k] !== undefined;
                  
                  return (
                    <tr key={k} style={{ borderBottom: '1px solid var(--border-color)' }}>
                      <td style={{ padding: '12px 16px', fontWeight: 500, fontFamily: 'monospace' }}>{k}</td>
                      <td style={{ padding: '8px' }}>
                        {isEditing ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                             {renderEditor(k, getParamMetadata(k), editState[k], (val) => handleParamChange(k, val))}
                             {validationErrors[k] && (
                                <div style={{ color: '#ef4444', fontSize: '11px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                                   <AlertCircle size={12}/> {validationErrors[k]}
                                </div>
                             )}
                          </div>
                        ) : (
                           <span style={{ fontFamily: 'monospace' }}>
                              {val} {getParamMetadata(k)?.unit ? <span className="text-muted text-sm">{getParamMetadata(k).unit}</span> : ''}
                           </span>
                        )}
                      </td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>
                        {isEditing ? (
                           <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                              <button className="btn btn-secondary" style={{ padding: '4px 8px' }} onClick={() => handleCancel(k)}>
                                <X size={14} />
                              </button>
                              <button className="btn btn-primary" style={{ padding: '4px 8px' }} onClick={() => handleSave(k, typeof val === 'number' && Number.isInteger(val) ? 'int' : 'float')}>
                                <Save size={14} />
                              </button>
                           </div>
                        ) : (
                           <button className="btn btn-secondary" style={{ padding: '6px 12px' }} onClick={() => handleParamChange(k, val)}>
                             <Edit2 size={14} />
                           </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {paramHistory.length > 0 && (
         <div className="card" style={{ padding: '16px', marginTop: '24px' }}>
           <h3 style={{ fontSize: '14px', marginBottom: '16px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Recent Parameter Changes</h3>
           <div style={{ overflowX: 'auto' }}>
             <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13px' }}>
               <thead>
                 <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                   <th style={{ padding: '8px', color: 'var(--text-muted)' }}>Time</th>
                   <th style={{ padding: '8px', color: 'var(--text-muted)' }}>Parameter</th>
                   <th style={{ padding: '8px', color: 'var(--text-muted)' }}>Change</th>
                   <th style={{ padding: '8px', color: 'var(--text-muted)' }}>Status</th>
                 </tr>
               </thead>
               <tbody>
                 {paramHistory.map((h, i) => (
                   <tr key={i} style={{ borderBottom: '1px solid var(--border-color)' }}>
                     <td style={{ padding: '8px' }}>{new Date(h.time).toLocaleTimeString()}</td>
                     <td style={{ padding: '8px', fontFamily: 'monospace', fontWeight: 500 }}>{h.name}</td>
                     <td style={{ padding: '8px' }}>
                       <span style={{ color: 'var(--text-muted)' }}>{h.old_value !== undefined ? h.old_value : 'null'}</span> 
                       <span style={{ margin: '0 8px' }}>→</span> 
                       <span style={{ fontWeight: 500 }}>{h.new_value}</span>
                     </td>
                     <td style={{ padding: '8px' }}>
                       {h.status === 'SUCCESS' ? (
                         <span style={{ color: '#10b981', fontWeight: 500 }}>SUCCESS</span>
                       ) : (
                         <span style={{ color: '#ef4444', fontWeight: 500 }} title={h.error}>FAILED</span>
                       )}
                     </td>
                   </tr>
                 ))}
               </tbody>
             </table>
           </div>
         </div>
      )}
    </div>
  );
}

function renderEditor(name, meta, val, onChange) {
    if (meta?.type === 'ENUM') {
        return (
            <select 
                className="input-field" 
                value={val} 
                onChange={(e) => onChange(e.target.value)}
                style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--primary)', borderRadius: '4px' }}
            >
                {Object.entries(meta.options).map(([optVal, optLabel]) => (
                    <option key={optVal} value={optVal}>{optVal} - {optLabel}</option>
                ))}
            </select>
        );
    }
    if (meta?.type === 'BOOLEAN') {
        return (
            <select 
                className="input-field" 
                value={val} 
                onChange={(e) => onChange(e.target.value)}
                style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--primary)', borderRadius: '4px' }}
            >
                <option value={0}>0 - False</option>
                <option value={1}>1 - True</option>
            </select>
        );
    }
    if (meta?.type === 'BITMASK') {
        const numVal = parseInt(val) || 0;
        return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', background: 'var(--bg-main)', padding: '8px', borderRadius: '4px', border: '1px solid var(--border-color)' }}>
                {Object.entries(meta.options).map(([bitStr, label]) => {
                    const bit = parseInt(bitStr);
                    const isSet = (numVal & bit) === bit;
                    return (
                        <label key={bit} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer' }}>
                            <input 
                                type="checkbox" 
                                checked={isSet}
                                onChange={(e) => {
                                    const newVal = e.target.checked ? (numVal | bit) : (numVal & ~bit);
                                    onChange(newVal);
                                }}
                            />
                            {label}
                        </label>
                    );
                })}
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>Value: {numVal}</div>
            </div>
        );
    }
    // Float, Int or fallback
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <input 
                type="number" 
                step={meta?.type === 'FLOAT' ? "any" : "1"}
                value={val} 
                onChange={(e) => onChange(e.target.value)}
                style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--primary)', borderRadius: '4px' }}
            />
            {meta?.unit && <span className="text-muted text-sm">{meta.unit}</span>}
        </div>
    );
}
