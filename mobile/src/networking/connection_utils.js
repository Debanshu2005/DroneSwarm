export const BUILT_IN_DEFAULT_WS_URL = "ws://swarmos-pi.local:8080";

export const isCapacitorPlatform = () => {
  if (typeof window === 'undefined') return false;
  if (typeof window.Capacitor !== 'undefined') return true;
  if (window.location?.protocol === 'capacitor:' || window.location?.protocol === 'ionic:') return true;
  return false;
};

export const shouldAutoConnectSimRelays = (currentWsUrl, searchString = '', env = {}) => {
  if (isCapacitorPlatform()) return false;

  const isBuiltInDefault = !currentWsUrl || currentWsUrl === BUILT_IN_DEFAULT_WS_URL;
  if (!isBuiltInDefault) return false;

  let hasSimQuery = false;
  try {
    const search = searchString !== undefined ? searchString : (typeof window !== 'undefined' ? window.location.search : '');
    const params = new URLSearchParams(search);
    hasSimQuery = params.get('sim') === '1' || params.get('sim') === 'true';
  } catch (_) {}

  const isEnvSim = Boolean(
    (env && (env.VITE_SIM_MODE === 'true' || env.VITE_SIM_MODE === true)) ||
    (typeof import.meta !== 'undefined' && import.meta.env && (import.meta.env.VITE_SIM_MODE === 'true' || import.meta.env.VITE_SIM_MODE === true))
  );

  return hasSimQuery || isEnvSim;
};
