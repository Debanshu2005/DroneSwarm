export const PX4_PARAM_METADATA = {
    'EKF2_AID_MASK': {
        type: 'BITMASK',
        default: 1,
        description: 'EKF2 sensor fusion aiding mask',
        options: {
            1: 'Use GPS',
            2: 'Use optical flow',
            4: 'Inhibit IMU bias estimation',
            8: 'Use vision position',
            16: 'Use vision yaw',
            32: 'Use multi-rotor drag'
        }
    },
    'EKF2_HGT_MODE': {
        type: 'ENUM',
        default: 0,
        description: 'Primary height sensor source',
        options: {
            0: 'Barometer',
            1: 'GPS',
            2: 'Range finder',
            3: 'Vision'
        }
    },
    'COM_ARM_WO_GPS': {
        type: 'BOOLEAN',
        default: 0,
        description: 'Allow arming without GPS'
    },
    'NAV_DLL_ACT': {
        type: 'ENUM',
        default: 0,
        description: 'Datalink loss failsafe action',
        options: {
            0: 'Disabled',
            1: 'Loiter',
            2: 'Return mode',
            3: 'Land mode'
        }
    },
    'COM_OBL_ACT': {
        type: 'ENUM',
        default: 0,
        description: 'Offboard loss failsafe action',
        options: {
            '-1': 'Disabled',
            0: 'Land mode',
            1: 'Hold mode',
            2: 'Return mode',
            3: 'Terminate'
        }
    },
    'MPC_XY_VEL_MAX': {
        type: 'FLOAT',
        min: 0,
        max: 20,
        unit: 'm/s',
        default: 5.0,
        description: 'Maximum horizontal velocity'
    },
    'MPC_Z_VEL_MAX_UP': {
        type: 'FLOAT',
        min: 0.5,
        max: 8.0,
        unit: 'm/s',
        default: 3.0,
        description: 'Maximum vertical ascent velocity'
    },
    'MPC_Z_VEL_MAX_DN': {
        type: 'FLOAT',
        min: 0.5,
        max: 4.0,
        unit: 'm/s',
        default: 1.0,
        description: 'Maximum vertical descent velocity'
    }
};

export function getParamMetadata(name) {
    return PX4_PARAM_METADATA[name] || {
        type: name.includes('ACT') || name.includes('MODE') ? 'INT' : 'FLOAT',
        description: 'No metadata available'
    };
}

export function validateParameter(name, value, type) {
    const meta = PX4_PARAM_METADATA[name];
    if (!meta) return { valid: true, error: null }; // Pass if no metadata

    let numValue = Number(value);
    
    if (meta.type === 'FLOAT' || meta.type === 'INT') {
        if (isNaN(numValue) || !isFinite(numValue)) return { valid: false, error: 'Must be a valid number' };
        if (meta.type === 'INT' && !Number.isInteger(numValue)) return { valid: false, error: 'Must be an integer' };
        if (meta.min !== undefined && numValue < meta.min) return { valid: false, error: `Minimum allowed is ${meta.min}` };
        if (meta.max !== undefined && numValue > meta.max) return { valid: false, error: `Maximum allowed is ${meta.max}` };
    }
    
    if (meta.type === 'ENUM') {
        if (!meta.options[numValue.toString()] && !meta.options[numValue]) {
            return { valid: false, error: `Invalid option. Allowed: ${Object.keys(meta.options).join(', ')}` };
        }
    }
    
    if (meta.type === 'BOOLEAN') {
        if (numValue !== 0 && numValue !== 1) {
            return { valid: false, error: 'Must be 0 (false) or 1 (true)' };
        }
    }
    
    return { valid: true, error: null };
}
