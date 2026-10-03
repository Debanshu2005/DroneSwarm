# Workspace Memory
This file is maintained automatically by Code Janitor so Claude, Codex, Bob, and any other AI agent can reuse repo context without rescanning everything from scratch.
Generated: 2026-10-03T11:42:56.118Z
Workspace: PhoneOS_Swarm
Workspace root: d:\CityGrid\my-project\PhoneOS_Swarm
Refresh reason: tracked-change
Output path: graphify-out/WORKSPACE_MEMORY.md
Shared mirror: workspacememory.md
Structured manifest: workspace.json
## Handoff Guidance
- Read `graphify-out/GRAPH_REPORT.md` first when the request is about architecture, dependencies, file ownership, or codebase navigation.
- Use this memory file and the workspace-root `workspacememory.md` mirror for recent activity, hot files, Git-aware status, and GitHub-enriched project context.
- Use the workspace-root `workspace.json` file when an AI agent wants machine-readable repo metadata, file inventory, package details, and Git/Graphify summaries without rescanning the repository.
- Refresh this file with the `Code Janitor: Refresh Workspace Memory` command after significant edits or branch changes.
## Repository Blueprint
- Audience: any AI agent working in this repository can treat this file as the current handoff ledger.
- Graphify report: available at `graphify-out/GRAPH_REPORT.md`
- Graphify graph: available at `graphify-out/graph.json`
- Last activity: 2026-10-03T11:42:52.901Z
## Workspace Focus
- Active file in focus: DroneOS/tests/test_terminal_controller.py
- Hottest files right now: DroneOS/shared/nlp/trajectory_engine.py (1), DroneOS/tests/test_terminal_controller.py (1)
- Suggested starting points: DroneOS/tests/test_terminal_controller.py, DroneOS/shared/nlp/trajectory_engine.py, .github/modernize/java-upgrade/.gitignore, .gitignore, .pytest_cache/.gitignore, .pytest_cache/README.md
## Current Workspace
- Active file: DroneOS/tests/test_terminal_controller.py
- Tracked files in snapshot: 3839
- Top-level areas: venv (1780), AirSim (1428), mobile (141), DroneOS (88), DroneOS1 (85), DroneOS2 (84), DroneOS3 (84), [root] (53)
- Primary file types: .py (1934), [no extension] (219), .hpp (214), .uasset (179), .cpp (126), .png (126), .md (106), .h (98)
- Key files: .github/modernize/java-upgrade/.gitignore, .gitignore, .pytest_cache/.gitignore, .pytest_cache/README.md, AirSim/.gitignore, AirSim/AirLib/.gitignore, AirSim/GazeboDrone/README.md, AirSim/MavLinkCom/MavLinkMoCap/Readme.md
## Package Snapshot
- Package metadata unavailable: package.json was not found.
## Current Stack
- Logged change events: 2
- Change mix: save (2)
- Remembered file snapshots: 2
- Working tree summary: 1 modified
## Tracked Snapshots
- DroneOS/shared/nlp/trajectory_engine.py | 957 lines | 36548 chars | hash f6ed4bf8d563
  Last snapshot: 2026-10-03T11:42:52.901Z
  Preview: """" / Natural-language command parsing and NED/global waypoint generation. / All local geometry is expressed in NED convention: / - x / north is positive forward toward geographic north. / - y / east is positive towar..."
- DroneOS/tests/test_terminal_controller.py | 246 lines | 8805 chars | hash be871eb34242
  Last snapshot: 2026-08-29T12:17:18.957Z
  Preview: "import pytest / from unittest.mock import AsyncMock, MagicMock, patch, call / import asyncio / import math / from DroneOS.core.terminal_controller import TerminalController / from DroneOS.core.interfaces import IFligh..."

## Recent Changes
### 2026-10-03T11:42:52.901Z | saved | DroneOS/shared/nlp/trajectory_engine.py
- Summary: Line 483: replaced 3 lines with 1 line.
- Before: 959 lines | 36,680 chars | hash c8c35cd8563c | preview: """" / Natural-language command parsing and NED/global waypoint generation. / All local geometry is expressed in NED convention: / - x / north is positive forward toward geographic north. / - y / east is positive towar..."
- After: 957 lines | 36,548 chars | hash f6ed4bf8d563 | preview: """" / Natural-language command parsing and NED/global waypoint generation. / All local geometry is expressed in NED convention: / - x / north is positive forward toward geographic north. / - y / east is positive towar..."
- Previous fragment: "current_alt = origin.relative_alt_m if origin.relative_alt_m is not None else 0.0 / default_alt = current_alt if current_alt >= 0.5 else 3.0 / altitude_m = _positive(task.params..."
- Current fragment: "altitude_m = _positive(task.params.get("h", origin.relative_alt_m or 3.0"

### 2026-08-29T12:17:18.957Z | saved | DroneOS/tests/test_terminal_controller.py
- Summary: Saved without a textual diff.
- Before: 246 lines | 8,805 chars | hash be871eb34242 | preview: "import pytest / from unittest.mock import AsyncMock, MagicMock, patch, call / import asyncio / import math / from DroneOS.core.terminal_controller import TerminalController / from DroneOS.core.interfaces import IFligh..."
- After: 246 lines | 8,805 chars | hash be871eb34242 | preview: "import pytest / from unittest.mock import AsyncMock, MagicMock, patch, call / import asyncio / import math / from DroneOS.core.terminal_controller import TerminalController / from DroneOS.core.interfaces import IFligh..."


## Hot Files
- DroneOS/shared/nlp/trajectory_engine.py (1 tracked changes)
- DroneOS/tests/test_terminal_controller.py (1 tracked changes)

## Git Snapshot
- Branch: main
- HEAD: 2026-10-02 64ec01e docs: add architecture, pipeline, and swarm mermaid diagrams to README
- Working tree summary: 1 modified
- M workspacememory.md

## GitHub Snapshot
GitHub Repository: Debanshu2005/DroneSwarm
Description: Multi-drone swarm platform with custom DroneOS, AirSim simulation, formation control, telemetry, and a real-time ground control interface for coordinated autonomous flight.
Visibility: public | Default branch: main
Stars: 1 | Forks: 0 | Open issues: 0

Latest commit on main:
- 64ec01e by Debanshu2005 on 2026-10-02
  docs: add architecture, pipeline, and swarm mermaid diagrams to README

URL: https://github.com/Debanshu2005/DroneSwarm

## Graphify Snapshot
## Overview

- **Total Files**: 1199
- **Total Dependencies**: 1109
- **Communities**: 212

## God Nodes (High Connectivity)

These files are central to the codebase architecture. Changes here affect many other files.

### DroneOS/shared/protocol/messages.py

- **Type**: python
- **Lines**: 256
- **Incoming Dependencies**: 46
- **Outgoing Dependencies**: 0
- **Total Connections**: 46

### DroneOS1/shared/protocol/messages.py

- **Type**: python
- **Lines**: 256
- **Incoming Dependencies**: 34
- **Outgoing Dependencies**: 0
- **Total Connections**: 34

### DroneOS2/shared/protocol/messages.py

- **Type**: python
- **Lines**: 256
- **Incoming Dependencies**: 33
- **Outgoing Dependencies**: 0
- **Total Connections**: 33

### DroneOS3/shared/protocol/messages.py

- **Type**: python
- **Lines**: 256
- **Incoming Dependencies**: 33
- **Outgoing Dependencies**: 0
- **Total Connections**: 33

### AirSim/PythonClient/multirotor/setup_path.py

- **Type**: python
- **Lines**: 53
- **Incoming Dependencies**: 31
- **Outgoing Dependencies**: 0
- **Total Connections**: 31

### DroneOS2/main.py

- **Type**: python
- **Lines**: 606
- **Incoming Dependencies**: 3
- **Outgoing Dependencies**: 26
-...

## Project Planner
- Project planner is not configured yet. Enable it in the chat panel to generate a time-based todo list and progress rescue briefs.

## Agent Notes
- If a future task asks what changed recently, start with `Recent Changes`, `Tracked Snapshots`, `Hot Files`, and `Git Snapshot`.
- If a future task asks how the project is organized, combine this file with `graphify-out/GRAPH_REPORT.md`.
- If a future task needs repository-level context, use `Package Snapshot`, the GitHub snapshot, and the Graphify snapshot before rescanning broad parts of the repo.
