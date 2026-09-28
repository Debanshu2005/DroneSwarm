import os
import re

file_path = "DroneOS/tests/test_airsim_adapter.py"
with open(file_path, "r") as f:
    content = f.read()

# Replace test_supersede_goto_with_land
old_test = """@pytest.mark.asyncio
async def test_supersede_goto_with_land(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=2.0)
        
        start = time.time()
        goto_task = asyncio.create_task(adapter.goto_local_ned(10.0, 0.0, -10.0))
        await asyncio.sleep(0.1)
        
        land_task = asyncio.create_task(adapter.land())
        
        land_res = await land_task
        goto_res = await goto_task
        
        duration = time.time() - start
        
        assert goto_res == False
        assert land_res == True
        assert duration < 2.5 # land preempted without waiting 2.0s lock
        assert adapter._mode == "HOLD"
        
        await adapter.disconnect()"""

new_test = """@pytest.mark.asyncio
async def test_supersede_goto_with_land(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=2.0)
        adapter.client.landAsync.return_value = FakeFuture(delay=0.1)
        
        start = time.time()
        goto_res = await adapter.goto_local_ned(10.0, 0.0, -10.0)
        await asyncio.sleep(0.1)
        
        land_res = await adapter.land()
        dur = time.time() - start
        
        assert goto_res == True
        assert land_res == True
        assert dur < 0.5
        
        await asyncio.sleep(2.0)
        # Land will have finished and set mode to HOLD
        assert adapter._mode == "HOLD"
        await adapter.disconnect()"""

content = content.replace(old_test, new_test)

b4_b8 = """
@pytest.mark.asyncio
async def test_b4_rtl_assertions(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.goHomeAsync.return_value = FakeFuture(delay=0.1)
        adapter.client.landAsync.return_value = FakeFuture(delay=0.1)
        
        start = time.time()
        res = await adapter.rtl()
        dur = time.time() - start
        assert res == True
        assert dur < 0.1
        
        await asyncio.sleep(0.3)
        adapter._telem_client._state.landed_state = 0
        await asyncio.sleep(0.2)
        
        telem = await adapter.get_telemetry()
        assert telem.armed_state == "DISARMED"
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b5_land_assertions(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.landAsync.return_value = FakeFuture(delay=0.1)
        
        res = await adapter.land()
        assert res == True
        
        await asyncio.sleep(0.2)
        adapter._telem_client._state.landed_state = 0
        await asyncio.sleep(0.2)
        
        telem = await adapter.get_telemetry()
        assert telem.armed_state == "DISARMED"
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b6_velocity_clamping_and_duration(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter.config.pipeline_hz = 10.0
        
        await adapter.move_velocity(10.0, 0.0, 0.0, 0.1)
        args = adapter.client.moveByVelocityBodyFrameAsync.call_args[0]
        assert args[0] == 5.0 # clamped
        assert args[3] >= 0.2 # max(0.1, 2/10)
        
        adapter.config.pipeline_hz = 20.0
        await adapter.move_velocity(1.0, 0.0, 0.0, 0.05)
        args2 = adapter.client.moveByVelocityBodyFrameAsync.call_args[0]
        assert args2[3] >= 0.1 # max(0.05, 2/20)
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b7_watcher_failure(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.landAsync.return_value = FakeFuture(result=Exception("Sim error"), delay=0.1)
        
        await adapter.land()
        await asyncio.sleep(0.2)
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b8_disconnect_cleans_tasks(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=10.0)
        await adapter.goto_location(47.0, -122.0, 10.0)
        
        assert len(adapter._bg_tasks) > 0
        await adapter.disconnect()
        assert len(adapter._bg_tasks) == 0
"""

content += b4_b8

with open(file_path, "w") as f:
    f.write(content)
print("Updated successfully")
