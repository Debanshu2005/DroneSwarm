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
    description: 'Controlled airspace advisory near airport perimeter. ATC permission may be required.',
  },
  red: {
    level: 'red',
    label: 'RED ZONE',
    shortLabel: 'RED',
    color: '#ff4b55',
    description: 'No-fly advisory near airport perimeter. Central Government permission is required.',
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

  if (nearestAirport && nearestAirport.distanceKm <= 5) {
    return {
      ...ZONE_LEGEND.red,
      source: nearestAirport.name,
      distanceLabel: `${nearestAirport.distanceKm.toFixed(1)} km`,
    };
  }

  if (nearestAirport && nearestAirport.distanceKm <= 12) {
    return {
      ...ZONE_LEGEND.yellow,
      source: nearestAirport.name,
      distanceLabel: `${nearestAirport.distanceKm.toFixed(1)} km`,
    };
  }

  return {
    ...ZONE_LEGEND.green,
    source: nearestAirport ? nearestAirport.name : 'Local advisory set',
    distanceLabel: nearestAirport ? `${nearestAirport.distanceKm.toFixed(1)} km` : null,
  };
}
