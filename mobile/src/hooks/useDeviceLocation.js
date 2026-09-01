import { useCallback, useEffect, useRef, useState } from 'react';

const GEO_OPTIONS = {
  enableHighAccuracy: true,
  timeout: 12000,
  maximumAge: 10000,
};

const getErrorLabel = (error) => {
  if (!error) return 'Unable to read device GPS.';
  if (error.code === 1) return 'GPS permission was denied.';
  if (error.code === 2) return 'GPS position is unavailable.';
  if (error.code === 3) return 'GPS request timed out.';
  return error.message || 'Unable to read device GPS.';
};

export function useDeviceLocation() {
  const watchIdRef = useRef(null);
  const [locationState, setLocationState] = useState({
    status: 'idle',
    coords: null,
    accuracy: null,
    error: null,
  });

  const requestLocation = useCallback(() => {
    if (!('geolocation' in navigator)) {
      setLocationState({
        status: 'unsupported',
        coords: null,
        accuracy: null,
        error: 'This device does not expose browser GPS.',
      });
      return;
    }

    if (watchIdRef.current != null) {
      navigator.geolocation.clearWatch(watchIdRef.current);
      watchIdRef.current = null;
    }

    setLocationState((current) => ({
      ...current,
      status: current.coords ? 'refreshing' : 'requesting',
      error: null,
    }));

    watchIdRef.current = navigator.geolocation.watchPosition(
      (position) => {
        setLocationState({
          status: 'granted',
          coords: [position.coords.latitude, position.coords.longitude],
          accuracy: position.coords.accuracy,
          error: null,
        });
      },
      (error) => {
        setLocationState((current) => ({
          ...current,
          status: error.code === 1 ? 'denied' : 'error',
          error: getErrorLabel(error),
        }));
      },
      GEO_OPTIONS
    );
  }, []);

  useEffect(() => {
    requestLocation();

    return () => {
      if (watchIdRef.current != null) {
        navigator.geolocation.clearWatch(watchIdRef.current);
      }
    };
  }, [requestLocation]);

  return {
    ...locationState,
    requestLocation,
  };
}
