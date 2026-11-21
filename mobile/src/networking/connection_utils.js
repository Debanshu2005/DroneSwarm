export const BUILT_IN_DEFAULT_WS_URL = "ws://swarmos-pi.local:8080";

export const isCapacitorPlatform = () => {
  if (typeof window === 'undefined') return false;
  if (typeof window.Capacitor !== 'undefined') return true;
  if (window.location?.protocol === 'capacitor:' || window.location?.protocol === 'ionic:') return true;
  return false;
};

export const shouldAutoConnectSimRelays = (currentWsUrl, searchString = '', env = {}, hostnameOverride = null) => {
  // Never auto-connect sims on Capacitor (the phone app)
  if (isCapacitorPlatform()) return false;

  // A user-entered custom URL must never be treated as the default.
  // Only the exact built-in default or an unset URL counts.
  const isDefaultOrUnset = (
    !currentWsUrl ||
    currentWsUrl === BUILT_IN_DEFAULT_WS_URL ||
    currentWsUrl === "ws://localhost:8080"
  );
  if (!isDefaultOrUnset) {
    return false;
  }

  // Check page hostname: localhost, 127.0.0.1, "", 192.168.*, or 10.*
  let hostname = hostnameOverride;
  if (hostname === null && typeof window !== 'undefined' && window.location) {
    hostname = window.location.hostname;
  }
  if (hostname === null || hostname === undefined) {
    hostname = '';
  }

  const isLocalOrLan = (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '' ||
    hostname.startsWith('192.168.') ||
    hostname.startsWith('10.')
  );

  // Extra triggers: ?sim=1 and VITE_SIM_MODE
  let hasSimQuery = false;
  try {
    const search = searchString !== undefined ? searchString : (typeof window !== 'undefined' && window.location ? window.location.search : '');
    const params = new URLSearchParams(search);
    hasSimQuery = params.get('sim') === '1' || params.get('sim') === 'true';
  } catch (_) {}

  const isEnvSim = Boolean(
    (env && (env.VITE_SIM_MODE === 'true' || env.VITE_SIM_MODE === true)) ||
    (typeof import.meta !== 'undefined' && import.meta.env && (import.meta.env.VITE_SIM_MODE === 'true' || import.meta.env.VITE_SIM_MODE === true))
  );

  return isLocalOrLan || hasSimQuery || isEnvSim;
};
