#!/usr/bin/env python3
"""
overlook-web — Web interface voor OverMesh berichten.
Draait standalone op poort 8085.

Gebruik:
  ./overlook_web.py [--port 8085] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "overlook_config.json")

DEFAULT_CONFIG = {
    "data_dir": os.environ.get(
        "OVERMESH_DATA_DIR",
        os.path.join(CONFIG_DIR, "..", "overmesh"),
    ),
    "host": "0.0.0.0",
    "port": 8085,
    "refresh_interval": 3,
    "max_messages": 2000,
    "default_limit": 500,
    "auto_refresh": True,
    "theme_accent": "#4ade80",
    "channel_names": {},
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


CFG = load_config()
DATA_DIR = os.path.abspath(CFG["data_dir"])
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)


def find_dbs():
    dbs = []
    try:
        for f in sorted(os.listdir(DATA_DIR)):
            path = os.path.join(DATA_DIR, f)
            if not f.endswith(".db") or not os.path.isfile(path):
                continue
            if f.startswith("overmesh_msgs_") and f != "overmesh_mc_msgs_":
                dbs.append({"id": f, "name": f, "net": "MT", "path": path})
            elif f.startswith("overmesh_mc_msgs_"):
                dbs.append({"id": f, "name": f, "net": "MC", "path": path})
    except OSError:
        pass
    return dbs


def safe_connect(path):
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM messages LIMIT 1")
        return conn
    except Exception:
        return None


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    global CFG, DATA_DIR
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        changed = False
        for k, v in data.items():
            if k in DEFAULT_CONFIG and v is not None:
                if CFG.get(k) != v:
                    CFG[k] = v
                    changed = True
        if changed:
            dd = CFG.get("data_dir", DATA_DIR)
            new_dir = os.path.abspath(dd)
            os.makedirs(new_dir, exist_ok=True)
            DATA_DIR = new_dir
            CFG["data_dir"] = new_dir
            save_config(CFG)
        return jsonify({"ok": True, "config": {k: CFG.get(k) for k in DEFAULT_CONFIG}})
    return jsonify({k: CFG.get(k) for k in DEFAULT_CONFIG})


@app.route("/api/dbs")
def api_dbs():
    dbs = find_dbs()
    result = []
    for db in dbs:
        conn = safe_connect(db["path"])
        count = 0
        if conn:
            try:
                cur = conn.execute("SELECT COUNT(*) FROM messages")
                count = cur.fetchone()[0]
            except Exception:
                pass
            conn.close()
        result.append({**db, "count": count, "size": os.path.getsize(db["path"])})
    return jsonify(result)


@app.route("/api/channels")
def api_channels():
    db_id = request.args.get("db")
    if not db_id:
        return jsonify([])
    path = os.path.join(DATA_DIR, db_id)
    conn = safe_connect(path)
    if not conn:
        return jsonify([])
    is_mc = db_id.startswith("overmesh_mc_msgs_")
    now = int(time.time())
    if is_mc:
        rows = conn.execute(
            "SELECT channel, subtype, COUNT(*) as cnt, MAX(ts) as last_ts "
            "FROM messages GROUP BY channel, subtype ORDER BY channel"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT channel, is_dm, COUNT(*) as cnt, MAX(ts) as last_ts "
            "FROM messages WHERE is_dm IS NULL OR is_dm=0 GROUP BY channel ORDER BY channel"
        ).fetchall()
    conn.close()
    channels = {}
    for r in rows:
        d = dict(r)
        ch = d["channel"]
        if ch not in channels:
            channels[ch] = {"channel": ch, "cnt": 0, "last_ts": 0}
        channels[ch]["cnt"] += d["cnt"]
        if d.get("last_ts", 0) > channels[ch]["last_ts"]:
            channels[ch]["last_ts"] = d["last_ts"]
    result = sorted(channels.values(), key=lambda x: x["channel"])
    names = CFG.get("channel_names", {})
    for ch in result:
        ch["last_ago"] = _ago(ch["last_ts"], now)
        ch["name"] = names.get(str(ch["channel"]), "")
    return jsonify(result)


def _ago(ts, now=None):
    if not ts:
        return "\u2014"
    if now is None:
        now = int(time.time())
    diff = now - ts
    if diff < 60:
        return "zojuist"
    if diff < 3600:
        return f"{diff // 60}m geleden"
    if diff < 86400:
        return f"{diff // 3600}u geleden"
    return f"{diff // 86400}d geleden"


@app.route("/api/search")
def api_search():
    db_id = request.args.get("db")
    if not db_id:
        return jsonify({"error": "db parameter required"}), 400
    path = os.path.join(DATA_DIR, db_id)
    conn = safe_connect(path)
    if not conn:
        return jsonify({"error": "database not found"}), 404
    is_mc = db_id.startswith("overmesh_mc_msgs_")
    channel = request.args.get("channel", type=int)
    text = request.args.get("text", "").strip()
    dm_filter = request.args.get("dm")
    since = request.args.get("since", type=int)
    until = request.args.get("until", type=int)
    after_id = request.args.get("after_id")
    limit = min(500, request.args.get("limit", 200, type=int))
    parts = ["SELECT * FROM messages WHERE 1=1"]
    params = []
    if channel is not None:
        parts.append("AND channel=?")
        params.append(channel)
    if text:
        parts.append("AND text LIKE ?")
        params.append(f"%{text}%")
    if is_mc:
        if dm_filter == "true":
            parts.append("AND subtype='dm'")
        elif dm_filter == "false":
            parts.append("AND (subtype IS NULL OR subtype!='dm')")
    else:
        if dm_filter == "true":
            parts.append("AND is_dm=1")
        elif dm_filter == "false":
            parts.append("AND (is_dm IS NULL OR is_dm=0)")
    if since:
        parts.append("AND ts >= ?")
        params.append(since)
    if until:
        parts.append("AND ts <= ?")
        params.append(until)
    if after_id:
        parts.append("AND id > ?")
        params.append(after_id)
    parts.append("ORDER BY ts ASC")
    if not after_id:
        parts.append(f"LIMIT {limit + 1}")
    rows = conn.execute(" ".join(parts), params).fetchall()
    conn.close()
    has_more = len(rows) > limit
    if not after_id and has_more:
        rows = rows[:limit]
    result = []
    for r in rows:
        d = dict(r)
        d["ts_str"] = datetime.fromtimestamp(d["ts"]).strftime("%d-%m-%Y %H:%M:%S") if d.get("ts") else "?"
        d["type"] = "dm" if (is_mc and d.get("subtype") == "dm") or (not is_mc and d.get("is_dm")) else "channel"
        result.append(d)
    return jsonify({"messages": result, "has_more": has_more, "channel_names": CFG.get("channel_names", {})})


HTML = r"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OverLook — OverMesh berichten</title>
<style>
  :root {
    --bg: #0f1219;
    --surface: #181c27;
    --surface2: #1e2332;
    --border: #2a2f42;
    --text: #e2e4eb;
    --muted: #7a7f94;
    --accent: #4ade80;
    --mc: #60a5fa;
    --mt: #f59e0b;
    --dm: #c084fc;
    --red: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); font-size: 13px; height: 100vh;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .header {
    padding: 10px 16px; background: var(--surface); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap; flex-shrink: 0;
  }
  .header h1 { font-size: 15px; font-weight: 600; white-space: nowrap; cursor: pointer; }
  .header h1 span { color: var(--accent); }
  select, input, button {
    background: var(--surface2); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 10px; font-size: 12px; outline: none;
  }
  select:focus, input:focus { border-color: var(--accent); }
  button { cursor: pointer; white-space: nowrap; transition: .15s; }
  button:hover { background: var(--border); }
  button.active { background: var(--accent); color: #0a0f1e; border-color: var(--accent); font-weight: 600; }
  .net-mt { color: var(--mt); border-color: var(--mt); }
  .net-mc { color: var(--mc); border-color: var(--mc); }
  .net-mt.active { background: var(--mt); color: #0a0f1e; }
  .net-mc.active { background: var(--mc); color: #0a0f1e; }
  .tabs { display: flex; gap: 0; margin-left: 8px; }
  .tabs .tab {
    padding: 4px 12px; font-size: 11px; cursor: pointer; color: var(--muted);
    border: 1px solid var(--border); border-right: none;
  }
  .tabs .tab:first-child { border-radius: 5px 0 0 5px; }
  .tabs .tab:last-child { border-radius: 0 5px 5px 0; border-right: 1px solid var(--border); }
  .tabs .tab.active { background: var(--accent); color: #0a0f1e; border-color: var(--accent); font-weight: 600; }
  .content { flex: 1; display: flex; min-height: 0; }
  .sidebar {
    width: 260px; flex-shrink: 0; background: var(--surface);
    border-right: 1px solid var(--border); display: flex; flex-direction: column;
  }
  .sidebar-section { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  .sidebar-section h3 { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); margin-bottom: 8px; }
  .sidebar-section label { font-size: 11px; color: var(--muted); display: block; margin-bottom: 4px; }
  .sidebar-section select, .sidebar-section input { width: 100%; }
  .channel-list { flex: 1; overflow-y: auto; padding: 6px; }
  .channel-item {
    padding: 7px 10px; border-radius: 5px; cursor: pointer; display: flex;
    justify-content: space-between; align-items: center; margin-bottom: 2px;
  }
  .channel-item:hover { background: var(--surface2); }
  .channel-item.active { background: var(--surface2); border-left: 3px solid var(--accent); }
  .channel-num { font-weight: 600; font-size: 12px; }
  .channel-count { color: var(--muted); font-size: 11px; }
  .channel-last { font-size: 10px; color: var(--muted); }
  .main-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .toolbar {
    padding: 8px 12px; background: var(--surface); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex-shrink: 0;
  }
  .toolbar .search-wrap { flex: 1; min-width: 160px; position: relative; }
  .toolbar .search-wrap input { width: 100%; padding-left: 28px; }
  .toolbar .search-wrap .icon { position: absolute; left: 8px; top: 50%; transform: translateY(-50%); color: var(--muted); font-size: 12px; pointer-events: none; }
  .messages {
    flex: 1; overflow-y: auto; padding: 8px 12px;
    display: flex; flex-direction: column; gap: 2px;
  }
  .msg {
    padding: 6px 10px; border-radius: 5px; line-height: 1.4;
    border-left: 3px solid var(--border);
  }
  .msg:hover { background: var(--surface); }
  .msg .head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .msg .ts { color: var(--muted); font-size: 10px; font-family: monospace; white-space: nowrap; }
  .msg .from { font-weight: 600; font-size: 12px; }
  .msg .to { color: var(--muted); font-size: 11px; }
  .msg .rssi { color: var(--muted); font-size: 10px; font-family: monospace; }
  .msg .text { margin-top: 1px; font-size: 13px; word-break: break-word; }
  .msg.type-dm { border-left-color: var(--dm); }
  .msg.type-channel { border-left-color: var(--accent); }
  .msg .sent { color: var(--muted); font-size: 10px; }
  .msg .tag { display: inline-block; font-size: 9px; padding: 1px 5px; border-radius: 3px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; }
  .tag-mt { background: rgba(245,158,11,0.15); color: var(--mt); }
  .tag-mc { background: rgba(96,165,250,0.15); color: var(--mc); }
  .sticky-date { font-size: 10px; color: var(--muted); text-align: center; padding: 6px 0 4px; font-weight: 600; }
  .empty { color: var(--muted); text-align: center; padding: 40px; font-size: 13px; }
  .status-bar {
    display: flex; align-items: center; gap: 12px; padding: 4px 12px;
    background: var(--surface); border-top: 1px solid var(--border); font-size: 11px; color: var(--muted); flex-shrink: 0;
  }
  .status-bar .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
  .dot-green { background: var(--accent); }
  .settings-pane { padding: 20px 24px; overflow-y: auto; flex: 1; max-width: 500px; }
  .settings-pane h2 { font-size: 14px; margin-bottom: 16px; }
  .setting-row { margin-bottom: 14px; }
  .setting-row label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .setting-row input, .setting-row select { width: 100%; }
  .setting-row .hint { font-size: 10px; color: var(--muted); margin-top: 3px; }
  .setting-row .save-btn { margin-top: 8px; }
  .toast {
    position: fixed; bottom: 20px; right: 20px; padding: 10px 18px;
    border-radius: 8px; font-size: 13px; z-index: 999;
    animation: fadein .3s;
  }
  .toast-ok { background: var(--accent); color: #0a0f1e; }
  .toast-err { background: var(--red); color: white; }
  @keyframes fadein { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  @media (max-width: 700px) { .sidebar { width: 180px; } }
</style>
</head>
<body>

<div class="header">
  <h1 onclick="switchTab('messages')">Over<span>Look</span></h1>
  <select id="db-select" onchange="onDbChange()"></select>
  <div class="tabs">
    <div class="tab active" id="tab-messages" onclick="switchTab('messages')">Berichten</div>
    <div class="tab" id="tab-settings" onclick="switchTab('settings')">Instellingen</div>
  </div>
  <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
    <button id="tail-btn" class="active" onclick="toggleTail()">&#x25B6; Live</button>
    <button onclick="loadMessages()">&#x21BB;</button>
  </div>
</div>

<div class="content" id="view-messages">
  <div class="sidebar">
    <div class="sidebar-section">
      <label>Filter op kanaal</label>
      <div style="display:flex;gap:4px">
        <input id="channel-min" type="number" min="0" max="255" placeholder="min" style="width:50%" onchange="onChannelRangeChange()">
        <input id="channel-max" type="number" min="0" max="255" placeholder="max" style="width:50%" onchange="onChannelRangeChange()">
      </div>
    </div>
    <div class="sidebar-section" style="display:flex;gap:4px">
      <button id="filter-all" class="active" style="flex:1;font-size:11px" onclick="setFilter('all')">Alles</button>
      <button id="filter-ch" style="flex:1;font-size:11px" onclick="setFilter('channel')">Kanaal</button>
      <button id="filter-dm" style="flex:1;font-size:11px" onclick="setFilter('dm')">DM</button>
    </div>
    <div class="channel-list" id="channel-list"><div class="empty">Selecteer een database</div></div>
  </div>
  <div class="main-panel">
    <div class="toolbar">
      <div class="search-wrap">
        <span class="icon">&#x1F50D;</span>
        <input id="search-text" type="text" placeholder="Zoek in berichten..." onkeydown="if(event.key==='Enter')loadMessages()">
      </div>
      <input id="date-from" type="date" onchange="loadMessages()" title="Vanaf datum">
      <input id="date-to" type="date" onchange="loadMessages()" title="Tot datum">
      <button onclick="clearFilters()">&#x2716; Wis</button>
      <span id="msg-count" style="color:var(--muted);font-size:11px;margin-left:auto"></span>
    </div>
    <div class="messages" id="msg-container">
      <div class="empty">Selecteer een database en kanaal om berichten te bekijken</div>
    </div>
    <div class="status-bar">
      <span id="status-db"></span>
      <span id="status-channel"></span>
      <span id="status-tail" style="display:none"><span class="dot dot-green"></span> Live</span>
    </div>
  </div>
</div>

<div class="content" id="view-settings" style="display:none">
  <div class="settings-pane">
    <h2>Instellingen</h2>
    <div class="setting-row">
      <label for="s-data-dir">OverMesh data map</label>
      <input id="s-data-dir" type="text" placeholder="/pad/naar/overmesh">
      <div class="hint">Waar de overmesh_*.db bestanden staan. Herstart de app na wijziging.</div>
    </div>
    <div class="setting-row">
      <label for="s-refresh">Ververs interval (seconden)</label>
      <input id="s-refresh" type="number" min="1" max="60">
    </div>
    <div class="setting-row">
      <label for="s-max-msg">Max berichten in geheugen</label>
      <input id="s-max-msg" type="number" min="100" max="10000">
    </div>
    <div class="setting-row">
      <label for="s-limit">Standaard laad limiet</label>
      <input id="s-limit" type="number" min="50" max="1000">
    </div>
    <div class="setting-row">
      <label for="s-auto-refresh">Live modus bij opstarten</label>
      <select id="s-auto-refresh">
        <option value="true">Aan</option>
        <option value="false">Uit</option>
      </select>
    </div>
    <div class="setting-row">
      <label for="s-accent">Accent kleur</label>
      <div style="display:flex;gap:8px">
        <input id="s-accent" type="color" style="width:48px;height:34px;padding:2px;flex:none">
        <input id="s-accent-hex" type="text" maxlength="7" style="flex:1;font-family:monospace" placeholder="#4ade80">
      </div>
      <div class="hint">Bijvoorbeeld #4ade80 (groen), #60a5fa (blauw), #f59e0b (oranje)</div>
    </div>
    <div style="margin-top:24px;border-top:1px solid var(--border);padding-top:16px">
      <h2>Kanaalnamen</h2>
      <div class="hint" style="margin-bottom:12px">Geef zelf namen aan kanalen. Deze worden lokaal opgeslagen.</div>
      <div id="channel-name-editor"></div>
    </div>
    <div style="margin-top:20px;display:flex;gap:10px">
      <button class="active" style="padding:8px 24px" onclick="saveSettings()">Opslaan</button>
      <button onclick="loadSettings()">Annuleren</button>
    </div>
  </div>
</div>

<script>
let state = {
  dbId: null, channels: [], filter: 'all', selectedChannel: null,
  channelMin: null, channelMax: null, tailing: true, lastId: null,
  loading: false, messages: [], autoRefresh: null, cfg: {}
};

async function init() {
  await loadSettings();
  await loadDbs();
  const saved = localStorage.getItem('overlook_db');
  if (saved) { document.getElementById('db-select').value = saved; await onDbChange(); }
  if (state.cfg.auto_refresh !== false) document.getElementById('tail-btn').click();
}
init();

async function loadSettings() {
  const r = await fetch('/api/settings');
  state.cfg = await r.json();
  document.getElementById('s-data-dir').value = state.cfg.data_dir || '';
  document.getElementById('s-refresh').value = state.cfg.refresh_interval || 3;
  document.getElementById('s-max-msg').value = state.cfg.max_messages || 2000;
  document.getElementById('s-limit').value = state.cfg.default_limit || 500;
  document.getElementById('s-auto-refresh').value = state.cfg.auto_refresh !== false ? 'true' : 'false';
  document.getElementById('s-accent').value = state.cfg.theme_accent || '#4ade80';
  document.getElementById('s-accent-hex').value = state.cfg.theme_accent || '#4ade80';
  setAccent(state.cfg.theme_accent || '#4ade80');
  renderChannelNameEditor(state.cfg.channel_names || {});
}

function renderChannelNameEditor(names) {
  const el = document.getElementById('channel-name-editor');
  const keyOrder = Object.keys(names).sort((a,b)=>parseInt(a)-parseInt(b));
  let html = '';
  if (keyOrder.length) {
    for (const ch of keyOrder) {
      html += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">' +
        '<span style="font-size:12px;width:80px;flex-shrink:0">Kanaal ' + ch + '</span>' +
        '<input class="chname-input" data-ch="' + ch + '" type="text" placeholder="Geen naam" value="' + escAttr(names[ch] || '') + '" style="flex:1">' +
        '<button class="chname-remove" data-ch="' + ch + '" style="padding:3px 8px;font-size:11px;color:var(--red);border-color:var(--red)" onclick="removeChannelName(' + ch + ')">x</button></div>';
    }
  }
  html += '<div style="display:flex;gap:8px;align-items:center;margin-top:8px">' +
    '<span style="font-size:12px;width:80px;flex-shrink:0">Kanaal</span>' +
    '<input id="chname-new-ch" type="number" min="0" max="255" placeholder="0" style="width:70px;flex:none">' +
    '<input id="chname-new-name" type="text" placeholder="Naam" style="flex:1">' +
    '<button style="padding:3px 10px;font-size:11px" onclick="addChannelName()">+ Toevoegen</button></div>';
  el.innerHTML = html;
}

function addChannelName() {
  const ch = document.getElementById('chname-new-ch').value;
  const name = document.getElementById('chname-new-name').value.trim();
  if (!ch || !name) return;
  const names = Object.assign({}, state.cfg.channel_names || {});
  names[ch] = name;
  state.cfg.channel_names = names;
  renderChannelNameEditor(names);
  document.getElementById('chname-new-ch').value = '';
  document.getElementById('chname-new-name').value = '';
}

function removeChannelName(ch) {
  const names = Object.assign({}, state.cfg.channel_names || {});
  delete names[ch];
  state.cfg.channel_names = names;
  renderChannelNameEditor(names);
}

function collectChannelNames() {
  const names = {};
  document.querySelectorAll('.chname-input').forEach(el => {
    const val = el.value.trim();
    if (val) names[el.dataset.ch] = val;
  });
  return names;
}

async function saveSettings() {
  const data = {
    data_dir: document.getElementById('s-data-dir').value.trim(),
    refresh_interval: parseInt(document.getElementById('s-refresh').value) || 3,
    max_messages: parseInt(document.getElementById('s-max-msg').value) || 2000,
    default_limit: parseInt(document.getElementById('s-limit').value) || 500,
    auto_refresh: document.getElementById('s-auto-refresh').value === 'true',
    theme_accent: document.getElementById('s-accent-hex').value.trim() || '#4ade80',
    channel_names: collectChannelNames(),
  };
  const r = await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
  });
  const res = await r.json();
  if (res.ok) {
    state.cfg = res.config;
    setAccent(state.cfg.theme_accent || '#4ade80');
    toast('Instellingen opgeslagen', 'ok');
    if (state.autoRefresh) { clearTimeout(state.autoRefresh); state.autoRefresh = null; }
    if (state.tailing) doTail();
  } else {
    toast('Fout bij opslaan', 'err');
  }
}

function setAccent(hex) {
  document.documentElement.style.setProperty('--accent', hex);
  document.getElementById('s-accent').value = hex;
  document.getElementById('s-accent-hex').value = hex;
}

document.getElementById('s-accent').addEventListener('input', function() {
  setAccent(this.value);
});
document.getElementById('s-accent-hex').addEventListener('input', function() {
  if (/^#[0-9a-f]{6}$/i.test(this.value)) setAccent(this.value);
});

function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast toast-' + (type || 'ok');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

function switchTab(tab) {
  for (const t of ['messages', 'settings']) {
    document.getElementById('tab-' + t).className = 'tab' + (t === tab ? ' active' : '');
    document.getElementById('view-' + t).style.display = t === tab ? 'flex' : 'none';
  }
}

async function loadDbs() {
  const r = await fetch('/api/dbs');
  const dbs = await r.json();
  const sel = document.getElementById('db-select');
  sel.innerHTML = dbs.map(d =>
    '<option value="' + escAttr(d.id) + '">[' + d.net + '] ' +
    esc(d.name.replace(/^overmesh_(mc_)?msgs_/, '').replace(/\.db$/, '')) +
    ' \u2014 ' + d.count + ' berichten</option>'
  ).join('');
}

async function onDbChange() {
  state.dbId = document.getElementById('db-select').value;
  localStorage.setItem('overlook_db', state.dbId);
  state.channels = []; state.selectedChannel = null; state.lastId = null; state.messages = [];
  state.channelMin = null; state.channelMax = null;
  document.getElementById('channel-min').value = ''; document.getElementById('channel-max').value = '';
  document.getElementById('search-text').value = ''; document.getElementById('date-from').value = ''; document.getElementById('date-to').value = '';
  if (!state.dbId) return;
  await loadChannels(); await loadMessages();
}

async function loadChannels() {
  if (!state.dbId) return;
  const r = await fetch('/api/channels?db=' + encodeURIComponent(state.dbId));
  state.channels = await r.json();
  renderChannels();
}

function renderChannels() {
  const el = document.getElementById('channel-list');
  let html = '<div style="padding:4px 0;font-size:10px;color:var(--muted)">Kanalen — <span style="cursor:pointer;color:var(--accent)" onclick="switchTab(\'settings\')">bewerk namen</span></div>';
  for (const ch of state.channels) {
    const active = ch.channel === state.selectedChannel ? 'active' : '';
    const chName = ch.name || '';
    html += '<div class="channel-item ' + active + '" onclick="selectChannel(' + ch.channel + ')">' +
      '<div><span class="channel-num">CH' + ch.channel + '</span>' +
      (chName ? '<span style="font-size:11px;color:var(--muted);margin-left:4px">' + esc(chName) + '</span>' : '') +
      '</div>' +
      '<div style="text-align:right"><div class="channel-count">' + ch.cnt + ' berichten</div>' +
      '<div class="channel-last">' + ch.last_ago + '</div></div></div>';
  }
  if (!state.channels.length) html = '<div class="empty">Geen kanalen gevonden</div>';
  el.innerHTML = html;
}

function selectChannel(ch) {
  state.selectedChannel = state.selectedChannel === ch ? null : ch;
  state.lastId = null; state.messages = []; loadMessages();
}

function onChannelRangeChange() {
  const min = document.getElementById('channel-min').value;
  const max = document.getElementById('channel-max').value;
  state.channelMin = min ? parseInt(min) : null;
  state.channelMax = max ? parseInt(max) : null;
  state.lastId = null; state.messages = []; loadMessages();
}

function setFilter(f) {
  state.filter = f;
  for (const id of ['filter-all', 'filter-ch', 'filter-dm'])
    document.getElementById(id).className = id.endsWith(f) ? 'active' : '';
  state.lastId = null; state.messages = []; loadMessages();
}

function clearFilters() {
  document.getElementById('search-text').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  state.lastId = null; state.messages = []; loadMessages();
}

function toggleTail() {
  state.tailing = !state.tailing;
  const btn = document.getElementById('tail-btn');
  btn.className = state.tailing ? 'active' : '';
  btn.innerHTML = state.tailing ? '\u25B6 Live' : '\u25A0 Pauze';
  document.getElementById('status-tail').style.display = state.tailing ? '' : 'none';
  if (state.tailing) { state.lastId = null; doTail(); }
  else if (state.autoRefresh) { clearTimeout(state.autoRefresh); state.autoRefresh = null; }
}

async function doTail() {
  if (!state.tailing || !state.dbId) return;
  await loadMessages();
  const interval = (state.cfg.refresh_interval || 3) * 1000;
  state.autoRefresh = setTimeout(doTail, interval);
}

async function loadMessages() {
  if (!state.dbId || state.loading) return;
  state.loading = true;
  const limit = state.cfg.default_limit || 500;
  const params = new URLSearchParams({ db: state.dbId, limit: limit });
  if (state.selectedChannel !== null) params.set('channel', state.selectedChannel);
  const text = document.getElementById('search-text').value.trim();
  if (text) params.set('text', text);
  const from = document.getElementById('date-from').value;
  const to = document.getElementById('date-to').value;
  if (from) params.set('since', Math.floor(new Date(from + 'T00:00:00').getTime() / 1000));
  if (to) params.set('until', Math.floor(new Date(to + 'T23:59:59').getTime() / 1000));
  if (state.filter === 'dm') params.set('dm', 'true');
  else if (state.filter === 'channel') params.set('dm', 'false');
  if (state.tailing && state.lastId && state.messages.length > 0) {
    params.set('after_id', state.lastId);
    params.set('limit', '100');
  }
  try {
    const r = await fetch('/api/search?' + params.toString());
    const data = await r.json();
    if (!data.messages) return;
    if (state.tailing && state.lastId) {
      if (!data.messages.length) { state.loading = false; return; }
      const filtered = filterChannelRange(data.messages);
      state.messages = state.messages.concat(filtered);
      if (filtered.length) state.lastId = filtered[filtered.length - 1].id;
    } else {
      state.messages = filterChannelRange(data.messages);
      if (state.messages.length) state.lastId = state.messages[state.messages.length - 1].id;
    }
    const maxMsg = state.cfg.max_messages || 2000;
    if (state.messages.length > maxMsg) state.messages = state.messages.slice(-Math.round(maxMsg / 2));
    renderMessages();
    updateStatus();
  } catch (e) { console.error(e); }
  state.loading = false;
}

function filterChannelRange(msgs) {
  if (state.channelMin === null && state.channelMax === null) return msgs;
  return msgs.filter(m => {
    if (state.channelMin !== null && m.channel < state.channelMin) return false;
    if (state.channelMax !== null && m.channel > state.channelMax) return false;
    return true;
  });
}

function renderMessages() {
  const el = document.getElementById('msg-container');
  if (!state.messages.length) {
    el.innerHTML = '<div class="empty">Geen berichten gevonden</div>';
    document.getElementById('msg-count').textContent = '0 berichten'; return;
  }
  const isMc = state.dbId && state.dbId.startsWith('overmesh_mc_msgs_');
  let lastDate = '', html = '', chCounts = {};
  for (const m of state.messages) {
    const ch = m.channel;
    chCounts[ch] = (chCounts[ch] || 0) + 1;
    const mDate = m.ts_str ? m.ts_str.split(' ')[0] : '';
    if (mDate && mDate !== lastDate) { lastDate = mDate; html += '<div class="sticky-date">\u2014 ' + mDate + ' \u2014</div>'; }
    const chName = (state.cfg.channel_names || {})[String(ch)] || '';
    const chLabel = chName ? 'CH' + ch + ' ' + esc(chName) : 'CH' + ch;
    const netTag = isMc ? '<span class="tag tag-mc">' + chLabel + '</span>' : '<span class="tag tag-mt">' + chLabel + '</span>';
    const typeClass = m.type === 'dm' ? 'type-dm' : 'type-channel';
    const from = m.from_name || m.from_id || '?';
    const to = m.to_name || m.to_id || '';
    const rssi = m.rx_rssi != null ? m.rx_rssi.toFixed(0) + ' dBm' : '';
    const sent = m.sent ? ' &nbsp;\u{1F4E4}' : '';
    const txt = m.text || '';
    html += '<div class="msg ' + typeClass + '"><div class="head">' +
      '<span class="ts">' + esc(m.ts_str || '') + '</span>' + netTag +
      '<span class="from">' + esc(from) + '</span>' +
      (to ? '<span class="to">\u2192 ' + esc(to) + '</span>' : '') +
      (rssi ? '<span class="rssi">' + rssi + '</span>' : '') +
      (sent ? '<span class="sent">verzonden</span>' : '') +
      '</div><div class="text">' + esc(txt) + '</div></div>';
  }
  el.innerHTML = html;
  const total = state.messages.length;
  const chNames = state.cfg.channel_names || {};
  const chSummary = Object.entries(chCounts).sort((a,b)=>a[0]-b[0]).map(([ch,cnt])=>{
    const n = chNames[ch] || '';
    return 'CH' + ch + (n ? ' ' + n : '') + ': ' + cnt;
  }).join(', ');
  document.getElementById('msg-count').textContent = total + ' berichten (' + chSummary + ')';
  if (state.tailing) el.scrollTop = el.scrollHeight;
}

function updateStatus() {
  const sel = document.getElementById('db-select');
  document.getElementById('status-db').textContent = sel.options[sel.selectedIndex]?.text || '';
  document.getElementById('status-channel').textContent = state.selectedChannel !== null ? 'Kanaal ' + state.selectedChannel : 'Alle kanalen';
}

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s) { if (!s) return ''; return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


def main():
    parser = argparse.ArgumentParser(description="overlook-web \u2014 Web UI voor OverMesh berichten")
    parser.add_argument("--port", type=int, default=None, help="Poort (default: uit config)")
    parser.add_argument("--host", default=None, help="Host (default: uit config)")
    args = parser.parse_args()
    host = args.host or CFG.get("host", "0.0.0.0")
    port = args.port or CFG.get("port", 8085)
    print(f"OverLook draait op http://{host}:{port}")
    print(f"Databases uit: {DATA_DIR}")
    print(f"Configuratie: {CONFIG_PATH}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
