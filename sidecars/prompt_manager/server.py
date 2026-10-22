"""
Antigravity Prompt & Rule Manager - Sidecar Server
实现随工作区自动匹配、动态拼装并注入 AGENTS.md 的后端服务
支持手动自定义模式（优先保留）与全量预设/积木编辑模式
"""

import os
import sys
import json
import sqlite3
import urllib.parse
import urllib.request
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# 终端编码兼容
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try: sys.stderr.reconfigure(encoding='utf-8')
    except Exception: pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")

ANTIGRAVITY_DIR = os.environ.get("ANTIGRAVITY_APP_DATA_DIR") or os.path.expanduser(r"~\.gemini\antigravity")
SUMMARY_DB = os.path.join(ANTIGRAVITY_DIR, "conversation_summaries.db")
GLOBAL_AGENTS_FILE = os.path.expanduser(r"~\.gemini\AGENTS.md")
CONFIG_FILE = os.path.join(ANTIGRAVITY_DIR, "prompt_manager_config.json")

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "auto_sync": True,
        "workspace_bindings": {},
        "custom_snippets": [],
        "custom_presets": [],
        "snippet_overrides": {},
        "preset_overrides": {},
        "deleted_snippets": [],
        "deleted_presets": []
    }

def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def load_builtin_presets() -> dict:
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"categories": [], "snippets": [], "presets": []}

def get_merged_catalog() -> dict:
    """合并内置预设/积木与用户自定义修改状态"""
    builtin = load_builtin_presets()
    cfg = load_config()
    
    snippet_overrides = cfg.get("snippet_overrides", {})
    deleted_snippets = set(cfg.get("deleted_snippets", []))
    custom_snippets = cfg.get("custom_snippets", [])
    
    # 1. 积木合并
    all_snippets = []
    for snip in builtin.get("snippets", []):
        sid = snip["id"]
        if sid in deleted_snippets:
            continue
        if sid in snippet_overrides:
            merged = dict(snip)
            merged.update(snippet_overrides[sid])
            merged["is_modified"] = True
            merged["is_builtin"] = True
            all_snippets.append(merged)
        else:
            item = dict(snip)
            item["is_builtin"] = True
            item["is_modified"] = False
            all_snippets.append(item)
            
    for cs in custom_snippets:
        item = dict(cs)
        item["is_custom"] = True
        item["is_builtin"] = False
        all_snippets.append(item)
        
    # 2. 方案预设合并
    preset_overrides = cfg.get("preset_overrides", {})
    deleted_presets = set(cfg.get("deleted_presets", []))
    custom_presets = cfg.get("custom_presets", [])
    
    all_presets = []
    for p in builtin.get("presets", []):
        pid = p["id"]
        if pid in deleted_presets:
            continue
        if pid in preset_overrides:
            merged = dict(p)
            merged.update(preset_overrides[pid])
            merged["is_modified"] = True
            merged["is_builtin"] = True
            all_presets.append(merged)
        else:
            item = dict(p)
            item["is_builtin"] = True
            item["is_modified"] = False
            all_presets.append(item)
            
    for cp in custom_presets:
        item = dict(cp)
        item["is_custom"] = True
        item["is_builtin"] = False
        all_presets.append(item)
        
    return {
        "categories": builtin.get("categories", []),
        "snippets": all_snippets,
        "presets": all_presets
    }

def save_preset(preset_data: dict) -> dict:
    cfg = load_config()
    pid = preset_data.get("id")
    if not pid:
        pid = f"preset_{int(time.time()*1000)}"
        preset_data["id"] = pid
        
    name = preset_data.get("name", "").strip()
    if not name:
        raise ValueError("预设方案名称不能为空")
        
    builtin = load_builtin_presets()
    builtin_ids = {p["id"] for p in builtin.get("presets", [])}
    
    if pid in builtin_ids:
        if "preset_overrides" not in cfg:
            cfg["preset_overrides"] = {}
        cfg["preset_overrides"][pid] = {
            "id": pid,
            "name": name,
            "description": preset_data.get("description", ""),
            "snippet_ids": preset_data.get("snippet_ids", []),
            "is_modified": True
        }
        del_list = cfg.get("deleted_presets", [])
        if pid in del_list:
            del_list.remove(pid)
            cfg["deleted_presets"] = del_list
    else:
        custom_presets = cfg.get("custom_presets", [])
        idx = next((i for i, p in enumerate(custom_presets) if p["id"] == pid), None)
        item = {
            "id": pid,
            "name": name,
            "description": preset_data.get("description", ""),
            "snippet_ids": preset_data.get("snippet_ids", []),
            "is_custom": True
        }
        if idx is not None:
            custom_presets[idx] = item
        else:
            custom_presets.append(item)
        cfg["custom_presets"] = custom_presets
        
    save_config(cfg)
    return {"ok": True, "preset": preset_data}

def delete_preset(pid: str) -> dict:
    if not pid:
        raise ValueError("缺少方案标识")
    cfg = load_config()
    builtin = load_builtin_presets()
    builtin_ids = {p["id"] for p in builtin.get("presets", [])}
    
    if pid in builtin_ids:
        del_list = set(cfg.get("deleted_presets", []))
        del_list.add(pid)
        cfg["deleted_presets"] = list(del_list)
        if "preset_overrides" in cfg and pid in cfg["preset_overrides"]:
            del cfg["preset_overrides"][pid]
    else:
        cfg["custom_presets"] = [p for p in cfg.get("custom_presets", []) if p["id"] != pid]
        
    save_config(cfg)
    return {"ok": True, "id": pid}

def save_snippet(snippet_data: dict) -> dict:
    cfg = load_config()
    sid = snippet_data.get("id")
    if not sid:
        sid = f"snippet_{int(time.time()*1000)}"
        snippet_data["id"] = sid
        
    title = snippet_data.get("title", "").strip()
    content = snippet_data.get("content", "").strip()
    if not title or not content:
        raise ValueError("片段标题与内容均不能为空")
        
    builtin = load_builtin_presets()
    builtin_ids = {s["id"] for s in builtin.get("snippets", [])}
    
    if sid in builtin_ids:
        if "snippet_overrides" not in cfg:
            cfg["snippet_overrides"] = {}
        cfg["snippet_overrides"][sid] = {
            "id": sid,
            "category": snippet_data.get("category", "persona"),
            "title": title,
            "description": snippet_data.get("description", ""),
            "content": content,
            "is_modified": True
        }
        del_list = cfg.get("deleted_snippets", [])
        if sid in del_list:
            del_list.remove(sid)
            cfg["deleted_snippets"] = del_list
    else:
        custom_snippets = cfg.get("custom_snippets", [])
        idx = next((i for i, s in enumerate(custom_snippets) if s["id"] == sid), None)
        item = {
            "id": sid,
            "category": snippet_data.get("category", "custom"),
            "title": title,
            "description": snippet_data.get("description", ""),
            "content": content,
            "is_custom": True
        }
        if idx is not None:
            custom_snippets[idx] = item
        else:
            custom_snippets.append(item)
        cfg["custom_snippets"] = custom_snippets
        
    save_config(cfg)
    return {"ok": True, "snippet": snippet_data}

def delete_snippet(sid: str) -> dict:
    if not sid:
        raise ValueError("缺少片段标识")
    cfg = load_config()
    builtin = load_builtin_presets()
    builtin_ids = {s["id"] for s in builtin.get("snippets", [])}
    
    if sid in builtin_ids:
        del_list = set(cfg.get("deleted_snippets", []))
        del_list.add(sid)
        cfg["deleted_snippets"] = list(del_list)
        if "snippet_overrides" in cfg and sid in cfg["snippet_overrides"]:
            del cfg["snippet_overrides"][sid]
    else:
        cfg["custom_snippets"] = [s for s in cfg.get("custom_snippets", []) if s["id"] != sid]
        
    save_config(cfg)
    return {"ok": True, "id": sid}

def reset_item(item_type: str, item_id: str = None) -> dict:
    cfg = load_config()
    if item_type == "snippet" and item_id:
        if "snippet_overrides" in cfg and item_id in cfg["snippet_overrides"]:
            del cfg["snippet_overrides"][item_id]
        if item_id in cfg.get("deleted_snippets", []):
            cfg["deleted_snippets"].remove(item_id)
    elif item_type == "preset" and item_id:
        if "preset_overrides" in cfg and item_id in cfg["preset_overrides"]:
            del cfg["preset_overrides"][item_id]
        if item_id in cfg.get("deleted_presets", []):
            cfg["deleted_presets"].remove(item_id)
    elif item_type == "all":
        cfg["preset_overrides"] = {}
        cfg["snippet_overrides"] = {}
        cfg["deleted_presets"] = []
        cfg["deleted_snippets"] = []
    save_config(cfg)
    return {"ok": True, "type": item_type, "id": item_id}

def uri_to_path(uri: str) -> str:
    """把 file:/// 路径转换为本地文件系统绝对路径"""
    if not uri:
        return ""
    if uri.startswith("file:///"):
        path = uri[8:]
        path = urllib.parse.unquote(path)
        if len(path) >= 2 and path[0] == '/' and path[2] == ':':
            path = path[1:]
        path = path.replace('/', '\\')
        if len(path) >= 2 and path[1] == ':':
            path = path[0].upper() + path[1:]
        return os.path.normpath(path)
    if len(uri) >= 2 and uri[1] == ':':
        uri = uri[0].upper() + uri[1:]
    return os.path.normpath(uri)

def get_active_workspace(conv_id: str = None) -> str:
    """从数据库获取当前活动会话对应的工作区路径"""
    if os.path.exists(SUMMARY_DB):
        try:
            conn = sqlite3.connect(f"file:{SUMMARY_DB}?mode=ro", uri=True)
            cur = conn.cursor()
            target_id = conv_id
            if not target_id:
                r = cur.execute("SELECT conversation_id FROM conversation_summaries WHERE status = 'CASCADE_RUN_STATUS_RUNNING' ORDER BY last_modified_time DESC LIMIT 1").fetchone()
                if r:
                    target_id = r[0]
                else:
                    r2 = cur.execute("SELECT conversation_id FROM conversation_summaries ORDER BY last_modified_time DESC LIMIT 1").fetchone()
                    if r2:
                        target_id = r2[0]

            if target_id:
                r_ws = cur.execute("SELECT workspace_uris FROM conversation_summaries WHERE conversation_id = ?", (target_id,)).fetchone()
                conn.close()
                if r_ws and r_ws[0]:
                    try:
                        uris = json.loads(r_ws[0])
                        if uris and isinstance(uris, list) and len(uris) > 0:
                            return uri_to_path(uris[0])
                    except Exception:
                        pass
            else:
                conn.close()
        except Exception:
            pass

    return os.path.normpath(os.getcwd())

def list_known_workspaces() -> list:
    """自动发现用户历史记录中已登记的工作区"""
    workspaces = {}
    
    def add_ws(raw_path):
        if not raw_path:
            return
        p = uri_to_path(raw_path)
        if p and os.path.exists(p):
            key = p.lower()
            if key not in workspaces:
                agents_path = os.path.join(p, "AGENTS.md")
                workspaces[key] = {
                    "path": p,
                    "name": os.path.basename(p) or p,
                    "has_agents": os.path.exists(agents_path),
                    "agents_path": agents_path
                }

    if os.path.exists(SUMMARY_DB):
        try:
            conn = sqlite3.connect(f"file:{SUMMARY_DB}?mode=ro", uri=True)
            rows = conn.execute("SELECT DISTINCT workspace_uris FROM conversation_summaries WHERE workspace_uris IS NOT NULL AND workspace_uris != ''").fetchall()
            conn.close()
            for r in rows:
                try:
                    uris = json.loads(r[0])
                    for u in uris:
                        add_ws(u)
                except Exception:
                    pass
        except Exception:
            pass

    cfg = load_config()
    for p in cfg.get("workspace_bindings", {}).keys():
        add_ws(p)

    return list(workspaces.values())

def resolve_target_file(target: str) -> tuple:
    """返回 (目标文件实际路径, 是否属于全局配置)"""
    if not target or target.lower() in ["global", "全局"]:
        return GLOBAL_AGENTS_FILE, True
    target_clean = os.path.normpath(target)
    if os.path.isdir(target_clean):
        return os.path.join(target_clean, "AGENTS.md"), False
    if target_clean.endswith(".md"):
        return target_clean, False
    return os.path.join(target_clean, "AGENTS.md"), False

def read_agents_file(target: str) -> dict:
    fpath, is_global = resolve_target_file(target)
    content = ""
    exists = os.path.exists(fpath)
    if exists:
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            content = f"# 读取错误: {str(e)}"
    
    bak_path = fpath + ".bak"
    has_backup = os.path.exists(bak_path)
    bak_time = None
    if has_backup:
        try:
            bak_time = datetime.fromtimestamp(os.path.getmtime(bak_path), timezone.utc).isoformat()
        except Exception:
            pass

    return {
        "file_path": fpath,
        "is_global": is_global,
        "exists": exists,
        "content": content,
        "has_backup": has_backup,
        "backup_path": bak_path,
        "backup_time": bak_time
    }

def inject_agents_file(target: str, content: str, snippet_ids: list = None, preset_id: str = None, save_as_binding: bool = True, is_custom_priority: bool = False) -> dict:
    fpath, is_global = resolve_target_file(target)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    
    # 写入前自动创建安全备份
    backup_created = False
    bak_path = fpath + ".bak"
    if os.path.exists(fpath):
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as old_f:
                old_content = old_f.read()
            with open(bak_path, "w", encoding="utf-8") as bak_f:
                bak_f.write(old_content)
            backup_created = True
        except Exception:
            pass

    # 写入新内容
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")

    # 更新工作区偏好记忆绑定
    is_custom_mode = is_custom_priority or (preset_id in ["custom_manual", "custom_supreme", "custom"])
    if save_as_binding and not is_global:
        cfg = load_config()
        ws_dir = os.path.dirname(fpath)
        cfg["workspace_bindings"][ws_dir] = {
            "preset_id": "custom_manual" if is_custom_mode else (preset_id or "custom"),
            "snippet_ids": snippet_ids or [],
            "is_custom_priority": is_custom_mode,
            "custom_content": content if is_custom_mode else "",
            "last_applied": datetime.now(timezone.utc).isoformat()
        }
        save_config(cfg)

    return {
        "ok": True,
        "file_path": fpath,
        "is_global": is_global,
        "backup_created": backup_created,
        "backup_path": bak_path,
        "is_custom_priority": is_custom_mode,
        "length": len(content)
    }

def restore_backup_file(target: str) -> dict:
    fpath, is_global = resolve_target_file(target)
    bak_path = fpath + ".bak"
    if not os.path.exists(bak_path):
        raise FileNotFoundError(f"未找到对应备份文件: {bak_path}")
    
    with open(bak_path, "r", encoding="utf-8", errors="ignore") as bf:
        bak_content = bf.read()
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(bak_content)

    return {
        "ok": True,
        "file_path": fpath,
        "restored": True
    }

# ---------------------------------------------------------------------------
# 开发者特性与开关常驻守护 (Developer Features & Power Flags Guardian)
# ---------------------------------------------------------------------------
POWER_FLAGS = {
    "enable-developer-mode": True,
    "show-all-models-in-gemini-app": True,
    "enable-conversation-forking": True,
    "enable-fork-at-historical-step": True,
    "enable-fork-in-new-worktree": True,
    "enable-conversation-navigator": True,
    "enable-conversation-selection-quoting": True,
    "enable-conversation-groups": True,
    "enable-conversation-search-v2": True,
    "enable-input-autocomplete": True,
    "enable-thought-editing": True,
    "enable-thought-steering-from-selection": True,
    "enable-file-explorer-tab": True,
    "enable-split-view": True,
    "enable-hub-builtin-browser": True,
    "enable-code-search-palette": True,
    "enable-context-menu-system": True,
    "enable-git-vcs-ui": True,
    "enable-jetbox-terminal-v2": True,
    "enable-notebook-rendering": True,
    "enable-pdf-viewer": True,
    "markdown-table-v2": True,
    "configure-artifact-width": True,
    "jetski-autonomous-mode": True,
    "enable-auto-permission-preset": True,
    "enable-permissions-selector": True,
    "enable-sidecar-pill-auto-open": True,
    "context-window-selector": True,
    "enable-project-4k": True,
    "enable-battle-mode": True
}

def auto_guard_feature_flags():
    """检测宿主端口变更并自动保持开发者高级开关生效"""
    active_port_file = os.path.join(os.environ.get("APPDATA", ""), "Antigravity", "DevToolsActivePort")
    if not os.path.exists(active_port_file):
        return
    try:
        import websockets
        import asyncio
        async def _check_and_inject():
            with open(active_port_file, "r") as f:
                port = f.readline().strip()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
            with urllib.request.urlopen(req, timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            main_page = next((t for t in targets if t.get('type') == 'page'), None)
            if not main_page:
                return

            async with websockets.connect(main_page['webSocketDebuggerUrl'], close_timeout=2) as ws:
                check_js = "window.localStorage.getItem('jetski.developer.customFlagOverrides');"
                await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': check_js, 'returnByValue': True}}))
                res = json.loads(await ws.recv())
                val = res.get('result', {}).get('result', {}).get('value')
                needs_inject = False
                if not val:
                    needs_inject = True
                else:
                    try:
                        cur = json.loads(val)
                        if not cur.get("enable-developer-mode") or not cur.get("enable-conversation-forking"):
                            needs_inject = True
                    except Exception:
                        needs_inject = True

                if needs_inject:
                    inject_js = f"""
                    (() => {{
                        const raw = window.localStorage.getItem('jetski.developer.customFlagOverrides');
                        let flags = {{}};
                        try {{ if (raw) flags = JSON.parse(raw); }} catch(e) {{}}
                        Object.assign(flags, {json.dumps(POWER_FLAGS)});
                        const newRaw = JSON.stringify(flags);
                        window.localStorage.setItem('jetski.developer.customFlagOverrides', newRaw);
                        window.dispatchEvent(new StorageEvent('storage', {{
                            key: 'jetski.developer.customFlagOverrides',
                            newValue: newRaw
                        }}));
                        return true;
                    }})()
                    """
                    await ws.send(json.dumps({'id': 2, 'method': 'Runtime.evaluate', 'params': {'expression': inject_js, 'returnByValue': True}}))
                    await ws.recv()
                    await ws.send(json.dumps({'id': 3, 'method': 'Page.reload', 'params': {}}))
                    await ws.recv()
                    print("[DevFlags-Guardian] 开发者特性开关已就绪并同步生效。")

        asyncio.run(_check_and_inject())
    except Exception:
        pass

class WorkspaceWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.last_ws = ""
        self.loop_count = 0

    def run(self):
        while True:
            try:
                time.sleep(3)
                self.loop_count += 1
                
                if self.loop_count % 3 == 0:
                    auto_guard_feature_flags()

                cfg = load_config()
                if not cfg.get("auto_sync", True):
                    continue

                active_ws = get_active_workspace()
                if active_ws and active_ws != self.last_ws:
                    self.last_ws = active_ws
                    bindings = cfg.get("workspace_bindings", {})
                    if active_ws in bindings:
                        binding = bindings[active_ws]
                        target_file = os.path.join(active_ws, "AGENTS.md")
                        # 仅当目标工作区未创建 AGENTS.md 时自动初始化，绝不覆盖已有用户文件
                        if not os.path.exists(target_file):
                            if binding.get("is_custom_priority") and binding.get("custom_content"):
                                inject_agents_file(target_file, binding["custom_content"], is_custom_priority=True, save_as_binding=False)
                                print(f"[Auto-Sync] 工作区规则已完成初始化: {target_file}")
                            else:
                                cat = get_merged_catalog()
                                all_snippets = {s["id"]: s["content"] for s in cat.get("snippets", [])}
                                assembled = [all_snippets[sid] for sid in binding.get("snippet_ids", []) if sid in all_snippets]
                                if assembled:
                                    full_txt = "\n\n".join(assembled)
                                    inject_agents_file(target_file, full_txt, binding.get("snippet_ids"), binding.get("preset_id"), save_as_binding=False)
                                    print(f"[Auto-Sync] 工作区规则已完成初始化: {target_file}")
            except Exception:
                pass

# ---------------------------------------------------------------------------
# HTTP 服务接口处理
# ---------------------------------------------------------------------------
class PromptManagerHandler(BaseHTTPRequestHandler):
    def _send_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Sidecar-Token')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path in ["/ui.html", "/"]:
            ui_file = os.path.join(BASE_DIR, "ui.html")
            if os.path.exists(ui_file):
                with open(ui_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
                return

        if path == "/health":
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "plugin": "antigravity-prompt-manager"}).encode("utf-8"))
            return

        if path == "/api/bootstrap":
            conv_id = params.get("conversationId", [None])[0]
            catalog = get_merged_catalog()
            active_ws = get_active_workspace(conv_id)
            known_ws = list_known_workspaces()
            cfg = load_config()

            # 首位放置手动自定义模式
            custom_manual_preset = {
                "id": "custom_manual",
                "name": "📌 自定义模式 (优先)",
                "description": "自由手动编写规则内容，编辑区文本优先保存，不会被预设重置覆盖",
                "snippet_ids": [],
                "is_custom_priority": True
            }
            presets = [custom_manual_preset] + catalog.get("presets", [])

            resp_data = {
                "active_workspace": active_ws,
                "global_file": GLOBAL_AGENTS_FILE,
                "workspaces": known_ws,
                "categories": catalog.get("categories", []),
                "snippets": catalog.get("snippets", []),
                "presets": presets,
                "workspace_bindings": cfg.get("workspace_bindings", {}),
                "auto_sync": cfg.get("auto_sync", True),
                "builtin_snippets": load_builtin_presets().get("snippets", []),
                "builtin_presets": load_builtin_presets().get("presets", [])
            }
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/agents-file":
            target = params.get("target", [""])[0]
            data = read_agents_file(target)
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8', errors='replace')
        try:
            req_data = json.loads(body) if body else {}
        except Exception:
            req_data = {}

        if path == "/api/inject-agents":
            try:
                target = req_data.get("target") or req_data.get("workspace")
                content = req_data.get("content", "")
                snippet_ids = req_data.get("snippet_ids", [])
                preset_id = req_data.get("preset_id")
                save_binding = req_data.get("save_as_binding", True)
                is_custom_priority = bool(req_data.get("is_custom_priority", False)) or (preset_id in ["custom_manual", "custom_supreme"])

                res = inject_agents_file(target, content, snippet_ids, preset_id, save_binding, is_custom_priority)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/restore-backup":
            try:
                target = req_data.get("target") or req_data.get("workspace")
                res = restore_backup_file(target)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path in ["/api/save-snippet", "/api/save-custom-snippet"]:
            try:
                snippet = req_data.get("snippet", {})
                res = save_snippet(snippet)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/delete-snippet":
            try:
                sid = req_data.get("id")
                res = delete_snippet(sid)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/save-preset":
            try:
                preset = req_data.get("preset", {})
                res = save_preset(preset)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/delete-preset":
            try:
                pid = req_data.get("id")
                res = delete_preset(pid)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/reset-item":
            try:
                itype = req_data.get("type", "all")
                iid = req_data.get("id")
                res = reset_item(itype, iid)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/set-auto-sync":
            try:
                cfg = load_config()
                cfg["auto_sync"] = bool(req_data.get("auto_sync", True))
                save_config(cfg)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "auto_sync": cfg["auto_sync"]}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path in ["/api/snippet", "/api/custom-snippet"]:
            try:
                sid = params.get("id", [None])[0]
                res = delete_snippet(sid)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/preset":
            try:
                pid = params.get("id", [None])[0]
                res = delete_preset(pid)
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

def discover_antigravity_ports():
    ports = []
    port_env = os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT") or os.environ.get("PORT")
    if port_env:
        try: ports.append(int(port_env))
        except Exception: pass

    active_port_file = os.path.join(os.environ.get("APPDATA", ""), "Antigravity", "DevToolsActivePort")
    if os.path.exists(active_port_file):
        try:
            with open(active_port_file, "r") as f:
                cdp_port = f.readline().strip()
            req = urllib.request.Request(f"http://127.0.0.1:{cdp_port}/json")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                targets = json.loads(resp.read().decode())
            import re
            for t in targets:
                url = t.get("url", "")
                m = re.search(r"antigravity-prompt-manager--prompt_manager-(\d+)", url)
                if m:
                    p = int(m.group(1))
                    if p not in ports:
                        ports.append(p)
        except Exception:
            pass

    for p in [39282, 27619, 24997, 32500]:
        if p not in ports:
            ports.append(p)
    return ports

def main():
    ports = discover_antigravity_ports()

    # 启动工作区动态监视与特性开关守护线程
    watcher = WorkspaceWatcher()
    watcher.start()

    servers = []
    for p in ports:
        try:
            srv = HTTPServer(('127.0.0.1', p), PromptManagerHandler)
            servers.append(srv)
            print(f"[Sidecar] Prompt Manager running on: http://127.0.0.1:{p}")
        except Exception:
            pass

    if not servers:
        print("[Sidecar] 无法绑定任何指定端口。")
        return

    for srv in servers[1:]:
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
