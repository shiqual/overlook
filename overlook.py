#!/usr/bin/env python3
"""
overlook — standalone OverMesh berichten-viewer en zoektool.

Leest direct de SQLite databases van OverMesh om berichten per kanaal
te bekijken, doorzoeken en te volgen (tail -f).

Gebruik:
  ./overlook.py list                    # Toon beschikbare databases/radios
  ./overlook.py channels <db>           # Toon kanalen in een database
  ./overlook.py search <db>             # Interactive zoekopdracht
  ./overlook.py search <db> --channel 0 --text "hallo" --tail
  ./overlook.py search <db> --channel 1 --dm --limit 50
  ./overlook.py search <db> --from "2026-07-01" --to "2026-07-30"
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime


DATA_DIR = os.environ.get("OVERMESH_DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/overmesh")


def find_dbs():
    dbs = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.startswith("overmesh_msgs_") and f.endswith(".db") and f != "overmesh_mc_msgs_":
            dbs.append(("MT", os.path.join(DATA_DIR, f), f))
        elif f.startswith("overmesh_mc_msgs_") and f.endswith(".db"):
            dbs.append(("MC", os.path.join(DATA_DIR, f), f))
    return dbs


def list_dbs():
    dbs = find_dbs()
    if not dbs:
        print(f"Geen OverMesh databases gevonden in {DATA_DIR}")
        print("Tip: zet OVERMESH_DATA_DIR of pas het pad aan bovenaan het script.")
        return
    for net, path, name in dbs:
        try:
            size = os.path.getsize(path)
            conn = sqlite3.connect(path)
            cur = conn.execute("SELECT COUNT(*) FROM messages")
            count = cur.fetchone()[0]
            conn.close()
            print(f"[{net}] {name:50s} {count:>5} berichten  {size:>8} bytes")
        except Exception as e:
            print(f"[{net}] {name:50s} FOUT: {e}")


def get_channels(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    name = os.path.basename(path)
    if name.startswith("overmesh_mc_msgs_"):
        rows = conn.execute(
            "SELECT channel, subtype, COUNT(*) as cnt, MAX(ts) as last "
            "FROM messages GROUP BY channel, subtype ORDER BY channel"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT channel, is_dm, COUNT(*) as cnt, MAX(ts) as last "
            "FROM messages GROUP BY channel, is_dm ORDER BY channel"
        ).fetchall()
    conn.close()
    print(f"\nKanalen in {os.path.basename(path)}:")
    for r in rows:
        d = dict(r)
        kind = "DM" if d.get("is_dm") or d.get("subtype") == "dm" else "CH"
        last = datetime.fromtimestamp(d["last"]).strftime("%d-%m %H:%M") if d["last"] else "?"
        print(f"  {kind} {d['channel']:2d}  {d['cnt']:>5} berichten  laatste: {last}")


def search_messages(path, channel=None, text=None, dm=None, limit=50, since=None, until=None, tail=False, interval=2):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    name = os.path.basename(path)
    is_mc = name.startswith("overmesh_mc_msgs_")

    def _fetch(max_id=None):
        parts = ["SELECT * FROM messages WHERE 1=1"]
        params = []
        if channel is not None:
            parts.append("AND channel=?")
            params.append(int(channel))
        if is_mc:
            if dm is True:
                parts.append("AND subtype='dm'")
            elif dm is False:
                parts.append("AND subtype!='dm'")
        else:
            if dm is True:
                parts.append("AND is_dm=1")
            elif dm is False:
                parts.append("AND (is_dm IS NULL OR is_dm=0)")
        if text:
            parts.append("AND text LIKE ?")
            params.append(f"%{text}%")
        if since:
            parts.append("AND ts >= ?")
            params.append(int(since))
        if until:
            parts.append("AND ts <= ?")
            params.append(int(until))
        if max_id is not None:
            parts.append("AND id > ?")
            params.append(max_id)
        parts.append("ORDER BY ts ASC")
        if max_id is None:
            parts.append(f"LIMIT {int(limit)}")
        q = " ".join(parts)
        return conn.execute(q, params).fetchall()

    rows = _fetch()
    if not tail:
        _print_rows(rows, is_mc)
        print(f"\n--- {len(rows)} berichten getoond ---")
        if len(rows) == limit:
            print("(beperkt tot LIMIT; gebruik --limit om aan te passen)")
        conn.close()
        return

    last_id = rows[-1]["id"] if rows else "0"
    _print_rows(rows, is_mc)
    try:
        while True:
            time.sleep(interval)
            new = _fetch(max_id=last_id)
            if new:
                _print_rows(new, is_mc)
                last_id = new[-1]["id"]
    except KeyboardInterrupt:
        print("\n--- gestopt ---")
    finally:
        conn.close()


def _print_rows(rows, is_mc):
    for r in rows:
        d = dict(r)
        ts = datetime.fromtimestamp(d["ts"]).strftime("%d-%m %H:%M:%S")
        ch = d["channel"]
        kind = "DM" if (d.get("is_dm") or d.get("subtype") == "dm") else "CH"
        frm = d.get("from_name") or d.get("from_id") or "?"
        to = d.get("to_name") or d.get("to_id") or ""
        txt = (d.get("text") or "")[:120]
        radio = d.get("radio_id") or d.get("radio_name") or ""
        rssi = ""
        if is_mc and d.get("rx_rssi") is not None:
            rssi = f" {d['rx_rssi']:+.0f}dBm"
        sent = ">>>" if d.get("sent") else ""
        print(f"{ts} [{net_label(d, is_mc)}{ch}] {kind:2s} {sent} {frm:20s} → {to:20s} {txt}{rssi}")


def net_label(d, is_mc):
    if is_mc:
        return "MC"
    return "MT"


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.timestamp())
        except ValueError:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Overlook — OverMesh berichten-viewer")
    parser.add_argument("action", nargs="?", default="list",
                        choices=["list", "channels", "search"],
                        help="list: toon databases | channels: toon kanalen | search: zoek berichten")
    parser.add_argument("db", nargs="?", help="Database bestandspad (of kies bij list)")

    parser.add_argument("--channel", "-c", type=int, help="Filter op kanaalnummer")
    parser.add_argument("--text", "-t", help="Filter op tekst (LIKE)")
    parser.add_argument("--dm", action="store_true", default=None, help="Alleen DMs")
    parser.add_argument("--no-dm", action="store_false", dest="dm", help="Alleen channel berichten")
    parser.add_argument("--limit", "-l", type=int, default=50, help="Max aantal berichten")
    parser.add_argument("--from", dest="from_date", help="Vanaf datum (YYYY-MM-DD of YYYY-MM-DD HH:MM)")
    parser.add_argument("--to", dest="to_date", help="Tot datum")
    parser.add_argument("--tail", "-f", action="store_true", help="Blijf volgen (tail -f)")
    parser.add_argument("--interval", type=int, default=2, help="Poll interval in seconden (tail)")

    args = parser.parse_args()

    if args.action == "list":
        list_dbs()
        return

    dbs = find_dbs()
    if args.action == "channels":
        if args.db:
            get_channels(args.db)
        elif len(dbs) == 1:
            get_channels(dbs[0][1])
        else:
            print("Beschikbare databases:")
            for i, (net, path, name) in enumerate(dbs):
                print(f"  {i}: [{net}] {name}")
            try:
                idx = int(input("Kies nummer: "))
                get_channels(dbs[idx][1])
            except (ValueError, IndexError):
                print("Ongeldige keuze")
        return

    if args.action == "search":
        db_path = args.db
        if not db_path:
            if len(dbs) == 1:
                db_path = dbs[0][1]
            elif not dbs:
                print("Geen databases gevonden.")
                return
            else:
                print("Beschikbare databases:")
                for i, (net, path, name) in enumerate(dbs):
                    print(f"  {i}: [{net}] {name}")
                try:
                    idx = int(input("Kies nummer: "))
                    db_path = dbs[idx][1]
                except (ValueError, IndexError):
                    print("Ongeldige keuze")
                    return

        since = parse_date(args.from_date)
        until = parse_date(args.to_date)

        search_messages(db_path,
                       channel=args.channel,
                       text=args.text,
                       dm=args.dm,
                       limit=args.limit,
                       since=since,
                       until=until,
                       tail=args.tail,
                       interval=args.interval)


if __name__ == "__main__":
    main()
