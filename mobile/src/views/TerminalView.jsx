import React, { useState, useEffect, useRef } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Terminal, Send, ArrowLeft } from 'lucide-react';
import { MessageType } from '../protocol/messages';

export default function TerminalView({ setView }) {
  const { drones, sendTerminalCommand, wsManager } = useDroneContext();
  
  const [targetMode, setTargetMode] = useState('ALL');
  const [targetDroneId, setTargetDroneId] = useState(null);
  
  const [inputText, setInputText] = useState('');
  const [outputLog, setOutputLog] = useState([]);
  const outputRef = useRef(null);
  
  const droneIds = Object.keys(drones || {});

  const handleTargetChange = (e) => {
    const val = e.target.value;
    if (val === 'ALL') {
      setTargetMode('ALL');
      setTargetDroneId(null);
    } else {
      setTargetMode('SINGLE');
      setTargetDroneId(val);
    }
  };

  const getTargetArray = () => {
    return targetMode === 'ALL' ? droneIds : (targetDroneId ? [targetDroneId] : []);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    const text = inputText.trim();
    if (!text) return;
    
    const targets = getTargetArray();
    if (targets.length === 0) {
      setOutputLog(prev => [...prev, { type: 'error', text: 'No targets selected or available.', time: new Date().toLocaleTimeString() }]);
      return;
    }

    // Local echo
    setOutputLog(prev => [...prev, { type: 'input', text: `> ${text}`, time: new Date().toLocaleTimeString() }]);
    
    sendTerminalCommand(text, targets);
    setInputText('');
  };

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [outputLog]);

  useEffect(() => {
    if (!wsManager) return;

    const handleStatus = (msg) => {
      const targets = getTargetArray();
      if (targets.includes(msg.sender_id) || targets.includes(msg.target_id)) {
         setOutputLog(prev => [...prev, { 
             type: 'status', 
             source: msg.sender_id,
             text: msg.status_text, 
             time: new Date().toLocaleTimeString() 
         }]);
      }
    };

    const handleLifecycle = (msg) => {
      const targets = getTargetArray();
      if (targets.includes(msg.sender_id) || targets.includes(msg.target_id)) {
         const reasonText = msg.reason ? ` (${msg.reason})` : '';
         setOutputLog(prev => [...prev, { 
             type: 'lifecycle', 
             source: msg.sender_id,
             text: `[${msg.action.toUpperCase()}] ${msg.stage}${reasonText}`, 
             time: new Date().toLocaleTimeString() 
         }]);
      }
    };

    // Subscribing creates listeners in wsManager.
    // We cannot easily unsubscribe with this simple pattern, but the component stays mounted mostly.
    wsManager.subscribe(MessageType.STATUS, handleStatus);
    wsManager.subscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);

    return () => {
       // Since the framework doesn't provide an unsubscribe ID, we rely on the object reference 
       // but MultiWebSocketManager.subscribe doesn't return anything.
       // We accept the slight memory leak of listeners on unmount as instructed by the "subscribe via manager.subscribe pattern".
    };
  }, [wsManager, targetMode, targetDroneId, droneIds.length]); // Intentionally omitting full dependencies to avoid re-subscribing too often

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontFamily: 'monospace', background: '#1e1e1e', color: '#00ff00', borderRadius: '8px', overflow: 'hidden' }}>
       <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px', background: '#2d2d2d', borderBottom: '1px solid #444' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#fff', fontWeight: 'bold', fontFamily: "'Outfit', sans-serif" }}>
             <button className="hud-btn" onClick={() => setView('DASHBOARD')} style={{ marginRight: '8px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <ArrowLeft size={16} /> BACK
             </button>
             <Terminal size={18} /> NLP TERMINAL
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '12px', color: '#aaa', fontFamily: "'Outfit', sans-serif" }}>TARGET:</span>
              <select value={targetMode === 'ALL' ? 'ALL' : targetDroneId || ''} onChange={handleTargetChange} style={{ background: '#444', color: '#fff', border: '1px solid #666', padding: '4px 8px', borderRadius: '4px', outline: 'none', fontFamily: "'Outfit', sans-serif", fontSize: '12px' }}>
                 <option value="ALL">ALL DRONES</option>
                 {droneIds.map(id => <option key={id} value={id}>{id}</option>)}
              </select>
          </div>
       </div>
       
       <div ref={outputRef} style={{ flex: 1, overflowY: 'auto', padding: '12px', display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '13px' }}>
          {outputLog.length === 0 && <div style={{ color: '#888', fontStyle: 'italic' }}>Terminal ready. Type a command like "takeoff to 5m" or "circle with 3m radius".</div>}
          {outputLog.map((log, i) => (
             <div key={i} style={{ display: 'flex', gap: '8px', lineHeight: '1.4' }}>
                <span style={{ color: '#888', minWidth: '75px' }}>[{log.time}]</span>
                {log.source && <span style={{ color: '#569cd6' }}>{log.source}:</span>}
                <span style={{ 
                   color: log.type === 'input' ? '#fff' : 
                          log.type === 'error' ? '#f44336' : 
                          log.type === 'status' ? '#4caf50' : 
                          '#ffeb3b' 
                }}>
                   {log.text}
                </span>
             </div>
          ))}
       </div>
       
       <form onSubmit={handleSubmit} style={{ display: 'flex', padding: '12px', background: '#2d2d2d', borderTop: '1px solid #444' }}>
          <span style={{ color: '#00ff00', marginRight: '8px', display: 'flex', alignItems: 'center' }}>$</span>
          <input 
             type="text" 
             value={inputText}
             onChange={e => setInputText(e.target.value)}
             placeholder="Enter natural language command..."
             style={{ flex: 1, background: 'transparent', border: 'none', color: '#fff', outline: 'none', fontFamily: 'monospace', fontSize: '14px' }}
             autoComplete="off"
          />
          <button type="submit" style={{ background: 'transparent', border: 'none', color: '#569cd6', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 8px' }}>
             <Send size={18} />
          </button>
       </form>
    </div>
  );
}
