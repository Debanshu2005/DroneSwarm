with open('mobile/src/context/DroneContext.jsx', 'r') as f:
    content = f.read()

safe_storage_code = """
const safeStorageGet = (key, fallback) => {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : fallback;
  } catch (e) {
    console.warn(`Storage access failed for ${key}`, e);
    return fallback;
  }
};

const safeStorageSet = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn(`Storage set failed for ${key}`, e);
  }
};
"""

if "safeStorageGet" not in content:
    content = content.replace("const GS_ID = \"gs_mobile_01\";", "const GS_ID = \"gs_mobile_01\";\n" + safe_storage_code)

content = content.replace("useState(() => localStorage.getItem(\"PhoneOS_WsUrl\") || \"ws://swarmos-pi.local:8080\")", "useState(() => safeStorageGet(\"PhoneOS_WsUrl\", \"ws://swarmos-pi.local:8080\"))")
content = content.replace("useState(() => localStorage.getItem(\"PhoneOS_TestMode\") === \"true\")", "useState(() => safeStorageGet(\"PhoneOS_TestMode\", \"false\") === \"true\")")
content = content.replace("useState(() => localStorage.getItem(\"PhoneOS_IndoorMode\") === \"true\")", "useState(() => safeStorageGet(\"PhoneOS_IndoorMode\", \"false\") === \"true\")")

content = content.replace("localStorage.setItem(\"PhoneOS_WsUrl\", wsUrl);", "safeStorageSet(\"PhoneOS_WsUrl\", wsUrl);")
content = content.replace("localStorage.setItem(\"PhoneOS_TestMode\", testMode);", "safeStorageSet(\"PhoneOS_TestMode\", testMode);")
content = content.replace("localStorage.setItem(\"PhoneOS_IndoorMode\", indoorMode);", "safeStorageSet(\"PhoneOS_IndoorMode\", indoorMode);")

with open('mobile/src/context/DroneContext.jsx', 'w') as f:
    f.write(content)

print("DroneContext.jsx updated.")
