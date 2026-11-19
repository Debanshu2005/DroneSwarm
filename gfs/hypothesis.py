"""
Stage B: New Global Object Formation (GFA v1.0).
Implements complete-linkage clique hypothesis formation, bounded Bron-Kerbosch enumeration,
component-level timeout deferral, and deterministic ambiguity quarantine.
"""

from __future__ import annotations
import math
import time
import uuid
import hashlib
from typing import List, Dict, Set, Tuple, Optional, overload, Literal
import numpy as np

from gfs.models import (
    LocalTracklet,
    GlobalObject,
    SourceEstimate,
    LifecycleState,
    enu_to_wgs84
)
from gfs.config import GFSConfig
from gfs.association import (
    compute_mahalanobis_2d,
    fuse_covariance_intersection
)


def tracklets_are_compatible(
    t1: LocalTracklet,
    t2: LocalTracklet,
    config: GFSConfig
) -> bool:
    """
    Evaluates mutual pairwise compatibility between two local tracklets.
    All pairwise constraints must be satisfied for an edge to exist.
    """
    # 1. Class identity
    if t1.label != t2.label:
        return False

    # 2. Drone exclusivity: two distinct tracklets from the same drone NEVER merge
    if t1.drone_id == t2.drone_id:
        return False

    # 3. Pairwise temporal compatibility
    time_diff = abs(t1.last_seen_ms - t2.last_seen_ms)
    if time_diff > (config.temporal_max_diff_ms + 2 * config.clock_skew_sigma_ms):
        return False

    # 4. Spatial physical distance outer bound
    dx = t1.east_m - t2.east_m
    dy = t1.north_m - t2.north_m
    dist = math.hypot(dx, dy)
    if dist > config.spatial_physical_r_max_m:
        return False

    # 5. Mahalanobis innovation gating
    d_m2, valid = compute_mahalanobis_2d(
        dx, dy,
        t1.cov_xx, t1.cov_yy, t1.cov_xy,
        t2.cov_xx, t2.cov_yy, t2.cov_xy,
        noise_floor=config.spatial_noise_floor_m2
    )
    if not valid or d_m2 > config.spatial_mahalanobis_thresh:
        return False

    return True


@overload
def bounded_bron_kerbosch(
    adj: Dict[str, Set[str]],
    max_cliques: int = ...,
    time_limit_ms: float = ...,
    return_reason: Literal[False] = ...,
) -> Tuple[List[Set[str]], bool]: ...


@overload
def bounded_bron_kerbosch(
    adj: Dict[str, Set[str]],
    max_cliques: int = ...,
    time_limit_ms: float = ...,
    return_reason: Literal[True] = ...,
) -> Tuple[List[Set[str]], bool, str]: ...


@overload
def bounded_bron_kerbosch(
    adj: Dict[str, Set[str]],
    max_cliques: int = ...,
    time_limit_ms: float = ...,
    return_reason: bool = ...,
) -> Tuple[List[Set[str]], bool] | Tuple[List[Set[str]], bool, str]: ...


def bounded_bron_kerbosch(
    adj: Dict[str, Set[str]],
    max_cliques: int = 500,
    time_limit_ms: float = 25.0,
    return_reason: bool = False
) -> Tuple[List[Set[str]], bool] | Tuple[List[Set[str]], bool, str]:
    """
    Bron-Kerbosch maximal clique enumeration with pivoting and strict resource bounds.
    Returns (cliques, is_truncated) by default, or (cliques, is_truncated, truncation_reason)
    if return_reason=True.
    """
    cliques: List[Set[str]] = []
    t_start = time.perf_counter()
    truncated = False
    truncation_reason = ""

    def bk(R: Set[str], P: Set[str], X: Set[str]) -> None:
        nonlocal truncated, truncation_reason
        if truncated:
            return

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        if elapsed_ms >= time_limit_ms:
            truncated = True
            truncation_reason = "CLIQUE_TIMEOUT_DEFERRED"
            return

        if len(cliques) >= max_cliques:
            truncated = True
            truncation_reason = "CLIQUE_COUNT_EXHAUSTED"
            return

        if not P and not X:
            cliques.append(R)
            return

        # Choose pivot vertex with maximum degree in P
        pivot = max(P | X, key=lambda u: len(P & adj[u]))
        for v in sorted(P - adj[pivot]):
            bk(R | {v}, P & adj[v], X & adj[v])
            if truncated:
                return
            P.remove(v)
            X.add(v)

    all_vertices = set(adj.keys())
    bk(set(), all_vertices, set())
    if return_reason:
        return cliques, truncated, truncation_reason
    return cliques, truncated


def form_new_global_objects(
    unmatched_tracklets: List[LocalTracklet],
    config: GFSConfig,
    ref_lat: float,
    ref_lon: float,
    ref_alt: float = 0.0
) -> Tuple[List[GlobalObject], List[LocalTracklet], List[LocalTracklet]]:
    """
    Forms new global objects from unmatched local tracklets using Stage B algorithm.
    Enforces deterministic ambiguity resolution, resource bounds, and order invariance.

    Returns:
        new_globals: List[GlobalObject] (accepted hypotheses)
        ambiguous_deferred: List[LocalTracklet] (quarantined due to competing cliques or timeout)
        low_confidence_deferred: List[LocalTracklet] (isolated single-drone observations)
    """
    if not unmatched_tracklets:
        return [], [], []

    # Deterministic input ordering (guarantees order invariance regardless of input permutations)
    sorted_tracklets = sorted(
        unmatched_tracklets,
        key=lambda t: (t.drone_id, t.local_track_id, t.first_seen_ms, t.east_m, t.north_m)
    )

    # Map tracklet key to object
    tracklet_map: Dict[str, LocalTracklet] = {
        f"{t.drone_id}:{t.local_track_id}": t for t in sorted_tracklets
    }
    tracklet_keys = sorted(tracklet_map.keys())

    # Build adjacency graph
    adj: Dict[str, Set[str]] = {k: set() for k in tracklet_keys}
    for i in range(len(tracklet_keys)):
        k1 = tracklet_keys[i]
        t1 = tracklet_map[k1]
        for j in range(i + 1, len(tracklet_keys)):
            k2 = tracklet_keys[j]
            t2 = tracklet_map[k2]
            if tracklets_are_compatible(t1, t2, config):
                adj[k1].add(k2)
                adj[k2].add(k1)

    # Decompose into connected components
    visited: Set[str] = set()
    components: List[Set[str]] = []
    for k in tracklet_keys:
        if k not in visited:
            comp: Set[str] = set()
            queue = [k]
            visited.add(k)
            while queue:
                curr = queue.pop(0)
                comp.add(curr)
                for neighbor in sorted(adj[curr]):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(comp)

    # Deterministic component ordering
    components.sort(key=lambda c: sorted(c)[0])

    new_globals: List[GlobalObject] = []
    ambiguous_deferred: List[LocalTracklet] = []
    low_confidence_deferred: List[LocalTracklet] = []

    for comp in components:
        # Check component vertex limit (structural memory and compute bound)
        if len(comp) > config.clique_max_observations:
            # Component exceeds max observations: safe deferral of entire candidate component
            for k in sorted(comp):
                ambiguous_deferred.append(tracklet_map[k])
            continue

        comp_adj = {k: (adj[k] & comp) for k in comp}
        cliques, truncated, truncation_reason = bounded_bron_kerbosch(
            comp_adj,
            max_cliques=config.clique_max_cliques,
            time_limit_ms=config.clique_time_limit_ms,
            return_reason=True
        )

        if truncated:
            # Resource limit reached (timeout or clique exhaustion):
            # Safe deferral of the ENTIRE affected connected component
            for k in sorted(comp):
                ambiguous_deferred.append(tracklet_map[k])
            continue

        # Ambiguity detection: count maximal cliques containing each tracklet
        membership_count = {k: 0 for k in comp}
        for c in cliques:
            for k in c:
                membership_count[k] += 1

        # Observations participating in multiple competing maximal cliques are ambiguous
        comp_ambiguous = {k for k, count in membership_count.items() if count > 1}
        for k in sorted(comp_ambiguous):
            ambiguous_deferred.append(tracklet_map[k])

        # Clean non-ambiguous observations
        v_clean = {k for k in comp if membership_count[k] == 1}

        # Extract independent unambiguous candidate hypotheses
        seen_clean_member_tuples: Set[Tuple[str, ...]] = set()
        clean_hypotheses: List[List[LocalTracklet]] = []

        for c in cliques:
            clean_keys = tuple(sorted(k for k in c if k in v_clean))
            if clean_keys and clean_keys not in seen_clean_member_tuples:
                seen_clean_member_tuples.add(clean_keys)
                clean_hypotheses.append([tracklet_map[k] for k in clean_keys])

        # Deterministic sorting of clean candidate hypotheses
        clean_hypotheses.sort(key=lambda members: (
            -len(members),
            sorted([m.drone_id for m in members])[0],
            sorted([m.local_track_id for m in members])[0]
        ))

        # Evaluate candidate cliques independently without favoring larger cliques
        for clean_members in clean_hypotheses:
            candidate = clean_members[0]
            valid_enu = math.isfinite(candidate.east_m) and math.isfinite(candidate.north_m) and math.isfinite(candidate.up_m)
            if candidate.observations:
                valid_gps = (
                    math.isfinite(candidate.lat) and math.isfinite(candidate.lon)
                    and -90.0 <= candidate.lat <= 90.0 and -180.0 <= candidate.lon <= 180.0
                    and not (abs(candidate.lat) < 1e-6 and abs(candidate.lon) < 1e-6)
                )
            else:
                valid_gps = (
                    math.isfinite(candidate.lat) and math.isfinite(candidate.lon)
                    and -90.0 <= candidate.lat <= 90.0 and -180.0 <= candidate.lon <= 180.0
                )
            valid_coords = valid_enu and valid_gps
            valid_label = bool(candidate.label and isinstance(candidate.label, str) and len(candidate.label.strip()) > 0)

            if not valid_coords or not valid_label:
                for t in clean_members:
                    low_confidence_deferred.append(t)
                continue

            distinct_drones = {t.drone_id for t in clean_members}
            max_conf = max(t.confidence for t in clean_members)

            # Hypothesis acceptance rule:
            # Multi-drone confirmation (>= 2 drones) OR single-drone high confidence (>= 0.70) -> CONFIRMED
            # Single-drone valid candidate (0.55 <= conf < 0.70) -> PROVISIONAL
            is_confirmed = (len(distinct_drones) >= 2 or max_conf >= 0.70)
            is_provisional = False
            provisional_reason = None

            if is_confirmed:
                lifecycle_state = LifecycleState.CONFIRMED.value
                confirmation_method = "multi_drone_hypothesis" if len(distinct_drones) >= 2 else "single_drone_high_confidence"
                expires_at_ms = None
            elif max_conf >= 0.55:
                is_provisional = True
                lifecycle_state = LifecycleState.PROVISIONAL.value
                confirmation_method = None
                provisional_reason = "SINGLE_DRONE_LOW_CONFIDENCE"
                ttl_ms = getattr(config, "provisional_object_ttl_ms", 60000)
                expires_at_ms = max(t.last_seen_ms for t in clean_members) + ttl_ms
            else:
                for t in clean_members:
                    low_confidence_deferred.append(t)
                continue

            if is_confirmed or is_provisional:
                # Form new global object
                source_estimates: Dict[str, SourceEstimate] = {}
                for t in clean_members:
                    source_estimates[t.drone_id] = SourceEstimate(
                        drone_id=t.drone_id,
                        drone_session_id=t.drone_session_id,
                        session_generation=t.session_generation,
                        local_track_id=t.local_track_id,
                        east_m=t.east_m,
                        north_m=t.north_m,
                        cov_xx=t.cov_xx,
                        cov_yy=t.cov_yy,
                        cov_xy=t.cov_xy,
                        confidence=t.confidence,
                        last_seen_ms=t.last_seen_ms,
                        observation_count=len(t.observations)
                    )

                # Fuse via Covariance Intersection
                est_list = list(source_estimates.values())
                f_east, f_north, f_cxx, f_cyy, f_cxy, diag = fuse_covariance_intersection(
                    est_list,
                    return_diagnostics=True
                )

                # Compute geodetic coordinates
                up_m = float(np.mean([t.up_m for t in clean_members]))
                lat, lon, alt = enu_to_wgs84(f_east, f_north, up_m, ref_lat, ref_lon, ref_alt)

                # Combined confidence
                prob_miss = 1.0
                for e in est_list:
                    prob_miss *= (1.0 - e.confidence)
                glob_conf = round(1.0 - prob_miss, 4)

                total_obs = sum(len(t.observations) for t in clean_members)
                min_seen = min(t.first_seen_ms for t in clean_members)
                max_seen = max(t.last_seen_ms for t in clean_members)

                # Deterministic synthetic global ID based on sorted member IDs
                member_sig = "_".join(sorted(f"{t.drone_id}:{t.drone_session_id}:{t.session_generation}:{t.local_track_id}" for t in clean_members))
                stable_suffix = hashlib.sha256(member_sig.encode('utf-8')).hexdigest()[:8]

                new_glob = GlobalObject(
                    global_id=f"G-{stable_suffix}",
                    label=clean_members[0].label,
                    status="ACTIVE",
                    lat=lat,
                    lon=lon,
                    alt=alt,
                    east_m=f_east,
                    north_m=f_north,
                    cov_xx=f_cxx,
                    cov_yy=f_cyy,
                    cov_xy=f_cxy,
                    confidence=glob_conf,
                    first_seen_ms=min_seen,
                    last_seen_ms=max_seen,
                    observation_count=total_obs,
                    source_count=len(source_estimates),
                    source_estimates=source_estimates,
                    version=1,
                    fusion_method=diag["method_applied"],
                    lifecycle_state=lifecycle_state,
                    confirmation_method=confirmation_method,
                    provisional_reason=provisional_reason,
                    expires_at_ms=expires_at_ms
                )
                new_globals.append(new_glob)
            else:
                # Single-drone candidate failing provisional criteria: defer
                for t in clean_members:
                    low_confidence_deferred.append(t)

    # Sort new globals deterministically
    new_globals.sort(key=lambda g: (-round(g.confidence, 4), g.label, g.east_m, g.north_m, g.global_id))
    return new_globals, ambiguous_deferred, low_confidence_deferred

