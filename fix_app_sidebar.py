with open('mobile/src/App.jsx', 'r') as f:
    content = f.read()

import re

# We want to conditionally render the sidebar ONLY if currentView !== 'FLIGHT'
old_sidebar_start = """  return (
      <div className="app-layout">
        {/* Sidebar for desktop/tablet */}
        <aside className={`sidebar ${isDrawerOpen ? 'open' : ''}`} style={{ zIndex: 1000, position: 'fixed' }}>"""

new_sidebar_start = """  return (
      <div className="app-layout">
        {/* Sidebar for desktop/tablet */}
        {currentView !== 'FLIGHT' && (
        <aside className={`sidebar ${isDrawerOpen ? 'open' : ''}`} style={{ zIndex: 1000, position: 'fixed' }}>"""

content = content.replace(old_sidebar_start, new_sidebar_start)

old_sidebar_end = """             </div>
          </div>
        </aside>

        {isDrawerOpen && <div className="drawer-overlay" style={{zIndex: 999}} onClick={() => setIsDrawerOpen(false)} />}"""

new_sidebar_end = """             </div>
          </div>
        </aside>
        )}

        {isDrawerOpen && currentView !== 'FLIGHT' && <div className="drawer-overlay" style={{zIndex: 999}} onClick={() => setIsDrawerOpen(false)} />}"""

content = content.replace(old_sidebar_end, new_sidebar_end)

with open('mobile/src/App.jsx', 'w') as f:
    f.write(content)
