import React, { useState, useEffect } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useDroneContext } from './context/DroneContext';
import { LayoutDashboard, Map as MapIcon, Route, Network, ShieldAlert, Activity, Settings, Menu, X, Navigation, TestTube } from 'lucide-react';
import { ScreenOrientation } from '@capacitor/screen-orientation';
import './App.css';

import DashboardView from './views/DashboardView';
import DronesView from './views/DronesView';
import DroneControlView from './views/DroneControlView';
import MissionView from './views/MissionView';
import SwarmView from './views/SwarmView';
import SafetyView from './views/SafetyView';
import DiagnosticsView from './views/DiagnosticsView';
import SettingsView from './views/SettingsView';
import ParameterView from './views/ParameterView';
import ProfileConfigView from './views/ProfileConfigView';
import SensorCalibrationView from './views/SensorCalibrationView';
import HardwareTestView from './views/HardwareTestView';
import SystemHealthView from './views/SystemHealthView';
import LogsView from './views/LogsView';
import AdvancedTestView from './views/AdvancedTestView';
import MapView from './views/MapView';

function App() {
  const { isConnected, testMode, indoorMode, nowMs, wsManager, drones } = useDroneContext();
  const [currentView, setCurrentView] = useState('DASHBOARD');
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  useEffect(() => {
    const applyOrientation = async () => {
      try {
        if (currentView === 'FLIGHT') {
          await ScreenOrientation.lock({ orientation: 'landscape' });
        } else {
          await ScreenOrientation.lock({ orientation: 'portrait' });
        }
      } catch (e) {
        console.warn('Screen orientation lock not supported on this platform', e);
      }
    };
    applyOrientation();
  }, [currentView]);

  
  // Latency calculation from wsManager
  const latency = wsManager?.latency || 0;
  
  const renderView = () => {
    switch (currentView) {
      case 'DASHBOARD': return <DashboardView />;
      case 'DRONES': return <DronesView setView={setCurrentView} />;
      case 'MISSION': return <MissionView />;
      case 'SWARM': return <SwarmView />;
      case 'SAFETY': return <SafetyView />;
      case 'DIAGNOSTICS': return <DiagnosticsView />;
      case 'SETTINGS': return <SettingsView />;
      case 'PARAMETERS': return <ParameterView />;
      case 'SENSORS': return <SensorCalibrationView />;
      case 'INDOOR_PROFILE': return <ProfileConfigView profileKey="INDOOR_PROFILE" setView={setCurrentView} />;
      case 'OUTDOOR_PROFILE': return <ProfileConfigView profileKey="OUTDOOR_GPS_PROFILE" setView={setCurrentView} />;
      case 'HARDWARE_TEST': return <HardwareTestView setView={setCurrentView} />;
      case 'SYSTEM_HEALTH': return <SystemHealthView />;
      case 'LOGS': return <LogsView />;
      case 'ADVANCED_TEST': return <AdvancedTestView />;
      case 'FLIGHT': return <DroneControlView setView={setCurrentView} />;
      case 'MAP': return <MapView />;
      default: return <DashboardView />;
    }
  };

  useEffect(() => {
    const handlePopState = () => {
      if (isDrawerOpen) {
        setIsDrawerOpen(false);
      }
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [isDrawerOpen]);

  const navigateTo = (view) => {
    setCurrentView(view);
    setIsDrawerOpen(false);
  };

  const getHardwareIdentityStatus = () => {
     if (isConnected !== "CONNECTED") return { text: "OFFLINE", color: "var(--danger)" };
     if (testMode) return { text: "SIMULATION / SITL", color: "var(--warning)" };
     
     const droneList = Object.values(drones || {});
     if (droneList.length === 0) return { text: "UNKNOWN", color: "var(--text-muted)" };
     
     // Check if any drone provides real hardware evidence
     let hasReal = false;
     let allStale = true;
     
     for (const d of droneList) {
         if (d.status !== 'STALE' && d.status !== 'OFFLINE') {
             allStale = false;
         }
         const fw = d.diagnostics?.px4?.firmware_version || "";
         if (fw && !fw.toLowerCase().includes('sitl') && !fw.toLowerCase().includes('sim') && !fw.toLowerCase().includes('none')) {
             if (d.telemetry && d.status === 'CONNECTED') {
                 hasReal = true;
             }
         }
     }
     
     if (allStale) return { text: "STALE", color: "var(--warning)" };
     if (hasReal) return { text: "REAL HARDWARE", color: "var(--good)" };
     return { text: "SITL", color: "var(--warning)" };
  };
  
  const handleOpenDrawer = () => {
    window.history.pushState({drawer: true}, '');
    setIsDrawerOpen(true);
  };

  const navCategories = [
    {
      title: "OVERVIEW",
      items: [
        { id: 'DASHBOARD', label: 'Dashboard', icon: <LayoutDashboard size={20}/> },
        { id: 'DRONES', label: 'Fleet', icon: <Network size={20}/> }
      ]
    },
    {
      title: "FLIGHT",
      items: [
        { id: 'FLIGHT', label: 'Flight Control', icon: <Navigation size={20}/> },
        { id: 'MAP', label: 'Map', icon: <MapIcon size={20}/> },
        { id: 'MISSION', label: 'Mission', icon: <Route size={20}/> }
      ]
    },
    {
      title: "SYSTEM",
      items: [
        { id: 'DIAGNOSTICS', label: 'Diagnostics', icon: <Activity size={20}/> },
        { id: 'HARDWARE_TEST', label: 'Hardware Test', icon: <Activity size={20}/> },
        { id: 'SYSTEM_HEALTH', label: 'System Health', icon: <Activity size={20}/> },
        { id: 'LOGS', label: 'Logs', icon: <Activity size={20}/> },
        { id: 'SETTINGS', label: 'Settings', icon: <Settings size={20}/> }
      ]
    }
  ];



  const getConnectionText = () => {
     if (isConnected === "CONNECTED") return "APP CONNECTED";
     if (isConnected === "CONNECTING") return "CONNECTING";
     return "OFFLINE";
  };

  return (
      <div className="app-layout">
        {/* Sidebar - hidden in Flight Control to give full screen to HUD */}
        {currentView !== 'FLIGHT' && (
        <aside className={`sidebar ${isDrawerOpen ? 'open' : ''}`} style={{ zIndex: 1000 }}>
          <div className="sidebar-header">
             <h2>PhoneOS GCS</h2>
             <button className="close-drawer-btn" onClick={() => setIsDrawerOpen(false)}>
               <X size={24} />
             </button>
          </div>

          <nav className="sidebar-nav">
            {navCategories.map((cat, i) => (
              <div key={i} style={{marginBottom: '16px'}}>
                <div style={{fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', marginBottom: '8px', paddingLeft: '16px', letterSpacing: '0.5px'}}>
                  {cat.title}
                </div>
                {cat.items.map(item => {
                  return (
                    <button
                      key={item.id}
                      className={`nav-btn ${currentView === item.id ? 'active' : ''}`}
                      onClick={() => {
                         navigateTo(item.id);
                      }}
                    >
                      {item.icon} <span>{item.label}</span>
                    </button>
                  );
                })}
              </div>
            ))}
          </nav>

          <div style={{marginTop: 'auto', padding: '16px', borderTop: '1px solid var(--border)', fontSize: '12px', color: 'var(--text-muted)'}}>
             <div style={{color: getHardwareIdentityStatus().color, fontWeight: 600}}>
                {getHardwareIdentityStatus().text}
             </div>
          </div>
        </aside>
        )}

        {isDrawerOpen && currentView !== 'FLIGHT' && <div className="drawer-overlay" style={{zIndex: 999}} onClick={() => setIsDrawerOpen(false)} />}

        {testMode && <div className="test-mode-banner" style={{position: 'absolute', top: 0, left: 0, right: 0, zIndex: 100}}>DEMO / TEST MODE</div>}
        {indoorMode && !testMode && <div className="test-mode-banner" style={{position: 'absolute', top: 0, left: 0, right: 0, zIndex: 100}}>INDOOR / BENCH TEST (NO GPS REQ)</div>}

        {currentView === 'FLIGHT' ? (
           <div style={{ flex: 1, position: 'relative', overflow: 'hidden', zIndex: 10 }}>
              <ErrorBoundary>
                  <DroneControlView setView={setCurrentView} />
              </ErrorBoundary>
           </div>
        ) : (
           <div style={{ flex: 1, padding: '24px', overflowY: 'auto', zIndex: 10 }}>
              <div style={{display: 'flex', alignItems: 'center', marginBottom: '24px'}}>
                 <button className="menu-btn mobile-only" onClick={handleOpenDrawer} style={{marginRight: '16px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', padding: '8px', cursor: 'pointer'}}>
                    <Menu size={24} />
                 </button>
                 <h1 style={{margin: 0, fontSize: '24px'}}>{currentView || 'DASHBOARD'}</h1>
              </div>
              <ErrorBoundary>
                 {renderView()}
              </ErrorBoundary>
           </div>
        )}
      </div>
  );
}

export default App;
