import { validateParameter } from './Px4Parameters.js';

function assert(condition, message) {
    if (!condition) {
        throw new Error(message);
    }
}

console.log("Running Parameter Validation Tests...");

// FLOAT
let res = validateParameter('MPC_XY_VEL_MAX', 15.5, 'FLOAT');
assert(res.valid === true, "Float inside limit should be valid");

res = validateParameter('MPC_XY_VEL_MAX', 25.0, 'FLOAT');
assert(res.valid === false, "Float outside max limit should be invalid");

res = validateParameter('MPC_XY_VEL_MAX', -1.0, 'FLOAT');
assert(res.valid === false, "Float outside min limit should be invalid");

// INT
res = validateParameter('EKF2_AID_MASK', 1, 'INT');
assert(res.valid === true, "Integer should be valid");

res = validateParameter('EKF2_AID_MASK', 1.5, 'INT');
assert(res.valid === false, "Float passed to INT parameter should be invalid");

// BOOLEAN
res = validateParameter('COM_ARM_WO_GPS', 1, 'BOOLEAN');
assert(res.valid === true, "1 for boolean should be valid");

res = validateParameter('COM_ARM_WO_GPS', 0, 'BOOLEAN');
assert(res.valid === true, "0 for boolean should be valid");

res = validateParameter('COM_ARM_WO_GPS', 2, 'BOOLEAN');
assert(res.valid === false, "2 for boolean should be invalid");

// ENUM
res = validateParameter('EKF2_HGT_MODE', 2, 'ENUM');
assert(res.valid === true, "Valid enum option should pass");

res = validateParameter('EKF2_HGT_MODE', 5, 'ENUM');
assert(res.valid === false, "Invalid enum option should fail");

// INVALID VALUES
res = validateParameter('MPC_XY_VEL_MAX', 'NaN', 'FLOAT');
assert(res.valid === false, "NaN should be invalid");

res = validateParameter('MPC_XY_VEL_MAX', 'abcd', 'FLOAT');
assert(res.valid === false, "String should be invalid");

// MISSING PARAMETER
res = validateParameter('UNKNOWN_PARAMETER', 10, 'FLOAT');
assert(res.valid === true, "Unknown parameter falls back to unvalidated pass");

console.log("All parameter validation tests passed!");
