#!/usr/bin/env bash
# contract-redact 一键安装：建 .venv → 装依赖 → 可编辑安装 →软链技能 → 自检。
#
# 用法：
#   ./install.sh                 默认安装
#   ./install.sh --list          只打印将要做什么，不动手
#   ./install.sh --target DIR    技能软链到指定目录（可重复；目录不存在会创建）
#   ./install.sh --no-skill      不链技能，只装命令行
#   ./install.sh --python PATH   指定 Python 解释器（默认自动找 >=3.10 的）
#
# 安装完成后：
#   .venv/bin/contract-redact --help
#   source .venv/bin/activate && contract-redact --help
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
VENV_PY="${VENV_DIR}/bin/python"
CLI="${VENV_DIR}/bin/contract-redact"
SKILL_NAME="contract-redact"
SKILL_SRC="${REPO_DIR}/skills/${SKILL_NAME}"
DEFAULT_SKILL_DIRS=("${HOME}/.hermes/skills" "${HOME}/.claude/skills")

DRY_RUN=0
NO_SKILL=0
CUSTOM_TARGETS=()
PYTHON_OVERRIDE=""
SKILL_TARGETS=()
MISSING_TARGETS=()
CLEANUP_DIR=""

usage() {
  cat <<'EOF'
contract-redact 一键安装

用法：
  ./install.sh                  建虚拟环境、装依赖、装命令行、软链技能、自检
  ./install.sh --list           只打印将要做什么，不动手（dry-run）
  ./install.sh --target DIR     技能软链到 DIR（可重复；目录不存在会创建）
  ./install.sh --no-skill       跳过技能软链，只装命令行
  ./install.sh --python PATH    指定 Python 解释器（默认自动找 >=3.10 的）
  ./install.sh -h, --help       显示本帮助

默认会把 skills/contract-redact 软链到下面这些「已经存在」的目录：
  ~/.hermes/skills
  ~/.claude/skills

装完会自检：contract-redact --help、一次真实脱敏/自检/还原，以及 tests/check.py。
EOF
}

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf '\n错误：%s\n' "$*" >&2; exit 1; }

cleanup() {
  if [ -n "${CLEANUP_DIR}" ] && [ -d "${CLEANUP_DIR}" ]; then
    rm -rf "${CLEANUP_DIR}"
  fi
}
trap cleanup EXIT

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --list)
        DRY_RUN=1
        shift
        ;;
      --no-skill)
        NO_SKILL=1
        shift
        ;;
      --python)
        [ "$#" -ge 2 ] || die "--python 后面要跟一个解释器路径"
        PYTHON_OVERRIDE="$2"
        shift 2
        ;;
      --python=*)
        PYTHON_OVERRIDE="${1#*=}"
        shift
        ;;
      --target)
        [ "$#" -ge 2 ] || die "--target 后面要跟一个目录"
        CUSTOM_TARGETS+=("$2")
        shift 2
        ;;
      --target=*)
        CUSTOM_TARGETS+=("${1#*=}")
        shift
        ;;
      *)
        printf '错误：不认识的参数 %s\n\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

expand_path() {
  case "$1" in
    "~") printf '%s' "${HOME}" ;;
    "~/"*) printf '%s/%s' "${HOME}" "${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

resolve_python() {
  local candidate="$1"
  local path=""
  # 只接受普通路径或命令名（字母数字和 _ . / -），拒绝空格与 shell 元字符。
  case "${candidate}" in
    ""|*[!A-Za-z0-9_./-]*) return 1 ;;
  esac
  if [ -x "${candidate}" ]; then
    path="${candidate}"
  else
    path="$(command -v "${candidate}" 2>/dev/null || true)"
  fi
  [ -n "${path}" ] || return 1
  if "${path}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    printf '%s' "${path}"
    return 0
  fi
  return 1
}

find_python() {
  local candidate
  if [ -n "${PYTHON_OVERRIDE}" ]; then
    resolve_python "${PYTHON_OVERRIDE}"
    return $?
  fi
  for candidate in "${CONTRACT_REDACT_PYTHON:-}" python3 python3.13 python3.12 python3.11 python3.10; do
    [ -n "${candidate}" ] || continue
    resolve_python "${candidate}" && return 0
  done
  return 1
}

build_skill_targets() {
  local dir
  SKILL_TARGETS=()
  MISSING_TARGETS=()
  [ "${NO_SKILL}" -eq 1 ] && return 0
  if [ "${#CUSTOM_TARGETS[@]}" -gt 0 ]; then
    for dir in "${CUSTOM_TARGETS[@]}"; do
      SKILL_TARGETS+=("$(expand_path "${dir}")")
    done
    return 0
  fi
  for dir in "${DEFAULT_SKILL_DIRS[@]}"; do
    if [ -d "${dir}" ]; then
      SKILL_TARGETS+=("${dir}")
    else
      MISSING_TARGETS+=("${dir}")
    fi
  done
}

print_plan() {
  say "（--list：只打印计划，不执行任何改动）"
  say ""
  say "仓库：      ${REPO_DIR}"
  say "Python：    ${PY}（$("${PY}" --version 2>&1)）"
  if [ -x "${VENV_PY}" ]; then
    say "虚拟环境：  ${VENV_DIR}（已存在，将复用）"
  else
    say "虚拟环境：  ${VENV_DIR}（将创建）"
  fi
  if [ -x "${CLI}" ]; then
    say "命令行：    ${CLI}（已存在，将重新安装）"
  else
    say "命令行：    ${CLI}（将生成）"
  fi
  say "依赖：      requirements.txt + pip install -e ."
  if [ "${NO_SKILL}" -eq 1 ]; then
    say "技能软链：  跳过（--no-skill）"
  elif [ "${#SKILL_TARGETS[@]}" -gt 0 ]; then
    for dir in "${SKILL_TARGETS[@]}"; do
      say "技能软链：  ${dir}/${SKILL_NAME}"
    done
  else
    say "技能软链：  没有可用的目标目录（用 --target DIR 指定）"
  fi
  if [ "${#MISSING_TARGETS[@]}" -gt 0 ]; then
    for dir in "${MISSING_TARGETS[@]}"; do
      say "            跳过 ${dir}（不存在）"
    done
  fi
  say "自检：      contract-redact --help、一次真实脱敏/自检/还原、tests/check.py"
}

ensure_venv() {
  step "1/4 准备虚拟环境"
  if [ -x "${VENV_PY}" ]; then
    say "已有虚拟环境，直接复用：${VENV_DIR}"
  else
    say "创建虚拟环境：${VENV_DIR}"
    "${PY}" -m venv "${VENV_DIR}"
  fi
  say "虚拟环境 Python：$("${VENV_PY}" --version 2>&1)"
}

install_package() {
  step "2/4 安装依赖和命令行"
  say "安装 requirements.txt（含当前平台的 OCR 依赖，可能需要下载一会儿）"
  "${VENV_PY}" -m pip install -r "${REPO_DIR}/requirements.txt"
  say "可编辑安装：pip install -e ."
  ( cd "${REPO_DIR}" && "${VENV_PY}" -m pip install -e . )
  [ -x "${CLI}" ] || die "没有生成 contract-redact 命令（${CLI}），安装没有成功"
}

link_one() {
  local dir="$1"
  local dest="${dir}/${SKILL_NAME}"
  local current=""

  if [ -L "${dest}" ]; then
    current="$(readlink "${dest}" 2>/dev/null || true)"
    if [ "${current}" = "${SKILL_SRC}" ]; then
      say "已链接：${dest}"
      return 0
    fi
    say "更新软链：${dest} -> ${SKILL_SRC}（原来指向 ${current}）"
    ln -sfn "${SKILL_SRC}" "${dest}"
    return 0
  fi
  if [ -e "${dest}" ]; then
    say "跳过：${dest} 已存在且不是软链（不覆盖你的文件；确认后可手动移走再跑）"
    return 0
  fi
  mkdir -p "${dir}"
  ln -s "${SKILL_SRC}" "${dest}"
  say "已链接：${dest} -> ${SKILL_SRC}"
}

link_skills() {
  step "3/4 链接技能目录"
  if [ "${NO_SKILL}" -eq 1 ]; then
    say "跳过（--no-skill）"
    return 0
  fi
  if [ ! -d "${SKILL_SRC}" ]; then
    say "警告：找不到技能目录 ${SKILL_SRC}，跳过"
    return 0
  fi
  if [ "${#MISSING_TARGETS[@]}" -gt 0 ]; then
    for dir in "${MISSING_TARGETS[@]}"; do
      say "跳过：${dir}（不存在）"
    done
  fi
  if [ "${#SKILL_TARGETS[@]}" -eq 0 ]; then
    say "没有可用的默认目录；要链到别处用 --target DIR"
    return 0
  fi
  for dir in "${SKILL_TARGETS[@]}"; do
    link_one "${dir}"
  done
}

self_check() {
  local tmp log
  step "4/4 自检"
  CLEANUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/contract-redact-install.XXXXXX")"
  tmp="${CLEANUP_DIR}"
  log="${tmp}/check.log"

  say "命令行：contract-redact --help"
  "${CLI}" --help >/dev/null || die "contract-redact --help 跑不起来"

  say "真实跑一遍：md 脱敏 → 自检 → 还原"
  cat > "${tmp}/样张.md" <<'SAMPLE'
甲方：星海精密制造有限公司
法定代表人：陈志远
电话：13800000001
统一社会信用代码：91340200MA2T8K9L3D
含税总价 1,000,000.00 元，逾期按 30% 计违约金。
SAMPLE

  "${CLI}" redact "${tmp}/样张.md" -o "${tmp}/样张_脱敏.md" \
    --map "${tmp}/样张.map.json" --no-audit >/dev/null \
    || die "脱敏这一步失败"

  grep -q "星海精密制造有限公司" "${tmp}/样张_脱敏.md" \
    && die "脱敏后仍能搜到公司名，自检不通过"
  grep -q "【公司A】" "${tmp}/样张_脱敏.md" \
    || die "脱敏后没有出现【公司A】，自检不通过"
  grep -q "1,000,000.00" "${tmp}/样张_脱敏.md" \
    || die "金额被误改，自检不通过"

  "${CLI}" check "${tmp}/样张_脱敏.md" >/dev/null \
    || die "contract-redact check 报有残留，自检不通过"

  "${CLI}" restore "${tmp}/样张_脱敏.md" --map "${tmp}/样张.map.json" \
    -o "${tmp}/样张_还原.md" >/dev/null \
    || die "还原这一步失败"
  cmp -s "${tmp}/样张.md" "${tmp}/样张_还原.md" \
    || die "还原后与原文不一致，自检不通过"

  if [ -f "${REPO_DIR}/tests/check.py" ]; then
    say "完整验收：tests/check.py（docx：无残留 / 金额不动 / 还原逐字一致）"
    if ! "${VENV_PY}" "${REPO_DIR}/tests/check.py" >"${log}" 2>&1; then
      printf '\n===== tests/check.py 最后 40 行 =====\n' >&2
      tail -40 "${log}" >&2 || true
      die "tests/check.py 没通过"
    fi
    grep "结果：" "${log}" | tail -1 || true
  else
    say "跳过 tests/check.py（仓库里没有这个文件）"
  fi

  if [ "${NO_SKILL}" -eq 0 ] && [ "${#SKILL_TARGETS[@]}" -gt 0 ]; then
    local dir
    for dir in "${SKILL_TARGETS[@]}"; do
      if [ -f "${dir}/${SKILL_NAME}/SKILL.md" ]; then
        say "技能可读：${dir}/${SKILL_NAME}/SKILL.md"
      fi
    done
  fi
  say "自检通过。"
}

print_done() {
  cat <<EOF

安装完成。

  命令行：    ${CLI}
  临时启用：  source "${VENV_DIR}/bin/activate"
  或直接写：  "${CLI}" --help

  技能目录：  ${SKILL_SRC}
  下一次重装：./install.sh
  看会做什么：./install.sh --list
EOF
}

main() {
  parse_args "$@"

  PY="$(find_python || true)"
  if [ -z "${PY}" ]; then
    if [ -n "${PYTHON_OVERRIDE}" ]; then
      die "--python 指定的解释器不可用，需要一个 Python 3.10 以上的可执行文件：${PYTHON_OVERRIDE}"
    fi
    die "没找到 Python 3.10 或更新版本。装一个再跑，或用 --python 指定解释器路径。"
  fi

  build_skill_targets

  if [ "${DRY_RUN}" -eq 1 ]; then
    print_plan
    exit 0
  fi

  ensure_venv
  install_package
  link_skills
  self_check
  print_done
}

main "$@"
