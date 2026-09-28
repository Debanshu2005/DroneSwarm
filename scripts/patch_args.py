import glob

def patch_start_scripts():
    for f in glob.glob("start_drone*.py"):
        with open(f, 'r') as file:
            content = file.read()
        
        # Pass sys.argv[1:] to relay
        old_relay_call = "[sys.executable, str(relay_script)]"
        new_relay_call = "[sys.executable, str(relay_script)] + sys.argv[1:]"
        content = content.replace(old_relay_call, new_relay_call)
        
        # In start_drone.py (and start_drone1.py if applicable)
        # start_drone.py has: if 'relay.py' in joined and str(project_root) in joined:
        old_sd_cond = "if 'relay.py' in joined and str(project_root) in joined:"
        new_sd_cond = "if 'relay.py' in joined and str(project_root) in joined:\n                    if os.environ.get('DRONEOS_PROFILE') == 'sim':\n                        # In sim mode, skip blind cleanup that would kill sibling nodes\n                        continue"
        content = content.replace(old_sd_cond, new_sd_cond)
        
        # start_drone1.py has: elif cmdline and 'relay.py' in ' '.join(cmdline) and 'DroneOS' in ' '.join(cmdline):
        old_sd1_cond = "elif cmdline and 'relay.py' in ' '.join(cmdline) and 'DroneOS' in ' '.join(cmdline):"
        new_sd1_cond = "elif cmdline and 'relay.py' in ' '.join(cmdline) and 'DroneOS' in ' '.join(cmdline):\n                if is_sim: continue"
        content = content.replace(old_sd1_cond, new_sd1_cond)
        
        # start_drone2..4.py has: elif cmdline and 'relay.py' in ' '.join(cmdline) and '8080' in ' '.join(cmdline):
        old_sd2_cond = "elif cmdline and 'relay.py' in ' '.join(cmdline) and '8080' in ' '.join(cmdline):"
        new_sd2_cond = "elif cmdline and 'relay.py' in ' '.join(cmdline) and '8080' in ' '.join(cmdline):\n                if is_sim: continue"
        content = content.replace(old_sd2_cond, new_sd2_cond)
        
        with open(f, 'w') as file:
            file.write(content)
        print(f"Patched {f}")

patch_start_scripts()
