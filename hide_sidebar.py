with open("mobile/src/App.jsx", "r") as f:
    content = f.read()

# I want to wrap the aside with {currentView !== 'FLIGHT' && ( ... )}

old_sidebar = """        {/* Sidebar - accessible on all views */}
        <aside className={`sidebar ${isDrawerOpen ? 'open' : ''}`} style={{ zIndex: 1000 }}>"""

new_sidebar = """        {/* Sidebar - hidden in Flight Control to give full screen to HUD */}
        {currentView !== 'FLIGHT' && (
        <aside className={`sidebar ${isDrawerOpen ? 'open' : ''}`} style={{ zIndex: 1000 }}>"""

old_drawer = """        </aside>

        {isDrawerOpen && <div className="drawer-overlay" style={{zIndex: 999}} onClick={() => setIsDrawerOpen(false)} />}"""

new_drawer = """        </aside>
        )}

        {isDrawerOpen && currentView !== 'FLIGHT' && <div className="drawer-overlay" style={{zIndex: 999}} onClick={() => setIsDrawerOpen(false)} />}"""

if old_sidebar in content and old_drawer in content:
    content = content.replace(old_sidebar, new_sidebar).replace(old_drawer, new_drawer)
    with open("mobile/src/App.jsx", "w") as f:
        f.write(content)
    print("Sidebar fixed")
else:
    print("Could not find sidebar strings")

