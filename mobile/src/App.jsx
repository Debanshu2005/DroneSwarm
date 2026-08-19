import React, { useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useDroneContext } from './context/DroneContext';
import { LayoutDashboard, Map as MapIcon, Route, Network, ShieldAlert, Activity, Settings, Menu, X } from 'lucide-react';
import './App.css';

import FleetView from './views/FleetView';
import MapView from './views/MapView';
import MissionView from './views/MissionView';
import SwarmView from './views/SwarmView';
import SafetyView from './views/SafetyView';
import DiagnosticsView from './views/DiagnosticsView';
import SettingsView from './views/SettingsView';

function App() {
  const { isConnected, testMode, indoorMode } = useDroneContext();
  const [currentView, setCurrentView] = useState('FLEET');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const renderView = () => {
    switch (currentView) {
      case 'FLEET': return <FleetView />;
      case 'MAP': return <MapView />;
      case 'MISSION': return <MissionView />;
      case 'SWARM': return <SwarmView />;
      case 'SAFETY': return <SafetyView />;
      case 'DIAGNOSTICS': return <DiagnosticsView />;
      case 'SETTINGS': return <SettingsView />;
      default: return <FleetView />;
    }
  };

  const navItems = [
    { id: 'FLEET', label: 'Fleet', icon: <LayoutDashboard size={20}/> },
    { id: 'MAP', label: 'Map', icon: <MapIcon size={20}/> },
    { id: 'MISSION', label: 'Mission', icon: <Route size={20}/> },
    { id: 'SWARM', label: 'Swarm', icon: <Network size={20}/> },
    { id: 'SAFETY', label: 'Safety', icon: <ShieldAlert size={20}/> },
    { id: 'DIAGNOSTICS', label: 'Diagnostics', icon: <Activity size={20}/> },
    { id: 'SETTINGS', label: 'Settings', icon: <Settings size={20}/> },
  ];

  return (
    <div className="app-layout">
      {/* Background blobs */}
      <div className="bg-blob blob-1"></div>
      <div className="bg-blob blob-2"></div>
      
      {testMode && <div className="test-mode-banner">DEMO / TEST MODE</div>}
      {indoorMode && !testMode && <div className="test-mode-banner" style={{background: 'var(--warning)', color: '#000'}}>INDOOR / BENCH TEST (NO GPS REQ)</div>}

      <div className="app-body">
        {/* Sidebar for desktop/tablet */}
        <aside className={`sidebar glass-panel ${mobileMenuOpen ? 'open' : ''}`}>
          <div className="sidebar-header">
             <h2>SwarmOS</h2>
             <button className="icon-btn mobile-close" onClick={() => setMobileMenuOpen(false)}><X/></button>
          </div>
          
          <div className="connection-pill" style={{margin: '0 1rem 1rem'}}>
             <div className={`indicator ${isConnected === "CONNECTED" ? "connected" : "disconnected"}`}></div>
             <span>{isConnected}</span>
          </div>

          <nav className="sidebar-nav">
            {navItems.map(item => (
              <button 
                key={item.id} 
                className={`nav-btn ${currentView === item.id ? 'active' : ''}`}
                onClick={() => { setCurrentView(item.id); setMobileMenuOpen(false); }}
              >
                {item.icon} <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        {/* Mobile Header */}
        <header className="mobile-header glass-panel">
           <div style={{display:'flex', alignItems:'center', gap:'10px'}}>
             <button className="icon-btn" onClick={() => setMobileMenuOpen(true)}><Menu/></button>
             <h2>SwarmOS</h2>
           </div>
           <div className={`indicator ${isConnected === "CONNECTED" ? "connected" : "disconnected"}`}></div>
        </header>

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
