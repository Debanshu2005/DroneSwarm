# Guide: Developer Quickstart

## 1. Prerequisites

Before setting up PhoneOS Swarm, ensure your development workstation meets the following requirements:

- **Operating System:** Windows 10/11 (with PowerShell) or Linux (Ubuntu 20.04/22.04 LTS).
- **Python:** Python 3.10, 3.11, or 3.12+ (tested up to 3.13).
- **Node.js:** Node.js 18.x or 20.x LTS with `npm`.
- **Git:** Git 2.30+.
- **Optional for Simulation:** Microsoft AirSim and Unreal Engine 4.27.
- **Optional for Android Builds:** Java Development Kit (JDK 17) and Android SDK Command-line Tools.

---

## 2. Repository Setup & Virtual Environment

Clone the repository and set up a clean Python virtual environment:

```bash
# Clone the repository
git clone https://github.com/Debanshu2005/DroneSwarm.git
cd DroneSwarm

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Linux / macOS:
source venv/bin/activate
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

---

## 3. Python Dependencies

Install the core dependencies required for DroneOS, networking, and testing:

```bash
pip install --upgrade pip
pip install pytest pytest-asyncio hypothesis pyyaml pydantic psutil websockets
```

If you plan to run AirSim simulation tests:
```bash
pip install msgpack-rpc-python airsim
```

If you plan to connect to Pixhawk hardware via MAVSDK:
```bash
pip install mavsdk grpcio
```

---

## 4. Ground Control Station Setup (Mobile App)

To run the React Ground Control Station locally:

```bash
cd mobile
npm install
npm run dev
```

The GCS interface will become available in your web browser at:
`http://localhost:5173`

---

## 5. Verifying the Installation (Running Tests)

Run the automated test suite to ensure your local environment is correctly configured:

```bash
# Run tests for DroneOS base
python -m pytest DroneOS/tests -q

# Run coordination regression tests across all four mirrored packages
python -m pytest DroneOS/tests/test_follower_recovery.py -v
python -m pytest DroneOS1/tests/test_follower_recovery.py -v
python -m pytest DroneOS2/tests/test_follower_recovery.py -v
python -m pytest DroneOS3/tests/test_follower_recovery.py -v
```

Expected result: All tests pass with zero failures.

---

## 6. Critical Developer Invariants

When contributing code to this repository, keep these three essential rules in mind:

1. **Mirrored Package Isolation:**
   - The repository maintains four mirrored packages: `DroneOS`, `DroneOS1`, `DroneOS2`, `DroneOS3`.
   - `DroneOS1`, `DroneOS2`, and `DroneOS3` must **never** import from `DroneOS.*`.
   - Verify zero cross-imports before committing:
     ```powershell
     python -c "import os; [print(f'{root}/{f}') for root, _, files in os.walk('.') for f in files if f.endswith('.py') and any(root.startswith(d) for d in ['DroneOS1','DroneOS2','DroneOS3']) for line in open(os.path.join(root,f), errors='ignore') if 'from DroneOS.' in line.split('#')[0]]"
     ```
2. **Deterministic Port Allocations:**
   - Do not change port assignments in `network.sim.yaml` or `start_drone*.py` without updating all four instances and the relay configurations.
3. **Safety Interlocks:**
   - Never bypass Pixhawk's native pre-arm or safety checks in `PX4Adapter`. DroneOS is an intelligence layer, not a replacement for autopilot safety authority.

