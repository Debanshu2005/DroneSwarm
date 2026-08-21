with open('mobile/src/main.jsx', 'r') as f:
    content = f.read()

if "ErrorBoundary" not in content:
    content = content.replace("import App from './App.jsx'", "import App from './App.jsx'\nimport { ErrorBoundary } from './components/ErrorBoundary'")
    
    content = content.replace("<DroneProvider>", "<ErrorBoundary>\n    <DroneProvider>")
    content = content.replace("</DroneProvider>", "</DroneProvider>\n    </ErrorBoundary>")

with open('mobile/src/main.jsx', 'w') as f:
    f.write(content)

print("main.jsx updated.")
