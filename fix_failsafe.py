import re

with open("DroneOS/core/safety.py", "r") as f:
    content = f.read()

# I need to add a reset method to safety.py
if "def reset_failsafe" not in content:
    reset_method = """
    def reset_failsafe(self) -> None:
        self.is_failsafe_active = False
        logger.info("Failsafe/Emergency stop reset manually.")
"""
    # Insert it right before def trigger_connection_lost_failsafe
    content = content.replace("    async def trigger_connection_lost_failsafe", reset_method + "\n    async def trigger_connection_lost_failsafe")

with open("DroneOS/core/safety.py", "w") as f:
    f.write(content)

with open("DroneOS/core/command_handler.py", "r") as f:
    content = f.read()

# Add a reset command handler
if "CommandAction.EMERGENCY_RESET" not in content:
    reset_handler_logic = """
        elif action == CommandAction.EMERGENCY_RESET:
            self.safety_module.reset_failsafe()
            return "" # Approved
"""
    content = content.replace("        elif action == CommandAction.MOVE:", reset_handler_logic + "\n        elif action == CommandAction.MOVE:")

with open("DroneOS/core/command_handler.py", "w") as f:
    f.write(content)

print("Updated safety reset")

