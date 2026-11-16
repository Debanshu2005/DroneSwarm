#!/bin/bash
export DRONEOS_PROFILE=test
export DRONEOS_PERCEPTION=1

# Launch GFS in background
python3 -m gfs.service &
P_GFS=$!

# Launch all 4 drones in the background
python3 start_drone1.py --ws-port 8081 &
P1=$!
python3 start_drone2.py --ws-port 8082 &
P2=$!
python3 start_drone3.py --ws-port 8083 &
P3=$!
python3 start_drone4.py --ws-port 8084 &
P4=$!

echo "Launched 4 sim nodes + GFS in test profile (PIDs: $P_GFS $P1 $P2 $P3 $P4)."
echo "Press Ctrl+C to terminate all nodes."

trap "kill $P_GFS $P1 $P2 $P3 $P4; exit" SIGINT SIGTERM
wait
