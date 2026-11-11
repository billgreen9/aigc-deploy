#!/usr/bin/env python3
"""
aigc-deploy —— 本机一键构建 & 启动多服务的主逻辑

子命令:
    image     从 apps.json 读取仓库列表，逐个 clone + 打 Docker 镜像
    start     根据 apps.json 生成 docker-compose.yml 并启动所有服务
    stop      停止所有 compose 服务
    rebuild   先 image 再 start

依赖: Python >= 3.10, Docker + docker compose 插件, git
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ============================================================================
# 基础路径
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
APPS_JSON = SCRIPT_DIR / "apps.json"
SOURCES_DIR = SCRIPT_DIR / "sources"
COMPOSE_FILE = SCRIPT_DIR / "tmp" / "docker-compose.yml"


# ============================================================================
# 彩色输出
# ============================================================================
class C:
    RESET = "\033[0m"
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"


def info(msg: str) -> None:
    print(f"{C.GREEN}[INFO]{C.RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"{C.YELLOW}[WARN]{C.RESET}  {msg}")


def error(msg: str) -> None:
    print(f"{C.RED}[ERROR]{C.RESET} {msg}", file=sys.stderr)


def step(msg: str) -> None:
    print(f"\n{C.CYAN}==== {msg} ===={C.RESET}")


# ============================================================================
# 通用工具
# ============================================================================
def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    """跑一个命令，实时输出，返回 CompletedProcess。"""
    info(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, **kw)


def require_tools() -> None:
    for t in ("docker", "git", "python3"):
        if shutil.which(t) is None:
            error(f"未检测到 {t}")
            sys.exit(1)
    # docker compose 插件
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if r.returncode != 0:
        error("未检测到 docker compose 插件")
        sys.exit(1)


def load_apps() -> dict[str, Any]:
    if not APPS_JSON.is_file():
        error(f"未找到配置文件: {APPS_JSON}")
        warn("请先创建 apps.json，格式见脚本头部注释")
        sys.exit(1)
    with APPS_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("apps"):
        error("apps.json 中没有 apps 列表")
        sys.exit(1)
    return data


def repo_dir_name(git_url: str) -> str:
    return Path(git_url.rstrip("/")).stem  # 自动去掉 .git


# ============================================================================
# image 子命令
# ============================================================================
def cmd_image() -> None:
    step("检查前置条件")
    require_tools()
    data = load_apps()
    branch = data.get("branch", "main")
    apps = data["apps"]

    SOURCES_DIR.mkdir(exist_ok=True)

    step(f"开始克隆并构建镜像 (共 {len(apps)} 个)")
    failed: list[str] = []

    for i, app in enumerate(apps, 1):
        git_url = app["git_url"]
        image = app["image"]
        name = app.get("name") or repo_dir_name(git_url)
        local_dir = SOURCES_DIR / name

        info(f"[{i}/{len(apps)}] {name} -> {image}")

        # 1) clone
        if local_dir.exists():
            warn(f"已存在 {local_dir}，删除后重新克隆")
            shutil.rmtree(local_dir)

        if run(
            ["git", "clone", "--branch", branch, "--depth", "1", git_url, str(local_dir)]
        ).returncode != 0:
            error(f"克隆失败: {git_url}")
            failed.append(f"{name}(clone)")
            continue

        # 2) 检查 Dockerfile
        dockerfile = local_dir / "Dockerfile"
        if not dockerfile.is_file():
            error(f"项目 {name} 根目录下未找到 Dockerfile")
            failed.append(f"{name}(no-dockerfile)")
            continue

        # 3) 构建
        info(f"构建镜像: docker build -t {image} {local_dir}")
        r = subprocess.run(
            ["docker", "build", "-t", image, str(local_dir)],
        )
        if r.returncode == 0:
            info(f"✓ 镜像 {image} 构建成功")
        else:
            error(f"✗ 镜像 {image} 构建失败")
            failed.append(f"{name}(build)")

    step("image 模式汇总")
    if not failed:
        info(f"全部 {len(apps)} 个镜像构建成功！")
    else:
        warn(f"以下 {len(failed)} 个项目构建异常：")
        for f in failed:
            print(f"    - {f}")
        sys.exit(1)


# ============================================================================
# compose 生成 (手写 YAML, 无第三方依赖)
# ============================================================================
def _yaml_scalar(v: Any) -> str:
    """把 Python 值转成 YAML 标量。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # 需要加引号的情况
    special = [":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`", "'", '"']
    if any(c in s for c in special) or s.startswith((" ", '"', "'")) or s.endswith((" ",)):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _indent(text: str, n: int) -> str:
    pad = "  " * n
    return "\n".join(pad + line if line else pad for line in text.splitlines())


def _resolve_host_path(p: str) -> str:
    """相对路径按项目根目录解析，避免 compose 文件位于 public/ 时被误导。"""
    if not p or p.startswith(("/", "~")):
        return p
    return str(SCRIPT_DIR / p)


def _render_service(app: dict[str, Any]) -> str:
    """把一个 app dict 渲染成一个 service 的 YAML 段落 (不带缩进)。"""
    name = app.get("name") or repo_dir_name(app["git_url"])
    lines = [f"{name}:"]

    # 基础字段
    lines.append(f"  image: {app['image']}")
    lines.append(f"  container_name: {name}")

    restart = app.get("restart", "unless-stopped")
    lines.append(f"  restart: {restart}")

    # 端口
    ports = app.get("ports", [])
    if ports:
        lines.append("  ports:")
        for p in ports:
            lines.append(f"    - {_yaml_scalar(p)}")

    # 环境变量
    env = app.get("env")
    if env:
        lines.append("  environment:")
        if isinstance(env, dict):
            for k, v in env.items():
                lines.append(f"    - {k}={_yaml_scalar(v)}")
        else:  # list[str]
            for e in env:
                lines.append(f"    - {_yaml_scalar(e)}")

    # 环境变量文件（敏感信息放这里，不入库）
    env_files = app.get("env_file", [])
    if isinstance(env_files, str):
        env_files = [env_files]
    if env_files:
        lines.append("  env_file:")
        for f in env_files:
            lines.append(f"    - {_yaml_scalar(_resolve_host_path(f))}")

    # volumes
    volumes = app.get("volumes", [])
    if volumes:
        lines.append("  volumes:")
        for v in volumes:
            if isinstance(v, str):
                parts = v.split(":")
                parts[0] = _resolve_host_path(parts[0])
                lines.append(f"    - {_yaml_scalar(':'.join(parts))}")
            elif isinstance(v, dict):
                host = _resolve_host_path(v.get("host", ""))
                container = v.get("container", "")
                mode = v.get("mode", "")
                s = f"{host}:{container}"
                if mode:
                    s += f":{mode}"
                lines.append(f"    - {_yaml_scalar(s)}")

    return "\n".join(lines)


def generate_compose(data: dict[str, Any]) -> Path:
    step("生成 docker-compose.yml")
    services = []
    for app in data["apps"]:
        services.append(_render_service(app))

    header = "# 本文件由 deploy.py 自动生成，请勿手动修改。修改 apps.json 后重新生成。"
    body = header + "\nservices:\n" + _indent("\n".join(services), 1) + "\n"

    COMPOSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSE_FILE.write_text(body, encoding="utf-8")
    info(f"已生成 {COMPOSE_FILE}")
    return COMPOSE_FILE


# ============================================================================
# start / stop / rebuild
# ============================================================================
def validate_env_files(data: dict[str, Any]) -> None:
    """启动前校验敏感配置文件是否已在本机准备好（这些文件不入库）。"""
    missing: list[str] = []
    for app in data["apps"]:
        files = app.get("env_file", [])
        if isinstance(files, str):
            files = [files]
        for f in files:
            p = Path(_resolve_host_path(f))
            if not p.is_file():
                missing.append(str(p))

    if missing:
        step("敏感配置检查")
        error("以下配置文件缺失，无法启动：")
        for m in missing:
            print(f"    - {m}")
        warn("这些文件含密钥，不会提交到 git；请在本机准备：cp .env.example .env 后填入真实值")
        sys.exit(1)


def cmd_start() -> None:
    require_tools()
    data = load_apps()
    validate_env_files(data)
    generate_compose(data)

    step("启动所有服务 (docker compose up -d)")
    run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"])

    step("服务状态")
    run(["docker", "compose", "-f", str(COMPOSE_FILE), "ps"])


def cmd_stop() -> None:
    require_tools()
    if not COMPOSE_FILE.is_file():
        warn(f"未找到 {COMPOSE_FILE}，跳过")
        return
    step("停止所有服务 (docker compose down)")
    run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"])


def cmd_rebuild() -> None:
    cmd_image()
    cmd_start()


# ============================================================================
# 入口
# ============================================================================
USAGE = """用法: python3 deploy.py <command>

命令:
  image     从 GitHub 克隆 apps.json 中列出的仓库，打 Docker 镜像到本机
  start     根据 apps.json 生成 docker-compose.yml 并启动所有服务
  stop      停止所有 compose 服务
  rebuild   先 image 再 start

apps.json 格式见 apps.json 注释示例。"""


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else ""
    handlers = {
        "image": cmd_image,
        "start": cmd_start,
        "stop": cmd_stop,
        "rebuild": cmd_rebuild,
    }
    if cmd in ("", "-h", "--help", "help"):
        print(USAGE)
        return
    if cmd not in handlers:
        error(f"未知命令: {cmd}")
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    handlers[cmd]()


if __name__ == "__main__":
    main(sys.argv[1:])
