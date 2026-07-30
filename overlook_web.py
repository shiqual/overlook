#!/usr/bin/env python3
"""
overlook-web — Web interface voor OverMesh berichten.
Draait standalone op poort 8085.

Gebruik:
  ./overlook-web.py [--port 8085] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

DATA_DIR = os.environ.get(
    "OVERMESH_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "overmesh"),
)
DATA_DIR = os.path.abspath(DATA_DIR)

app = Flask(__name__)


def find_dbs():
    dbs = []
    for f in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, f)
        if not f.endswith(".db") or not os.path.isfile(path):
            continue
        if f.startswith("overmesh_msgs_") and f != "overmesh_mc_msgs_":
            dbs.append({"id": f, "name": f, "net": "MT", "path": path})
        elif f.startswith("overmesh_mc_msgs_"):
            dbs.append({"id": f, "name": f, "net": "MC", "path": path})
    return dbs


def safe_connect(path):
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM messages LIMIT 1")
        return conn
    except Exception:
        return None


@app.route("/api/dbs")
def api_dbs():
    dbs = find_dbs()
    result = []
    for db in dbs:
        conn = safe_connect(db["path"])
        if conn:
            try:
                cur = conn.execute("SELECT COUNT(*) FROM messages")
                count = cur.fetchone()[0]
            except Exception:
                count = 0
            conn.close()
        else:
            count = 0
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
    for ch in result:
        ch["last_ago"] = _ago(ch["last_ts"], now)
    return jsonify(result)


def _ago(ts, now=None):
    if not ts:
        return "—"
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
    dm_filter = request.args.get("dm")  # "true", "false", or None
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

    q = " ".join(parts)
    rows = conn.execute(q, params).fetchall()
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

    return jsonify({"messages": result, "has_more": has_more})


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
  .header h1 { font-size: 15px; font-weight: 600; white-space: nowrap; }
  .header h1 span { color: var(--accent); }
  select, input, button {
    background: var(--surface2); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 10px; font-size: 12px; outline: none;
  }
  select:focus, input:focus { border-color: var(--accent); }
  button { cursor: pointer; white-space: nowrap; }
  button:hover { background: var(--border); }
  button.active { background: var(--accent); color: #0a0f1e; border-color: var(--accent); font-weight: 600; }
  .net-mt { color: var(--mt); border-color: var(--mt); }
  .net-mc { color: var(--mc); border-color: var(--mc); }
  .net-mt.active { background: var(--mt); color: #0a0f1e; }
  .net-mc.active { background: var(--mc); color: #0a0f1e; }

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
  .msg .tag {
    display: inline-block; font-size: 9px; padding: 1px 5px; border-radius: 3px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;
  }
  .tag-mt { background: rgba(245,158,11,0.15); color: var(--mt); }
  .tag-mc { background: rgba(96,165,250,0.15); color: var(--mc); }

  .msg-row { display: flex; gap: 8px; align-items: center; }
  .msg-row .text { flex: 1; }
  .sticky-date { font-size: 10px; color: var(--muted); text-align: center; padding: 6px 0 4px; font-weight: 600; }
  .empty { color: var(--muted); text-align: center; padding: 40px; font-size: 13px; }

  .db-info { font-size: 11px; color: var(--muted); padding: 8px 12px; border-top: 1px solid var(--border); background: var(--surface); flex-shrink: 0; }
  .badge { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: 600; }
  .badge-mt { background: rgba(245,158,11,0.2); color: var(--mt); }
  .badge-mc { background: rgba(96,165,250,0.2); color: var(--mc); }

  .status-bar {
    display: flex; align-items: center; gap: 12px; padding: 4px 12px;
    background: var(--surface); border-top: 1px solid var(--border); font-size: 11px; color: var(--muted); flex-shrink: 0;
  }
  .status-bar .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
  .dot-green { background: var(--accent); }
  .dot-gray { background: var(--border); }
  .tail-indicator { color: var(--accent); font-weight: 600; }

  @media (max-width: 700px) {
    .sidebar { width: 180px; }
    .header h1 { font-size: 13px; }
  }
</style>
</head>
<body>
<div class="header">
  <h1>Over<span>Look</span></h1>
  <select id="db-select" onchange="onDbChange()"></select>
  <span id="db-info" style="font-size:11px;color:var(--muted)"></span>
  <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
    <button id="tail-btn" class="active" onclick="toggleTail()" title="Nieuwe berichten automatisch laden">&#x25B6; Live</button>
    <button onclick="loadMessages()" title="Verversen">&#x21BB;</button>
  </div>
</div>

<div class="content">
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
    <div class="channel-list" id="channel-list">
      <div class="empty">Selecteer een database</div>
    </div>
  </div>

  <div class="main-panel">
    <div class="toolbar">
      <div class="search-wrap">
        <span class="icon">&#x1F50D;</span>
        <input id="search-text" type="text" placeholder="Zoek in berichten..." onkeydown="if(event.key==='Enter')loadMessages()">
      </div>
      <input id="date-from" type="date" onchange="loadMessages()" title="Vanaf datum">
      <input id="date-to" type="date" onchange="loadMessages()" title="Tot datum">
      <button onclick="clearFilters()" title="Wis filters">&#x2716; Wis</button>
      <span id="msg-count" style="color:var(--muted);font-size:11px;margin-left:auto"></span>
    </div>

    <div class="messages" id="msg-container">
      <div class="empty">Selecteer een database en kanaal om berichten te bekijken</div>
    </div>

    <div class="status-bar">
      <span id="status-db"></span>
      <span id="status-channel"></span>
      <span id="status-tail" style="display:none"><span class="dot dot-green"></span> Live — nieuwe berichten worden automatisch geladen</span>
      <span id="status-scroll" style="margin-left:auto;display:none;cursor:pointer;color:var(--accent)" onclick="scrollToBottom()">&#x2193; Naar onder</span>
    </div>
  </div>
</div>

<script>
// --- State ---
let state = {
  dbId: null,
  channels: [],
  filter: 'all',       // 'all', 'channel', 'dm'
  selectedChannel: null,
  channelMin: null,
  channelMax: null,
  tailing: true,
  lastId: null,
  loading: false,
  messages: [],
  autoRefresh: null,
};

// --- Init ---
async function init() {
  await loadDbs();
  const saved = localStorage.getItem('overlook_db');
  if (saved) {
    document.getElementById('db-select').value = saved;
    await onDbChange();
  }
  document.getElementById('tail-btn').click();
}
init();

// --- DB ---
async function loadDbs() {
  const r = await fetch('/api/dbs');
  const dbs = await r.json();
  const sel = document.getElementById('db-select');
  sel.innerHTML = dbs.map(d =>
    `<option value="${d.id}">[${d.net}] ${d.name.replace(/^overmesh_(mc_)?msgs_/, '').replace(/\.db$/, '')} — ${d.count} berichten</option>`
  ).join('');
}

async function onDbChange() {
  state.dbId = document.getElementById('db-select').value;
  localStorage.setItem('overlook_db', state.dbId);
  state.channels = [];
  state.selectedChannel = null;
  state.lastId = null;
  state.messages = [];
  state.channelMin = null;
  state.channelMax = null;
  document.getElementById('channel-min').value = '';
  document.getElementById('channel-max').value = '';
  document.getElementById('search-text').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';

  if (!state.dbId) return;
  await loadChannels();
  await loadMessages();
}

// --- Channels ---
async function loadChannels() {
  if (!state.dbId) return;
  const r = await fetch(`/api/channels?db=${encodeURIComponent(state.dbId)}`);
  state.channels = await r.json();
  renderChannels();
}

function renderChannels() {
  const el = document.getElementById('channel-list');
  let html = '<div style="padding:4px 0;font-size:10px;color:var(--muted)">Kanalen</div>';
  for (const ch of state.channels) {
    const active = ch.channel === state.selectedChannel ? 'active' : '';
    html += `<div class="channel-item ${active}" onclick="selectChannel(${ch.channel})">
      <div><span class="channel-num">Kanaal ${ch.channel}</span></div>
      <div style="text-align:right">
        <div class="channel-count">${ch.cnt} berichten</div>
        <div class="channel-last">${ch.last_ago}</div>
      </div>
    </div>`;
  }
  if (!state.channels.length) html = '<div class="empty">Geen kanalen gevonden</div>';
  el.innerHTML = html;
}

function selectChannel(ch) {
  state.selectedChannel = state.selectedChannel === ch ? null : ch;
  state.lastId = null;
  state.messages = [];
  loadMessages();
}

function onChannelRangeChange() {
  const min = document.getElementById('channel-min').value;
  const max = document.getElementById('channel-max').value;
  state.channelMin = min ? parseInt(min) : null;
  state.channelMax = max ? parseInt(max) : null;
  state.lastId = null;
  state.messages = [];
  loadMessages();
}

// --- Filter ---
function setFilter(f) {
  state.filter = f;
  for (const id of ['filter-all', 'filter-ch', 'filter-dm']) {
    document.getElementById(id).className = id.endsWith(f) ? 'active' : '';
  }
  state.lastId = null;
  state.messages = [];
  loadMessages();
}

function clearFilters() {
  document.getElementById('search-text').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  state.lastId = null;
  state.messages = [];
  loadMessages();
}

// --- Tail ---
function toggleTail() {
  state.tailing = !state.tailing;
  const btn = document.getElementById('tail-btn');
  btn.className = state.tailing ? 'active' : '';
  btn.innerHTML = state.tailing ? '&#x25B6; Live' : '&#x25A0; Pauze';
  document.getElementById('status-tail').style.display = state.tailing ? '' : 'none';
  if (state.tailing) {
    state.lastId = null;
    doTail();
  } else if (state.autoRefresh) {
    clearTimeout(state.autoRefresh);
    state.autoRefresh = null;
  }
}

async function doTail() {
  if (!state.tailing || !state.dbId) return;
  await loadMessages();
  state.autoRefresh = setTimeout(doTail, 3000);
}

// --- Messages ---
async function loadMessages() {
  if (!state.dbId || state.loading) return;
  state.loading = true;

  const isMc = state.dbId.startsWith('overmesh_mc_msgs_');
  const params = new URLSearchParams({ db: state.dbId, limit: '500' });

  if (state.selectedChannel !== null) {
    params.set('channel', state.selectedChannel);
  } else if (state.channelMin !== null) {
    // We'll handle range client-side
  }

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
    const r = await fetch(`/api/search?${params}`);
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

    if (state.tailing && state.messages.length > 2000) {
      state.messages = state.messages.slice(-1000);
    }

    renderMessages(isMc);
    updateStatus();
  } catch (e) {
    console.error(e);
  }
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

function renderMessages(isMc) {
  const el = document.getElementById('msg-container');
  if (!state.messages.length) {
    el.innerHTML = '<div class="empty">Geen berichten gevonden</div>';
    document.getElementById('msg-count').textContent = '0 berichten';
    return;
  }

  let lastDate = '';
  let html = '';
  let chCounts = {};

  for (const m of state.messages) {
    const ch = m.channel;
    chCounts[ch] = (chCounts[ch] || 0) + 1;

    const mDate = m.ts_str ? m.ts_str.split(' ')[0] : '';
    if (mDate && mDate !== lastDate) {
      lastDate = mDate;
      html += `<div class="sticky-date">— ${mDate} —</div>`;
    }

    const netTag = isMc ? '<span class="tag tag-mc">MC</span>' : '<span class="tag tag-mt">MT</span>';
    const typeClass = m.type === 'dm' ? 'type-dm' : 'type-channel';
    const from = m.from_name || m.from_id || '?';
    const to = m.to_name || m.to_id || '';
    const rssi = m.rx_rssi != null ? `${m.rx_rssi.toFixed(0)} dBm` : '';
    const sent = m.sent ? '&nbsp;&#x1F4E4;' : '';
    const txt = m.text || '';

    html += `<div class="msg ${typeClass}">
      <div class="head">
        <span class="ts">${m.ts_str || ''}</span>
        ${netTag}
        <span class="from">${esc(from)}</span>
        ${to ? `<span class="to">→ ${esc(to)}</span>` : ''}
        ${rssi ? `<span class="rssi">${rssi}</span>` : ''}
        ${sent ? `<span class="sent">verzonden</span>` : ''}
      </div>
      <div class="text">${esc(txt)}</div>
    </div>`;
  }

  el.innerHTML = html;

  const total = state.messages.length;
  const chSummary = Object.entries(chCounts)
    .sort((a, b) => a[0] - b[0])
    .map(([ch, cnt]) => `CH${ch}: ${cnt}`).join(', ');
  document.getElementById('msg-count').textContent = `${total} berichten (${chSummary})`;

  if (state.tailing) {
    scrollToBottom();
  }
}

function scrollToBottom() {
  const el = document.getElementById('msg-container');
  el.scrollTop = el.scrollHeight;
}

function updateStatus() {
  const sel = document.getElementById('db-select');
  const dbName = sel.options[sel.selectedIndex]?.text || '';
  document.getElementById('status-db').textContent = dbName;
  document.getElementById('status-channel').textContent = state.selectedChannel !== null
    ? `Kanaal ${state.selectedChannel}` : 'Alle kanalen';
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)


def main():
    parser = argparse.ArgumentParser(description="overlook-web — Web UI voor OverMesh berichten")
    parser.add_argument("--port", type=int, default=8085, help="Poort (default: 8085)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()
    print(f"OverLook draait op http://{args.host}:{args.port}")
    print(f"Databases uit: {DATA_DIR}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
