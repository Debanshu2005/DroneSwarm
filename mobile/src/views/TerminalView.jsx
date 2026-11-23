import React, { useState, useEffect, useRef } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Terminal, Send, ArrowLeft } from 'lucide-react';
import { MessageType } from '../protocol/messages';

export default function TerminalView({ setView }) {
  const { drones, sendTerminalCommand, wsManager, terminalLog, appendTerminalLog } = useDroneContext();

  const [targetMode, setTargetMode] = useState('ALL');
  const [targetDroneId, setTargetDroneId] = useState(null);

  const [inputText, setInputText] = useState('');
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

  // Stable ref for targets so listeners don't re-subscribe on target changes
  const targetsRef = useRef(getTargetArray());
  useEffect(() => {
    targetsRef.current = getTargetArray();
  }, [targetMode, targetDroneId, droneIds]);

  const handleSubmit = (e) => {
    e.preventDefault();
    const text = inputText.trim();
    if (!text) return;

    const targets = getTargetArray();
    const isBroadcast = targetMode === 'ALL';
    const success = sendTerminalCommand(text, targets, isBroadcast);

    if (!success) {
      appendTerminalLog({
        type: 'error',
        text: 'Not connected; command not sent',
        time: new Date().toLocaleTimeString()
      });
      return;
    }

    // Local echo on success
    appendTerminalLog({
      type: 'input',
      text: `> ${text}`,
      time: new Date().toLocaleTimeString()
    });
    setInputText('');
  };

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [terminalLog]);

  useEffect(() => {
    if (!wsManager) return;

    const handleStatus = (msg) => {
      const targets = targetsRef.current;
      if (targets.includes(msg.sender_id) || targets.includes(msg.target_id)) {
        appendTerminalLog({
          type: 'status',
          severity: msg.severity,
          source: msg.sender_id,
          text: msg.status_text,
          time: new Date().toLocaleTimeString()
        });
      }
    };

    const handleLifecycle = (msg) => {
      const targets = targetsRef.current;
      if (targets.includes(msg.sender_id) || targets.includes(msg.target_id)) {
        const reasonText = msg.reason ? ` (${msg.reason})` : '';
        appendTerminalLog({
          type: 'lifecycle',
          source: msg.sender_id,
          text: `[${msg.action.toUpperCase()}] ${msg.stage}${reasonText}`,
          time: new Date().toLocaleTimeString()
        });
      }
    };

    // Subscribing creates listeners in wsManager.
    // Subscribe once on mount with stable handlers using targetsRef; unsubscribe on unmount.
    wsManager.subscribe(MessageType.STATUS, handleStatus);
    wsManager.subscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);

    return () => {
      wsManager.unsubscribe(MessageType.STATUS, handleStatus);
      wsManager.unsubscribe(MessageType.COMMAND_LIFECYCLE, handleLifecycle);
    };
  }, [wsManager, appendTerminalLog]);

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
        {(!terminalLog || terminalLog.length === 0) && <div style={{ color: '#888', fontStyle: 'italic' }}>Terminal ready. Type a command like "takeoff to 5m" or "circle with 3m radius".</div>}
        {(terminalLog || []).map((log, i) => (
          <div key={i} style={{ display: 'flex', gap: '8px', lineHeight: '1.4' }}>
            <span style={{ color: '#888', minWidth: '75px' }}>[{log.time}]</span>
            {log.source && <span style={{ color: '#569cd6' }}>{log.source}:</span>}
            <span style={{
              color: log.type === 'input' ? '#fff' :
                (log.type === 'error' || log.severity === 'error' || log.severity === 'critical') ? '#f44336' :
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
