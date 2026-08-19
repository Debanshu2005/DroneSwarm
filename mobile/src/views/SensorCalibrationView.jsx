import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Compass, Wind, Activity, Maximize, AlertCircle, CheckCircle2 } from 'lucide-react';

export default function SensorCalibrationView() {
  const { drones, selectedDrones } = useDroneContext();
  
  const targetId = Array.from(selectedDrones)[0];
  const targetDrone = targetId ? drones[targetId] : null;

  if (!targetDrone) {
    return (
      <div className="view-container">
         <div className="card" style={{textAlign: 'center', padding: '40px'}}>
            <AlertCircle size={48} color="var(--text-muted)" style={{marginBottom: '16px'}}/>
            <h3>No Drone Selected</h3>
            <p className="text-muted">Select a drone to view sensor calibration status.</p>
         </div>
      </div>
    );
  }

  // Telemetry might not have full sensor detail yet, we mock the UI for the GCS structure
  // In production, this would read from a new SENSOR_STATUS message or extended telemetry.
  const isArmed = targetDrone.telemetry?.armed_state === 'ARMED';
  
  const sensors = [
     { name: 'Gyroscope (IMU 0)', icon: <Activity size={20}/>, status: 'CALIBRATED', color: '#10b981' },
     { name: 'Accelerometer (IMU 0)', icon: <Maximize size={20}/>, status: 'CALIBRATED', color: '#10b981' },
     { name: 'Magnetometer (Compass)', icon: <Compass size={20}/>, status: 'NEEDS CALIBRATION', color: '#f59e0b' },
     { name: 'Barometer', icon: <Wind size={20}/>, status: 'OK', color: '#10b981' },
  ];

  return (
    <div className="view-container">
      <div className="view-header">
        <h2>Sensor Calibration</h2>
        <p className="text-muted">{targetId} • Pre-flight Sensor Status</p>
      </div>

      {isArmed && (
         <div className="card" style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid #ef4444', marginBottom: '24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: '#ef4444' }}>
               <AlertCircle size={24} />
               <div>
                  <h4 style={{ margin: 0 }}>Drone is ARMED</h4>
                  <p style={{ margin: 0, fontSize: '12px' }}>Sensor calibration is disabled while the vehicle is armed.</p>
               </div>
            </div>
         </div>
      )}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
         <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
            <thead>
               <tr style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-main)' }}>
                  <th style={{ padding: '16px', fontWeight: 600, fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Sensor</th>
                  <th style={{ padding: '16px', fontWeight: 600, fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Status</th>
                  <th style={{ padding: '16px', fontWeight: 600, fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', textAlign: 'right' }}>Action</th>
               </tr>
            </thead>
            <tbody>
               {sensors.map((sensor, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid var(--border-color)' }}>
                     <td style={{ padding: '16px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                           <div style={{ color: 'var(--text-muted)' }}>{sensor.icon}</div>
                           <span style={{ fontWeight: 500 }}>{sensor.name}</span>
                        </div>
                     </td>
                     <td style={{ padding: '16px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: sensor.color, fontWeight: 500, fontSize: '14px' }}>
                           {sensor.status === 'CALIBRATED' || sensor.status === 'OK' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                           {sensor.status}
                        </div>
                     </td>
                     <td style={{ padding: '16px', textAlign: 'right' }}>
                        <button className="btn btn-secondary" disabled={isArmed}>
                           Calibrate
                        </button>
                     </td>
                  </tr>
               ))}
            </tbody>
         </table>
      </div>
      
      <div style={{ marginTop: '24px', textAlign: 'center' }}>
         <p className="text-muted text-sm">Note: Calibration commands require the vehicle to be disarmed and stationary on a level surface.</p>
      </div>
    </div>
  );
}
