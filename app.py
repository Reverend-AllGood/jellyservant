import os, json, requests, threading, shutil, uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

app = Flask(__name__)
app.secret_key = "jellyservant_secret_2026_clifton"
VERSION = "1.8.0"

OUTPUT_BASE = os.getenv("OUTPUT_DIR", "/output")
CONFIG_FILE = os.getenv("CONFIG_FILE", "/config/config.json")
LOG_MAX     = 50
sync_lock   = threading.Lock()

# ── Default user slots ────────────────────────────────────────────────────────

DEFAULT_USERS = [
    {"id": "shared", "label": "Shared",  "enabled": True,  "last_selection": [], "known_ids": []},
    {"id": "user2",  "label": "User 2",  "enabled": False, "last_selection": [], "known_ids": []},
    {"id": "user3",  "label": "User 3",  "enabled": False, "last_selection": [], "known_ids": []},
    {"id": "user4",  "label": "User 4",  "enabled": False, "last_selection": [], "known_ids": []},
    {"id": "user5",  "label": "User 5",  "enabled": False, "last_selection": [], "known_ids": []},
]

# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_FILE):
        cfg = json.load(open(CONFIG_FILE))
        # Migrate: add users block if missing (upgrade from pre-1.8)
        if "users" not in cfg:
            cfg["users"] = [dict(u) for u in DEFAULT_USERS]
            # Carry forward any old top-level selection into Shared slot
            cfg["users"][0]["last_selection"] = cfg.pop("last_selection", [])
            cfg["users"][0]["known_ids"]       = cfg.pop("known_ids", [])
        return cfg
    return {
        "server_url":   "",
        "api_key":      "",
        "sync_domain":  "",
        "nx_user":      "",
        "nx_pass":      "",
        "server_name":  "",
        "schedules":    [],
        "sync_log":     [],
        "users":        [dict(u) for u in DEFAULT_USERS],
    }

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_user(cfg, user_id):
    return next((u for u in cfg.get("users", []) if u["id"] == user_id), None)

def clean_domain(url):
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url.rstrip("/")

# ── Jellyfin helpers ──────────────────────────────────────────────────────────

def jf_get(url, key, endpoint, params=None):
    headers = {"X-Emby-Token": key}
    r = requests.get(f"{url}/{endpoint}", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def jf_get_detail(url, key, item_id, fallback=None):
    try:
        return jf_get(url, key, f"Items/{item_id}",
                      {"Fields": "Genres,People,Overview,OfficialRating,CommunityRating,ProductionYear"})
    except Exception:
        pass
    try:
        return jf_get(url, key, f"Items/{item_id}")
    except Exception:
        pass
    return fallback or {}

def jf_server_name(url, key):
    """Fetch the Jellyfin server's friendly name from /System/Info."""
    try:
        info = jf_get(url, key, "System/Info")
        return info.get("ServerName") or info.get("Name") or ""
    except Exception:
        return ""

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

def write_episode_nfo(path, detail, show_name):
    nfo = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<episodedetails>
  <title>{detail.get('Name', '')}</title>
  <showtitle>{show_name}</showtitle>
  <season>{detail.get('ParentIndexNumber', '')}</season>
  <episode>{detail.get('IndexNumber', '')}</episode>
  <plot>{detail.get('Overview', '')}</plot>
  <mpaa>{detail.get('OfficialRating', '')}</mpaa>
  <rating>{detail.get('CommunityRating', '')}</rating>
  <year>{detail.get('ProductionYear', '')}</year>
</episodedetails>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(nfo)

# ── Subtitle downloader ───────────────────────────────────────────────────────

def download_subtitles(server_url, api_key, item_id, dest_folder, base_name):
    try:
        detail  = jf_get(server_url, api_key, f"Items/{item_id}", {"Fields": "MediaStreams"})
        streams = detail.get("MediaStreams", [])
    except Exception:
        return
    for stream in streams:
        if stream.get("Type") != "Subtitle" or stream.get("IsExternal"):
            continue
        index    = stream.get("Index")
        lang     = stream.get("Language") or f"track{index}"
        codec    = (stream.get("Codec") or "srt").lower()
        ext      = "ass" if codec in ("ass", "ssa") else "srt"
        sub_path = os.path.join(dest_folder, f"{base_name}.{lang}.{ext}")
        if os.path.exists(sub_path):
            continue
        try:
            r = requests.get(
                f"{server_url}/Videos/{item_id}/{item_id}/Subtitles/{index}/Stream.{ext}",
                headers={"X-Emby-Token": api_key}, timeout=30)
            if r.status_code == 200:
                with open(sub_path, "wb") as f:
                    f.write(r.content)
        except Exception:
            pass

# ── Poster downloader ─────────────────────────────────────────────────────────

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
    except Exception:
        pass

# ── Orphan removal ────────────────────────────────────────────────────────────

def remove_orphans(output_root, live_movie_ids, live_series_ids, live_episode_ids):
    """
    Walk output_root (a single user slot's folder).
    Delete folders whose .strm item ID is no longer in the live library.
    """
    removed  = 0
    all_live = live_movie_ids | live_series_ids | live_episode_ids

    for root, dirs, files in os.walk(output_root, topdown=False):
        for fname in files:
            if not fname.endswith(".strm"):
                continue
            strm_path = os.path.join(root, fname)
            try:
                content  = open(strm_path).read().strip()
                parts    = content.split("/Videos/")
                if len(parts) < 2:
                    continue
                item_id  = parts[1].split("/")[0]
            except Exception:
                continue
            if item_id not in all_live:
                if os.path.exists(root):
                    shutil.rmtree(root, ignore_errors=True)
                    removed += 1
                break

    # Clean up empty show/movie parent dirs
    for top in ["Movies", "TV Shows"]:
        top_path = os.path.join(output_root, top)
        if not os.path.exists(top_path):
            continue
        for entry in os.listdir(top_path):
            ep = os.path.join(top_path, entry)
            if os.path.isdir(ep) and not os.listdir(ep):
                os.rmdir(ep)

    return removed

# ── Core sync ─────────────────────────────────────────────────────────────────

def do_sync(server_url, api_key, sync_domain, nx_user, nx_pass,
            output_root, selected_ids=None, auto_add_new=False, known_ids=None):
    """
    Sync media for a single user slot into output_root.
    Returns (movies_written, shows_written, removed_count, new_ids_added).
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
        known_set  = set(known_ids)
        new_in_lib = (live_movie_ids | live_series_ids) - known_set
        if new_in_lib and selected_ids is not None:
            new_ids_added = list(new_in_lib)
            selected_ids  = set(selected_ids) | new_in_lib

    selected = set(selected_ids) if selected_ids is not None else None
    movies   = [m for m in all_movies if selected is None or m["Id"] in selected]
    series   = [s for s in all_series if selected is None or s["Id"] in selected]

    movies_written = 0
    shows_written  = 0

    # ── Movies ───────────────────────────────────────────────────
    for m in movies:
        sn     = safe_name(m["Name"])
        folder = os.path.join(output_root, "Movies",
                              f"{sn} ({m.get('ProductionYear', '0000')})")
        os.makedirs(folder, exist_ok=True)
        jf_date   = m.get("DateModified")
        strm_path = os.path.join(folder, f"{sn}.strm")
        strm_url  = (f"https://{nx_user}:{nx_pass}@{clean_domain(sync_domain)}"
                     f"/Videos/{m['Id']}/stream?static=true")

        if not os.path.exists(strm_path) or open(strm_path).read().strip() != strm_url:
            with open(strm_path, "w") as f:
                f.write(strm_url)
            movies_written += 1

        nfo_path = os.path.join(folder, "movie.nfo")
        if _needs_update(nfo_path, jf_date):
            detail = jf_get_detail(server_url, api_key, m["Id"], fallback=m)
            write_movie_nfo(nfo_path, detail)

        save_poster(m["Id"], folder, server_url, api_key, jf_date)
        download_subtitles(server_url, api_key, m["Id"], folder, sn)

    # ── TV Shows ─────────────────────────────────────────────────
    for s in series:
        sn       = safe_name(s["Name"])
        s_folder = os.path.join(output_root, "TV Shows", sn)
        os.makedirs(s_folder, exist_ok=True)
        jf_date  = s.get("DateModified")

        ep_data = jf_get(server_url, api_key, f"Shows/{s['Id']}/Episodes",
                         {"Fields": "DateModified,Name,Overview,OfficialRating,CommunityRating,ProductionYear"})
        show_had_changes = False

        for ep in ep_data.get("Items", []):
            season_num    = str(ep.get("ParentIndexNumber", 1)).zfill(2)
            ep_num        = str(ep.get("IndexNumber", 1)).zfill(2)
            season_folder = os.path.join(s_folder, f"Season {season_num}")
            os.makedirs(season_folder, exist_ok=True)

            base_name = f"{sn} - S{season_num}E{ep_num}"
            strm_path = os.path.join(season_folder, f"{base_name}.strm")
            strm_url  = (f"https://{nx_user}:{nx_pass}@{clean_domain(sync_domain)}"
                         f"/Videos/{ep['Id']}/stream?static=true")

            if not os.path.exists(strm_path) or open(strm_path).read().strip() != strm_url:
                with open(strm_path, "w") as f:
                    f.write(strm_url)
                show_had_changes = True

            ep_nfo_path = os.path.join(season_folder, f"{base_name}.nfo")
            if _needs_update(ep_nfo_path, ep.get("DateModified")):
                write_episode_nfo(ep_nfo_path, ep, s["Name"])

            download_subtitles(server_url, api_key, ep["Id"], season_folder, base_name)

        if show_had_changes:
            shows_written += 1

        nfo_path = os.path.join(s_folder, "tvshow.nfo")
        if _needs_update(nfo_path, jf_date):
            detail = jf_get_detail(server_url, api_key, s["Id"], fallback=s)
            write_tvshow_nfo(nfo_path, detail)

        save_poster(s["Id"], s_folder, server_url, api_key, jf_date)

    # ── Orphan removal ───────────────────────────────────────────
    removed = remove_orphans(output_root, live_movie_ids, live_series_ids, live_episode_ids)

    return movies_written, shows_written, removed, new_ids_added

# ── Output path helper ────────────────────────────────────────────────────────

def user_output_root(server_name, user_label):
    """output/<ServerName>/<UserLabel>/"""
    sn = safe_name(server_name) if server_name else "Server"
    ul = safe_name(user_label)  if user_label  else "Shared"
    return os.path.join(OUTPUT_BASE, sn, ul)

# ── Scheduled sync ────────────────────────────────────────────────────────────

def run_scheduled_sync(schedule_id):
    with sync_lock:
        cfg   = load_config()
        sched = next((s for s in cfg["schedules"] if s["id"] == schedule_id), None)
        if not sched or not sched.get("enabled"):
            return

        user = get_user(cfg, sched.get("user_id", "shared"))
        if not user or not user.get("enabled"):
            return

        auto_add  = sched.get("auto_add_new", False)
        known_ids = user.get("known_ids", [])
        selected  = None if sched.get("scope") == "full" else (user.get("last_selection") or None)

        output_root = user_output_root(cfg.get("server_name", ""), user["label"])
        timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M")

        try:
            m, s, removed, added = do_sync(
                cfg["server_url"], cfg["api_key"], cfg["sync_domain"],
                cfg["nx_user"],   cfg["nx_pass"],
                output_root=output_root,
                selected_ids=selected,
                auto_add_new=auto_add,
                known_ids=known_ids
            )
            if added:
                user["known_ids"]       = list(set(known_ids) | set(added))
                if user.get("last_selection"):
                    user["last_selection"] = list(set(user["last_selection"]) | set(added))

            entry = {"ts": timestamp, "trigger": sched["label"],
                     "user": user["label"],
                     "movies": m, "shows": s, "removed": removed,
                     "added": len(added), "error": None}
        except Exception as e:
            entry = {"ts": timestamp, "trigger": sched["label"],
                     "user": user["label"],
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

# ── Config ────────────────────────────────────────────────────────────────────

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
    # Auto-fetch server name if we have a URL and key and name isn't set yet
    if cfg.get("server_url") and cfg.get("api_key") and not cfg.get("server_name"):
        cfg["server_name"] = jf_server_name(cfg["server_url"], cfg["api_key"])
    save_config(cfg)
    return jsonify({"ok": True, "server_name": cfg.get("server_name", "")})

@app.route('/api/server-name', methods=['POST'])
def api_fetch_server_name():
    """Explicitly re-fetch and store the server name."""
    cfg = load_config()
    url = cfg.get("server_url", "")
    key = cfg.get("api_key", "")
    if not url or not key:
        return jsonify({"error": "Server URL and API key must be configured first."}), 400
    name = jf_server_name(url, key)
    if not name:
        return jsonify({"error": "Could not retrieve server name. Check URL and API key."}), 502
    cfg["server_name"] = name
    save_config(cfg)
    return jsonify({"server_name": name})

# ── Users ─────────────────────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
def api_get_users():
    return jsonify(load_config().get("users", []))

@app.route('/api/users', methods=['POST'])
def api_save_users():
    """
    Accept the full users array from the UI.
    Shared slot label is always locked to 'Shared'; id is always 'shared'.
    """
    cfg   = load_config()
    body  = request.json  # list of {id, label, enabled}
    users = cfg.get("users", [dict(u) for u in DEFAULT_USERS])

    for incoming in body:
        uid  = incoming.get("id")
        slot = next((u for u in users if u["id"] == uid), None)
        if slot is None:
            continue
        if uid == "shared":
            slot["enabled"] = True   # Shared is always on
            slot["label"]   = "Shared"
        else:
            slot["label"]   = incoming.get("label", slot["label"]) or slot["label"]
            slot["enabled"] = bool(incoming.get("enabled", slot["enabled"]))

    cfg["users"] = users
    save_config(cfg)
    return jsonify({"ok": True})

# ── Browse ────────────────────────────────────────────────────────────────────

@app.route('/api/browse', methods=['POST'])
def api_browse():
    cfg = load_config()
    url = cfg.get("server_url", "").rstrip("/")
    key = cfg.get("api_key", "")
    if not url:
        return jsonify({"error": "No server URL configured. Go to the Config tab first."}), 400
    if not key:
        return jsonify({"error": "No API key configured. Go to the Config tab first."}), 400
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

# ── Sync ──────────────────────────────────────────────────────────────────────

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Sync a single user slot."""
    body      = request.json
    cfg       = load_config()
    user_id   = body.get("user_id", "shared")
    user      = get_user(cfg, user_id)
    if not user:
        return jsonify({"error": f"Unknown user slot '{user_id}'"}), 400

    selected  = body.get("selected_ids") or None
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    output_root = user_output_root(cfg.get("server_name", ""), user["label"])

    try:
        m, s, removed, added = do_sync(
            cfg["server_url"], cfg["api_key"], cfg["sync_domain"],
            cfg["nx_user"],   cfg["nx_pass"],
            output_root=output_root,
            selected_ids=selected
        )
        if selected is not None:
            user["last_selection"] = list(selected)
        known = body.get("known_ids")
        if known:
            user["known_ids"] = known

        entry = {"ts": timestamp, "trigger": "Manual",
                 "user": user["label"],
                 "movies": m, "shows": s, "removed": removed,
                 "added": 0, "error": None}
    except Exception as e:
        entry = {"ts": timestamp, "trigger": "Manual",
                 "user": user["label"],
                 "movies": 0, "shows": 0, "removed": 0,
                 "added": 0, "error": str(e)}

    cfg["sync_log"] = ([entry] + cfg.get("sync_log", []))[:LOG_MAX]
    save_config(cfg)
    return jsonify(entry)

@app.route('/api/sync-all', methods=['POST'])
def api_sync_all():
    """Sync all enabled user slots sequentially. Returns list of per-user results."""
    cfg       = load_config()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    results   = []

    for user in cfg.get("users", []):
        if not user.get("enabled"):
            continue
        selected    = user.get("last_selection") or None
        output_root = user_output_root(cfg.get("server_name", ""), user["label"])
        try:
            m, s, removed, added = do_sync(
                cfg["server_url"], cfg["api_key"], cfg["sync_domain"],
                cfg["nx_user"],   cfg["nx_pass"],
                output_root=output_root,
                selected_ids=selected
            )
            entry = {"ts": timestamp, "trigger": "Sync All",
                     "user": user["label"],
                     "movies": m, "shows": s, "removed": removed,
                     "added": 0, "error": None}
        except Exception as e:
            entry = {"ts": timestamp, "trigger": "Sync All",
                     "user": user["label"],
                     "movies": 0, "shows": 0, "removed": 0,
                     "added": 0, "error": str(e)}

        results.append(entry)
        cfg["sync_log"] = ([entry] + cfg.get("sync_log", []))[:LOG_MAX]

    save_config(cfg)
    return jsonify(results)

# ── Schedules ─────────────────────────────────────────────────────────────────

@app.route('/api/schedules', methods=['GET'])
def api_get_schedules():
    return jsonify(load_config().get("schedules", []))

@app.route('/api/schedules', methods=['POST'])
def api_save_schedule():
    body      = request.json
    cfg       = load_config()
    schedules = cfg.get("schedules", [])
    sid       = body.get("id") or str(uuid.uuid4())[:8]
    sched     = {
        "id":           sid,
        "label":        body.get("label", "Auto Sync"),
        "user_id":      body.get("user_id", "shared"),
        "days":         body.get("days", ["mon"]),
        "hour":         int(body.get("hour", 3)),
        "minute":       int(body.get("minute", 0)),
        "timezone":     body.get("timezone", "UTC"),
        "scope":        body.get("scope", "selection"),
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
    cfg              = load_config()
    cfg["schedules"] = [s for s in cfg.get("schedules", []) if s["id"] != sid]
    save_config(cfg)
    reload_schedules()
    return jsonify({"ok": True})

# ── Log / Version ─────────────────────────────────────────────────────────────

@app.route('/api/log', methods=['GET'])
def api_log():
    return jsonify(load_config().get("sync_log", []))

@app.route('/api/version', methods=['GET'])
def api_version():
    return jsonify({"version": VERSION})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
