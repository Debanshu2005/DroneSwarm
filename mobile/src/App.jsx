import React, { useState } from 'react';
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

function App() {
  const { isConnected, testMode, indoorMode, nowMs, wsManager } = useDroneContext();
  const [currentView, setCurrentView] = useState('DASHBOARD');
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  
  // Latency calculation from wsManager
  const latency = wsManager?.latency || 0;
  
  const renderView = () => {
    switch (currentView) {
      case 'DASHBOARD': return <DashboardView />;
      case 'DRONES': return <DronesView setView={setCurrentView} />;
      case 'DRONE_CONTROL': return <DroneControlView setView={setCurrentView} />;
      case 'MAP': return <MapView />;
      case 'MISSION': return <MissionView />;
      case 'SWARM': return <SwarmView />;
      case 'SAFETY': return <SafetyView />;
      case 'DIAGNOSTICS': return <DiagnosticsView />;
      case 'SETTINGS': return <SettingsView />;
      case 'PARAMETERS': return <ParameterView />;
      default: return <DashboardView />;
    }
  };

  const navigateTo = (view) => {
    setCurrentView(view);
    setIsDrawerOpen(false);
  };

  const navItems = [
    { id: 'DASHBOARD', label: 'Dashboard', icon: <LayoutDashboard size={20}/> },
    { id: 'MAP', label: 'Map', icon: <MapIcon size={20}/> },
    { id: 'MISSION', label: 'Mission', icon: <Route size={20}/> },
    { id: 'DRONES', label: 'Drones', icon: <Navigation size={20}/> },
    { id: 'SWARM', label: 'Swarm', icon: <Network size={20}/> },
    { id: 'SAFETY', label: 'Safety', icon: <ShieldAlert size={20}/> },
    { id: 'DIAGNOSTICS', label: 'Diagnostics', icon: <Activity size={20}/> },
    { id: 'SETTINGS', label: 'Settings', icon: <Settings size={20}/> },
  ];

  const getConnectionText = () => {
     if (isConnected === "CONNECTED") return "CONNECTED";
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
          {navItems.map(item => (
            <button 
              key={item.id} 
              className={`nav-btn ${currentView === item.id ? 'active' : ''}`}
              onClick={() => navigateTo(item.id)}
            >
              {item.icon} <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      
      {isDrawerOpen && <div className="drawer-overlay" onClick={() => setIsDrawerOpen(false)} />}

      <div className="app-body">
        {/* Mobile Header */}
        <header className="mobile-header">
           <div style={{display:'flex', alignItems:'center', gap:'12px'}}>
             <button className="menu-btn" onClick={() => setIsDrawerOpen(true)}>
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

        {/* Mobile Bottom Nav */}
        <nav className="mobile-bottom-nav">
          {navItems.map(item => (
            <button 
              key={item.id} 
              className={`nav-btn ${currentView === item.id ? 'active' : ''}`}
              onClick={() => navigateTo(item.id)}
            >
              {item.icon} <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}

export default App;
