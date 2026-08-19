import sys
import asyncio
from pathlib import Path
from PySide6.QtWidgets import QApplication
import qasync

from GroundStation.shared.utils.logger import setup_logger
from GroundStation.shared.communication.serializers import JsonSerializer
from GroundStation.shared.communication.network_node import UdpNetworkAdapter
from GroundStation.core.network_manager import GSNetworkManager
from GroundStation.ui.main_window import GSMainWindow
from GroundStation.shared.config.loader import load_yaml_config
from GroundStation.shared.config.models import NetworkConfig, GSUIConfig

logger = setup_logger("GroundStation")

async def run_groundstation():
    # Load Configs
    config_dir = Path(__file__).resolve().parent / "configs"
    net_cfg = load_yaml_config(config_dir / "network.yaml", NetworkConfig)
    ui_cfg = load_yaml_config(config_dir / "groundstation.yaml", GSUIConfig)
    
    gs_id = ui_cfg.gs_id

    # qasync.run() already creates QApplication.instance() and sets the event loop
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    serializer = JsonSerializer()
    network_adapter = UdpNetworkAdapter(
        gs_id, 
        net_cfg.host, 
        net_cfg.port, 
        net_cfg.broadcast_address, 
        serializer,
        net_cfg.peer_host,
        net_cfg.peer_port
    )
    network_manager = GSNetworkManager(network_adapter, gs_id)
    
    await network_manager.start()
    
    # Start the periodic GS heartbeat
    async def gs_heartbeat_loop():
        try:
            while True:
                await network_manager.broadcast_heartbeat()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("GroundStation heartbeat loop cancelled.")
            
    heartbeat_task = asyncio.create_task(gs_heartbeat_loop())
    
    # Create Main Window
    window = GSMainWindow(network_manager)
    window.show()
    
    logger.info("GroundStation UI started.")
    
    # Keep the async task alive until the Qt Application quits
    stop_event = asyncio.Event()
    app.aboutToQuit.connect(stop_event.set)
    
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down GroundStation networking...")
        await network_manager.stop()
        heartbeat_task.cancel()
        logger.info("GroundStation shutdown complete.")

if __name__ == "__main__":
    try:
        qasync.run(run_groundstation())
    except KeyboardInterrupt:
        pass
