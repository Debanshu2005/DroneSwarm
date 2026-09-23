import os

for i in ['', '1', '2', '3']:
    pkg = f'DroneOS{i}'
    path = os.path.join(pkg, 'main.py')
    with open(path, 'r') as f:
        content = f.read()

    # Apply changes to run()
    run_target = "        await self.network.start()\n"
    run_replacement = f"""        await self.network.start()
        
        import logging
        from {pkg}.shared.utils.remote_log_handler import RemoteLogHandler
        self.remote_log_handler = RemoteLogHandler(self.node_id, self.network, asyncio.get_running_loop())
        logging.getLogger().addHandler(self.remote_log_handler)
        self._dispatch_task(self.remote_log_handler.drain_task())\n"""

    content = content.replace(run_target, run_replacement)

    # Apply changes to shutdown()
    shutdown_target = "        self._active_tasks.clear()\n"
    shutdown_replacement = """        self._active_tasks.clear()
        
        if hasattr(self, 'remote_log_handler'):
            self.remote_log_handler.stop()
            import logging
            logging.getLogger().removeHandler(self.remote_log_handler)\n"""

    content = content.replace(shutdown_target, shutdown_replacement)

    with open(path, 'w') as f:
        f.write(content)
    print(f'Patched {path}')
