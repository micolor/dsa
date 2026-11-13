#!/usr/bin/env bash
# One-click Docker build (and optional start) for the all-in-one service.
#
# Usage:
#   ./scripts/docker-oneclick.sh            # build image only
#   ./scripts/docker-oneclick.sh --up       # build, then start Web UI + scheduler
#   ./scripts/docker-oneclick.sh --no-cache --up
#
# The compose file lives at docker/docker-compose.yml; the combined `app`
# service runs `main.py --serve` (Web UI + runtime scheduler).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker/docker-compose.yml"
ENV_FILE="$ROOT_DIR/.env"

UP=0
NO_CACHE=0
for arg in "$@"; do
  case "$arg" in
    --up) UP=1 ;;
    --no-cache) NO_CACHE=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker，请先安装 Docker Desktop 或 Docker Engine。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker 服务未运行，请先启动 Docker。" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  echo "未找到 .env，已从 .env.example 生成：$ENV_FILE"
  echo "请先按需修改模型 Key、股票列表和通知渠道，再重新执行本脚本。"
fi

# Bind-mount targets must exist on the host before compose starts; otherwise
# Docker creates them as root and the non-root container user may fail to write.
mkdir -p \
  "$ROOT_DIR/data" \
  "$ROOT_DIR/logs" \
  "$ROOT_DIR/reports" \
  "$ROOT_DIR/strategies" \
  "$ROOT_DIR/longbridge_tokens"

build_args=(build app)
if [ "$NO_CACHE" -eq 1 ]; then
  build_args=(build --no-cache app)
fi

echo "==> 构建 Docker 镜像（docker compose -f docker/docker-compose.yml ${build_args[*]}）"
docker compose -f "$COMPOSE_FILE" "${build_args[@]}"

if [ "$UP" -eq 1 ]; then
  echo "==> 启动一体化服务（Web UI + 调度器）"
  docker compose -f "$COMPOSE_FILE" up -d app
  port="$(grep -E '^API_PORT=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  port="${port:-8000}"
  echo "==> 已启动。访问 http://127.0.0.1:${port}"
  echo "    查看日志: docker compose -f docker/docker-compose.yml logs -f app"
else
  echo "==> 镜像构建完成。启动命令："
  echo "    docker compose -f docker/docker-compose.yml up -d app"
fi
