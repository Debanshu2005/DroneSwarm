export const PX4_PROFILES = {
  INDOOR_PROFILE: {
    name: 'Indoor Flight',
    description: 'Configuration for safe indoor flight using Optical Flow/VIO without GPS.',
    requiredSensors: ['IMU', 'Barometer'], // mock sensor requirements
    positioning: 'optical_flow', // or 'vio'
    parameters: {
      'EKF2_AID_MASK': { value: 2, type: 'int' }, // use optical flow
      'EKF2_HGT_MODE': { value: 2, type: 'int' }, // range finder
      'COM_ARM_WO_GPS': { value: 1, type: 'int' }, // allow arming without GPS
      'NAV_DLL_ACT': { value: 0, type: 'int' }, // disable GPS datalink loss failsafe
      'COM_OBL_ACT': { value: -1, type: 'int' } // disable offboard loss failsafe (or configure it safely)
    }
  },
  OUTDOOR_GPS_PROFILE: {
    name: 'Outdoor GPS',
    description: 'Standard outdoor flight using GPS.',
    requiredSensors: ['IMU', 'Barometer', 'GPS', 'Magnetometer'],
    positioning: 'gps',
    parameters: {
      'EKF2_AID_MASK': { value: 1, type: 'int' }, // use GPS
      'EKF2_HGT_MODE': { value: 0, type: 'int' }, // barometer
      'COM_ARM_WO_GPS': { value: 0, type: 'int' },
      'NAV_DLL_ACT': { value: 1, type: 'int' }
    }
  },
  MANUAL_CONTROL_PROFILE: {
    name: 'Manual Control',
    description: 'Direct manual RC/Offboard control configuration.',
    requiredSensors: ['IMU'],
    positioning: 'any',
    parameters: {
      'COM_RC_IN_MODE': { value: 1, type: 'int' },
      'COM_RC_LOSS_T': { value: 0.5, type: 'float' }
    }
  }
};
