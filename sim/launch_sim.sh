#!/bin/bash
export DRONEOS_PROFILE=sim

# Launch all 4 drones in the background
python3 start_drone1.py --ws-port 8080 &
P1=$!
python3 start_drone2.py --ws-port 8081 &
P2=$!
python3 start_drone3.py --ws-port 8082 &
P3=$!
python3 start_drone4.py --ws-port 8083 &
P4=$!

echo "Launched 4 sim nodes (PIDs: $P1 $P2 $P3 $P4)."
echo "Press Ctrl+C to terminate all nodes."

trap "kill $P1 $P2 $P3 $P4; exit" SIGINT SIGTERM
wait
