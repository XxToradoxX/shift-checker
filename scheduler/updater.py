"""在线更新：检查 GitHub Release、下载安装包、覆盖式自更新。

只依赖标准库，打包成 exe 后同样可用。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CURRENT_VERSION = "1.0.4"
USER_AGENT = "ShiftChecker-Updater"

_state: dict = {
    "state": "idle",        # idle | downloading | ready | error
    "downloaded": 0,
    "total": 0,
    "speed": 0.0,
    "version": "",
    "message": "",
    "file": "",
}
_lock = threading.Lock()
_thread: threading.Thread | None = None


# ---------------------------------------------------------------- 基础信息
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
UPDATE_DIR = BASE_DIR / "data" / "update"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    """当前版本；可用环境变量伪装成旧版本，便于测试更新流程。"""
    return os.environ.get("SHIFT_UPDATE_FAKE_VERSION") or CURRENT_VERSION


def _read_settings() -> dict:
    try:
        with (BASE_DIR / "config" / "settings.json").open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update_conf() -> dict:
    conf = dict((_read_settings().get("update") or {}))
    conf.setdefault("repo", "XxToradoxX/shift-checker")
    conf.setdefault("asset", "ShiftChecker.zip")
    conf.setdefault("token", "")
    token = os.environ.get("SHIFT_UPDATE_TOKEN") or conf.get("token") or ""
    conf["token"] = str(token).strip()
    return conf


def _headers(token: str) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _ver(text: str) -> tuple:
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


# ---------------------------------------------------------------- 检查更新
def check() -> dict:
    conf = _update_conf()
    repo = conf["repo"]
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    result = {
        "current": current_version(),
        "latest": "",
        "hasUpdate": False,
        "notes": "",
        "size": 0,
        "url": "",
        "assetId": 0,
        "publishedAt": "",
        "frozen": is_frozen(),
        "repo": repo,
        "error": "",
    }
    try:
        req = urllib.request.Request(url, headers=_headers(conf["token"]))
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            result["error"] = (
                "无法访问 Release（404）：请检查仓库名与 Release 是否存在，或网络是否可用。"
                "若仓库是私有的，需要在 config/settings.json 的 update.token 填入 GitHub Token。"
            )
        elif exc.code in (401, 403):
            result["error"] = f"GitHub 拒绝访问（{exc.code}），请检查 update.token 是否有效。"
        else:
            result["error"] = f"检查更新失败：HTTP {exc.code}"
        return result
    except Exception as exc:  # 网络异常
        result["error"] = f"检查更新失败：{type(exc).__name__}: {exc}"
        return result

    tag = str(data.get("tag_name") or "")
    result["latest"] = tag.lstrip("vV")
    result["notes"] = str(data.get("body") or "")
    result["publishedAt"] = str(data.get("published_at") or "")
    for asset in data.get("assets") or []:
        if str(asset.get("name")) == conf["asset"]:
            result["url"] = str(asset.get("browser_download_url"))
            result["assetId"] = int(asset.get("id") or 0)
            result["size"] = int(asset.get("size") or 0)
            break
    result["hasUpdate"] = _ver(result["latest"]) > _ver(current_version()) and bool(result["url"])
    if not result["url"]:
        result["error"] = f"该 Release 里没有名为 {conf['asset']} 的附件。"
    return result


# ---------------------------------------------------------------- 下载
def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def progress() -> dict:
    with _lock:
        return dict(_state)


def download(url: str, version: str, size: int = 0, asset_id: int = 0) -> dict:
    """后台线程下载安装包，进度写到 _state。"""
    global _thread
    if _thread and _thread.is_alive():
        return {"ok": False, "message": "已经在下载中"}

    def worker():
        conf = _update_conf()
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        target = UPDATE_DIR / "package.zip"
        part = UPDATE_DIR / "package.part"
        _set(state="downloading", downloaded=0, total=size, speed=0.0,
             version=version, message="", file="")
        try:
            if asset_id:
                # 私有仓库：走 API 资源地址，Accept: octet-stream 直接返回二进制
                api = f"https://api.github.com/repos/{conf['repo']}/releases/assets/{asset_id}"
                headers = _headers(conf["token"])
                headers["Accept"] = "application/octet-stream"
                req = urllib.request.Request(api, headers=headers)
            else:
                req = urllib.request.Request(url, headers=_headers(conf["token"]))
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or size or 0)
                _set(total=total)
                with open(part, "wb") as fh:
                    got = 0
                    window: list = []  # [(时间, 累计字节)]，用于算「当前速度」
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        now = time.perf_counter()
                        window.append((now, got))
                        while len(window) > 2 and now - window[0][0] > 3.0:
                            window.pop(0)
                        dt = now - window[0][0]
                        speed = (got - window[0][1]) / dt if dt > 0.15 else 0.0
                        _set(downloaded=got, speed=speed)
            os.replace(part, target)
            _set(state="ready", downloaded=got, total=max(total, got), file=str(target),
                 message="下载完成，可以安装")
        except Exception as exc:
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            _set(state="error", message=f"下载失败：{type(exc).__name__}: {exc}")

    _thread = threading.Thread(target=worker, daemon=True)
    _thread.start()
    return {"ok": True}


# ---------------------------------------------------------------- 应用更新
def _extract(zip_path: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        # 压缩包里通常是 ShiftChecker/xxx，剥掉最外层目录
        roots = {n.split("/")[0] for n in names if "/" in n}
        strip = ""
        if len(roots) == 1 and all(n.startswith(next(iter(roots)) + "/") for n in names if n.strip("/")):
            strip = next(iter(roots)) + "/"
        for name in names:
            rel = name[len(strip):] if strip and name.startswith(strip) else name
            if not rel or rel.endswith("/"):
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def apply_update() -> dict:
    """解压到 staging，写一个自更新脚本，然后退出让脚本替换文件。"""
    if not is_frozen():
        return {"ok": False, "message": "当前是源码运行模式，请用 git pull 更新"}
    pkg = UPDATE_DIR / "package.zip"
    if not pkg.exists():
        return {"ok": False, "message": "还没有下载安装包"}
    staging = UPDATE_DIR / "staging"
    try:
        _extract(pkg, staging)
    except Exception as exc:
        return {"ok": False, "message": f"解压失败：{type(exc).__name__}: {exc}"}

    exe_name = Path(sys.executable).name
    # 重启时沿用原来的命令行参数（--port / --no-browser 等）
    extra_args = " ".join(a for a in sys.argv[1:] if a)
    script = BASE_DIR / "apply_update.bat"
    log = UPDATE_DIR / "apply.log"
    bat = f"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [%date% %time%] 等待程序退出... >> "{log}"
:wait
tasklist /fi "imagename eq {exe_name}" 2>nul | find /i "{exe_name}" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
echo [%date% %time%] 开始覆盖文件... >> "{log}"
robocopy "{staging}" "%CD%" /E /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS >> "{log}" 2>&1
echo [%date% %time%] 覆盖完成，启动新版本... >> "{log}"
start "" "%CD%\\{exe_name}" {extra_args}
timeout /t 2 /nobreak >nul
del "%~f0"
"""
    script.write_text(bat, encoding="gbk", errors="ignore")

    flags = 0
    if os.name == "nt":
        # 只给 CREATE_NEW_CONSOLE；和 DETACHED_PROCESS 一起用会报 WinError 87
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen(["cmd", "/c", str(script)], cwd=str(BASE_DIR),
                         creationflags=flags, close_fds=True)
    except Exception as exc:
        return {"ok": False, "message": f"无法启动更新脚本：{exc}"}

    def bye():
        time.sleep(1.2)
        os._exit(0)

    threading.Thread(target=bye, daemon=True).start()
    return {"ok": True, "message": "正在重启并应用更新"}
