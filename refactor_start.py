import glob
import re

def refactor_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. Remove wait_for_port definition
    content = re.sub(r'def wait_for_port.*?return False\n', '', content, flags=re.DOTALL)
    
    # 2. Remove get_mavsdk_server_path definition
    content = re.sub(r'def get_mavsdk_server_path.*?\n\n', '\n', content, flags=re.DOTALL)
    
    # 3. Remove manual MAVSDK spawn
    spawn_pattern = r'# 2\. Spawn MAVSDK Server manually.*?print\(f"\[\{drone_cfg\.drone_id\}\] MAVSDK server ready\. Starting Relay\.\.\."\)'
    content = re.sub(spawn_pattern, '', content, flags=re.DOTALL)
    
    # 4. Fix monkey patch
    old_patch = r"kwargs\['mavsdk_server_address'\] = '127\.0\.0\.1'\s+kwargs\['port'\] = server_port"
    new_patch = r"kwargs['port'] = server_port"
    content = re.sub(old_patch, new_patch, content)
    
    # 5. Remove MAVSDK_SERVER_PORT env var
    env_pattern = r"# Tell px4_adapter not to kill the pre-spawned mavsdk_server\s+import os\s+os\.environ\['MAVSDK_SERVER_PORT'\] = str\(server_port\)"
    content = re.sub(env_pattern, '', content)
    
    # 6. Remove mavsdk_proc cleanup in finally
    finally_pattern = r'print\(f"\[\{drone_cfg\.drone_id\}\] Terminating managed MAVSDK server.*?mavsdk_proc\.kill\(\)\s+'
    content = re.sub(finally_pattern, '', content, flags=re.DOTALL)

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Refactored {filepath}")

for f in glob.glob('start_drone[1-4].py'):
    refactor_file(f)
