# Windows + WSL Operations

This project is intended to run the QMT Bridge server on Windows and consume it from WSL for research, factor mining, and trading scripts.

## Layout

- `scripts/start-qmt-bridge.ps1` starts the Windows `qmt-server.exe`, refreshes `qmt-bridge.pid`, ignores `wslrelay.exe`, and rotates `logs/windows-startup-server.*.log`.
- `scripts/install-startup-task.ps1` installs the Windows logon scheduled task `QMT Bridge Server`.
- `scripts/check-qmt-bridge-windows.ps1` checks the Windows scheduled task, the real `qmt-server.exe` listener, and Windows localhost API health.
- `scripts/wsl-forward-18888.py` forwards WSL `127.0.0.1:18888` to the Windows QMT Bridge service.
- `scripts/install-wsl-forward-systemd.sh` installs the WSL user service `qmt-bridge-forward.service`.
- `scripts/check-qmt-bridge-health.sh` checks the full path from WSL: Windows server, WSL forward, and API health.
- `qmt_client.py` is a small environment-driven client factory for strategy and research scripts.

## Recommended Checks

From WSL:

```bash
scripts/check-qmt-bridge-health.sh
```

From Windows PowerShell:

```powershell
.\scripts\check-qmt-bridge-windows.ps1
```

Expected healthy state:

- Windows has a real `qmt-server.exe` listener on `0.0.0.0:18888`.
- Windows may also show `wslrelay.exe` on `127.0.0.1:18888`; that is normal when WSL forwarding is active.
- WSL has `qmt-bridge-forward.service` enabled and active.
- `http://127.0.0.1:18888/api/meta/health` returns `{"status":"ok"}` from WSL.

## Repair Commands

Install or repair Windows logon startup:

```powershell
.\scripts\install-startup-task.ps1
```

Start Windows server manually:

```powershell
.\scripts\start-qmt-bridge.ps1
```

Install or repair WSL forwarding:

```bash
scripts/install-wsl-forward-systemd.sh
```

## Client Environment

For WSL research scripts, prefer:

```bash
export QMT_BRIDGE_CLIENT_HOST=127.0.0.1
export QMT_BRIDGE_PORT=18888
```

Only set `QMT_BRIDGE_API_KEY` for authenticated trading, fund, credit, bank, or SMT endpoints. Do not print or commit it.

```python
from qmt_client import make_qmt_client

client = make_qmt_client()
print(client.health_check())
```

## Git Hygiene

Runtime files are intentionally ignored:

- `.env`
- `.venv/`
- `data/`
- `logs/`
- `*.pid`
- `__pycache__/`

When committing Windows/WSL operations work, stage only the scripts and docs that changed intentionally. The repository may show large line-ending-only modifications on Windows-mounted files; do not include those in operational commits unless you are doing a dedicated normalization pass.
