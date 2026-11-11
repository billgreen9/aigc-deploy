# aigc-deploy

本机一键批量构建 & 部署多项目的 Docker 工具。主逻辑用 Python 实现，配置文件采用 JSON 格式，通过 `run.sh` 统一入口调用。

自动从 GitHub 拉取仓库 → 构建镜像 → 生成 docker-compose.yml → 编排启动。

## 前置条件

- macOS（已在 macOS 上验证）
- Docker Desktop（含 docker compose 插件）
- Python 3.10+（本机 Python 3.14 已验证）
- Git 2.x

## 项目结构

```
aigc-deploy/
├── run.sh              # Shell 薄包装，定位 python3 并转发
├── deploy.py           # Python 主逻辑（image/start/stop/rebuild）
├── apps.json           # 应用清单（你编辑这个）
├── .gitignore
└── README.md
```

首次运行后自动生成：

```
sources/                # 从 GitHub clone 下来的源码目录
docker-compose.yml      # 由 deploy.py start 自动生成，勿手动修改
```

## apps.json 格式

完整示例（已填入 volc-speech）：

```json
{
  "branch": "main",
  "apps": [
    {
      "name": "volc-speech",
      "git_url": "https://github.com/billgreen9/volc-speech.git",
      "image": "volc-speech",
      "ports": ["8080:8080"],
      "env": {
        "SPEECH_ADDR": ":8080"
      },
      "volumes": [
        {"host": ".env", "container": "/app/.env", "mode": "ro"}
      ]
    }
  ]
}
```

### 顶层字段

| 字段 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `branch` | ✗ | `"main"` | 所有仓库默认拉取的分支 |
| `apps` | ✓ | — | 应用列表 |

### 每个 app 的字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✗ | 自动从 git_url 推断 | 服务名（也是容器名），建议显式写 |
| `git_url` | ✓ | — | HTTPS 或 SSH 地址 |
| `image` | ✓ | — | `docker build -t` 的本地镜像名，**不推远程** |
| `ports` | ✗ | `[]` | 端口映射数组，每项是 `"host:container"` 字符串 |
| `env` | ✗ | `{}` | 环境变量，支持 dict（`KEY: VALUE`）或 list（`"KEY=VALUE"`） |
| `volumes` | ✗ | `[]` | 卷映射，字符串形式 `"host:container[:mode]"` 或 dict 形式 |
| `restart` | ✗ | `"unless-stopped"` | compose 的 restart 策略 |

### 添加新应用

在 `apps` 数组里追加一项即可：

```json
{
  "name": "my-web",
  "git_url": "https://github.com/myorg/my-web.git",
  "image": "my-web",
  "ports": ["8081:80"],
  "env": {"DEBUG": "true"}
}
```

**约定**：每个项目的 **Dockerfile 必须在仓库根目录**，构建上下文就是该目录。

## 命令速查

```bash
./run.sh image     # 克隆所有仓库 + 逐个构建 Docker 镜像（仅本地）
./run.sh start     # 根据 apps.json 生成 docker-compose.yml 并启动所有服务
./run.sh stop      # 停止所有 compose 服务
./run.sh rebuild   # image + start 一条龙
./run.sh --help    # 查看帮助

# 也可以直接用 python:
python3 deploy.py image
python3 deploy.py rebuild
```

## 快速开始

### 第 1 步：编辑 apps.json

```bash
vim apps.json
```

### 第 2 步：一键构建 + 启动

```bash
./run.sh rebuild
```

或者分两步：

```bash
./run.sh image    # 只打镜像
./run.sh start    # 再启动
```

### 第 3 步：查看服务状态

```bash
docker compose -f docker-compose.yml ps
```

## 工作原理

### image 模式

1. 读取 `apps.json`
2. 对每个 app：`rm -rf sources/<name>` → `git clone --depth 1` → 检查根目录 Dockerfile → `docker build -t <image> sources/<name>`
3. 某个项目失败不会中断其他项目，最后汇总报告

### start 模式

1. 读取 `apps.json`
2. 纯 Python 手写生成 `docker-compose.yml`（**无需 PyYAML**，无第三方依赖）
3. `docker compose up -d` 后台启动全部服务

生成的 compose 示例（对应上面 apps.json）：

```yaml
# 本文件由 deploy.py 自动生成，请勿手动修改。修改 apps.json 后重新生成。
services:
  volc-speech:
    image: volc-speech
    container_name: volc-speech
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - SPEECH_ADDR=":8080"
    volumes:
      - ".env:/app/.env:ro"
```

## 架构选择

| 组件 | 选择 | 原因 |
|------|------|------|
| 配置格式 | **JSON** | 结构化强、注释清晰、人人能看懂；避免 `.conf` 管道符拼接的心智负担 |
| 主逻辑 | **Python 标准库** | macOS 自带，零第三方依赖；比 shell 处理 JSON 更自然 |
| compose 生成 | **手写 YAML** | 避免 PyYAML 依赖；实际输出经 `docker compose config` 校验通过 |
| 入口脚本 | **run.sh 薄包装** | 把 python 路径解析留给 shell，跨环境更稳 |

## 注意事项

- **镜像只打本地**：脚本不会 push 到任何远程 registry，镜像名即为 compose 中的 `image:` 值
- **Dockerfile 位置**：每个项目的 `Dockerfile` 必须在仓库根目录
- **分支统一**：所有仓库共用 `apps.json` 顶层的 `branch` 字段，默认 `main`
- **SSH vs HTTPS**：写 SSH 地址需要本机已配置好 GitHub SSH key；HTTPS 则是匿名 clone
- **端口冲突**：宿主机端口不能与已有服务重复，否则 compose 启动失败
- **源码缓存**：每次 `image` 会删除旧目录再 clone 最新，确保拿到最新 main 分支
