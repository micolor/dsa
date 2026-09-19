#!/bin/sh
# 容器健康检查。
#
# 只有「本次运行会起 HTTP 服务」时才要求端点真的能应答：
#   - 定时任务容器（`python main.py --schedule`）没有 HTTP 端点，健康状态只代表进程还活着；
#   - 服务容器（`--serve` / `--serve-only` / `--webui` / `--webui-only`，或 WEBUI_ENABLED=true）
#     必须真的探测到 API。旧实现末尾那句 `python -c "import sys; sys.exit(0)"` 是无条件成功的，
#     于是 API 挂死或根本没起来时容器照样报 healthy；而且端口写死 8000，与文档里
#     API_PORT=8888 / 8080 的用法不符，那些部署两条 curl 都打空、直接落到「无条件健康」。
#
# 探测端口按实际绑定值取：命令行里的 --port 优先，其次环境变量 API_PORT，最后 8000。
set -eu

# 容器里 PID 1 就是被 entrypoint exec 出来的应用进程（docker/entrypoint.sh 末尾是 exec "$@"），
# 因此 /proc/1/cmdline 就是本次实际启动命令。用 python 读是因为参数之间是 NUL 分隔，
# 直接按行读会拼成一整串。
app_args="$(python - <<'PY'
try:
    with open('/proc/1/cmdline', 'rb') as handle:
        raw = handle.read()
except OSError:
    raw = b''
args = [item for item in raw.split(b'\0') if item]
print(' '.join(item.decode('utf-8', 'replace') for item in args))
PY
)"

serve_mode=0
case " ${app_args} " in
    *" --serve "*|*" --serve-only "*|*" --webui "*|*" --webui-only "*)
        serve_mode=1
        ;;
esac
if [ "${serve_mode}" -eq 0 ] && [ "${WEBUI_ENABLED:-false}" = "true" ]; then
    serve_mode=1
fi

if [ "${serve_mode}" -eq 0 ]; then
    echo "非服务模式，无 HTTP 端点可探测: ${app_args}"
    exit 0
fi

port=""
previous=""
for arg in ${app_args}; do
    case "${arg}" in
        --port=*)
            port="${arg#--port=}"
            ;;
        *)
            if [ "${previous}" = "--port" ]; then
                port="${arg}"
            fi
            ;;
    esac
    previous="${arg}"
done
if [ -z "${port}" ]; then
    port="${API_PORT:-8000}"
fi

for path in /api/health /health; do
    if curl -fsS --max-time 5 "http://127.0.0.1:${port}${path}" >/dev/null 2>&1; then
        echo "服务模式健康检查通过: http://127.0.0.1:${port}${path}"
        exit 0
    fi
done

echo "服务模式健康检查失败：http://127.0.0.1:${port}/api/health 与 /health 均无应答（命令: ${app_args}）" >&2
exit 1
