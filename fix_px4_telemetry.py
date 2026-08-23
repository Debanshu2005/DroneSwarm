import re

with open("DroneOS/adapters/px4_adapter.py", "r") as f:
    content = f.read()

# Pattern for all subscribe methods
pattern = r'(async def _subscribe_\w+\(self\):\n)(        try:\n(?:            .*\n)+?        except asyncio\.CancelledError:\n            raise\n        except Exception as e:\n            logger\.error\(f"PX4 \w+ subscription failed: \{e\}"\)\n            self\._connected = False\n)'

def replace_match(match):
    header = match.group(1)
    body = match.group(2)
    
    # We want to indent the body by 4 spaces and insert `while self._connected:`
    # And replace `self._connected = False` with `await asyncio.sleep(2.0)`
    
    lines = body.split("\n")
    new_body = "        while self._connected:\n"
    for line in lines[:-1]:
        if "self._connected = False" in line:
            new_body += "                if \"AioRpcError\" in str(type(e)) and (\"UNAVAILABLE\" in str(e) or \"Stream removed\" in str(e)):\n"
            new_body += "                    pass # Stream dropped, let loop retry\n"
            new_body += "                await asyncio.sleep(2.0)\n"
        elif line.strip() != "":
            new_body += "    " + line + "\n"
        else:
            new_body += "\n"
            
    return header + new_body

new_content = re.sub(pattern, replace_match, content, flags=re.MULTILINE)

# Now manually fix position and velocity which have `import math` inside `try`
# The regex above captures the `import math` safely inside the body!

with open("DroneOS/adapters/px4_adapter.py", "w") as f:
    f.write(new_content)

