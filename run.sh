#!/usr/bin/env bash
# =============================================================================
# aigc-deploy —— 本机一键构建 & 启动多服务脚本 (薄包装)
# 真实逻辑在 deploy.py，本脚本只负责定位 python3 并传递参数。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 优先用系统 python3，fallback 到常见路径
PY="${PYTHON:-$(command -v python3 || command -v python)}"
if [[ -z "$PY" ]]; then
    echo "[ERROR] 未找到 python3，请先安装 Python 3.10+" >&2
    exit 1
fi

exec "$PY" "${SCRIPT_DIR}/deploy.py" "$@"
