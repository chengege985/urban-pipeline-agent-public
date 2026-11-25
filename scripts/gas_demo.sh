#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
workspace="$(dirname -- "$here")"
profile=
default_runtime=/nix/wpn-gas-mainline-runtime
if [[ -f "$workspace/gas-demo-release.json" ]]; then
  profile="$(python3 -c 'from pathlib import Path; import json,sys; print(json.loads(Path(sys.argv[1]).read_text()).get("demo_profile", ""))' "$workspace/gas-demo-release.json")"
  if [[ $profile == gas-pilot-workflow ]]; then default_runtime=/nix/wpn-gas-release-04-runtime; fi
fi
runtime="${WPN_GAS_DEMO_RUNTIME:-$default_runtime}"
action="${1:-start}"
if (( $# > 0 )); then shift; fi
case "$action" in
  start|status|stop|restart|doctor|credentials|operator-credentials) ;;
  *) printf '用法：bash scripts/gas_demo.sh {start|status|stop|restart|doctor|credentials|operator-credentials} [--json]\n' >&2; exit 2 ;;
esac
format=human
if [[ ${1:-} == --json ]]; then format=json; shift; fi
if (( $# )); then printf '无法识别的演示参数。\n' >&2; exit 2; fi

export PYTHONDONTWRITEBYTECODE=1
preferred_python=/tmp/gas-fix21-venv/bin/python
if [[ -f "$workspace/gas-demo-release.json" && -x /workdata/WaterPipNet/.venv-agent/bin/python ]]; then
  preferred_python=/workdata/WaterPipNet/.venv-agent/bin/python
fi
platform_python="${WPN_GAS_DEMO_PYTHON:-$preferred_python}"
if [[ ! -v WPN_GAS_DEMO_PYTHON && ! -x "$platform_python" ]]; then
  platform_python=/workdata/WaterPipNet/.venv-agent/bin/python
fi
run() {
  local repo="$workspace" command="$1"
  local -a flags=()
  local workflow=0 model_wait=480
  if [[ $profile == gas-m0-workflow || $profile == gas-pilot-workflow ]]; then workflow=1; fi
  if [[ $profile == gas-pilot-workflow ]]; then model_wait=900; fi
  if [[ ${WPN_GAS_DISPOSITION_DEMO:-$workflow} == 1 ]]; then
    flags+=(--disposition-demo)
  fi
  if [[ ${WPN_GAS_CUV_COLLABORATION_DEMO:-$workflow} == 1 ]]; then
    flags+=(--cuv-collaboration-demo)
  fi
  if [[ $command == operator-credentials ]]; then
    command=credentials
    flags+=(--credential-role operator)
  fi
  if [[ -f "$workspace/gas-demo-release.json" ]]; then
    local -a options=()
    case "$1" in stop|status) options+=(--management) ;; esac
    repo="$(python3 "$here/prepare_gas_demo.py" "$workspace" "${options[@]}")" || return
  fi
  if [[ $profile == gas-pilot-workflow && ( $command == start || $command == doctor ) ]]; then
    local pilot_store
    pilot_store="$(PYTHONPATH="$repo/engineering" "$platform_python" -B "$repo/scripts/prepare_gas_pilot_demo.py" "$workspace" "$runtime")" || return
    export GAS_PILOT_STORE="$pilot_store"
    export GAS_PILOT_PREVIEW_DATASET=syn-gas-pilot-history-01
  fi
  python3 "$repo/scripts/gas_dev.py" "$command" --runtime "$runtime" --python "$platform_python" "${flags[@]}" \
    --model "${WPN_GAS_DEMO_MODEL:-auto}" \
    --model-dir "${WPN_GAS_DEMO_MODEL_DIR:-/workdata/VLM/models/Qwen3.8-27B}" \
    --model-start-timeout "${WPN_GAS_DEMO_MODEL_WAIT:-$model_wait}" \
    --ttl-minutes "${WPN_GAS_DEMO_TTL_MINUTES:-25}"
}
if [[ "$action" == restart ]]; then
  run stop >/dev/null
  action=start
fi
if [[ "$format" == json || "$action" == doctor || "$action" == credentials || "$action" == operator-credentials ]]; then
  run "$action"
  exit
fi
result="$(run "$action")"
GAS_DEMO_ACTION="$action" python3 -c '
import datetime, json, os, sys
v=json.load(sys.stdin)
act=os.environ["GAS_DEMO_ACTION"]
if act=="stop":
    print("本次燃气演示已停止。" if v["stopped"] else "演示仍有待退出的进程。")
    sys.exit(0 if v["stopped"] else 1)
if act=="start":
    print("已复用运行中的演示。" if v.get("already_running") else "燃气演示已启动。")
print(f"前端 {v['"'"'frontend'"'"']}  FastAPI {v['"'"'fastapi'"'"']}  Agent {v['"'"'agent_status'"'"']}  Qwen {v['"'"'qwen_status'"'"']}")
if v.get("source"):
    print("源码提交 " + v["source"])
if v["ppu_memory"]:
    print("PPU 占用："+"，".join(f"{m['"'"'used_mib'"'"']}/{m['"'"'total_mib'"'"']} MiB" for m in v["ppu_memory"]))
else:
    print("PPU 占用：当前无采样")
if v.get("startup_error"):
    print("启动状态：" + v["startup_error"])
if v.get("stale_owned_pids"):
    print("检测到本任务残留进程，下次启动或停止时将清理。")
if v.get("expires_at"):
    print("自动停止时间：" + datetime.datetime.fromtimestamp(v["expires_at"]).astimezone().strftime("%H:%M:%S %Z"))
if v["frontend"]=="running":
    print(v["url"])
if act=="start":
    print("浏览器要求登录时，运行 IDE 任务「查看燃气演示临时登录」。")
    if v.get("disposition_demo"):
        print("合成处置已开启。操作身份请在交互终端运行 bash scripts/gas_demo.sh operator-credentials。")
    if v.get("cuv_collaboration_demo"):
        print("CUV 合成协同已开启。操作身份请在交互终端运行 bash scripts/gas_demo.sh operator-credentials。")
    if v["qwen_status"]=="starting":
        print("Qwen 正在后台加载，页面会更新真实状态，当前可使用只读规则查询。")
' <<< "$result"
