# Windows + WSL Operations

This project is intended to run the StarBridge Quant server on Windows and consume it from WSL for research, factor mining, and trading scripts.

## Layout

- `scripts/start-starbridge-quant.ps1` starts the Windows `starbridge-server.exe`, refreshes `starbridge-quant.pid`, ignores `wslrelay.exe`, and rotates `logs/windows-startup-server.*.log`.
- `scripts/install-starbridge-startup-task.ps1` installs the Windows logon scheduled task `StarBridge Quant Server`.
- `scripts/check-starbridge-quant-windows.ps1` checks the Windows scheduled task, the real `starbridge-server.exe` listener, and Windows localhost API health.
- `scripts/wsl-forward-18888.py` forwards WSL `127.0.0.1:18888` to the Windows StarBridge Quant service.
- `scripts/install-starbridge-forward-systemd.sh` installs the WSL user service `starbridge-quant-forward.service`.
- `scripts/check-starbridge-quant-health.sh` checks the full path from WSL: Windows server, WSL forward, and API health.
- `starbridge_quant.client_factory` is the preferred environment-driven client factory for strategy and research scripts. The repo-root `starbridge_client.py` remains only as a legacy compatibility wrapper.

## Recommended Checks

From WSL:

```bash
scripts/check-starbridge-quant-health.sh
```

From Windows PowerShell:

```powershell
.\scripts\check-starbridge-quant-windows.ps1
```

Expected healthy state:

- Windows has a real `starbridge-server.exe` listener on `0.0.0.0:18888`.
- Windows may also show `wslrelay.exe` on `127.0.0.1:18888`; that is normal when WSL forwarding is active.
- WSL has `starbridge-quant-forward.service` enabled and active.
- `http://127.0.0.1:18888/api/meta/health` returns `{"status":"ok"}` from WSL.

## Repair Commands

Install or repair Windows logon startup:

```powershell
.\scripts\install-starbridge-startup-task.ps1
```

Start Windows server manually:

```powershell
.\scripts\start-starbridge-quant.ps1
```

Install or repair WSL forwarding:

```bash
scripts/install-starbridge-forward-systemd.sh
```

## Client Environment

For WSL research scripts, prefer:

```bash
export STARBRIDGE_CLIENT_HOST=127.0.0.1
export STARBRIDGE_PORT=18888
```

Only set `STARBRIDGE_API_KEY` for authenticated trading, fund, credit, bank, or SMT endpoints. Do not print or commit it.

```python
from starbridge_quant.client_factory import make_starbridge_client

client = make_starbridge_client()
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
