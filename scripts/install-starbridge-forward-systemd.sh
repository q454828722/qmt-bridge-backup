#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_dir="${HOME}/.config/systemd/user"
service_file="${service_dir}/starbridge-quant-forward.service"
python_bin="${PYTHON_BIN:-$(command -v python3)}"

mkdir -p "${service_dir}"

cat > "${service_file}" <<EOF
[Unit]
Description=Forward WSL localhost:18888 to Windows StarBridge Quant
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${project_root}
ExecStart=${python_bin} -u ${project_root}/scripts/wsl-forward-18888.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now starbridge-quant-forward.service
systemctl --user --no-pager status starbridge-quant-forward.service
