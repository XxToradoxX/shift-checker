"""员工月排班冲突检查器 —— Flask 后端入口。

运行：python app.py
首次运行会自动创建 config/ 与 data/ 目录，并写入默认配置与 2026-10 的初始排班数据。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import threading
import traceback
import uuid
import webbrowser
from pathlib import Path

# 优先使用已安装的 Flask；缺依赖时回退到项目自带的 vendor/ 目录（离线可用）
def _resource_dir() -> Path:
    """模板与静态资源目录：源码运行时是项目目录，打包后是临时解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


RES_DIR = _resource_dir()
_VENDOR = RES_DIR / "vendor"
try:
    from flask import Flask, jsonify, render_template, request, send_file
except ImportError:  # pragma: no cover
    if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
        sys.path.insert(0, str(_VENDOR))
    try:
        from flask import Flask, jsonify, render_template, request, send_file
    except ImportError:
        print("缺少 Flask 依赖。请执行： pip install -r requirements.txt")
        raise

from scheduler import csvio, generator, model, updater
from scheduler import rules as rulebook

APP_TITLE = "员工月排班冲突检查器"
DEFAULT_YEAR = 2026
DEFAULT_MONTH = 10

app = Flask(
    __name__,
    static_folder=str(RES_DIR / "static"),
    template_folder=str(RES_DIR / "templates"),
)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["JSON_AS_ASCII"] = False
app.json.ensure_ascii = False


# --------------------------------------------------------------------------
# 状态组装
# --------------------------------------------------------------------------
def build_state(year: int, month: int, extra: dict | None = None) -> dict:
    employees = model.load_employees()
    doc = model.load_month(year, month, employees)
    result = rulebook.check(doc, employees)
    state = {
        "ok": True,
        "appTitle": APP_TITLE,
        "now": model.now_iso(),
        "year": year,
        "month": month,
        "employees": employees,
        "doc": doc,
        "check": result,
        "months": model.list_months(),
        "catalog": {
            "base": rulebook.RULE_CATALOG,
            "extra": rulebook.EXTRA_RULE_TYPES,
        },
    }
    if extra:
        state.update(extra)
    return state


def error(message: str, code: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), code


@app.after_request
def no_cache(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@app.route("/")
def index():
    today = _dt.date.today()
    dist = Path(app.static_folder) / "dist" / "app.js"
    if not dist.exists():
        return render_template("need_build.html", app_title=APP_TITLE), 503
    return render_template(
        "index.html",
        app_title=APP_TITLE,
        default_year=DEFAULT_YEAR,
        default_month=DEFAULT_MONTH,
        today_year=today.year,
        today_month=today.month,
    )


@app.route("/api/state")
def api_state():
    year = _int(request.args.get("year"), DEFAULT_YEAR)
    month = _int(request.args.get("month"), DEFAULT_MONTH)
    if not (1 <= month <= 12):
        return error("月份必须在 1-12 之间")
    return jsonify(build_state(year, month))


# --------------------------------------------------------------------------
# 保存状态（排班 / 可用日 / 规则）
# --------------------------------------------------------------------------
@app.route("/api/state", methods=["POST"])
def api_save_state():
    payload = request.get_json(force=True, silent=True) or {}
    employees = model.load_employees()
    year = _int(payload.get("year"), DEFAULT_YEAR)
    month = _int(payload.get("month"), DEFAULT_MONTH)
    doc = model.load_month(year, month, employees)

    if isinstance(payload.get("schedule"), dict):
        doc["schedule"] = payload["schedule"]
    if isinstance(payload.get("availability"), dict):
        doc["availability"] = payload["availability"]
    if isinstance(payload.get("extraRules"), list):
        doc["extraRules"] = payload["extraRules"]
    if isinstance(payload.get("ruleOverrides"), dict):
        doc["ruleOverrides"] = payload["ruleOverrides"]
    if isinstance(payload.get("note"), str):
        doc["note"] = payload["note"]

    doc = model.normalize_month(doc, employees)
    doc = model.save_month(doc)
    return jsonify(build_state(year, month, {"message": "已保存"}))


@app.route("/api/reset", methods=["POST"])
def api_reset():
    payload = request.get_json(force=True, silent=True) or {}
    employees = model.load_employees()
    year = _int(payload.get("year"), DEFAULT_YEAR)
    month = _int(payload.get("month"), DEFAULT_MONTH)
    doc = model.load_month(year, month, employees)
    # 不再内置示例排班：重置 = 清空本月（保留可用日 / 特殊规则 / 规则覆盖）
    doc["schedule"] = model.blank_schedule(year, month, employees)
    doc["source"] = "blank"
    doc["note"] = ""
    doc = model.normalize_month(doc, employees)
    doc = model.save_month(doc)
    return jsonify(build_state(year, month, {"message": "已重置"}))


# --------------------------------------------------------------------------
# 生成排班
# --------------------------------------------------------------------------
@app.route("/api/generate", methods=["POST"])
def api_generate():
    payload = request.get_json(force=True, silent=True) or {}
    employees = model.load_employees()
    year = _int(payload.get("year"), DEFAULT_YEAR)
    month = _int(payload.get("month"), DEFAULT_MONTH)
    doc = model.load_month(year, month, employees)

    # 允许先带上未保存的规则/可用日
    if isinstance(payload.get("extraRules"), list):
        doc["extraRules"] = payload["extraRules"]
    if isinstance(payload.get("ruleOverrides"), dict):
        doc["ruleOverrides"] = payload["ruleOverrides"]
    if isinstance(payload.get("availability"), dict):
        doc["availability"] = payload["availability"]

    budget = float(payload.get("budget") or 5.0)
    budget = max(1.0, min(60.0, budget))
    iters = _int(payload.get("iterations"), 0)
    iters = max(0, min(2000000, iters)) or None
    workers = max(1, min(8, _int(payload.get("workers"), 1)))
    result = generator.generate_best(doc, employees, budget=budget,
                                     iterations=iters, workers=workers)
    if not result.get("ok"):
        return error(result.get("error") or "生成失败")

    doc["schedule"] = result["schedule"]
    doc["source"] = "generated"
    doc = model.normalize_month(doc, employees)
    doc = model.save_month(doc)

    notes = list(result.get("notes") or [])
    state = build_state(year, month)
    residual = [v for v in state["check"]["violations"] if v["severity"] == "error"]
    if residual:
        items = "；".join(f'{v["code"]} {v["message"]}' for v in residual[:4])
        notes.append("仍需人工确认：" + items)
    if not residual:
        notes.insert(0, "已生成无硬冲突的排班表。")
    state["generate"] = {
        "hard": result.get("hard"),
        "soft": result.get("soft"),
        "seconds": result.get("seconds"),
        "iterations": result.get("iterations"),
        "workers": result.get("workers"),
        "wall": result.get("wall"),
        "notes": notes,
    }
    return jsonify(state)


# --------------------------------------------------------------------------
# 员工管理
# --------------------------------------------------------------------------
@app.route("/api/employees", methods=["POST"])
def api_employees():
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get("action") or "update"
    employees = model.load_employees()
    by_id = {e["id"]: e for e in employees}

    if action == "create":
        data = payload.get("employee") or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return error("请填写员工姓名")
        if len(name) > 6:
            return error("姓名不超过 6 个字")
        if name in {e["name"] for e in employees}:
            return error(f"员工「{name}」已存在")
        eid = "e_" + uuid.uuid4().hex[:8]
        new_emp = model._normalize_employee({**data, "id": eid, "name": name}, len(employees))
        employees.append(new_emp)
    elif action == "delete":
        eid = payload.get("id")
        if eid not in by_id:
            return error("员工不存在")
        if len(employees) <= 1:
            return error("至少保留 1 名员工")
        employees = [e for e in employees if e["id"] != eid]
        _purge_employee(eid)
    elif action == "reorder":
        order = payload.get("order") or []
        if not isinstance(order, list):
            return error("排序参数无效")
        ordered = [by_id[i] for i in order if i in by_id]
        seen = {e["id"] for e in ordered}
        ordered += [e for e in employees if e["id"] not in seen]
        if len(ordered) != len(employees):
            return error("排序参数与员工列表不匹配")
        employees = ordered
    elif action == "move":
        eid = payload.get("id")
        delta = _int(payload.get("delta"), 0)
        if eid not in by_id or not delta:
            return error("移动参数无效")
        idx = next(i for i, e in enumerate(employees) if e["id"] == eid)
        target = max(0, min(len(employees) - 1, idx + delta))
        if target != idx:
            item = employees.pop(idx)
            employees.insert(target, item)
    else:
        eid = payload.get("id")
        if eid not in by_id:
            return error("员工不存在")
        data = payload.get("employee") or {}
        merged = {**by_id[eid], **{k: v for k, v in data.items() if k not in ("id",)}}
        name = str(merged.get("name") or "").strip()
        if not name:
            return error("请填写员工姓名")
        if any(e["name"] == name and e["id"] != eid for e in employees):
            return error(f"员工「{name}」已存在")
        merged["name"] = name
        updated = model._normalize_employee(merged, 0)
        employees = [updated if e["id"] == eid else e for e in employees]

    model.save_employees(employees)
    year = _int(payload.get("year"), DEFAULT_YEAR)
    month = _int(payload.get("month"), DEFAULT_MONTH)
    return jsonify(build_state(year, month, {"message": "员工已更新"}))


def _purge_employee(eid: str) -> None:
    for path in model.SCHEDULE_DIR.glob("*.json"):
        try:
            year, month = path.stem.split("-")
            doc = model._read_json(path, None)
            if not isinstance(doc, dict):
                continue
            touched = False
            for cell in (doc.get("schedule") or {}).values():
                for key in ("morning", "evening", "duty"):
                    if eid in (cell.get(key) or []):
                        cell[key] = [x for x in cell[key] if x != eid]
                        touched = True
                if cell.get("opener") == eid:
                    cell["opener"] = ""
                    touched = True
            if isinstance(doc.get("availability"), dict) and eid in doc["availability"]:
                del doc["availability"][eid]
                touched = True
            if touched:
                model._write_json(path, doc)
        except Exception:
            continue


# --------------------------------------------------------------------------
# 导入 / 导出
# --------------------------------------------------------------------------
@app.route("/api/export.csv")
def api_export_csv():
    employees = model.load_employees()
    year = _int(request.args.get("year"), DEFAULT_YEAR)
    month = _int(request.args.get("month"), DEFAULT_MONTH)
    doc = model.load_month(year, month, employees)
    text = csvio.export_csv(doc, employees)
    if request.args.get("matrix") in ("1", "true"):
        text = csvio.export_matrix_csv(doc, employees)
    data = ("\ufeff" + text).encode("utf-8")
    name = f"schedule_{year}-{month:02d}.csv"
    return send_file(
        __import__("io").BytesIO(data),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=name,
    )


@app.route("/api/import", methods=["POST"])
def api_import():
    employees = model.load_employees()
    year = _int(request.form.get("year") or request.args.get("year"), DEFAULT_YEAR)
    month = _int(request.form.get("month") or request.args.get("month"), DEFAULT_MONTH)
    doc = model.load_month(year, month, employees)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return error("请选择要导入的文件")
    filename = upload.filename
    suffix = Path(filename).suffix.lower()
    tmp_path = None
    try:
        if suffix in (".xlsx", ".xlsm"):
            tmp_dir = model.DATA_DIR / "imports"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / f"import_{uuid.uuid4().hex}{suffix}"
            upload.save(tmp_path)
            parsed = csvio.import_xlsx(str(tmp_path), doc, employees)
        else:
            raw = upload.read()
            text = None
            for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                return error("无法识别文件编码，请另存为 UTF-8 或 GBK 的 CSV")
            parsed = csvio.import_table(text, doc, employees)
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:  # pragma: no cover
        traceback.print_exc()
        return error(f"导入失败：{exc}")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    doc["schedule"] = parsed["schedule"]
    doc["source"] = f"import:{filename}"
    doc = model.normalize_month(doc, employees)
    doc = model.save_month(doc)
    state = build_state(year, month, {
        "message": f"已导入 {parsed['rows']} 行（{parsed['layout']} 布局）",
    })
    return jsonify(state)


# --------------------------------------------------------------------------
# 其它
# --------------------------------------------------------------------------
@app.route("/api/diagnose")
def api_diagnose():
    """多核自检：现场创建一次进程池，把完整异常栈返回。"""
    import platform
    import traceback
    from concurrent.futures import ProcessPoolExecutor

    info = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "platform": platform.platform(),
        "cpu": os.cpu_count(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "multiprocessing": __import__("multiprocessing").get_start_method(),
        "pool": False,
        "error": None,
    }
    try:
        with ProcessPoolExecutor(max_workers=1) as pool:
            info["pool"] = list(pool.map(abs, [-1])) == [1]
    except Exception:
        info["error"] = traceback.format_exc()
    return jsonify(info)


@app.route("/api/months")
def api_months():
    return jsonify({"ok": True, "months": model.list_months()})


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "time": model.now_iso()})


# --------------------------------------------------------------------------
# 在线更新
# --------------------------------------------------------------------------
@app.route("/api/update/check")
def api_update_check():
    return jsonify(updater.check())


@app.route("/api/update/download", methods=["POST"])
def api_update_download():
    payload = request.get_json(force=True, silent=True) or {}
    info = updater.check()
    if not info.get("hasUpdate") or not info.get("url"):
        return error(info.get("error") or "当前已是最新版本")
    return jsonify(updater.download(info["url"], info["latest"],
                                    info.get("size") or 0, info.get("assetId") or 0))


@app.route("/api/update/progress")
def api_update_progress():
    return jsonify(updater.progress())


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    result = updater.apply_update()
    if not result.get("ok"):
        return error(result.get("message") or "更新失败")
    return jsonify(result)


@app.route("/api/version")
def api_version():
    return jsonify({"version": updater.current_version(), "frozen": updater.is_frozen()})


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def bootstrap() -> None:
    model.ensure_dirs()
    model.load_settings()
    employees = model.load_employees()
    model.load_month(DEFAULT_YEAR, DEFAULT_MONTH, employees)


def _setup_console() -> None:
    """Windows 控制台默认 GBK，输出中文会乱码；这里切到 UTF-8。"""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _probe_multiprocessing() -> tuple[bool, str]:
    """启动时探测多核并行是否可用。"""
    try:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=1) as pool:
            if list(pool.map(abs, [-1])) != [1]:
                raise RuntimeError("进程池返回异常")
        return True, f"{mp.cpu_count()} 核"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--host", default=os.environ.get("SCHED_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SCHED_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    bootstrap()
    url = f"http://{args.host}:{args.port}/"
    _setup_console()
    mp_ok, mp_info = _probe_multiprocessing()
    print("=" * 62)
    print(f"  {APP_TITLE}")
    print(f"  配置目录：{model.CONFIG_DIR}")
    print(f"  数据目录：{model.DATA_DIR}")
    print(f"  访问地址：{url}")
    print(f"  多核并行：{'可用（' + mp_info + '）' if mp_ok else '不可用（' + mp_info + '），生成将退回单进程'}")
    print("=" * 62)
    if not args.no_browser and not args.debug:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # PyInstaller 打包后子进程入口
    main()
