from pipeline.routing import route_worker


def test_routing_detour_cap():
    # Simple grid network
    # 0 -- 1 -- 2 (short path, uncovered, len 10+10 = 20)
    # |         |
    # 3 -- 4 -- 5 (long path, covered, len 15+15+15 = 45)
    # origin: 0, dest: 2

    edges_dict = {
        "u": [0, 1, 0, 3, 4, 5, 2],
        "v": [1, 2, 3, 4, 5, 2, 5],  # 2-5 connection
        "length_m": [10.0, 10.0, 15.0, 15.0, 15.0, 5.0, 5.0],
        "is_covered": [0, 0, 1, 1, 1, 1, 1],
    }
    # shortest = 0-1-2 (len = 20)
    # sheltered = 0-3-4-5-2 (len = 15+15+15+5 = 50)

    od_pairs = {0: [2]}
    # lambda=3.0 makes uncovered cost = 20 * 4 = 80.
    # sheltered path cost = 50 (since it's covered).
    # So sheltered routing will pick the 50m path.
    shelter_lambda = 3.0
    detour_budget = 1.25

    # Budget = 20 * 1.25 = 25. The covered path (50) is > 25.
    # It should fallback to shortest.

    res = route_worker((edges_dict, od_pairs, shelter_lambda, detour_budget))
    assert len(res) == 1

    assert res[0]["routing_type"] == "shortest_due_to_detour"
    assert res[0]["length_m"] == 20.0


def test_routing_sheltered_success():
    # 0 -- 1 -- 2 (short path, uncovered, len 10+10 = 20)
    # |         |
    # 3 -- 4 -- 2 (long path, covered, len 10+10+2 = 22)
    # origin: 0, dest: 2
    edges_dict = {
        "u": [0, 1, 0, 3, 4],
        "v": [1, 2, 3, 4, 2],
        "length_m": [10.0, 10.0, 10.0, 10.0, 2.0],
        "is_covered": [0, 0, 1, 1, 1],
    }

    # shortest = 0-1-2 (len = 20)
    # sheltered = 0-3-4-2 (len = 22)
    # budget = 20 * 1.25 = 25. 22 is within budget!

    od_pairs = {0: [2]}
    res = route_worker((edges_dict, od_pairs, 0.6, 1.25))

    assert res[0]["routing_type"] == "sheltered"
    assert res[0]["length_m"] == 22.0
    assert res[0]["covered_m"] == 22.0
    assert res[0]["covered_ratio"] == 1.0


def test_routing_lambda_zero():
    # 0 -- 1 -- 2 (short path, uncovered, len 20)
    # |         |
    # 3 -- 4 -- 2 (long path, covered, len 22)
    # origin: 0, dest: 2
    edges_dict = {
        "u": [0, 1, 0, 3, 4],
        "v": [1, 2, 3, 4, 2],
        "length_m": [10.0, 10.0, 10.0, 10.0, 2.0],
        "is_covered": [0, 0, 1, 1, 1],
    }
    od_pairs = {0: [2]}

    # Lambda = 0 means we do NOT care about shelter.
    # Cost is equal to geometric length.
    # Therefore, shortest path (20) wins over covered path (22).
    # Since it's within budget (1.25*20 = 25), it might be labeled 'sheltered'
    # because it fell within budget, BUT the path length will be 20.0, not 22.0.
    res = route_worker((edges_dict, od_pairs, 0.0, 1.25))

    assert res[0]["length_m"] == 20.0
    assert res[0]["covered_m"] == 0.0
    assert res[0]["covered_ratio"] == 0.0
