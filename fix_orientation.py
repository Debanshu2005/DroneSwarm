with open('mobile/src/App.jsx', 'r') as f:
    content = f.read()

import re

old_use_effect = """  useEffect(() => {
    let mounted = true;
    const lockLandscape = async () => {
      try {
        // slight delay to let capacitor bridge initialize
        await new Promise(r => setTimeout(r, 200));
        if (mounted) {
            await ScreenOrientation.lock({ orientation: 'landscape' });
        }
      } catch (e) {
        console.warn('Screen orientation lock not supported on this platform', e);
      }
    };
    lockLandscape();
    return () => { mounted = false; };
  }, []);"""

new_use_effect = """  useEffect(() => {
    const applyOrientation = async () => {
      try {
        if (currentView === 'FLIGHT') {
          await ScreenOrientation.lock({ orientation: 'landscape' });
        } else {
          await ScreenOrientation.lock({ orientation: 'portrait' });
        }
      } catch (e) {
        console.warn('Screen orientation lock not supported on this platform', e);
      }
    };
    applyOrientation();
  }, [currentView]);"""

content = content.replace(old_use_effect, new_use_effect)

with open('mobile/src/App.jsx', 'w') as f:
    f.write(content)
