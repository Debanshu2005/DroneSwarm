import glob

files = glob.glob('DroneOS*/adapters/px4_adapter.py')

for f in files:
    with open(f, 'r') as file:
        content = file.read()
    
    # Update kill_orphaned_mavsdk signature
    if 'def kill_orphaned_mavsdk(target_conn: str):' in content:
        content = content.replace(
            "def kill_orphaned_mavsdk(target_conn: str):",
            "def kill_orphaned_mavsdk(target_conn: str) -> bool:\n            killed = False"
        )
        content = content.replace(
            "proc.kill()",
            "proc.kill()\n                            killed = True"
        )
        content = content.replace(
            "logger.warning(f\"Failed to kill orphaned MAVSDK server: {e}\")",
            "logger.warning(f\"Failed to kill orphaned MAVSDK server: {e}\")\n            return killed"
        )
    
    # Update the loop
    if 'kill_orphaned_mavsdk(conn_str)\n            # Recreate System' in content:
        content = content.replace(
            "kill_orphaned_mavsdk(conn_str)\n            # Recreate System",
            "if kill_orphaned_mavsdk(conn_str):\n                await asyncio.sleep(2.0)\n            # Recreate System"
        )
    
    with open(f, 'w') as file:
        file.write(content)
