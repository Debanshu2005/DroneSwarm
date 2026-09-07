import pytest
from typing import List, Tuple

from DroneOS2.core.repulsion_field import compute_repulsion

def test_repulsion_zero_outside_radius():
    rep_n, rep_e = compute_repulsion([(3.0, 0.0)], radius=2.5, gain=1.0, max_displacement=2.0)
    assert rep_n == 0.0
    assert rep_e == 0.0

def test_repulsion_grows_inside_radius():
    rep_n1, rep_e1 = compute_repulsion([(2.0, 0.0)], radius=2.5, gain=1.0, max_displacement=2.0)
    rep_n2, rep_e2 = compute_repulsion([(1.0, 0.0)], radius=2.5, gain=1.0, max_displacement=2.0)
    
    assert rep_n1 < 0.0
    assert rep_n2 < rep_n1 
    assert rep_e1 == 0.0
    assert rep_e2 == 0.0
    assert abs(rep_n1 - (-0.1)) < 1e-5
    assert abs(rep_n2 - (-0.6)) < 1e-5

def test_repulsion_direction():
    rep_n, rep_e = compute_repulsion([(1.0, 1.0)], radius=2.5, gain=1.0, max_displacement=2.0)
    assert rep_n < 0.0
    assert rep_e < 0.0
    assert abs(rep_n - rep_e) < 1e-5

def test_repulsion_clamps():
    rep_n, rep_e = compute_repulsion([(0.1, 0.0)], radius=2.5, gain=1.0, max_displacement=2.0)
    assert abs(rep_n - (-2.0)) < 1e-5
    assert rep_e == 0.0
    
    rep_n_multi, rep_e_multi = compute_repulsion(
        [(0.1, 0.0), (0.0, 0.1)], 
        radius=2.5, gain=1.0, max_displacement=2.0
    )
    total_dist = (rep_n_multi**2 + rep_e_multi**2)**0.5
    assert abs(total_dist - 2.0) < 1e-5
    assert rep_n_multi < 0
    assert rep_e_multi < 0

def test_repulsion_epsilon_guard():
    rep_n, rep_e = compute_repulsion([(0.0, 0.0)], radius=2.5, gain=1.0, max_displacement=2.0)
    assert rep_n == 0.0
    assert rep_e == 0.0
