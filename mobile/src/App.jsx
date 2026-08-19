import React, { useState, useEffect } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useDroneContext } from './context/DroneContext';
import { LayoutDashboard, Map as MapIcon, Route, Network, ShieldAlert, Activity, Settings, Menu, X, Navigation } from 'lucide-react';
import './App.css';

import DashboardView from './views/DashboardView';
import DronesView from './views/DronesView';
import DroneControlView from './views/DroneControlView';
import MapView from './views/MapView';
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

function App() {
  const { isConnected, testMode, indoorMode, nowMs, wsManager } = useDroneContext();
  const [currentView, setCurrentView] = useState('DASHBOARD');
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  
  // Latency calculation from wsManager
  const latency = wsManager?.latency || 0;
  
  const renderView = () => {
    switch (currentView) {
      case 'DASHBOARD': return <DashboardView setView={setCurrentView} />;
      case 'FLIGHT': return <DroneControlView setView={setCurrentView} />;
      case 'DRONES': return <DronesView setView={setCurrentView} />;
      case 'MAP': return <MapView />;
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
      default: return <DashboardView setView={setCurrentView} />;
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
      title: "CONFIGURATION",
      items: [
        { id: 'PARAMETERS', label: 'Parameters', icon: <Settings size={20}/> },
        { id: 'OUTDOOR_PROFILE', label: 'Profiles', icon: <Network size={20}/> },
        { id: 'INDOOR_PROFILE', label: 'Indoor', icon: <Network size={20}/> },
        { id: 'SWARM', label: 'Swarm', icon: <Network size={20}/> }
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
      {/* Sidebar for desktop/tablet */}
      <aside className={`sidebar ${isDrawerOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
           <h2>PhoneOS GCS</h2>
           <button className="close-drawer-btn" onClick={() => setIsDrawerOpen(false)}>
             <X size={24} />
           </button>
        </div>
        
        <div className="connection-pill" style={{margin: '0 16px 24px'}}>
           <div className={`indicator ${isConnected === "CONNECTED" ? "connected" : "disconnected"}`}></div>
           <span>{getConnectionText()}</span>
           {isConnected === "CONNECTED" && <span style={{marginLeft: 'auto', fontSize: '12px', color: 'var(--text-muted)'}}>{latency}ms</span>}
        </div>

        <nav className="sidebar-nav">
          {navCategories.map((cat, i) => (
            <div key={i} style={{marginBottom: '16px'}}>
              <div style={{fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', marginBottom: '8px', paddingLeft: '16px', letterSpacing: '0.5px'}}>
                {cat.title}
              </div>
              {cat.items.map(item => (
                <button 
                  key={item.id} 
                  className={`nav-btn ${currentView === item.id ? 'active' : ''}`}
                  onClick={() => navigateTo(item.id)}
                >
                  {item.icon} <span>{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        
        <div style={{marginTop: 'auto', padding: '16px', borderTop: '1px solid var(--border)', fontSize: '12px', color: 'var(--text-muted)'}}>
           {testMode ? <div style={{color: 'var(--warning)', fontWeight: 600}}>SIMULATION / SITL</div> : <div style={{color: 'var(--good)', fontWeight: 600}}>REAL HARDWARE</div>}
        </div>
      </aside>
      
      {isDrawerOpen && <div className="drawer-overlay" onClick={() => setIsDrawerOpen(false)} />}

      <div className="app-body">
        {/* Mobile Header */}
        <header className="mobile-header">
           <div style={{display:'flex', alignItems:'center', gap:'12px'}}>
             <button className="menu-btn" onClick={handleOpenDrawer}>
               <Menu size={24} color="var(--text-main)" />
             </button>
             <h2>PhoneOS GCS</h2>
           </div>
           <div className="connection-pill">
              <div className={`indicator ${isConnected === "CONNECTED" ? "connected" : "disconnected"}`}></div>
              <span>{getConnectionText()}</span>
           </div>
        </header>

        {testMode && <div className="test-mode-banner">DEMO / TEST MODE</div>}
        {indoorMode && !testMode && <div className="test-mode-banner">INDOOR / BENCH TEST (NO GPS REQ)</div>}

        {/* Main View Area */}
        <main className="view-area">
          <ErrorBoundary>
            {renderView()}
          </ErrorBoundary>
        </main>

      </div>
    </div>
  );
}

export default App;
