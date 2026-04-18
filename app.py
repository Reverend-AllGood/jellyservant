import os, json, requests, threading, shutil
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

app = Flask(__name__)
app.secret_key = "jellyservant_secret_2026_clifton"

OUTPUT_BASE = os.getenv("OUTPUT_DIR", "/output")
CONFIG_FILE = os.getenv("CONFIG_FILE", "/config/config.json")
LOG_MAX     = 50
sync_lock   = threading.Lock()

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "server_url":     "",
        "api_key":        "",
        "sync_domain":    "",
        "nx_user":        "",
        "nx_pass":        "",
        "schedules":      [],
        "last_selection": [],   # IDs the user chose on their last manual sync
        "known_ids":      [],   # all IDs that existed at time of first/last sync
        "sync_log":       []
    }

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Jellyfin helpers ──────────────────────────────────────────────────────────

def jf_get(url, key, endpoint, params=None):
    headers = {"X-Emby-Token": key}
    r = requests.get(f"{url}/{endpoint}", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def jf_get_detail(url, key, item_id):
    return jf_get(url, key, f"Items/{item_id}",
                  {"Fields": "Genres,People,Overview,OfficialRating,CommunityRating,ProductionYear"})

def safe_name(s):
    return "".join(c for c in s if c.isalnum() or c in (' ', '.', '_')).strip()

# ── Change detection ──────────────────────────────────────────────────────────

def _needs_update(local_path, jf_date):
    if not os.path.exists(local_path):
        return True
    if not jf_date:
        return False
    try:
        local_mtime = datetime.utcfromtimestamp(os.path.getmtime(local_path))
        jf_dt = datetime.strptime(jf_date[:19], "%Y-%m-%dT%H:%M:%S")
        return jf_dt > local_mtime
    except (ValueError, TypeError):
        return True

# ── NFO writers ───────────────────────────────────────────────────────────────

def write_movie_nfo(path, detail):
    genres = "".join(f"  <genre>{g}</genre>\n" for g in detail.get("Genres", []))
    actors = "".join(
        f"  <actor><name>{p['Name']}</name><role>{p.get('Role','')}</role></actor>\n"
        for p in detail.get("People", []) if p.get("Type") == "Actor"
    )
    nfo = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>{detail.get('Name','')}</title>
  <year>{detail.get('ProductionYear','')}</year>
  <plot>{detail.get('Overview','')}</plot>
  <mpaa>{detail.get('OfficialRating','')}</mpaa>
  <rating>{detail.get('CommunityRating','')}</rating>
{genres}{actors}</movie>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(nfo)

def write_tvshow_nfo(path, detail):
    genres = "".join(f"  <genre>{g}</genre>\n" for g in detail.get("Genres", []))
    actors = "".join(
        f"  <actor><name>{p['Name']}</name><role>{p.get('Role','')}</role></actor>\n"
        for p in detail.get("People", []) if p.get("Type") == "Actor"
    )
    nfo = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tvshow>
  <title>{detail.get('Name','')}</title>
  <year>{detail.get('ProductionYear','')}</year>
  <plot>{detail.get('Overview','')}</plot>
  <mpaa>{detail.get('OfficialRating','')}</mpaa>
  <rating>{detail.get('CommunityRating','')}</rating>
{genres}{actors}</tvshow>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(nfo)

def save_poster(item_id, folder, url, key, jf_date=None):
    dest = os.path.join(folder, "poster.jpg")
    if os.path.exists(dest) and jf_date:
        try:
            local_mtime = datetime.utcfromtimestamp(os.path.getmtime(dest))
            jf_dt = datetime.strptime(jf_date[:19], "%Y-%m-%dT%H:%M:%S")
            if local_mtime >= jf_dt:
                return
        except (ValueError, TypeError):
            pass
    try:
        r = requests.get(f"{url}/Items/{item_id}/Images/Primary",
                         headers={"X-Emby-Token": key}, timeout=10)
        if r.status_code == 200:
            with open(dest, "wb") as f:
                f.write(r.content)
    except:
        pass

# ── Orphan removal ────────────────────────────────────────────────────────────

def remove_orphans(server_url, api_key, live_movie_ids, live_series_ids, live_episode_ids):
    """
    Walk the output folder. For every .strm file found, extract the Jellyfin
    item ID from its URL. If that ID is no longer in the live library, delete
    the entire containing folder (strm + nfo + poster).
    Returns count of folders removed.
    """
    removed = 0
    all_live = live_movie_ids | live_series_ids | live_episode_ids

    for root, dirs, files in os.walk(OUTPUT_BASE, topdown=False):
        for fname in files:
            if not fname.endswith(".strm"):
                continue
            strm_path = os.path.join(root, fname)
            try:
                content = open(strm_path).read().strip()
                # Extract ID from URL pattern /Videos/<ID>/stream
                parts = content.split("/Videos/")
                if len(parts) < 2:
                    continue
                item_id = parts[1].split("/")[0]
            except Exception:
                continue

            if item_id not in all_live:
                # Delete the entire folder this .strm lives in
                folder_to_delete = root
                if os.path.exists(folder_to_delete):
                    shutil.rmtree(folder_to_delete, ignore_errors=True)
                    removed += 1
                break  # folder is gone, stop iterating its files

    # Clean up any empty parent directories
    for top in ["Movies", "TV Shows"]:
        top_path = os.path.join(OUTPUT_BASE, top)
        if not os.path.exists(top_path):
            continue
        for entry in os.listdir(top_path):
            entry_path = os.path.join(top_path, entry)
            if os.path.isdir(entry_path) and not os.listdir(entry_path):
                os.rmdir(entry_path)

    return removed

# ── Core sync ─────────────────────────────────────────────────────────────────

def do_sync(server_url, api_key, sync_domain, nx_user, nx_pass,
            selected_ids=None, auto_add_new=False, known_ids=None):
    """
    Sync selected items. Returns (movies_written, shows_written, removed_count, new_ids_added).

    auto_add_new=True  — any ID in the current library that is NOT in known_ids
                         gets added to selected_ids automatically.
    known_ids          — set of IDs seen at time of last selection snapshot.
    """
    movies_data = jf_get(server_url, api_key, "Items",
                         {"Recursive": "true", "IncludeItemTypes": "Movie",
                          "Fields": "ProductionYear,DateModified"})
    series_data = jf_get(server_url, api_key, "Items",
                         {"Recursive": "true", "IncludeItemTypes": "Series",
                          "Fields": "ProductionYear,DateModified"})

    all_movies  = movies_data.get("Items", [])
    all_series  = series_data.get("Items", [])

    live_movie_ids  = {m["Id"] for m in all_movies}
    live_series_ids = {s["Id"] for s in all_series}

    # Build full episode ID set for orphan detection
    live_episode_ids = set()
    for s in all_series:
        try:
            ep_data = jf_get(server_url, api_key, f"Shows/{s['Id']}/Episodes",
                             {"Fields": "DateModified"})
            for ep in ep_data.get("Items", []):
                live_episode_ids.add(ep["Id"])
        except Exception:
            pass

    # ── Auto-add new items ────────────────────────────────────────
    new_ids_added = []
    if auto_add_new and known_ids is not None:
        known_set   = set(known_ids)
        all_live    = live_movie_ids | live_series_ids
        new_in_lib  = all_live - known_set
        if new_in_lib and selected_ids is not None:
            new_ids_added = list(new_in_lib)
            selected_ids  = set(selected_ids) | new_in_lib

    # Apply selection filter
    selected = set(selected_ids) if selected_ids is not None else None
    movies   = [m for m in all_movies  if selected is None or m["Id"] in selected]
    series   = [s for s in all_series  if selected is None or s["Id"] in selected]

    movies_written = 0
    shows_written  = 0

    # ── Movies ───────────────────────────────────────────────────
    for m in movies:
        sn     = safe_name(m["Name"])
        folder = os.path.join(OUTPUT_BASE, "Movies",
                              f"{sn} ({m.get('ProductionYear', '0000')})")
        os.makedirs(folder, exist_ok=True)
        jf_date   = m.get("DateModified")
        strm_path = os.path.join(folder, f"{sn}.strm")
        strm_url  = (f"https://{nx_user}:{nx_pass}@{sync_domain}"
                     f"/Videos/{m['Id']}/stream?static=true")

        if not os.path.exists(strm_path) or open(strm_path).read().strip() != strm_url:
            with open(strm_path, "w") as f:
                f.write(strm_url)
            movies_written += 1

        nfo_path = os.path.join(folder, "movie.nfo")
        if _needs_update(nfo_path, jf_date):
            detail = jf_get_detail(server_url, api_key, m["Id"])
            write_movie_nfo(nfo_path, detail)

        save_poster(m["Id"], folder, server_url, api_key, jf_date)

    # ── TV Shows ─────────────────────────────────────────────────
    for s in series:
        sn       = safe_name(s["Name"])
        s_folder = os.path.join(OUTPUT_BASE, "TV Shows", sn)
        os.makedirs(s_folder, exist_ok=True)
        jf_date  = s.get("DateModified")

        ep_data          = jf_get(server_url, api_key, f"Shows/{s['Id']}/Episodes",
                                  {"Fields": "DateModified"})
        show_had_changes = False

        for ep in ep_data.get("Items", []):
            season_num    = str(ep.get("ParentIndexNumber", 1)).zfill(2)
            ep_num        = str(ep.get("IndexNumber", 1)).zfill(2)
            season_folder = os.path.join(s_folder, f"Season {season_num}")
            os.makedirs(season_folder, exist_ok=True)

            strm_path = os.path.join(season_folder, f"{sn} - S{season_num}E{ep_num}.strm")
            strm_url  = (f"https://{nx_user}:{nx_pass}@{sync_domain}"
                         f"/Videos/{ep['Id']}/stream?static=true")

            if not os.path.exists(strm_path) or open(strm_path).read().strip() != strm_url:
                with open(strm_path, "w") as f:
                    f.write(strm_url)
                show_had_changes = True

        if show_had_changes:
            shows_written += 1

        nfo_path = os.path.join(s_folder, "tvshow.nfo")
        if _needs_update(nfo_path, jf_date):
            detail = jf_get_detail(server_url, api_key, s["Id"])
            write_tvshow_nfo(nfo_path, detail)

        save_poster(s["Id"], s_folder, server_url, api_key, jf_date)

    # ── Orphan removal ───────────────────────────────────────────
    removed = remove_orphans(server_url, api_key,
                             live_movie_ids, live_series_ids, live_episode_ids)

    return movies_written, shows_written, removed, new_ids_added

# ── Scheduled sync ────────────────────────────────────────────────────────────

def run_scheduled_sync(schedule_id):
    with sync_lock:
        cfg   = load_config()
        sched = next((s for s in cfg["schedules"] if s["id"] == schedule_id), None)
        if not sched or not sched.get("enabled"):
            return

        auto_add  = sched.get("auto_add_new", False)
        known_ids = cfg.get("known_ids", [])

        if sched.get("scope") == "full":
            selected = None
        else:
            selected = cfg.get("last_selection") or None

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            m, s, removed, added = do_sync(
                cfg["server_url"], cfg["api_key"], cfg["sync_domain"],
                cfg["nx_user"],   cfg["nx_pass"],
                selected_ids=selected,
                auto_add_new=auto_add,
                known_ids=known_ids
            )
            # Update known_ids and last_selection to include any newly added items
            if added:
                cfg["known_ids"]      = list(set(known_ids) | set(added))
                if cfg.get("last_selection"):
                    cfg["last_selection"] = list(set(cfg["last_selection"]) | set(added))

            entry = {"ts": timestamp, "trigger": sched["label"],
                     "movies": m, "shows": s, "removed": removed,
                     "added": len(added), "error": None}
        except Exception as e:
            entry = {"ts": timestamp, "trigger": sched["label"],
                     "movies": 0, "shows": 0, "removed": 0,
                     "added": 0, "error": str(e)}

        cfg["sync_log"] = ([entry] + cfg.get("sync_log", []))[:LOG_MAX]
        save_config(cfg)

# ── Scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="UTC")
scheduler.start()

def reload_schedules():
    for job in scheduler.get_jobs():
        job.remove()
    cfg = load_config()
    for s in cfg.get("schedules", []):
        if not s.get("enabled"):
            continue
        days = ",".join(s.get("days", ["mon"]))
        scheduler.add_job(
            run_scheduled_sync,
            CronTrigger(day_of_week=days, hour=s["hour"], minute=s["minute"],
                        timezone=s.get("timezone", "UTC")),
            args=[s["id"]],
            id=s["id"],
            replace_existing=True
        )

reload_schedules()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/jellyfin_skeleton_meal.jpg')
def serve_bg():
    return send_from_directory('.', 'jellyfin_skeleton_meal.jpg')

@app.route('/api/config', methods=['GET'])
def api_get_config():
    cfg = load_config()
    cfg.pop("api_key", None)
    cfg.pop("nx_pass", None)
    return jsonify(cfg)

@app.route('/api/config', methods=['POST'])
def api_save_config():
    cfg  = load_config()
    body = request.json
    for field in ("server_url", "api_key", "sync_domain", "nx_user", "nx_pass"):
        if body.get(field):
            cfg[field] = body[field]
    save_config(cfg)
    return jsonify({"ok": True})

@app.route('/api/browse', methods=['POST'])
def api_browse():
    body = request.json
    url  = body.get("server_url", "").rstrip("/")
    key  = body.get("api_key", "")
    try:
        movies = jf_get(url, key, "Items",
                        {"Recursive": "true", "IncludeItemTypes": "Movie",
                         "Fields": "ProductionYear"})
        series = jf_get(url, key, "Items",
                        {"Recursive": "true", "IncludeItemTypes": "Series",
                         "Fields": "ProductionYear"})
        items  = [{"id": m["Id"], "name": m["Name"], "type": "Movie",
                   "year": m.get("ProductionYear", "")}
                  for m in movies.get("Items", [])]
        items += [{"id": s["Id"], "name": s["Name"], "type": "Series",
                   "year": s.get("ProductionYear", "")}
                  for s in series.get("Items", [])]
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def api_sync():
    body      = request.json
    cfg       = load_config()
    selected  = body.get("selected_ids") or None
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        m, s, removed, added = do_sync(
            cfg["server_url"], cfg["api_key"], cfg["sync_domain"],
            cfg["nx_user"],   cfg["nx_pass"],
            selected_ids=selected
        )
        if selected:
            # Snapshot what was selected and what the full library looked like
            cfg["last_selection"] = list(selected)
            # known_ids = everything that existed at browse time (passed from UI)
            known = body.get("known_ids")
            if known:
                cfg["known_ids"] = known

        entry = {"ts": timestamp, "trigger": "Manual",
                 "movies": m, "shows": s, "removed": removed,
                 "added": 0, "error": None}
    except Exception as e:
        entry = {"ts": timestamp, "trigger": "Manual",
                 "movies": 0, "shows": 0, "removed": 0,
                 "added": 0, "error": str(e)}

    cfg["sync_log"] = ([entry] + cfg.get("sync_log", []))[:LOG_MAX]
    save_config(cfg)
    return jsonify(entry)

@app.route('/api/schedules', methods=['GET'])
def api_get_schedules():
    return jsonify(load_config().get("schedules", []))

@app.route('/api/schedules', methods=['POST'])
def api_save_schedule():
    import uuid
    body      = request.json
    cfg       = load_config()
    schedules = cfg.get("schedules", [])
    sid       = body.get("id") or str(uuid.uuid4())[:8]
    sched     = {
        "id":           sid,
        "label":        body.get("label", "Auto Sync"),
        "days":         body.get("days", ["mon"]),
        "hour":         int(body.get("hour", 3)),
        "minute":       int(body.get("minute", 0)),
        "timezone":     body.get("timezone", "UTC"),
        "scope":        body.get("scope", "full"),
        "auto_add_new": body.get("auto_add_new", False),
        "enabled":      body.get("enabled", True)
    }
    cfg["schedules"] = [s for s in schedules if s["id"] != sid]
    cfg["schedules"].append(sched)
    save_config(cfg)
    reload_schedules()
    return jsonify(sched)

@app.route('/api/schedules/<sid>', methods=['DELETE'])
def api_delete_schedule(sid):
    cfg             = load_config()
    cfg["schedules"] = [s for s in cfg.get("schedules", []) if s["id"] != sid]
    save_config(cfg)
    reload_schedules()
    return jsonify({"ok": True})

@app.route('/api/log', methods=['GET'])
def api_log():
    return jsonify(load_config().get("sync_log", []))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
