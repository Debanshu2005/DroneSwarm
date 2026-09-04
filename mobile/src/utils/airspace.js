export const DEFAULT_MAP_CENTER = [20.5937, 78.9629];
export const DIGITAL_SKY_AIRSPACE_URL = 'https://digitalsky.aai.aero/airspace-map';

const AIRPORT_ADVISORY_ZONES = [
  { id: 'VECC', name: 'Kolkata Airport', lat: 22.6547, lng: 88.4467 },
  { id: 'VIDP', name: 'Delhi IGI Airport', lat: 28.5562, lng: 77.1000 },
  { id: 'VABB', name: 'Mumbai Airport', lat: 19.0896, lng: 72.8656 },
  { id: 'VOBL', name: 'Bengaluru Airport', lat: 13.1986, lng: 77.7066 },
  { id: 'VOMM', name: 'Chennai Airport', lat: 12.9941, lng: 80.1709 },
  { id: 'VOHS', name: 'Hyderabad Airport', lat: 17.2403, lng: 78.4294 },
  { id: 'VEBS', name: 'Bhubaneswar Airport', lat: 20.2444, lng: 85.8178 },
];

const MILITARY_ADVISORY_ZONES = [
  { id: 'VEDX', name: 'Kalaikunda Air Force Station', lat: 22.3395, lng: 87.2145 },
  { id: 'SALUA', name: 'Air Force Station Salua', lat: 22.27278, lng: 87.28944 },
  { id: 'VIAM', name: 'Ambala Air Force Station', lat: 30.37083, lng: 76.81778 },
  { id: 'VIDX', name: 'Hindan Air Force Station', lat: 28.70778, lng: 77.35833 },
  // Additional verified bases
  { id: 'VAPO', name: 'Pune Air Force Station', lat: 18.58222, lng: 73.91972 },
  { id: 'VIGR', name: 'Gwalior Air Force Station', lat: 26.29333, lng: 78.22778 },
  { id: 'VIPK', name: 'Pathankot Air Force Station', lat: 32.23361, lng: 75.63444 },
  { id: 'VIAG', name: 'Agra Air Force Station', lat: 27.16194, lng: 77.97083 },
  { id: 'VOSR', name: 'Sulur Air Force Station', lat: 11.01361, lng: 77.15972 },
  { id: 'VIJO', name: 'Jodhpur Air Force Station', lat: 26.25722, lng: 73.05167 },
];

export const ZONE_LEGEND = {
  green: {
    level: 'green',
    label: 'GREEN ZONE',
    shortLabel: 'GREEN',
    color: '#28d17c',
    description: 'No bundled red/yellow advisory detected near this GPS point.',
  },
  yellow: {
    level: 'yellow',
    label: 'YELLOW ZONE',
    shortLabel: 'YELLOW',
    color: '#ffbf3d',
    description: 'Controlled airspace advisory near installation perimeter. Permission may be required.',
  },
  red: {
    level: 'red',
    label: 'RED ZONE',
    shortLabel: 'RED',
    color: '#ff4b55',
    description: 'No-fly advisory near installation perimeter. Permission is required.',
  },
  unknown: {
    level: 'unknown',
    label: 'GPS REQUIRED',
    shortLabel: 'GPS',
    color: '#8a96a8',
    description: 'Allow device GPS to classify the active flight area.',
  }
};

const toRad = (value) => (value * Math.PI) / 180;

export const distanceKm = (aLat, aLng, bLat, bLng) => {
  const earthRadiusKm = 6371;
  const dLat = toRad(bLat - aLat);
  const dLng = toRad(bLng - aLng);
  const lat1 = toRad(aLat);
  const lat2 = toRad(bLat);

  const h =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);

  return 2 * earthRadiusKm * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
};

export function resolveAirspaceZone(lat, lng) {
  if (lat == null || lng == null || Number.isNaN(lat) || Number.isNaN(lng)) {
    return {
      ...ZONE_LEGEND.unknown,
      source: 'Device GPS',
      distanceLabel: null,
    };
  }

  const nearestAirport = AIRPORT_ADVISORY_ZONES
    .map((airport) => ({
      ...airport,
      distanceKm: distanceKm(lat, lng, airport.lat, airport.lng),
    }))
    .sort((a, b) => a.distanceKm - b.distanceKm)[0];

  const nearestMilitary = MILITARY_ADVISORY_ZONES
    .map((base) => ({
      ...base,
      distanceKm: distanceKm(lat, lng, base.lat, base.lng),
    }))
    .sort((a, b) => a.distanceKm - b.distanceKm)[0];

  let currentZoneLevel = 'green';
  let activeAdvisory = null;
  
  // Check Military first (stricter buffers)
  if (nearestMilitary) {
    if (nearestMilitary.distanceKm <= 10) {
      currentZoneLevel = 'red';
      activeAdvisory = nearestMilitary;
    } else if (nearestMilitary.distanceKm <= 20) {
      currentZoneLevel = 'yellow';
      activeAdvisory = nearestMilitary;
    }
  }

  // Check Civilian (standard buffers)
  if (nearestAirport) {
    if (nearestAirport.distanceKm <= 5) {
      // RED overrides anything
      currentZoneLevel = 'red';
      activeAdvisory = nearestAirport;
    } else if (nearestAirport.distanceKm <= 12 && currentZoneLevel !== 'red') {
      // YELLOW overrides GREEN
      currentZoneLevel = 'yellow';
      // Only swap the advisory if we weren't already yellow from military,
      // or if we want to show the closest. Usually, military takes precedence, 
      // but if we are green from military and yellow from civilian, we use civilian.
      if (!activeAdvisory || activeAdvisory.distanceKm > 20) {
         activeAdvisory = nearestAirport;
      } else {
         // Both are yellow, pick the closer one relatively? Let's just pick the closer absolute distance.
         if (nearestAirport.distanceKm < activeAdvisory.distanceKm) {
             activeAdvisory = nearestAirport;
         }
      }
    }
  }

  if (currentZoneLevel === 'red') {
    return {
      ...ZONE_LEGEND.red,
      source: activeAdvisory.name,
      distanceLabel: `${activeAdvisory.distanceKm.toFixed(1)} km`,
    };
  }
  
  if (currentZoneLevel === 'yellow') {
    return {
      ...ZONE_LEGEND.yellow,
      source: activeAdvisory.name,
      distanceLabel: `${activeAdvisory.distanceKm.toFixed(1)} km`,
    };
  }

  // Green Zone
  // Let's just show the closest tracked facility (civilian or military) as the source, even if green
  const closestFacility = [nearestAirport, nearestMilitary]
    .filter(Boolean)
    .sort((a, b) => a.distanceKm - b.distanceKm)[0];

  return {
    ...ZONE_LEGEND.green,
    source: closestFacility ? closestFacility.name : 'Local advisory set',
    distanceLabel: closestFacility ? `${closestFacility.distanceKm.toFixed(1)} km` : null,
  };
}
