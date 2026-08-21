with open('mobile/src/App.jsx', 'r') as f:
    content = f.read()

import re

# Fix renderView
old_render_view = """  const renderView = () => {
    switch (currentView) {
      case 'DASHBOARD': return <DashboardView />;
      case 'DRONES': return <DronesView />;
      case 'MISSION': return <MissionView />;
      case 'SETTINGS': return <SettingsView />;
      case 'PARAMETERS': return <ParameterView />;"""

new_render_view = """  const renderView = () => {
    switch (currentView) {
      case 'DASHBOARD': return <DashboardView />;
      case 'DRONES': return <DronesView />;
      case 'MISSION': return <MissionView />;
      case 'SWARM': return <SwarmView />;
      case 'SAFETY': return <SafetyView />;
      case 'DIAGNOSTICS': return <DiagnosticsView />;
      case 'SETTINGS': return <SettingsView />;
      case 'PARAMETERS': return <ParameterView />;"""

content = content.replace(old_render_view, new_render_view)

# Fix onClick for Dashboard
content = content.replace("""                         if (item.id === 'DASHBOARD') {
                            setCurrentView(null);
                         } else {
                            navigateTo(item.id);
                         }""", """                         navigateTo(item.id);""")

# Fix the render structure
start_idx = content.find("        {/* ALWAYS RENDER FLIGHT CONTROL AS BACKGROUND */}")
end_idx = content.find("      </div>\n  );\n}")

if start_idx != -1 and end_idx != -1:
    new_ui = """        {currentView === 'FLIGHT' ? (
           <div style={{ position: 'absolute', inset: 0, overflow: 'hidden', zIndex: 10 }}>
              <button className="menu-btn" onClick={() => navigateTo('DASHBOARD')} style={{position: 'absolute', top: '16px', left: '16px', zIndex: 50, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', padding: '8px', cursor: 'pointer'}}>
                 <Menu size={24} color="var(--text-main)" />
              </button>
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
                 <h1 style={{margin: 0, fontSize: '24px'}}>{currentView}</h1>
              </div>
              <ErrorBoundary>
                 {renderView()}
              </ErrorBoundary>
           </div>
        )}"""
    
    content = content[:start_idx] + new_ui + "\n" + content[end_idx:]

with open('mobile/src/App.jsx', 'w') as f:
    f.write(content)
