import React, { useState, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Settings, RefreshCw, Search, Save, AlertCircle } from 'lucide-react';

export default function ParameterView() {
  const { drones, selectedDrones, sendParamRequest } = useDroneContext();
  
  const [searchTerm, setSearchTerm] = useState('');
  const [editState, setEditState] = useState({}); // { param_name: current_edited_value }
  
  // If multiple drones are selected, just show the first one's params for now, 
  // or prompt to select exactly one.
  const targetId = Array.from(selectedDrones)[0];
  const targetDrone = targetId ? drones[targetId] : null;

  const handleRefreshAll = () => {
    if (targetId) sendParamRequest('read_all', null, null, null, targetId);
  };

  const handleParamChange = (name, val) => {
    setEditState(prev => ({ ...prev, [name]: val }));
  };

  const handleSave = (name, originalType) => {
    const val = editState[name];
    if (val !== undefined && targetId) {
       // Infer type based on whether it's an int or float in JS, but it's string from input
       const isFloat = val.includes('.');
       const paramType = isFloat ? 'float' : 'int';
       sendParamRequest('write', name, val, paramType, targetId);
       
       // Clear edit state for this param so it returns to viewing mode while syncing
       setEditState(prev => {
          const next = { ...prev };
          delete next[name];
          return next;
       });
    }
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
  
  const filteredParams = Object.keys(parameters)
    .filter(k => k.toLowerCase().includes(searchTerm.toLowerCase()))
    .sort();

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

      <div className="card" style={{ padding: '16px', marginBottom: '24px' }}>
        <div className="search-bar" style={{ display: 'flex', alignItems: 'center', background: 'var(--bg-main)', padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
          <Search size={18} color="var(--text-muted)" style={{ marginRight: '8px' }} />
          <input 
            type="text" 
            placeholder="Search parameters (e.g., MPC_XY_P)" 
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{ border: 'none', background: 'transparent', flex: 1, outline: 'none', color: 'var(--text-main)' }}
          />
        </div>
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
                {filteredParams.map(name => {
                  const val = parameters[name];
                  const isEditing = editState[name] !== undefined;
                  const displayVal = isEditing ? editState[name] : val;
                  
                  return (
                    <tr key={name} style={{ borderBottom: '1px solid var(--border-color)' }}>
                      <td style={{ padding: '12px 16px', fontWeight: 500, fontFamily: 'monospace' }}>{name}</td>
                      <td style={{ padding: '12px 16px' }}>
                        {isEditing ? (
                           <input 
                              type="number" 
                              step="any"
                              value={displayVal} 
                              onChange={(e) => handleParamChange(name, e.target.value)}
                              style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--primary)', borderRadius: '4px', outline: 'none' }}
                           />
                        ) : (
                           <span style={{ fontFamily: 'monospace' }}>{val}</span>
                        )}
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        {isEditing ? (
                           <div style={{ display: 'flex', gap: '8px' }}>
                              <button className="btn btn-primary" style={{ padding: '6px 12px' }} onClick={() => handleSave(name, typeof val)}>
                                <Save size={14} /> Save
                              </button>
                              <button className="btn btn-secondary" style={{ padding: '6px 12px' }} onClick={() => handleParamChange(name, undefined)}>
                                Cancel
                              </button>
                           </div>
                        ) : (
                           <button className="btn btn-secondary" style={{ padding: '6px 12px' }} onClick={() => handleParamChange(name, val)}>
                             Edit
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
    </div>
  );
}
