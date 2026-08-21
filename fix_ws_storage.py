with open('mobile/src/networking/MultiWebSocketManager.js', 'r') as f:
    content = f.read()

safe_storage = """    _safeStorageSet(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (e) {
            console.warn(`Storage set failed for ${key}`);
        }
    }
    
    _safeStorageGet(key) {
        try {
            return localStorage.getItem(key);
        } catch (e) {
            console.warn(`Storage get failed for ${key}`);
            return null;
        }
    }
"""

if "_safeStorageSet" not in content:
    content = content.replace("    _saveConnections() {", safe_storage + "\n    _saveConnections() {")

content = content.replace("localStorage.setItem(\"PhoneOS_Swarm_Connections\", JSON.stringify(urls));", "this._safeStorageSet(\"PhoneOS_Swarm_Connections\", JSON.stringify(urls));")
content = content.replace("const saved = JSON.parse(localStorage.getItem(\"PhoneOS_Swarm_Connections\"));", """const raw = this._safeStorageGet("PhoneOS_Swarm_Connections");
            if (!raw) return;
            const saved = JSON.parse(raw);""")

with open('mobile/src/networking/MultiWebSocketManager.js', 'w') as f:
    f.write(content)

print("MultiWebSocketManager.js updated.")
