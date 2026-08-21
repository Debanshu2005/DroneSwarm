with open('mobile/src/App.jsx', 'r') as f:
    content = f.read()

# 1. Add import
if "import { ScreenOrientation }" not in content:
    content = content.replace("import './App.css';", "import { ScreenOrientation } from '@capacitor/screen-orientation';\nimport './App.css';")

# 2. Add useEffect for orientation
if "ScreenOrientation.lock" not in content:
    hook = """
  useEffect(() => {
    const lockLandscape = async () => {
      try {
        await ScreenOrientation.lock({ orientation: 'landscape' });
      } catch (e) {
        console.warn('Screen orientation lock not supported on this platform', e);
      }
    };
    lockLandscape();
  }, []);
"""
    content = content.replace("const [isDrawerOpen, setIsDrawerOpen] = useState(false);", "const [isDrawerOpen, setIsDrawerOpen] = useState(false);\n" + hook)

# 3. Remove MAP from renderView
content = content.replace("case 'MAP': return <MapView />;\n", "")
# Remove MapView import
content = content.replace("import MapView from './views/MapView';\n", "")

with open('mobile/src/App.jsx', 'w') as f:
    f.write(content)
