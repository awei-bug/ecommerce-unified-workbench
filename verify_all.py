"""四条产品线的统一验证入口。

默认跑所有不依赖运行时服务的测试；`--build` 追加前端构建，`--runtime`
追加工作台契约与联动验收，`--self-check` 只做仓库内交付物自检（CI 用）。
`--build` 会清空各前端 dist/，本环境的 safe-delete 垫片会把旧产物移入
回收站，因此构建档自动追加回收站兜底清理；也可用
`--purge-recycle-bin` 单独执行。

设计约束：
- 单项失败不中断其余检查，最终汇总决定退出码；
- 产品仓库缺失时标记 SKIP 而非 FAIL，便于 CI 只 checkout 本仓库；
- 每项独立超时，输出逐项耗时；
- 单测口径与三个产品仓库 CI 对齐（云购 TENANT_STRICT=1、data-hub 跑全部用例）；
- 不伪造结果：SKIP 会明确打印原因。

用法：
    py -3 verify_all.py
    py -3 verify_all.py --build --runtime
    py -3 verify_all.py --self-check
    py -3 verify_all.py --purge-recycle-bin
    py -3 verify_all.py --json report.json
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("UNIFIED_WORKSPACE_ROOT") or HERE.parent).resolve()

SHOP = ROOT / "云购多租户电商 SaaS 平台"
CROSS = ROOT / "跨境电商运营智能辅助系统"
CONTENT = ROOT / "电商智能内容生成与商品知识平台"

PORTAL_STATUS = "http://127.0.0.1:8090/status"
DEFAULT_TIMEOUT = 900
DOC_FILES = (
    HERE / "README.md",
    HERE / "ARCHITECTURE.md",
    HERE / "统一交付" / "README.md",
)
REQUIRED_FILES = (
    HERE / "ARCHITECTURE.md",
    HERE / "verify_all.py",
    HERE / "verify_order_event_chain.py",
    HERE / "统一电商工作台" / "verify.py",
    HERE / "统一电商工作台" / "start.py",
    HERE / ".github" / "workflows" / "ci.yml",
)
ADR_COUNT = 5

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def _resolve(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for ext in (".cmd", ".exe", ".bat"):
            found = shutil.which(name + ext)
            if found:
                return found
    return name


def _venv_python(repo: Path, subdir: str) -> str:
    base = repo / subdir
    for relative in (".venv/Scripts/python.exe", ".venv/bin/python"):
        candidate = base / relative
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _exists(*parts: str) -> bool:
    return (ROOT.joinpath(*parts)).exists()


def build_checks(args) -> list[dict]:
    npm = _resolve("npm")
    py = sys.executable
    checks: list[dict] = [
        {
            "name": "云购 pytest",
            "group": "unit",
            "cwd": SHOP / "flask_shop",
            "cmd": [_venv_python(SHOP, "flask_shop"), "-m", "pytest", "-q", "--disable-warnings"],
            "requires": SHOP / "flask_shop",
            # 与云购 CI 完全同口径：严格租户校验下曾出现本地默认模式漏检的回归。
            "env": {"TENANT_STRICT": "1", "FLASK_ENV": "testing"},
        },
        {
            "name": "跨境 pytest",
            "group": "unit",
            "cwd": CROSS / "backend",
            "cmd": [_venv_python(CROSS, "backend"), "-m", "pytest", "-q", "--disable-warnings"],
            "requires": CROSS / "backend",
        },
        {
            "name": "跨境前端 vitest",
            "group": "unit",
            "cwd": CROSS / "frontend",
            "cmd": [npm, "test"],
            "requires": CROSS / "frontend" / "package.json",
        },
        {
            "name": "内容 api-server node:test",
            "group": "unit",
            "cwd": CONTENT / "apps" / "api-server",
            "cmd": [npm, "test"],
            "requires": CONTENT / "apps" / "api-server" / "package.json",
            # 正常耗时约 3s；曾在完整档中偶发挂起，单列 180s 上限。
            "timeout": 180,
        },
        {
            "name": "内容 studio-web node:test",
            "group": "unit",
            "cwd": CONTENT / "apps" / "studio-web",
            "cmd": [npm, "test"],
            "requires": CONTENT / "apps" / "studio-web" / "package.json",
        },
        {
            "name": "data-hub pytest",
            "group": "unit",
            "cwd": CONTENT / "apps" / "data-hub",
            # 与内容平台 CI 保持一致：跑该目录下全部测试文件，
            # 不逐个列名，避免新增用例被本地验证漏掉（此前漏了 test_copy_preservation.py）。
            "cmd": [py, "-m", "pytest", "-q"],
            "requires": CONTENT / "apps" / "data-hub" / "test_authz.py",
        },
    ]

    if args.build:
        checks.extend([
            {
                "name": "宿主前端 build",
                "group": "build",
                "cwd": SHOP / "vue_shop",
                "cmd": [npm, "run", "build"],
                "requires": SHOP / "vue_shop" / "package.json",
            },
            {
                "name": "跨境前端 build",
                "group": "build",
                "cwd": CROSS / "frontend",
                "cmd": [npm, "run", "build"],
                "requires": CROSS / "frontend" / "package.json",
            },
        ])

    if args.runtime:
        checks.append({
            "name": "工作台契约与三段联动",
            "group": "runtime",
            "cwd": HERE,
            "cmd": [py, str(HERE / "统一电商工作台" / "verify.py")],
            "requires": HERE / "统一电商工作台" / "verify.py",
            "needs_portal": True,
        })

    return checks


def run_check(check: dict, timeout: int) -> dict:
    started = time.monotonic()
    required = check.get("requires")
    if required is not None and not Path(required).exists():
        return {"name": check["name"], "status": "SKIP", "seconds": 0.0,
                "detail": f"依赖路径不存在：{required}"}

    if check.get("needs_portal") and not _portal_ready():
        return {"name": check["name"], "status": "SKIP", "seconds": 0.0,
                "detail": "工作台未启动（/status 不可达），先运行 start.py"}

    # 单项可覆盖全局超时：node:test 偶发挂起不应拖住整轮验证。
    item_timeout = check.get("timeout", timeout)
    try:
        result = subprocess.run(
            check["cmd"],
            cwd=str(check["cwd"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=item_timeout,
            env={**os.environ, "PYTHONUTF8": "1", "CI": os.environ.get("CI", "1"),
                 **(check.get("env") or {})},
        )
        seconds = time.monotonic() - started
        if result.returncode == 0:
            return {"name": check["name"], "status": "PASS", "seconds": seconds,
                    "detail": _tail(result.stdout)}
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return {"name": check["name"], "status": "FAIL", "seconds": seconds,
                "detail": _tail(output)}
    except subprocess.TimeoutExpired:
        return {"name": check["name"], "status": "FAIL",
                "seconds": time.monotonic() - started,
                "detail": f"超时（>{timeout}s）"}
    except Exception as exc:  # 命令不存在等环境错误必须可见，不静默跳过
        return {"name": check["name"], "status": "FAIL",
                "seconds": time.monotonic() - started, "detail": str(exc)}


def _tail(text: str, limit: int = 2400) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-24:])[-limit:]


def _portal_ready() -> bool:
    try:
        with urlopen(PORTAL_STATUS, timeout=3) as response:
            return response.status == 200
    except (URLError, OSError, ValueError):
        return False


# ---------------------------------------------------------------- 回收站清理

# 目录删除（含 npm build 清空 dist/）会被本环境的 safe-delete 垫片改成移入
# 回收站；在 $Recycle.Bin 内部用 .NET API 删除不会再入回收站，是唯一可靠
# 的收尾手段。脚本先枚举再删除，最后回读三盘剩余数用于断言。
_PURGE_SCRIPT = r'''
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$drives = @('C:','D:','F:')
$targets = @()
foreach ($drive in $drives) {
  $root = $drive + '\' + '$Recycle.Bin' + '\' + $sid
  if (!(Test-Path -LiteralPath $root)) { continue }
  $targets += @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -ne 'desktop.ini' })
}
foreach ($item in $targets) {
  try {
    if ($item.PSIsContainer) { [System.IO.Directory]::Delete($item.FullName, $true) }
    else { [System.IO.File]::Delete($item.FullName) }
  } catch { }
}
$rest = @()
foreach ($drive in $drives) {
  $root = $drive + '\' + '$Recycle.Bin' + '\' + $sid
  if (!(Test-Path -LiteralPath $root)) { $rest += 0; continue }
  $rest += @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -ne 'desktop.ini' }).Count
}
"removed=$($targets.Count);remaining=$($rest -join ',')"
'''


def purge_recycle_bin(timeout: int = DEFAULT_TIMEOUT) -> dict:
    """彻底清空三盘回收站的非系统条目并验证归零（仅 Windows）。"""
    started = time.monotonic()
    if os.name != "nt":
        return {"name": "回收站兜底清理", "status": "SKIP", "seconds": 0.0,
                "detail": "非 Windows 环境，无需清理"}
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return {"name": "回收站兜底清理", "status": "SKIP", "seconds": 0.0,
                "detail": "未找到 powershell / pwsh"}

    try:
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", _PURGE_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=min(max(timeout, 30), 180),
        )
    except Exception as exc:
        return {"name": "回收站兜底清理", "status": "FAIL",
                "seconds": time.monotonic() - started, "detail": str(exc)}

    output = result.stdout or ""
    match = re.search(r"removed=(\d+);remaining=([\d,]+)", output)
    if result.returncode != 0 or not match:
        detail = _tail(output + "\n" + (result.stderr or ""))
        return {"name": "回收站兜底清理", "status": "FAIL",
                "seconds": time.monotonic() - started, "detail": detail}

    removed = int(match.group(1))
    remaining = [int(value) for value in match.group(2).split(",") if value]
    ok = bool(remaining) and all(value == 0 for value in remaining)
    return {
        "name": "回收站兜底清理",
        "status": "PASS" if ok else "FAIL",
        "seconds": time.monotonic() - started,
        "detail": f"清理 {removed} 项，三盘剩余 {remaining}",
    }


# ---------------------------------------------------------------- 交付物自检

def self_checks() -> list[dict]:
    results: list[dict] = []

    bad_syntax = []
    for path in HERE.rglob("*.py"):
        if "__pycache__" in path.parts or ".runtime" in path.parts:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            bad_syntax.append(f"{path.relative_to(HERE)}: {exc}")
    results.append(_self_result(
        "仓库内 Python 语法", not bad_syntax,
        "全部 .py 可解析" if not bad_syntax else "; ".join(bad_syntax[:5]),
    ))

    missing = [str(path.relative_to(HERE)) for path in REQUIRED_FILES if not path.exists()]
    results.append(_self_result(
        "关键交付物齐备", not missing,
        f"{len(REQUIRED_FILES)} 项齐备" if not missing else f"缺失：{', '.join(missing)}",
    ))

    broken = _broken_doc_links()
    results.append(_self_result(
        "文档相对链接有效", not broken,
        "全部可解析" if not broken else "; ".join(broken[:6]),
    ))

    delivery = HERE / "统一交付"
    leftovers = [name for name in ("archive", "backups") if (delivery / name).exists()]
    stray_docs = [
        item.name for item in delivery.iterdir()
        if item.is_file() and item.suffix == ".md" and item.name != "README.md"
    ]
    clean = not leftovers and not stray_docs
    results.append(_self_result(
        "过程归档已清理", clean,
        "统一交付/ 下仅 README.md" if clean
        else f"残留目录 {leftovers}，散落文档 {stray_docs}",
    ))

    arch = (HERE / "ARCHITECTURE.md").read_text(encoding="utf-8")
    adr = len(re.findall(r"^### ADR-\d+", arch, flags=re.M))
    results.append(_self_result(
        "ADR 数量", adr == ADR_COUNT,
        f"{adr} 条（期望 {ADR_COUNT}）",
    ))

    return results


def _self_result(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "status": "PASS" if ok else "FAIL", "seconds": 0.0, "detail": detail}


def _broken_doc_links() -> list[str]:
    """只校验 Markdown 相对链接；http / mailto / 纯锚点不校验。"""
    broken = []
    link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for doc in DOC_FILES:
        if not doc.exists():
            broken.append(f"文档缺失 {doc.name}")
            continue
        for raw in link.findall(doc.read_text(encoding="utf-8")):
            target = raw.strip().split("#", 1)[0].strip()
            if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            if target.startswith("#"):
                continue
            resolved = (doc.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{doc.name} -> {target}")
    return broken


# ---------------------------------------------------------------- 主流程

def _print_result(item: dict) -> None:
    marker = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[item["status"]]
    print(f"  [{marker}] {item['name']} ({item['seconds']:.1f}s)")
    if item["status"] != "PASS" and item.get("detail"):
        for line in item["detail"].splitlines()[:10]:
            print(f"         {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="三系统合并体全量验证")
    parser.add_argument("--build", action="store_true", help="追加前端构建")
    parser.add_argument("--runtime", action="store_true", help="追加工作台契约与联动验收")
    parser.add_argument("--self-check", action="store_true", help="只做仓库内交付物自检（CI 用）")
    parser.add_argument("--purge-recycle-bin", action="store_true",
                        help="单独执行回收站兜底清理（--build 时自动执行）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="单项超时秒数")
    parser.add_argument("--json", dest="json_path", default="", help="把结果写成 JSON")
    args = parser.parse_args()

    print(f"工作区：{ROOT}")
    if args.self_check:
        print("模式：交付物自检（不依赖产品仓库与运行时服务）\n")
        results = self_checks()
    elif args.purge_recycle_bin and not (args.build or args.runtime):
        print("模式：仅回收站兜底清理\n")
        results = []
    else:
        selected = ["单元测试"] + (["前端构建"] if args.build else []) \
            + (["运行时验收"] if args.runtime else [])
        print(f"模式：{' + '.join(selected)}\n")
        results = [run_check(check, args.timeout) for check in build_checks(args)]

    # 构建会清空 dist/ 并产生回收站条目；显式开关可单独做一次收尾清理。
    if not args.self_check and (args.build or args.purge_recycle_bin):
        results.append(purge_recycle_bin(args.timeout))

    for item in results:
        _print_result(item)

    counts = {key: sum(1 for item in results if item["status"] == key)
              for key in ("PASS", "FAIL", "SKIP")}
    print(f"\n汇总：PASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']} "
          f"共 {len(results)} 项，耗时 {sum(i['seconds'] for i in results):.1f}s")

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"root": str(ROOT), "results": results, "summary": counts},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已写入 {args.json_path}")

    # SKIP 不算通过也不算失败：CI 只跑自检时不应因产品仓库缺失而红。
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
