import pytest
from hypothesis import given, strategies as st
from pipeline.scoring import (
    score_transit_access,
    score_bus_connectivity,
    score_rain_shelter,
    score_heat_comfort,
    score_crossing_friction,
    calculate_composite_score,
    NO_TRANSIT_IN_RANGE,
    NOT_YET_SCORED
)

# Dummy params for tests based on params.yaml
TRANSIT_PARAMS = {
    'full_credit_m': 400.0,
    'linear_floor_m': 800.0,
    'score_at_800m': 40.0,
    'zero_credit_m': 1200.0,
    'bus_interchange_full_credit_m': 200.0
}

BUS_PARAMS = {
    'full_credit_wait_min': 2.0,
    'zero_credit_wait_min': 15.0
}

CROSSING_PARAMS = {
    'penalty_per_crossing': 20.0
}

WEIGHTS = {
    'transit_access': 0.35,
    'bus_connectivity': 0.20,
    'rain_shelter': 0.25,
    'heat_comfort': 0.15,
    'crossing_friction': 0.05
}

def test_transit_access_edge_cases():
    # no MRT within 1,200m -> NO_TRANSIT_IN_RANGE
    assert score_transit_access(1201.0, TRANSIT_PARAMS) == NO_TRANSIT_IN_RANGE
    
    # exactly 1200m is still scored
    assert score_transit_access(1200.0, TRANSIT_PARAMS) == 0.0
    
    # fully within 400m
    assert score_transit_access(400.0, TRANSIT_PARAMS) == 100.0
    
    # bus interchange
    assert score_transit_access(200.0, TRANSIT_PARAMS, is_bus_interchange=True) == 100.0
    assert score_transit_access(201.0, TRANSIT_PARAMS, is_bus_interchange=True) < 100.0

def test_bus_connectivity_edge_cases():
    # zero buses -> NO_TRANSIT_IN_RANGE
    assert score_bus_connectivity(None, BUS_PARAMS) == NO_TRANSIT_IN_RANGE
    
    # great bus connectivity
    assert score_bus_connectivity(2.0, BUS_PARAMS) == 100.0
    
    # poor bus connectivity
    assert score_bus_connectivity(15.0, BUS_PARAMS) == 0.0
    assert score_bus_connectivity(16.0, BUS_PARAMS) == 0.0

def test_rain_shelter_edge_cases():
    # fully covered path
    assert score_rain_shelter(100.0, 100.0) == 100.0
    assert score_rain_shelter(0.0, 0.0) == 100.0  # length 0 implies perfectly covered or void
    
    # partially covered
    assert score_rain_shelter(50.0, 100.0) == 50.0

def test_heat_comfort_edge_cases():
    assert score_heat_comfort(100.0, 100.0) == 100.0
    assert score_heat_comfort(50.0, 100.0) == 50.0

def test_crossing_friction_edge_cases():
    # 0 crossings
    assert score_crossing_friction(0, CROSSING_PARAMS) == 100.0
    # 6+ crossings floor (score [0, 100])
    assert score_crossing_friction(6, CROSSING_PARAMS) == 0.0
    assert score_crossing_friction(10, CROSSING_PARAMS) == 0.0

def test_composite_score_edge_cases():
    # no MRT within 1,200m -> NO_TRANSIT_IN_RANGE triggers composite NO_TRANSIT_IN_RANGE
    subscores = {
        'transit_access': NO_TRANSIT_IN_RANGE,
        'bus_connectivity': 50.0,
        'rain_shelter': 50.0,
        'heat_comfort': 50.0,
        'crossing_friction': 50.0
    }
    assert calculate_composite_score(subscores, WEIGHTS) == NO_TRANSIT_IN_RANGE
    
    # bus NO_TRANSIT_IN_RANGE does not fail composite if transit is ok, treats as 0
    subscores_bus_fail = {
        'transit_access': 100.0,
        'bus_connectivity': NO_TRANSIT_IN_RANGE,
        'rain_shelter': 100.0,
        'heat_comfort': 100.0,
        'crossing_friction': 100.0
    }
    # Expected: 100*0.35 + 0*0.20 + 100*0.25 + 100*0.15 + 100*0.05 = 35 + 0 + 25 + 15 + 5 = 80
    assert abs(calculate_composite_score(subscores_bus_fail, WEIGHTS) - 80.0) < 1e-6

@given(st.floats(min_value=0.0, max_value=1500.0))
def test_property_transit_access(dist):
    score = score_transit_access(dist, TRANSIT_PARAMS)
    if score != NO_TRANSIT_IN_RANGE:
        assert 0.0 <= score <= 100.0

@given(st.floats(min_value=0.0, max_value=30.0))
def test_property_bus_connectivity(wait):
    score = score_bus_connectivity(wait, BUS_PARAMS)
    if score != NO_TRANSIT_IN_RANGE:
        assert 0.0 <= score <= 100.0

@given(st.floats(min_value=0.0, max_value=1000.0), st.floats(min_value=0.0, max_value=1000.0))
def test_property_rain_shelter(sheltered, total):
    # Ensure sheltered <= total for logical consistency
    if sheltered > total:
        total = sheltered
    score = score_rain_shelter(sheltered, total)
    assert 0.0 <= score <= 100.0

@given(st.integers(min_value=0, max_value=100))
def test_property_crossing_friction(crossings):
    score = score_crossing_friction(crossings, CROSSING_PARAMS)
    assert 0.0 <= score <= 100.0

@given(
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=100.0)
)
def test_property_composite_weighted_sum(t, b, r, h, c):
    subscores = {
        'transit_access': t,
        'bus_connectivity': b,
        'rain_shelter': r,
        'heat_comfort': h,
        'crossing_friction': c
    }
    score = calculate_composite_score(subscores, WEIGHTS)
    
    # composite = exact weighted sum
    expected = (
        t * WEIGHTS['transit_access'] +
        b * WEIGHTS['bus_connectivity'] +
        r * WEIGHTS['rain_shelter'] +
        h * WEIGHTS['heat_comfort'] +
        c * WEIGHTS['crossing_friction']
    )
    assert isinstance(score, float)
    assert abs(score - expected) < 1e-6
