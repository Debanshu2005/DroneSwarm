with open('mobile/src/main.jsx', 'r') as f:
    content = f.read()

diag_banner = """
      <div style={{ position: 'fixed', top: 50, left: 50, zIndex: 99999, background: 'red', color: 'white', padding: '20px', fontSize: '30px', fontWeight: 'bold' }}>
          PHONEOS GCS STARTED
      </div>
      <App />
"""

content = content.replace("<App />", diag_banner)

with open('mobile/src/main.jsx', 'w') as f:
    f.write(content)
print("Diagnostic banner added to main.jsx")
