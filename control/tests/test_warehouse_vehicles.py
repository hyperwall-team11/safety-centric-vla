import numpy as np
import pytest

from warehouse import build_warehouse
from warehouse_vehicles import FORK_PERIOD, WarehouseVehicles, forklift_pose, hitch_pose, tug_pose


def test_forklift_lifts_pallet_carries_it_and_leaves_it_at_dock():
    model, data = build_warehouse()
    vehicles = WarehouseVehicles(model, data)
    forklift, forks, pallet = vehicles.ids[:3]

    data.time = 5
    vehicles.update(data)
    assert data.mocap_pos[pallet][2] > 0.2
    assert data.mocap_pos[forks][2] > data.mocap_pos[pallet][2]
    initial_y = data.mocap_pos[forklift][1]

    data.time = 8
    vehicles.update(data)
    assert data.mocap_pos[forklift][1] > initial_y
    delivered_y = data.mocap_pos[pallet][1]
    data.time = 14
    vehicles.update(data)
    assert data.mocap_pos[pallet][1] == delivered_y
    assert data.mocap_pos[pallet][2] == 0
    assert forklift_pose(0) == forklift_pose(FORK_PERIOD)


def test_tug_stops_twice_without_detaching_the_three_carts():
    model, data = build_warehouse()
    vehicles = WarehouseVehicles(model, data)
    assert len(vehicles.hitches) == 3
    np.testing.assert_allclose(tug_pose(1), tug_pose(3))
    np.testing.assert_allclose(tug_pose(18), tug_pose(20))
    assert tug_pose(10) != tug_pose(18)

    data.time = 12
    vehicles.update(data)
    for cart, mocap in enumerate(vehicles.hitches, start=1):
        x, y, _, length = hitch_pose(data.time, cart)
        np.testing.assert_allclose(data.mocap_pos[mocap], (x, y, 0.14))
        assert length == pytest.approx(hitch_pose(1, cart)[3])


def test_tug_delivers_to_each_supply_table_and_recovers_parcels_next_round():
    from warehouse_vehicles import SUPPLY_TABLES, supply_parcel_pose

    assert supply_parcel_pose(4, 0)[:2] == pytest.approx(SUPPLY_TABLES[0])
    assert supply_parcel_pose(4, 1)[:2] != pytest.approx(SUPPLY_TABLES[1])
    assert supply_parcel_pose(22, 1)[:2] == pytest.approx(SUPPLY_TABLES[1])
    assert supply_parcel_pose(40, 0)[:2] != pytest.approx(SUPPLY_TABLES[0])
    assert supply_parcel_pose(58, 1)[:2] != pytest.approx(SUPPLY_TABLES[1])
