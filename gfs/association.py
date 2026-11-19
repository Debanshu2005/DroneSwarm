"""
Stage A: Existing Global Track Association (GFA v1.0).
Implements per-drone independent Hungarian assignment against a frozen snapshot of global tracks,
Mahalanobis gating, identity-history enforcement, and deterministic Covariance Intersection (CI).
"""

from __future__ import annotations
import math
from typing import List, Dict, Set, Tuple, Optional, Any, Union, overload, Literal
import numpy as np
from scipy.optimize import linear_sum_assignment, minimize_scalar, minimize

from gfs.models import (
    Observation,
    LocalTracklet,
    GlobalObject,
    SourceEstimate,
    LifecycleState,
    wgs84_to_enu,
    enu_to_wgs84
)
from gfs.config import GFSConfig


def build_local_tracklets(observations: List[Observation]) -> List[LocalTracklet]:
    """
    Converts a batch of raw observations into local tracklets grouped by
    (drone_id, drone_session_id, session_generation, local_track_id).
    """
    grouped: Dict[Tuple[str, str, int, str], List[Observation]] = {}
    for obs in observations:
        key = (obs.drone_id, obs.drone_session_id, obs.session_generation, obs.local_track_id)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(obs)

    tracklets: List[LocalTracklet] = []
    for key, obs_list in grouped.items():
        tracklet = LocalTracklet.from_observations(obs_list)
        tracklets.append(tracklet)

    return tracklets


def compute_mahalanobis_2d(
    dx: float, dy: float,
    cov1_xx: float, cov1_yy: float, cov1_xy: float,
    cov2_xx: float, cov2_yy: float, cov2_xy: float,
    noise_floor: float = 0.25
) -> Tuple[float, bool]:
    """
    Computes 2D Mahalanobis distance squared:
    d^2 = [dx, dy] * (Cov1 + Cov2 + noise_floor * I)^(-1) * [dx, dy]^T
    Returns (d_m2, is_valid).
    """
    S_xx = cov1_xx + cov2_xx + noise_floor
    S_yy = cov1_yy + cov2_yy + noise_floor
    S_xy = cov1_xy + cov2_xy

    det = S_xx * S_yy - S_xy * S_xy
    if det <= 1e-9:
        return float('inf'), False

    inv_xx = S_yy / det
    inv_yy = S_xx / det
    inv_xy = -S_xy / det

    d_m2 = dx * (inv_xx * dx + inv_xy * dy) + dy * (inv_xy * dx + inv_yy * dy)
    return max(0.0, float(d_m2)), True


def solve_exact_forced_prefix_hungarian_int(
    cost_int: np.ndarray,
    infeasible_threshold: int = 10**9
) -> Tuple[List[Tuple[int, int]], int]:
    """
    Solves minimum-cost assignment using integer arithmetic.
    Guarantees deterministic lexicographical tie-breaking for row 0, row 1, etc.
    Returns (assignment_pairs, optimal_cost). Pairs with col == -1 denote unassigned.
    """
    R, C = cost_int.shape
    assert R <= C, f"Cost matrix must have R <= C (got {R}x{C})"

    r_ind, c_ind = linear_sum_assignment(cost_int)
    Z_star = int(cost_int[r_ind, c_ind].sum())

    assigned_cols: Set[int] = set()
    final_assignment: List[Tuple[int, int]] = []

    for r in range(R):
        remaining_rows = [i for i in range(r + 1, R)]
        found = False

        # Evaluate candidate columns in ascending canonical order
        for c in range(C):
            if c in assigned_cols:
                continue
            edge_cost = int(cost_int[r, c])
            if edge_cost >= infeasible_threshold:
                continue

            if not remaining_rows:
                cand_total = sum(cost_int[ri, ci] for ri, ci in final_assignment if ci >= 0) + edge_cost
                if cand_total == Z_star:
                    final_assignment.append((r, c))
                    assigned_cols.add(c)
                    found = True
                    break
            else:
                sub_cols = [j for j in range(C) if j not in assigned_cols and j != c]
                sub_cost = cost_int[np.ix_(remaining_rows, sub_cols)]
                sub_r, sub_c = linear_sum_assignment(sub_cost)
                sub_val = int(sub_cost[sub_r, sub_c].sum())

                cand_total = sum(cost_int[ri, ci] for ri, ci in final_assignment if ci >= 0) + edge_cost + sub_val
                if cand_total == Z_star:
                    final_assignment.append((r, c))
                    assigned_cols.add(c)
                    found = True
                    break

        if not found:
            # Row has no feasible edge that attains Z_star (e.g. all remaining edges are infeasible)
            final_assignment.append((r, -1))

    return final_assignment, Z_star


def associate_drone_tracklets_to_globals(
    drone_tracklets: List[LocalTracklet],
    active_globals: List[GlobalObject],
    identity_history: Dict[Tuple[str, str, int, str], str],  # (drone, session, gen, track) -> global_id
    config: GFSConfig
) -> Tuple[Dict[str, str], List[LocalTracklet]]:
    """
    Performs independent Hungarian association for tracklets from a SINGLE drone
    against the snapshot of active global objects.

    Returns:
        matched_pairs: Dict[local_track_id, global_id]
        unmatched_tracklets: List[LocalTracklet]
    """
    if not drone_tracklets:
        return {}, []

    if not active_globals:
        # No existing global objects, all tracklets remain unmatched
        return {}, list(drone_tracklets)

    R = len(drone_tracklets)
    M = len(active_globals)
    C = M + R  # Augment with R dummy unassigned columns

    cost_int = np.full((R, C), 10**9, dtype=np.int64)
    dummy_cost = config.unassigned_dummy_cost

    for i, trk in enumerate(drone_tracklets):
        # Set dummy column for row i
        dummy_col = M + i
        cost_int[i, dummy_col] = dummy_cost

        # Historical identity check
        hist_key = (trk.drone_id, trk.drone_session_id, trk.session_generation, trk.local_track_id)
        bound_global_id = identity_history.get(hist_key)

        for j, glob in enumerate(active_globals):
            # Identity constraint: if previously bound to a different global, forbidden
            if bound_global_id is not None and bound_global_id != glob.global_id:
                continue

            # Class compatibility
            if trk.label != glob.label:
                continue

            # Temporal compatibility: reject stale observations arriving from the past
            if trk.last_seen_ms < glob.last_seen_ms and (glob.last_seen_ms - trk.last_seen_ms) > config.temporal_max_diff_ms:
                continue

            # Gated check against expired provisional candidates
            if glob.expires_at_ms is not None and trk.last_seen_ms > glob.expires_at_ms:
                continue

            # Spatial physical distance bound
            dx = trk.east_m - glob.east_m
            dy = trk.north_m - glob.north_m
            dist = math.hypot(dx, dy)
            if dist > config.spatial_physical_r_max_m:
                continue

            # Mahalanobis gating
            d_m2, valid = compute_mahalanobis_2d(
                dx, dy,
                trk.cov_xx, trk.cov_yy, trk.cov_xy,
                glob.cov_xx, glob.cov_yy, glob.cov_xy,
                noise_floor=config.spatial_noise_floor_m2
            )
            if not valid or d_m2 > config.spatial_mahalanobis_thresh:
                continue

            # Quantize cost into micro-units (integer representation)
            # Add small confidence penalty to prefer higher-confidence matches
            cost_val = d_m2 + (1.0 - trk.confidence) * 0.1
            cost_int[i, j] = int(round(cost_val * 10**6))

    assignment, _ = solve_exact_forced_prefix_hungarian_int(cost_int, infeasible_threshold=10**9)

    matched_pairs: Dict[str, str] = {}
    unmatched_tracklets: List[LocalTracklet] = []

    for r_idx, c_idx in assignment:
        trk = drone_tracklets[r_idx]
        if c_idx >= 0 and c_idx < M:
            # Matched to real global object
            glob = active_globals[c_idx]
            matched_pairs[trk.local_track_id] = glob.global_id
        else:
            # Assigned to dummy column or unassigned
            unmatched_tracklets.append(trk)

    return matched_pairs, unmatched_tracklets


@overload
def fuse_covariance_intersection(
    estimates: List[SourceEstimate],
    noise_floor: float = ...,
    method: str = ...,
    return_diagnostics: Literal[False] = ...,
) -> Tuple[float, float, float, float, float]: ...


@overload
def fuse_covariance_intersection(
    estimates: List[SourceEstimate],
    noise_floor: float = ...,
    method: str = ...,
    return_diagnostics: Literal[True] = ...,
) -> Tuple[float, float, float, float, float, Dict[str, Any]]: ...


@overload
def fuse_covariance_intersection(
    estimates: List[SourceEstimate],
    noise_floor: float = ...,
    method: str = ...,
    return_diagnostics: bool = ...,
) -> Union[Tuple[float, float, float, float, float], Tuple[float, float, float, float, float, Dict[str, Any]]]: ...


def fuse_covariance_intersection(
    estimates: List[SourceEstimate],
    noise_floor: float = 0.25,
    method: str = "optimized",
    return_diagnostics: bool = False
) -> Union[Tuple[float, float, float, float, float], Tuple[float, float, float, float, float, Dict[str, Any]]]:
    """
    Computes Covariance Intersection (CI) across retained drone estimates:
        P_CI^(-1) = sum_{k=1}^K omega_k * P_k^(-1) = Y_CI
        mu_CI = P_CI * sum_{k=1}^K omega_k * P_k^(-1) * mu_k
    where sum omega_k = 1 and omega_k >= 0.

    Mathematical Equivalence:
        Since P_CI = Y_CI^(-1), det(P_CI) = 1 / det(Y_CI).
        Therefore:
            min_{omega} det(P_CI) <=> max_{omega} det(Y_CI) <=> min_{omega} -log det(Y_CI)
        Solving min_{omega} -log det(Y_CI) is strictly equivalent to minimizing det(P_CI).

    Methods:
        - 'optimized': Deterministic determinant-optimal CI. For K=2, solves scalar
          convex optimization min_{omega in [0, 1]} -log det(Y_CI(omega)) via Brent's method.
          For K > 2, solves constrained convex optimization min -log det(sum omega_k P_k^-1) via SLSQP.
          If solver fails or does not converge, deterministically falls back to 'inverse_trace'.
        - 'inverse_trace': Deterministic trace-normalized heuristic weights omega_k proportional to 1/Tr(P_k).
        - 'equal_weight': Conservative equal weights omega_k = 1/K (valid conservative CI, NOT determinant-optimal).

    Invariants Enforced:
        1. Order Invariance: Canonical sorting of estimates ensures identical results regardless of input ordering.
        2. Non-negativity & Unity Sum: All weights omega_k >= 0 and sum omega_k == 1.0.
        3. SPD Guarantee: Fused covariance P_CI is strictly symmetric positive definite.
        4. Conditioning: Ill-conditioned or degenerate covariances are regularized with eigenvalue floor.
        5. Telemetry Integrity: Optimizer failure cannot silently be reported as optimized CI.

    Returns:
        (fused_east, fused_north, fused_cov_xx, fused_cov_yy, fused_cov_xy)
        or (fused_east, fused_north, fused_cov_xx, fused_cov_yy, fused_cov_xy, diagnostics) if return_diagnostics=True.
    """
    K = len(estimates)
    diagnostics: Dict[str, Any] = {
        "method_requested": method,
        "method_applied": "single_estimate" if K == 1 else method,
        "optimizer_success": True,
        "weights": [1.0] if K == 1 else [],
        "input_count": K,
        "input_conditions": [],
        "output_condition": 1.0,
        "eigenvalues_P": []
    }

    if K == 0:
        raise ValueError("Cannot perform Covariance Intersection on 0 estimates")
    if K == 1:
        e = estimates[0]
        res_single = (e.east_m, e.north_m, e.cov_xx, e.cov_yy, e.cov_xy)
        return (*res_single, diagnostics) if return_diagnostics else res_single

    # 1. Canonical deterministic sorting to guarantee input order invariance
    canonical_estimates = sorted(
        estimates,
        key=lambda e: (e.drone_id, e.local_track_id, e.last_seen_ms, e.east_m, e.north_m)
    )

    # 2. Extract, symmetrize, and condition information matrices
    info_matrices = []
    info_vectors = []
    cov_matrices = []

    for est in canonical_estimates:
        # Construct 2x2 covariance
        P = np.array([[est.cov_xx, est.cov_xy], [est.cov_xy, est.cov_yy]], dtype=np.float64)
        # Symmetrize
        P = 0.5 * (P + P.T)

        # Spectral decomposition for robust eigenvalue regularization
        eigvals, eigvecs = np.linalg.eigh(P)
        # Floor eigenvalues to noise floor and numerical epsilon
        min_eig = max(noise_floor, 1e-6)
        clamped_eigvals = np.maximum(eigvals, min_eig)
        diagnostics["input_conditions"].append(float(clamped_eigvals[1] / max(clamped_eigvals[0], 1e-12)))

        P_conditioned = eigvecs @ np.diag(clamped_eigvals) @ eigvecs.T
        P_conditioned = 0.5 * (P_conditioned + P_conditioned.T)
        cov_matrices.append(P_conditioned)

        # Invert to information matrix Y = P^-1
        inv_eigvals = 1.0 / clamped_eigvals
        Y = eigvecs @ np.diag(inv_eigvals) @ eigvecs.T
        Y = 0.5 * (Y + Y.T)
        info_matrices.append(Y)

        state_vec = np.array([est.east_m, est.north_m], dtype=np.float64)
        info_vectors.append(Y @ state_vec)

    # 3. Compute weights according to requested method
    if method == "equal_weight":
        raw_weights = [1.0 / K] * K
        diagnostics["method_applied"] = "equal_weight"
        diagnostics["optimizer_success"] = True
    elif method == "inverse_trace":
        inv_traces = [1.0 / max(float(np.trace(P)), 1e-6) for P in cov_matrices]
        sum_it = sum(inv_traces)
        raw_weights = [it / sum_it for it in inv_traces]
        diagnostics["method_applied"] = "inverse_trace"
        diagnostics["optimizer_success"] = True
    elif method == "optimized":
        if K == 2:
            # Deterministic scalar optimization over omega in [0, 1]
            Y1 = info_matrices[0]
            Y2 = info_matrices[1]

            def obj_2(w: float) -> float:
                w_clamped = min(max(w, 0.0), 1.0)
                Y_f = w_clamped * Y1 + (1.0 - w_clamped) * Y2
                sign, logdet = np.linalg.slogdet(Y_f)
                if sign <= 0:
                    return 1e12
                # Minimizing det(P) is equivalent to maximizing log det(Y)
                return -logdet

            res = minimize_scalar(obj_2, bounds=(0.0, 1.0), method="bounded")
            if getattr(res, "success", True):
                best_w = float(min(max(res.x, 0.0), 1.0))
                raw_weights = [best_w, 1.0 - best_w]
                diagnostics["method_applied"] = "optimized_brent"
                diagnostics["optimizer_success"] = True
            else:
                inv_traces = [1.0 / max(float(np.trace(P)), 1e-6) for P in cov_matrices]
                sum_it = sum(inv_traces)
                raw_weights = [it / sum_it for it in inv_traces]
                diagnostics["method_applied"] = "fallback_inverse_trace"
                diagnostics["optimizer_success"] = False
        else:
            # Deterministic multi-dimensional constrained optimization
            # Initial guess: inverse-trace weights
            inv_traces = [1.0 / max(float(np.trace(P)), 1e-6) for P in cov_matrices]
            sum_it = sum(inv_traces)
            w0 = np.array([it / sum_it for it in inv_traces])

            def obj_k(w: np.ndarray) -> float:
                Y_f = np.zeros((2, 2))
                for wi, Yi in zip(w, info_matrices):
                    Y_f += wi * Yi
                sign, logdet = np.linalg.slogdet(Y_f)
                if sign <= 0:
                    return 1e12
                return -logdet

            constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0})
            bounds = [(0.0, 1.0) for _ in range(K)]
            opt_res = minimize(obj_k, w0, method='SLSQP', bounds=bounds, constraints=constraints)
            if opt_res.success:
                raw_weights = [float(x) for x in opt_res.x]
                diagnostics["method_applied"] = "optimized_slsqp"
                diagnostics["optimizer_success"] = True
            else:
                raw_weights = [float(x) for x in w0]
                diagnostics["method_applied"] = "fallback_inverse_trace"
                diagnostics["optimizer_success"] = False
    else:
        raise ValueError(f"Unknown Covariance Intersection method: {method}")

    # 4. Enforce strict non-negativity and exact sum normalization
    clamped_weights = [max(0.0, float(w)) for w in raw_weights]
    total_w = sum(clamped_weights)
    if total_w <= 1e-9:
        weights = [1.0 / K] * K
    else:
        weights = [w / total_w for w in clamped_weights]

    # 5. Weighted combination in information form
    P_inv_fused: np.ndarray = np.zeros((2, 2), dtype=np.float64)
    info_vec_fused: np.ndarray = np.zeros(2, dtype=np.float64)

    for w, Y, y in zip(weights, info_matrices, info_vectors):
        P_inv_fused += w * Y
        info_vec_fused += w * y

    # Symmetrize information matrix
    P_inv_fused = 0.5 * (P_inv_fused + P_inv_fused.T)

    # Spectral inversion back to covariance domain
    eigvals_Y, eigvecs_Y = np.linalg.eigh(P_inv_fused)
    eigvals_P = 1.0 / np.maximum(eigvals_Y, 1e-8)
    P_fused = eigvecs_Y @ np.diag(eigvals_P) @ eigvecs_Y.T
    P_fused = 0.5 * (P_fused + P_fused.T)

    # State estimate mean
    mu_fused = P_fused @ info_vec_fused

    eigvals_out = np.linalg.eigvalsh(P_fused)
    diagnostics["weights"] = [float(w) for w in weights]
    diagnostics["output_condition"] = float(eigvals_out[1] / max(eigvals_out[0], 1e-12))
    diagnostics["eigenvalues_P"] = [float(x) for x in eigvals_out]

    out_tuple = (
        float(mu_fused[0]),
        float(mu_fused[1]),
        float(P_fused[0, 0]),
        float(P_fused[1, 1]),
        float(P_fused[0, 1])
    )
    if return_diagnostics:
        return (*out_tuple, diagnostics)
    return out_tuple


def update_global_object_with_tracklet(
    glob: GlobalObject,
    tracklet: LocalTracklet,
    ref_lat: float,
    ref_lon: float,
    ref_alt: float = 0.0,
    config: Optional[GFSConfig] = None
) -> None:
    """
    Updates an existing GlobalObject with a matched LocalTracklet using
    retained source-level Covariance Intersection.
    Handles promotion from PROVISIONAL to CONFIRMED when multi-drone or high-confidence
    criteria are satisfied.
    """
    # Capture previous estimate for single-drone temporal baseline computation
    prev_est = glob.source_estimates.get(tracklet.drone_id)
    prev_east = prev_est.east_m if prev_est is not None else None
    prev_north = prev_est.north_m if prev_est is not None else None

    # 1. Update retained source estimate for this drone
    prev_obs_count = prev_est.observation_count if prev_est is not None else 0
    merged_confidence = max(prev_est.confidence, tracklet.confidence) if prev_est is not None else tracklet.confidence

    glob.source_estimates[tracklet.drone_id] = SourceEstimate(
        drone_id=tracklet.drone_id,
        drone_session_id=tracklet.drone_session_id,
        session_generation=tracklet.session_generation,
        local_track_id=tracklet.local_track_id,
        east_m=tracklet.east_m,
        north_m=tracklet.north_m,
        cov_xx=tracklet.cov_xx,
        cov_yy=tracklet.cov_yy,
        cov_xy=tracklet.cov_xy,
        confidence=merged_confidence,
        last_seen_ms=tracklet.last_seen_ms,
        observation_count=prev_obs_count + len(tracklet.observations)
    )

    # 2. Deterministic multi-estimate CI over all retained drone estimates
    estimates = list(glob.source_estimates.values())
    f_east, f_north, f_cxx, f_cyy, f_cxy, diag = fuse_covariance_intersection(
        estimates,
        return_diagnostics=True
    )
    glob.fusion_method = diag["method_applied"]

    glob.east_m = f_east
    glob.north_m = f_north
    glob.cov_xx = f_cxx
    glob.cov_yy = f_cyy
    glob.cov_xy = f_cxy

    # Convert ENU back to geodetic WGS84
    new_lat, new_lon, new_alt = enu_to_wgs84(f_east, f_north, tracklet.up_m, ref_lat, ref_lon, ref_alt)
    glob.lat = new_lat
    glob.lon = new_lon
    glob.alt = new_alt

    # Combine confidence (probabilistic union across sources)
    # 1 - prod(1 - conf_k)
    prob_miss = 1.0
    for est in estimates:
        prob_miss *= (1.0 - est.confidence)
    glob.confidence = round(1.0 - prob_miss, 4)

    # 3. Lifecycle Evaluation and Promotion
    current_state = getattr(glob, "lifecycle_state", LifecycleState.CONFIRMED.value)
    if current_state == LifecycleState.PROVISIONAL.value:
        distinct_drones = set(glob.source_estimates.keys())
        max_conf = max(e.confidence for e in estimates)

        # Rule A: Multi-drone confirmation (>= 2 distinct drones)
        if len(distinct_drones) >= 2:
            glob.lifecycle_state = LifecycleState.CONFIRMED.value
            glob.confirmation_method = "multi_drone_association"
            glob.provisional_reason = None
            glob.expires_at_ms = None
        # Rule B: Single-drone high confidence (>= 0.70)
        elif max_conf >= 0.70:
            glob.lifecycle_state = LifecycleState.CONFIRMED.value
            glob.confirmation_method = "single_drone_high_confidence"
            glob.provisional_reason = None
            glob.expires_at_ms = None
        # Rule C: Gated experimental temporal parallax confirmation
        elif config and getattr(config, "enable_temporal_parallax_confirmation", False):
            # Same drone tracked for >= min_duration with >= min_baseline translation
            min_dur_ms = int(getattr(config, "temporal_parallax_min_duration_s", 5.0) * 1000)
            min_base_m = float(getattr(config, "temporal_parallax_min_baseline_m", 4.0))
            tracking_dur_ms = max(glob.last_seen_ms, tracklet.last_seen_ms) - glob.first_seen_ms

            baseline_m = 0.0
            if prev_east is not None and prev_north is not None:
                baseline_m = math.hypot(tracklet.east_m - prev_east, tracklet.north_m - prev_north)
            elif len(tracklet.observations) >= 2:
                o_first = tracklet.observations[0]
                o_last = tracklet.observations[-1]
                baseline_m = math.hypot(o_last.east_m - o_first.east_m, o_last.north_m - o_first.north_m)

            if tracking_dur_ms >= min_dur_ms and baseline_m >= min_base_m:
                glob.lifecycle_state = LifecycleState.CONFIRMED.value
                glob.confirmation_method = "temporal_parallax"
                glob.provisional_reason = None
                glob.expires_at_ms = None
            else:
                ttl_ms = getattr(config, "provisional_object_ttl_ms", 60000)
                glob.expires_at_ms = max(glob.last_seen_ms, tracklet.last_seen_ms) + ttl_ms
        else:
            # Remains provisional; refresh TTL
            ttl_ms = getattr(config, "provisional_object_ttl_ms", 60000) if config else 60000
            glob.expires_at_ms = max(glob.last_seen_ms, tracklet.last_seen_ms) + ttl_ms
    elif current_state == LifecycleState.CONFIRMED.value:
        distinct_drones = set(glob.source_estimates.keys())
        if len(distinct_drones) >= 2 and glob.confirmation_method != "multi_drone_association":
            glob.confirmation_method = "multi_drone_association"

    # Update metadata
    glob.last_seen_ms = max(glob.last_seen_ms, tracklet.last_seen_ms)
    glob.observation_count += len(tracklet.observations)
    glob.source_count = len(glob.source_estimates)
    glob.version += 1

