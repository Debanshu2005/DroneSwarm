import { resolveAirspaceZone } from './src/utils/airspace.js';

const iitKgp = resolveAirspaceZone(22.3149, 87.3105);
console.log('IIT Kharagpur (22.3149, 87.3105):', iitKgp);

const mumbai = resolveAirspaceZone(19.0896, 72.8656);
console.log('Mumbai Airport (19.0896, 72.8656):', mumbai);

const randomPlace = resolveAirspaceZone(20.0, 78.0);
console.log('Random Place (20.0, 78.0):', randomPlace);
