import argparse
import ctypes
import json
from datetime import datetime
from html import unescape
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from demoparser2 import DemoParser
from PIL import ImageChops, ImageGrab, ImageOps, ImageStat


TICKRATE = 64.0
APP_NAME = "Demo Analiz"
APP_VERSION = "1.5.0"
DEFAULT_GITHUB_OWNER = "Semziu"
DEFAULT_GITHUB_REPO = "Demo-Analiz"
DEFAULT_RELEASE_ASSET_NAME = "DemoAnaliz.exe"
DEMO_LIBRARY_DIR = Path.cwd() / "biblioteka_dem"
DEMO_LIBRARY_INDEX = DEMO_LIBRARY_DIR / "index.json"
LIVE_COACH_DIR = Path.cwd() / "live_coach_sessions"
APP_CONFIG_PATH = Path.cwd() / "app_config.json"
THEMES = {
    "Grafit": {
        "bg": "#101826",
        "panel": "#172233",
        "panel_alt": "#0f1723",
        "card": "#101826",
        "text": "#e5ecf4",
        "muted": "#9cb0c7",
        "accent": "#3d7bfd",
        "accent_hover": "#6b9cff",
        "nav_idle": "#172233",
        "nav_active": "#24344a",
        "input_bg": "#ffffff",
        "input_text": "#111111",
    },
    "Jasny": {
        "bg": "#eef3f8",
        "panel": "#dce5ef",
        "panel_alt": "#ffffff",
        "card": "#eef3f8",
        "text": "#15202b",
        "muted": "#52606d",
        "accent": "#2d6cdf",
        "accent_hover": "#5d8ef0",
        "nav_idle": "#dce5ef",
        "nav_active": "#bfd2ea",
        "input_bg": "#ffffff",
        "input_text": "#111111",
    },
    "Piasek": {
        "bg": "#f5ede1",
        "panel": "#eadcc7",
        "panel_alt": "#fff8ee",
        "card": "#f5ede1",
        "text": "#3a2b1f",
        "muted": "#78614e",
        "accent": "#c96f3a",
        "accent_hover": "#db8b57",
        "nav_idle": "#eadcc7",
        "nav_active": "#dec5a4",
        "input_bg": "#fffdf8",
        "input_text": "#111111",
    },
}

GLOSSARY_TEXT = """Slownik pojec

Crosshair placement
- Trzymanie celownika tam, gdzie za chwile moze pojawic sie glowa przeciwnika.

First bullet
- Pierwsza kula po pelnym zatrzymaniu.

Re-peek
- Ponowne wychylenie tego samego kata po pierwszym kontakcie.

Spacing
- Odstep i tempo gry wzgledem teammate'ow.

Anti-flash
- Ustawienie lub timing, dzieki ktoremu nie przyjmujesz pelnego flasha.

Dry peek
- Wychylenie bez flasha, smoka albo innego wsparcia.

Pre-aim
- Wczesniejsze ustawienie celownika na typowy kat.

Grounded peek
- Wychylenie bez skoku, z kontrola ruchu i gotowoscia do strzalu po stopie.
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analiza dema CS2 pod katem ruchu, decyzji i aimu."
    )
    parser.add_argument("--demo", help="Sciezka do pliku demo.")
    parser.add_argument("--pick-demo", action="store_true", help="Otworz okno wyboru pliku demo.")
    parser.add_argument("--player", help="Nick gracza z dema.")
    parser.add_argument("--top-deaths", type=int, default=5, help="Ile zgonow pokazac w szczegolach.")
    parser.add_argument("--gui", action="store_true", help="Uruchom GUI.")
    return parser.parse_args()


def seconds_from_ticks(ticks):
    return ticks / TICKRATE


def pick_demo_file(parent=None):
    return filedialog.askopenfilename(
        parent=parent,
        title="Wybierz plik demo CS2",
        filetypes=[("Demo CS2", "*.dem"), ("Wszystkie pliki", "*.*")],
    )


def pick_demo_files(parent=None):
    return filedialog.askopenfilenames(
        parent=parent,
        title="Wybierz kilka plikow demo CS2",
        filetypes=[("Demo CS2", "*.dem"), ("Wszystkie pliki", "*.*")],
    )


def resolve_demo_path(args):
    if args.pick_demo:
        selected = pick_demo_file()
        if not selected:
            raise ValueError("Nie wybrano pliku demo.")
        demo_path = Path(selected)
    elif args.demo:
        demo_path = Path(args.demo)
    else:
        default_demo = Path("dust2.dem")
        if not default_demo.exists():
            raise FileNotFoundError("Nie znaleziono domyslnego dema. Uzyj --demo albo --pick-demo.")
        demo_path = default_demo

    if not demo_path.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku demo: {demo_path}")
    if demo_path.suffix.lower() != ".dem":
        raise ValueError(f"Wybrany plik nie jest plikiem .dem: {demo_path}")
    return demo_path.resolve()


def ensure_demo_library():
    DEMO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    if not DEMO_LIBRARY_INDEX.exists():
        DEMO_LIBRARY_INDEX.write_text("[]", encoding="utf-8")


def load_demo_library_index():
    ensure_demo_library()
    try:
        data = json.loads(DEMO_LIBRARY_INDEX.read_text(encoding="utf-8"))
    except Exception:
        data = []
    items = []
    for item in data:
        path = Path(item.get("path", ""))
        if path.exists():
            items.append(
                {
                    "name": item.get("name") or path.name,
                    "path": str(path.resolve()),
                    "source": item.get("source") or "",
                }
            )
    return items


def save_demo_library_index(items):
    ensure_demo_library()
    normalized = []
    seen = set()
    for item in items:
        path = str(Path(item["path"]).resolve())
        if path in seen:
            continue
        seen.add(path)
        normalized.append(
            {
                "name": item.get("name") or Path(path).name,
                "path": path,
                "source": item.get("source") or "",
            }
        )
    DEMO_LIBRARY_INDEX.write_text(json.dumps(normalized, ensure_ascii=True, indent=2), encoding="utf-8")


def import_demos_to_library(paths):
    ensure_demo_library()
    index = load_demo_library_index()
    existing_paths = {item["path"] for item in index}
    added = []
    skipped = []
    for raw_path in paths:
        source = Path(raw_path).resolve()
        if source.suffix.lower() != ".dem" or not source.exists():
            skipped.append(str(source))
            continue
        target = DEMO_LIBRARY_DIR / source.name
        stem = source.stem
        counter = 2
        while target.exists() and source.read_bytes() != target.read_bytes():
            target = DEMO_LIBRARY_DIR / f"{stem}_{counter}{source.suffix}"
            counter += 1
        if not target.exists():
            shutil.copy2(source, target)
        item = {"name": target.name, "path": str(target.resolve()), "source": str(source)}
        if item["path"] in existing_paths:
            skipped.append(str(source))
            continue
        index.append(item)
        existing_paths.add(item["path"])
        added.append(item)
    save_demo_library_index(index)
    return added, skipped


def remove_demo_library_items(paths):
    index = load_demo_library_index()
    targets = {str(Path(path).resolve()) for path in paths}
    kept = []
    removed = []
    for item in index:
        item_path = str(Path(item["path"]).resolve())
        if item_path in targets:
            removed.append(item_path)
            try:
                Path(item_path).unlink(missing_ok=True)
            except Exception:
                pass
        else:
            kept.append(item)
    save_demo_library_index(kept)
    return removed


def ensure_live_coach_dir():
    LIVE_COACH_DIR.mkdir(parents=True, exist_ok=True)


def load_app_config():
    default = {
        "github_owner": DEFAULT_GITHUB_OWNER,
        "github_repo": DEFAULT_GITHUB_REPO,
        "github_asset_name": DEFAULT_RELEASE_ASSET_NAME,
    }
    if not APP_CONFIG_PATH.exists():
        return default
    try:
        data = json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    default.update({key: data.get(key, value) for key, value in default.items()})
    return default


def save_app_config(config):
    APP_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=True, indent=2), encoding="utf-8")


def parse_version_text(value):
    cleaned = str(value).strip().lstrip("vV")
    parts = []
    for chunk in cleaned.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def current_app_executable():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def fetch_json(url):
    request = Request(
        url,
        headers={
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            "Accept": "application/vnd.github+json, application/json",
        },
    )
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data


def fetch_github_latest_release(owner, repo):
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    return fetch_json(api_url)


def find_release_asset(release_data, asset_name):
    assets = release_data.get("assets") or []
    for asset in assets:
        if str(asset.get("name", "")).strip().lower() == asset_name.strip().lower():
            return asset
    exe_assets = [asset for asset in assets if str(asset.get("name", "")).lower().endswith(".exe")]
    if len(exe_assets) == 1:
        return exe_assets[0]
    return None


def extract_release_version(release_data):
    tag_name = str(release_data.get("tag_name", "")).strip()
    release_name = str(release_data.get("name", "")).strip()
    return tag_name or release_name


def download_update_file(download_url):
    temp_dir = Path(tempfile.gettempdir()) / f"{APP_NAME}_updates"
    temp_dir.mkdir(parents=True, exist_ok=True)
    target = temp_dir / f"{APP_NAME}_{int(time.time())}.exe"
    request = Request(download_url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def apply_downloaded_update(downloaded_path, current_exe):
    updater_bat = current_exe.parent / "update_app.bat"
    process_name = current_exe.name
    script = "\r\n".join(
        [
            "@echo off",
            "setlocal",
            f"set SRC={downloaded_path}",
            f"set DEST={current_exe}",
            ":waitloop",
            "timeout /t 1 /nobreak >nul",
            f'tasklist /FI "IMAGENAME eq {process_name}" | find /I "{process_name}" >nul',
            "if %ERRORLEVEL%==0 goto waitloop",
            'copy /Y "%SRC%" "%DEST%" >nul',
            'start "" "%DEST%"',
            'del "%SRC%"',
            'del "%~f0"',
            "endlocal",
        ]
    )
    updater_bat.write_text(script, encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", str(updater_bat)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def list_live_coach_sessions():
    ensure_live_coach_dir()
    sessions = []
    for path in sorted(LIVE_COACH_DIR.glob("session_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sessions.append(
                {
                    "path": str(path.resolve()),
                    "label": data.get("session_label", path.stem),
                    "summary": data.get("summary", {}),
                    "started_at": data.get("started_at", ""),
                }
            )
        except Exception:
            continue
    return sessions


def load_live_coach_session(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def capture_live_frame():
    image = None
    errors = []
    for kwargs in ({}, {"all_screens": True}):
        try:
            image = ImageGrab.grab(**kwargs)
            break
        except Exception as error:
            errors.append(str(error))
    if image is None:
        raise OSError("screen grab failed: " + " | ".join(errors))
    gray = ImageOps.grayscale(image).resize((320, 180))
    return image, gray


def analyze_live_frame(prev_gray, gray, state):
    brightness = float(ImageStat.Stat(gray).mean[0])
    center_box = (
        gray.width // 3,
        gray.height // 3,
        gray.width - gray.width // 3,
        gray.height - gray.height // 3,
    )
    center_gray = gray.crop(center_box)
    center_brightness = float(ImageStat.Stat(center_gray).mean[0])
    lower_left_box = (0, int(gray.height * 0.72), int(gray.width * 0.22), gray.height)
    lower_right_box = (int(gray.width * 0.78), int(gray.height * 0.72), gray.width, gray.height)
    bottom_center_box = (int(gray.width * 0.38), int(gray.height * 0.83), int(gray.width * 0.62), gray.height)
    top_center_box = (int(gray.width * 0.34), 0, int(gray.width * 0.66), int(gray.height * 0.16))
    lower_left = gray.crop(lower_left_box)
    lower_right = gray.crop(lower_right_box)
    bottom_center = gray.crop(bottom_center_box)
    top_center = gray.crop(top_center_box)
    lower_left_std = float(ImageStat.Stat(lower_left).stddev[0])
    lower_right_std = float(ImageStat.Stat(lower_right).stddev[0])
    bottom_center_std = float(ImageStat.Stat(bottom_center).stddev[0])
    top_center_std = float(ImageStat.Stat(top_center).stddev[0])
    hud_score = (lower_left_std + lower_right_std + bottom_center_std) / 3.0
    motion = 0.0
    center_motion = 0.0
    if prev_gray is not None:
        diff = ImageChops.difference(prev_gray, gray)
        motion = float(ImageStat.Stat(diff).mean[0])
        center_prev = prev_gray.crop(center_box)
        center_diff = ImageChops.difference(center_prev, center_gray)
        center_motion = float(ImageStat.Stat(center_diff).mean[0])

    motion_history = state.setdefault("motion_history", [])
    center_history = state.setdefault("center_history", [])
    motion_history.append(motion)
    center_history.append(center_motion)
    if len(motion_history) > 8:
        motion_history.pop(0)
    if len(center_history) > 8:
        center_history.pop(0)

    def cooldown_ready(tag, seconds):
        last_time = state.setdefault("last_event_times", {}).get(tag, 0.0)
        now_time = time.time()
        if now_time - last_time >= seconds:
            state["last_event_times"][tag] = now_time
            return True
        return False

    avg_motion_4 = sum(motion_history[-4:]) / min(len(motion_history), 4)
    avg_center_4 = sum(center_history[-4:]) / min(len(center_history), 4)
    gameplay_confidence = 0.0
    if 35 <= brightness <= 190:
        gameplay_confidence += 0.25
    if hud_score >= 18:
        gameplay_confidence += 0.35
    if avg_motion_4 >= 3:
        gameplay_confidence += 0.15
    if center_brightness >= 40:
        gameplay_confidence += 0.10
    if top_center_std <= 45:
        gameplay_confidence += 0.15
    gameplay_confidence = min(gameplay_confidence, 1.0)
    spectate_risk = 0.0
    if top_center_std >= 40:
        spectate_risk += 0.35
    if hud_score < 16:
        spectate_risk += 0.35
    if avg_motion_4 < 2.2:
        spectate_risk += 0.10
    if brightness < 28 or brightness > 205:
        spectate_risk += 0.10
    if lower_left_std < 12 and lower_right_std < 12:
        spectate_risk += 0.20
    spectate_risk = min(spectate_risk, 1.0)

    events = []
    brightness_jump = brightness - state.get("last_brightness", brightness)
    if brightness > 210 and brightness_jump > 35 and gameplay_confidence >= 0.6 and cooldown_ready("flash", 8):
        events.append(("mozliwy_flash", "Bardzo jasny skok obrazu. To bardziej wyglada na flash albo mocny efekt niz zwykly ruch myszki."))

    if avg_motion_4 >= 18 and avg_center_4 >= 16 and gameplay_confidence >= 0.72:
        state["combat_frames"] = state.get("combat_frames", 0) + 1
    else:
        state["combat_frames"] = 0
    if state["combat_frames"] >= 4 and cooldown_ready("chaos", 10):
        events.append(("chaos_w_centrum", "Przez kilka probek ruch w centrum byl bardzo wysoki. To wyglada bardziej na dluzszy chaotyczny kontakt niz pojedynczy flick."))
    elif avg_motion_4 >= 12 and avg_center_4 <= 7 and gameplay_confidence >= 0.72 and cooldown_ready("stabilny_ruch", 12):
        events.append(("stabilne_centrum", "Ruch byl szybki, ale centrum obrazu zostalo wzglednie stabilne. To wyglada spokojniej niz paniczne machanie myszka."))

    if motion < 1.6 and 35 <= brightness <= 190 and gameplay_confidence >= 0.68:
        state["still_frames"] = state.get("still_frames", 0) + 1
    else:
        state["still_frames"] = 0
    if state["still_frames"] >= 10 and cooldown_ready("holding", 18):
        events.append(("dlugie_stanie", "Obraz przez dluzszy moment prawie sie nie zmienia. Jesli to nie byl hold ustawiony z sensem, uwazaj na zbyt dluga ekspozycje.")) 

    if avg_motion_4 >= 14 and gameplay_confidence >= 0.72:
        state["high_motion_frames"] = state.get("high_motion_frames", 0) + 1
    else:
        state["high_motion_frames"] = 0
    if state["high_motion_frames"] >= 7 and cooldown_ready("duel", 16):
        events.append(("dlugi_kontakt", "Przez dluzszy czas obraz byl bardzo dynamiczny. To wyglada na przedluzony kontakt i moze oznaczac brak resetu po pierwszej walce."))
    if spectate_risk >= 0.72 and cooldown_ready("spectate", 20):
        events.append(("niska_pewnosc", "Niska pewnosc, ze to Twoj aktywny POV. Mozliwy spectate, scoreboard albo ekran przejsciowy, wiec ograniczam mocne wnioski."))

    state["last_brightness"] = brightness
    return {
        "brightness": round(brightness, 2),
        "center_brightness": round(center_brightness, 2),
        "motion": round(motion, 2),
        "center_motion": round(center_motion, 2),
        "avg_motion_4": round(avg_motion_4, 2),
        "avg_center_4": round(avg_center_4, 2),
        "hud_score": round(hud_score, 2),
        "top_center_std": round(top_center_std, 2),
        "gameplay_confidence": round(gameplay_confidence, 2),
        "spectate_risk": round(spectate_risk, 2),
        "events": events,
    }


def summarize_live_session(session_data):
    samples = session_data.get("samples", [])
    events = session_data.get("events", [])
    if not samples:
        return {"samples": 0, "events": 0, "avg_motion": 0.0, "avg_brightness": 0.0}
    avg_motion = sum(sample["motion"] for sample in samples) / len(samples)
    avg_brightness = sum(sample["brightness"] for sample in samples) / len(samples)
    avg_confidence = sum(sample.get("gameplay_confidence", 0.0) for sample in samples) / len(samples)
    tag_counts = {}
    for event in events:
        tag = event.get("tag", "inne")
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tag = max(tag_counts.items(), key=lambda item: item[1])[0] if tag_counts else ""
    return {
        "samples": len(samples),
        "events": len(events),
        "avg_motion": round(avg_motion, 2),
        "avg_brightness": round(avg_brightness, 2),
        "avg_confidence": round(avg_confidence, 2),
        "top_tag": top_tag,
    }


def is_cs2_running():
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/FO", "CSV", "/NH"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    return "cs2.exe" in output.lower()


def get_foreground_window_state():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"title": "", "is_minimized": False, "is_cs2_foreground": False}
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value or ""
        is_minimized = bool(user32.IsIconic(hwnd))
        title_lower = title.lower()
        is_cs2_foreground = ("counter-strike 2" in title_lower or "counter strike 2" in title_lower or "cs2" in title_lower) and not is_minimized
        return {
            "title": title,
            "is_minimized": is_minimized,
            "is_cs2_foreground": is_cs2_foreground,
        }
    except Exception:
        return {"title": "", "is_minimized": False, "is_cs2_foreground": False}


def load_parser(demo_path):
    parser = DemoParser(str(demo_path))
    header = parser.parse_header()
    players = load_players(parser)
    return parser, header, players


def load_players(parser):
    players = parser.parse_player_info().copy()
    players = players.dropna(subset=["name", "steamid"])
    players["name_lower"] = players["name"].astype(str).str.lower()
    players["steamid_text"] = players["steamid"].astype(str)
    return players.sort_values(["team_number", "name"]).reset_index(drop=True)


def format_players(players):
    lines = ["Gracze znalezieni w demie:"]
    seen_names = set()
    for _, row in players.iterrows():
        name = str(row["name"])
        key = name.strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        lines.append(f"- {name}")
    return "\n".join(lines)


def build_player_option_maps(players, with_counts=None):
    values = []
    lookup = {}
    seen_names = set()
    for _, row in players.iterrows():
        name = str(row["name"])
        key = name.strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        suffix = ""
        if with_counts and name in with_counts:
            suffix = f" | {with_counts[name]}"
        label = f"{name}{suffix}"
        values.append(label)
        lookup[label] = name
    return values, lookup


def resolve_player(players, player_query):
    query = player_query.strip().lower()
    exact = players[players["name_lower"] == query]
    if len(exact) == 1:
        return exact.iloc[0]
    if len(exact) > 1 and exact["steamid_text"].nunique() == 1:
        return exact.iloc[0]

    partial = players[players["name_lower"].str.contains(query, na=False)]
    if len(partial) == 1:
        return partial.iloc[0]
    if len(partial) > 1 and partial["steamid_text"].nunique() == 1:
        return partial.iloc[0]

    if len(exact) > 1 or len(partial) > 1:
        matches = exact if len(exact) > 1 else partial
        raise ValueError("Nazwa gracza jest niejednoznaczna: " + ", ".join(matches["name"].astype(str).tolist()))
    raise ValueError("Nie znaleziono gracza.")


def parse_round_starts(parser):
    result = parser.parse_events(["round_freeze_end"])
    return result[0][1]["tick"].dropna().astype(int).sort_values().tolist()


def assign_round_numbers(frame, round_starts):
    starts = pd.Series(round_starts, name="round_start_tick")
    frame = frame.copy()
    frame["round_number"] = starts.searchsorted(frame["tick"], side="right")
    frame["round_start_tick"] = frame["round_number"].apply(
        lambda number: round_starts[number - 1] if 0 < number <= len(round_starts) else None
    )
    return frame


def compress_places(place_values):
    route = []
    last = None
    for place in place_values:
        if not isinstance(place, str) or not place:
            continue
        if place == last:
            continue
        route.append(place)
        last = place
    return route


def build_opening_route(round_slice):
    route = compress_places(round_slice["last_place_name"].tolist())
    if not route:
        return "unknown"
    return " -> ".join(route[:4])


def infer_side_from_round(round_slice):
    opening_places = round_slice["last_place_name"].dropna().astype(str).tolist()[:24]
    for place in opening_places:
        lower = place.lower()
        if "ctspawn" in lower or "ct spawn" in lower:
            return "CT"
        if "tspawn" in lower or "t spawn" in lower:
            return "Terro"
    return "Nieznana"


def analyze_player_rounds(player_ticks, round_starts):
    round_summaries = []
    for round_number, round_slice in player_ticks.groupby("round_number"):
        if round_number <= 0 or round_slice.empty:
            continue
        round_slice = round_slice.sort_values("tick").copy()
        alive_slice = round_slice[round_slice["is_alive"] == True].copy()
        if alive_slice.empty:
            continue

        dx = alive_slice["X"].diff().fillna(0.0)
        dy = alive_slice["Y"].diff().fillna(0.0)
        path_distance = (dx.pow(2) + dy.pow(2)).pow(0.5).sum()
        alive_ticks = alive_slice["tick"].max() - alive_slice["tick"].min()
        alive_seconds = max(seconds_from_ticks(alive_ticks), 0.0)
        avg_speed = path_distance / alive_seconds if alive_seconds > 0 else 0.0
        start_tick = round_starts[round_number - 1]
        opening_window = round_slice[
            (round_slice["tick"] >= start_tick) &
            (round_slice["tick"] <= start_tick + int(20 * TICKRATE))
        ]
        side_label = infer_side_from_round(round_slice)

        round_summaries.append(
            {
                "round_number": int(round_number),
                "side": side_label,
                "path_distance": float(path_distance),
                "alive_seconds": float(alive_seconds),
                "avg_speed": float(avg_speed),
                "crouch_share": float((alive_slice["duck_amount"] > 0.5).mean()),
                "airborne_share": float(alive_slice["is_airborne"].mean()),
                "opening_route": build_opening_route(opening_window),
            }
        )
    return pd.DataFrame(round_summaries)


def build_event_frame(parser, event_name):
    return parser.parse_events([event_name])[0][1].copy()


def build_grenade_usage(parser, player_steamid):
    grenade_events = [
        "flashbang_detonate",
        "hegrenade_detonate",
        "smokegrenade_detonate",
        "inferno_startburn",
    ]
    frames = []
    for event_name in grenade_events:
        frame = build_event_frame(parser, event_name)
        if frame.empty:
            continue
        frame["event_name"] = event_name
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["tick", "event_name"])

    grenades = pd.concat(frames, ignore_index=True)
    grenades["user_steamid"] = grenades["user_steamid"].astype(str)
    grenades = grenades[grenades["user_steamid"] == player_steamid].copy()
    return grenades.sort_values("tick").reset_index(drop=True)


def build_blind_events(parser, player_steamid):
    blinds = build_event_frame(parser, "player_blind")
    blinds["user_steamid"] = blinds["user_steamid"].astype(str)
    blinds = blinds[blinds["user_steamid"] == player_steamid].copy()
    if blinds.empty:
        return blinds
    blinds["blind_end_tick"] = blinds["tick"] + (blinds["blind_duration"] * TICKRATE)
    return blinds.sort_values("tick").reset_index(drop=True)


def build_hurt_events(parser, player_steamid):
    hurts = build_event_frame(parser, "player_hurt")
    hurts["user_steamid"] = hurts["user_steamid"].astype(str)
    hurts["attacker_steamid"] = hurts["attacker_steamid"].astype(str)
    hurts = hurts[hurts["user_steamid"] == player_steamid].copy()
    return hurts.sort_values("tick").reset_index(drop=True)


def build_damage_dealt(parser, player_steamid):
    hurts = build_event_frame(parser, "player_hurt")
    hurts["attacker_steamid"] = hurts["attacker_steamid"].astype(str)
    hurts = hurts[hurts["attacker_steamid"] == player_steamid].copy()
    return hurts.sort_values("tick").reset_index(drop=True)


def build_kill_events(parser, player_steamid):
    kills = build_event_frame(parser, "player_death")
    kills["attacker_steamid"] = kills["attacker_steamid"].astype(str)
    kills = kills[kills["attacker_steamid"] == player_steamid].copy()
    return kills.sort_values("tick").reset_index(drop=True)


def build_weapon_fire_events(parser, player_steamid):
    fires = build_event_frame(parser, "weapon_fire")
    fires["user_steamid"] = fires["user_steamid"].astype(str)
    fires = fires[fires["user_steamid"] == player_steamid].copy()
    return fires.sort_values("tick").reset_index(drop=True)


def build_death_report(parser, player_ticks, player_steamid, round_starts, round_summary):
    deaths = build_event_frame(parser, "player_death")
    deaths["user_steamid"] = deaths["user_steamid"].astype(str)
    deaths = deaths[deaths["user_steamid"] == player_steamid].copy()
    if deaths.empty:
        return pd.DataFrame(
            columns=[
                "tick",
                "round_number",
                "round_start_tick",
                "attacker_name",
                "weapon",
                "last_place_name",
                "is_airborne",
                "was_blinded",
                "recent_unique_attackers",
                "time_into_round",
            ]
        )

    deaths = assign_round_numbers(deaths.sort_values("tick").reset_index(drop=True), round_starts)
    tick_snapshot = player_ticks[["tick", "is_airborne", "last_place_name"]].sort_values("tick")
    deaths = pd.merge_asof(
        deaths.sort_values("tick"),
        tick_snapshot,
        on="tick",
        direction="nearest",
        tolerance=10,
    )

    blinds = build_blind_events(parser, player_steamid)
    hurts = build_hurt_events(parser, player_steamid)
    blind_flags = []
    recent_attackers = []

    for _, death in deaths.iterrows():
        death_tick = int(death["tick"])
        active_blind = blinds[
            (blinds["tick"] <= death_tick) &
            (blinds["blind_end_tick"] >= death_tick)
        ]
        blind_flags.append(not active_blind.empty)
        recent_hurts = hurts[
            (hurts["tick"] >= death_tick - int(5 * TICKRATE)) &
            (hurts["tick"] <= death_tick)
        ]
        recent_attackers.append(int(recent_hurts["attacker_steamid"].nunique()))

    deaths["was_blinded"] = blind_flags
    deaths["recent_unique_attackers"] = recent_attackers
    side_map = round_summary.set_index("round_number")["side"].to_dict() if not round_summary.empty else {}
    deaths["side"] = deaths["round_number"].map(side_map).fillna("Nieznana")
    deaths["time_into_round"] = deaths.apply(
        lambda row: seconds_from_ticks(row["tick"] - row["round_start_tick"])
        if pd.notna(row["round_start_tick"]) else None,
        axis=1,
    )
    return deaths


def build_aim_report(parser, player_steamid, round_summary):
    damage_dealt = build_damage_dealt(parser, player_steamid)
    kills = build_kill_events(parser, player_steamid)
    fires = build_weapon_fire_events(parser, player_steamid)
    total_kills = len(kills)
    total_hits = len(damage_dealt)
    total_shots = len(fires)
    total_damage = int(damage_dealt["dmg_health"].sum()) if not damage_dealt.empty else 0
    hs_kills = int(kills["headshot"].fillna(False).sum()) if not kills.empty else 0
    top_fire_weapon = fires["weapon"].astype(str).value_counts().index[0] if not fires.empty else "unknown"
    return {
        "total_kills": total_kills,
        "total_hits": total_hits,
        "total_shots": total_shots,
        "total_damage": total_damage,
        "hs_kill_ratio": (hs_kills / total_kills) if total_kills else 0.0,
        "hit_per_shot_ratio": (total_hits / total_shots) if total_shots else 0.0,
        "damage_per_hit": (total_damage / total_hits) if total_hits else 0.0,
        "kills_per_round": (total_kills / len(round_summary)) if len(round_summary) else 0.0,
        "top_fire_weapon": top_fire_weapon,
    }


def build_report_data(parser, demo_path, players, player_query, top_deaths, header=None):
    player = resolve_player(players, player_query)
    player_name = str(player["name"])
    player_steamid = str(player["steamid_text"])
    header = header or parser.parse_header()

    ticks = parser.parse_ticks(
        [
            "tick",
            "steamid",
            "name",
            "X",
            "Y",
            "Z",
            "is_alive",
            "duck_amount",
            "is_airborne",
            "last_place_name",
        ]
    )
    ticks["steamid_text"] = ticks["steamid"].astype(str)
    player_ticks = ticks[ticks["steamid_text"] == player_steamid].copy()
    player_ticks = player_ticks.sort_values("tick").reset_index(drop=True)
    if player_ticks.empty:
        raise ValueError("Nie znaleziono danych tickow dla wybranego gracza.")

    round_starts = parse_round_starts(parser)
    player_ticks = assign_round_numbers(player_ticks, round_starts)
    round_summary = analyze_player_rounds(player_ticks, round_starts)

    return {
        "demo_path": str(demo_path),
        "map_name": header.get("map_name", "unknown"),
        "server_name": header.get("server_name", "unknown"),
        "player_name": player_name,
        "round_summary": round_summary,
        "grenades": build_grenade_usage(parser, player_steamid),
        "death_report": build_death_report(parser, player_ticks, player_steamid, round_starts, round_summary),
        "aim_report": build_aim_report(parser, player_steamid, round_summary),
        "top_deaths": top_deaths,
    }


def get_issue_flags(report):
    flags = []
    death_report = report["death_report"]
    round_summary = report["round_summary"]
    grenades = report["grenades"]
    aim_report = report["aim_report"]

    if not death_report.empty:
        if len(death_report[death_report["time_into_round"] <= 20]) / len(death_report) >= 0.4:
            flags.append("Za szybkie otwieranie rund")
        if len(death_report[death_report["was_blinded"] == True]) >= 1:
            flags.append("Slabe reakcje po flashu")
        if len(death_report[death_report["is_airborne"] == True]) >= 1:
            flags.append("Skakanie w duelach")
        if len(death_report[death_report["recent_unique_attackers"] >= 2]) >= max(2, len(death_report) // 3):
            flags.append("Za duza ekspozycja na kilka katow")

    if not round_summary.empty:
        route_counts = round_summary["opening_route"].value_counts()
        if not route_counts.empty and route_counts.iloc[0] / len(round_summary) >= 0.5:
            flags.append("Przewidywalne otwarcia")
        crouch_heavy_rounds = round_summary[round_summary["crouch_share"] >= 0.35]
        if len(crouch_heavy_rounds) >= max(2, len(round_summary) // 3):
            flags.append("Za duzo kucania")

    if len(round_summary) and len(grenades) / len(round_summary) < 0.5:
        flags.append("Za malo utility")
    if aim_report["hs_kill_ratio"] < 0.35:
        flags.append("Crosshair za rzadko na glowie")
    if aim_report["hit_per_shot_ratio"] < 0.18:
        flags.append("Za slaba pierwsza kula")
    return flags


def get_hot_zones(report):
    death_report = report["death_report"]
    if death_report.empty:
        return []
    counts = death_report["last_place_name"].fillna("unknown").value_counts()
    return [zone for zone in counts.index.tolist()[:3] if zone != "unknown"]


def build_summary_lines(report):
    death_report = report["death_report"]
    early_deaths = death_report[death_report["time_into_round"] <= 20]
    blind_deaths = death_report[death_report["was_blinded"] == True]
    airborne_deaths = death_report[death_report["is_airborne"] == True]
    round_summary = report["round_summary"]
    side_lines = []
    if not round_summary.empty:
        for side_name in ["Terro", "CT"]:
            side_rounds = round_summary[round_summary["side"] == side_name]
            if side_rounds.empty:
                continue
            side_deaths = death_report[death_report["side"] == side_name] if not death_report.empty else death_report
            side_lines.append(
                f"- {side_name}: rundy {len(side_rounds)}, zgony {len(side_deaths)}, sredni czas zycia {side_rounds['alive_seconds'].mean():.1f}s"
            )
    lines = [
        f"Demo: {Path(report['demo_path']).name}",
        f"Mapa: {report['map_name']}",
        f"Analizowany gracz: {report['player_name']}",
        "",
        "Szybkie podsumowanie:",
        f"- Rundy: {len(report['round_summary'])}",
        f"- Zgony: {len(death_report)}",
        f"- Wczesne zgony: {len(early_deaths)}",
        f"- Zgony po flashu: {len(blind_deaths)}",
        f"- Zgony w ruchu/skoku: {len(airborne_deaths)}",
        f"- Uzyte granaty: {len(report['grenades'])}",
        "",
        "Podzial na strony:",
    ]
    lines.extend(side_lines if side_lines else ["- Brak danych o stronach."])
    return lines


def build_coach_review_lines(report):
    lines = ["", "Co ten mecz mowi o graczu:"]
    death_report = report["death_report"]
    aim_report = report["aim_report"]
    if death_report.empty:
        lines.append("- Ten mecz nie pokazuje duzej liczby karanych bledow, wiec glowny nacisk idzie na nawyki i regularnosc.")
    else:
        early_deaths = len(death_report[death_report["time_into_round"] <= 20])
        if early_deaths >= max(1, len(death_report) // 2):
            lines.append(f"- Najmocniej rzuca sie w oczy zbyt szybkie wchodzenie w akcje. Wczesnych zgonow bylo az {early_deaths}.")
        else:
            lines.append("- Problem nie siedzi tylko w otwarciach. Wiecej zyskasz na lepszym repozycjonowaniu i spokojniejszym doborze walk.")
        hot_zones = get_hot_zones(report)
        if hot_zones:
            lines.append(f"- Najczesciej mecz karal cie w miejscach: {', '.join(hot_zones)}.")

    if aim_report["hit_per_shot_ratio"] >= 0.18 and aim_report["damage_per_hit"] >= 25:
        lines.append("- Surowy aim nie wyglada zle. Wiekszy wyciek jest w jakosci duelow i timingu.")
    else:
        lines.append("- Mechanika strzelania wymaga doszlifowania, glownie pierwszej kuli i zatrzymania przed strzalem.")
    return lines


def build_aim_review_lines(report):
    aim = report["aim_report"]
    lines = [
        "",
        "Ocena aimu prostym jezykiem:",
        f"- Kille: {aim['total_kills']}, trafienia: {aim['total_hits']}, strzaly: {aim['total_shots']}",
        f"- Headshot ratio: {aim['hs_kill_ratio']:.0%}",
        f"- Trafienia na strzal: {aim['hit_per_shot_ratio']:.0%}",
        f"- Sredni damage na trafienie: {aim['damage_per_hit']:.1f}",
        f"- Najczestsza bron: {aim['top_fire_weapon']}",
    ]
    if aim["total_shots"] == 0:
        return lines + ["- Za malo danych o strzalach, zeby uczciwie ocenic aim."]
    if aim["hs_kill_ratio"] < 0.35:
        lines.append("- Crosshair za rzadko startuje na glowie. To pierwszy element do poprawy.")
    else:
        lines.append("- Wysokosc crosshaira bywa dobra. To juz daje baze pod dalszy progres.")
    if aim["hit_per_shot_ratio"] < 0.18:
        lines.append("- Pierwsza kula i wejscie w spray sa za slabe. Najwiecej da spokojniejszy stop przed strzalem.")
    else:
        lines.append("- Celowanie jest przyzwoite. Wiecej rund uratuje lepszy wybor kiedy walczyc, a kiedy odpuscic.")
    return lines


def build_suggestion_lines(report):
    items = get_issue_flags(report)
    lines = ["", "Najwazniejsze poprawki na teraz:"]
    if "Za szybkie otwieranie rund" in items:
        lines.append("- Zwolnij pierwsze 20 sekund rundy. Najpierw info albo utility, dopiero potem pelny duel.")
    if "Slabe reakcje po flashu" in items:
        lines.append("- Trenuj anti-flash: po granacie przeciwnika nie wychylaj od razu na autopilocie.")
    if "Skakanie w duelach" in items:
        lines.append("- Odetnij jump-peeki w pelnych walkach. Skok ma dawac info, nie byc glownym sposobem wejscia.")
    if "Za duza ekspozycja na kilka katow" in items:
        lines.append("- Ustawiaj sie tak, zeby walczyc z jednym katem na raz.")
    if "Przewidywalne otwarcia" in items:
        lines.append("- Mieszaj trasy i tempo otwarcia rundy, bo stajesz sie czytelny.")
    if "Za malo utility" in items:
        lines.append("- Czesniej i czesciej zaczynaj walke od flasha lub smoka.")
    if "Crosshair za rzadko na glowie" in items:
        lines.append("- Skup trening na stalej wysokosci glowy podczas clearowania.")
    if "Za slaba pierwsza kula" in items:
        lines.append("- Priorytet: stop przed strzalem i krotsze serie.")
    if len(lines) == 2:
        lines.append("- Ten mecz nie pokazal jednej ogromnej dziury. Najwiekszy zysk da regularnosc i review kolejnych dem.")
    return lines


def build_training_plan_lines(report):
    items = get_issue_flags(report)
    focus_1 = "crosshair na wysokosci glowy" if "Crosshair za rzadko na glowie" in items else "czysty stop i pierwsza kula"
    focus_2 = "bez skakania w duelach" if "Skakanie w duelach" in items else "lepsze wejscie w duel"
    focus_3 = "anti-flash i spokoj po utility" if "Slabe reakcje po flashu" in items else "kontrola sprayu i krotsze serie"
    return [
        "",
        f"Plan treningowy dla {report['player_name']}:",
        "- 3 glowne priorytety:",
        f"  1. {focus_1}",
        f"  2. {focus_2}",
        f"  3. {focus_3}",
        "- Dzienny blok 45-60 minut:",
        "  1. 10 min boty statyczne: same one-tapy i pelne zatrzymanie przed strzalem.",
        "  2. 10 min peek drill: wychylenie, stop, 1-3 kule, powrot za oslone.",
        "  3. 15 min DM na AK/M4: grasz pod jakosc pierwszych kul, nie pod wynik.",
        "  4. 10 min pre-aim walk na mapie z dema i w najczesciej przegrywanych strefach.",
        "  5. 5-15 min review: 3 zgony, 3 pytania - po co walczyles, z ilu katow, czy crosshair byl gotowy.",
        "- Zasady na mecz:",
        "  1. Nie bierz pelnego duela od razu po flashu przeciwnika.",
        "  2. Nie skacz w walke, jesli nie robisz tylko info peeka.",
        "  3. Po 2-3 chybionych kulach resetuj pojedynek zamiast cisnac zly spray.",
    ]


def build_map_focus_lines(report):
    zones = get_hot_zones(report)
    lines = ["", "Mapy i strefy do treningu:"]
    if zones:
        lines.append(f"- Mapa z tego dema: {report['map_name']}. Najpierw przejdz na sucho miejsca: {', '.join(zones)}.")
        lines.append("- Na tej mapie zrob 10 minut pre-aim walk i 10 minut samych wychylen pod te strefy.")
    else:
        lines.append(f"- Mapa z tego dema: {report['map_name']}. Brakuje jednej wyraznej goracej strefy, wiec skup sie na standardowych katach wejscia.")
    lines.append("- Do planu tygodniowego dodaj ta mape z dema oraz jeszcze jedna mape, na ktorej grasz najczesciej.")
    return lines


def build_workshop_queries(report):
    queries = []
    map_name = str(report["map_name"]).replace("de_", "").replace("cs_", "")
    issues = set(get_issue_flags(report))
    hot_zones = get_hot_zones(report)

    if map_name and map_name != "unknown":
        queries.append(f"cs2 {map_name} prefire")
        queries.append(f"cs2 {map_name} training")
    if "Crosshair za rzadko na glowie" in issues or "Za slaba pierwsza kula" in issues:
        queries.append("cs2 aim training")
    if "Skakanie w duelach" in issues or "Za szybkie otwieranie rund" in issues:
        queries.append("cs2 peek training")
    if "Za malo utility" in issues:
        queries.append("cs2 utility training")
    if hot_zones:
        queries.append(f"cs2 {map_name} prefire")

    seen = set()
    ordered = []
    for query in queries:
        key = query.lower().strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(query)
    return ordered[:4]


def search_steam_workshop(query, limit=2):
    params = urlencode({"appid": 730, "searchtext": query})
    url = f"https://steamcommunity.com/workshop/browse/?{params}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urlopen(request, timeout=20).read().decode("utf-8-sig", errors="ignore")
    pattern = re.compile(
        r'<a[^>]+href="(?P<link>https://steamcommunity\.com/sharedfiles/filedetails/\?id=\d+[^"]*)"[^>]*class="item_link"[^>]*>\s*<div class="workshopItemTitle[^"]*">(?P<title>.*?)</div>',
        re.S,
    )
    results = []
    seen_links = set()
    for match in pattern.finditer(html):
        title = unescape(re.sub(r"<.*?>", "", match.group("title")).strip())
        link = match.group("link")
        if not title or link in seen_links:
            continue
        seen_links.add(link)
        results.append({"title": title, "link": link, "query": query})
        if len(results) >= limit:
            break
    return results


def get_workshop_recommendations(report):
    recommendations = []
    seen_links = set()
    for query in build_workshop_queries(report):
        try:
            matches = search_steam_workshop(query, limit=2)
        except Exception:
            continue
        for item in matches:
            if item["link"] in seen_links:
                continue
            seen_links.add(item["link"])
            recommendations.append(item)
            if len(recommendations) >= 5:
                return recommendations
    return recommendations


def build_workshop_lines(report):
    lines = ["", "Mapy z warsztatu Steam do dalszego treningu:"]
    recommendations = get_workshop_recommendations(report)
    if not recommendations:
        lines.append("- Nie udalo sie pobrac propozycji ze Steam Workshop. Sprawdz polaczenie z internetem i sprobuj ponownie.")
        return lines
    for item in recommendations:
        lines.append(f"- {item['title']} | {item['link']}")
    return lines


def build_death_detail_lines(report):
    death_report = report["death_report"]
    limit = report["top_deaths"]
    if death_report.empty:
        return ["", "Brak zgonow do pokazania."]
    lines = ["", f"Najwazniejsze zgony (top {min(limit, len(death_report))}):"]
    for _, death in death_report.head(limit).iterrows():
        round_number = int(death["round_number"]) if pd.notna(death["round_number"]) else -1
        place = death["last_place_name"] if pd.notna(death["last_place_name"]) else "unknown"
        seconds_text = f"{death['time_into_round']:.1f}s" if pd.notna(death["time_into_round"]) else "brak czasu"
        flash_note = "po flashu" if death["was_blinded"] else "na czysto"
        air_note = "w ruchu/skoku" if death["is_airborne"] else "na ziemi"
        attacker_name = death["attacker_name"] if pd.notna(death["attacker_name"]) else "unknown"
        weapon = death["weapon"] if pd.notna(death["weapon"]) else "unknown"
        lines.append(f"- R{round_number}: {seconds_text}, {place}, zabil {attacker_name} ({weapon}), {flash_note}, {air_note}")
    return lines


def render_report_text(report):
    sections = []
    sections.extend(build_summary_lines(report))
    sections.extend(build_coach_review_lines(report))
    sections.extend(build_aim_review_lines(report))
    sections.extend(build_suggestion_lines(report))
    sections.extend(build_training_plan_lines(report))
    sections.extend(build_map_focus_lines(report))
    sections.extend(build_workshop_lines(report))
    sections.extend(build_death_detail_lines(report))
    return "\n".join(sections)


def build_multi_report_text(reports, player_name):
    lines = [f"Zbiorcza analiza gracza: {player_name}", ""]
    issue_counter = {}
    workshop_lines = []
    workshop_seen = set()
    for report in reports:
        lines.append(f"Mapa {report['map_name']} | plik: {Path(report['demo_path']).name}")
        lines.extend(build_coach_review_lines(report)[1:3])
        lines.extend(build_suggestion_lines(report)[1:4])
        hot_zones = get_hot_zones(report)
        if hot_zones:
            lines.append(f"- Strefy do przepracowania na tej mapie: {', '.join(hot_zones)}")
        lines.append("")
        for issue in get_issue_flags(report):
            issue_counter[issue] = issue_counter.get(issue, 0) + 1
        for item in get_workshop_recommendations(report):
            if item["link"] in workshop_seen:
                continue
            workshop_seen.add(item["link"])
            workshop_lines.append(f"- {item['title']} | {item['link']}")

    lines.append("Co powtarza sie przez kilka meczow:")
    if not issue_counter:
        lines.append("- Brak jednego dominujacego problemu. Potrzeba wiecej dem albo bardziej szczegolowej analizy.")
    else:
        for issue, count in sorted(issue_counter.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {issue}: {count}x")

    lines.append("")
    lines.append("Sredni plan poprawy po kilku meczach:")
    if "Crosshair za rzadko na glowie" in issue_counter:
        lines.append("- Codziennie 10 minut botow tylko pod wysokosc glowy.")
    if "Skakanie w duelach" in issue_counter:
        lines.append("- Odetnij jump-duels na wszystkich mapach i zostaw skok tylko pod info.")
    if "Za szybkie otwieranie rund" in issue_counter:
        lines.append("- Przez tydzien graj pierwsze 20 sekund rundy wolniej i zaczynaj od utility/info.")
    if "Za malo utility" in issue_counter:
        lines.append("- Na kazdej mapie przygotuj po 2 proste flash/smoke zagrania do swojej roli.")
    lines.append("- W trening map dodaj wszystkie mapy z tej serii dem i przejdz po 5-10 minut hot zony na kazdej.")

    lines.append("")
    lines.append("Warsztat Steam po pelnej analizie:")
    if workshop_lines:
        lines.extend(workshop_lines[:8])
    else:
        lines.append("- Nie udalo sie pobrac rekomendacji z Workshopu.")
    return "\n".join(lines)


def summarize_report_metrics(report):
    round_summary = report["round_summary"]
    death_report = report["death_report"]
    aim = report["aim_report"]
    rounds = len(round_summary)
    deaths = len(death_report)
    early_rate = (len(death_report[death_report["time_into_round"] <= 20]) / deaths) if deaths else 0.0
    blind_rate = (len(death_report[death_report["was_blinded"] == True]) / deaths) if deaths else 0.0
    airborne_rate = (len(death_report[death_report["is_airborne"] == True]) / deaths) if deaths else 0.0
    utility_per_round = (len(report["grenades"]) / rounds) if rounds else 0.0
    avg_alive_seconds = float(round_summary["alive_seconds"].mean()) if rounds else 0.0
    avg_speed = float(round_summary["avg_speed"].mean()) if rounds else 0.0
    return {
        "rounds": rounds,
        "deaths": deaths,
        "early_rate": early_rate,
        "blind_rate": blind_rate,
        "airborne_rate": airborne_rate,
        "utility_per_round": utility_per_round,
        "avg_alive_seconds": avg_alive_seconds,
        "avg_speed": avg_speed,
        "kills": aim["total_kills"],
        "hs_ratio": aim["hs_kill_ratio"],
        "hit_ratio": aim["hit_per_shot_ratio"],
    }


def build_compare_report_text(my_report, pro_report):
    my_metrics = summarize_report_metrics(my_report)
    pro_metrics = summarize_report_metrics(pro_report)
    lines = [
        f"Porownanie dem: {my_report['player_name']} vs {pro_report['player_name']}",
        f"Mapa / demo 1: {my_report['map_name']} | {Path(my_report['demo_path']).name}",
        f"Mapa / demo 2: {pro_report['map_name']} | {Path(pro_report['demo_path']).name}",
        "",
        "Szybkie porownanie liczb:",
        f"- Czas zycia na runde: Ty {my_metrics['avg_alive_seconds']:.1f}s | Lepszy gracz {pro_metrics['avg_alive_seconds']:.1f}s",
        f"- Wczesne zgony: Ty {my_metrics['early_rate']*100:.0f}% | Lepszy gracz {pro_metrics['early_rate']*100:.0f}%",
        f"- Utility na runde: Ty {my_metrics['utility_per_round']:.2f} | Lepszy gracz {pro_metrics['utility_per_round']:.2f}",
        f"- Zgony po flashu: Ty {my_metrics['blind_rate']*100:.0f}% | Lepszy gracz {pro_metrics['blind_rate']*100:.0f}%",
        f"- Zgony w ruchu/skoku: Ty {my_metrics['airborne_rate']*100:.0f}% | Lepszy gracz {pro_metrics['airborne_rate']*100:.0f}%",
        f"- Trafienia na strzal: Ty {my_metrics['hit_ratio']*100:.0f}% | Lepszy gracz {pro_metrics['hit_ratio']*100:.0f}%",
        "",
        "Co lepszy gracz robi lepiej:",
    ]

    lessons = []
    if pro_metrics["avg_alive_seconds"] > my_metrics["avg_alive_seconds"] + 15:
        lessons.append("- Zyje duzo dluzej, wiec lepiej wybiera moment wejscia i szybciej odpuszcza zle pojedynki.")
    if pro_metrics["early_rate"] + 0.15 < my_metrics["early_rate"]:
        lessons.append("- Rzadziej oddaje darmowe otwarcia. To zwykle znaczy spokojniejszy start rundy i mniej suchych peekow.")
    if pro_metrics["utility_per_round"] > my_metrics["utility_per_round"] + 0.25:
        lessons.append("- Czesciej przygotowuje sobie walke granatem, zamiast brac kontakt na surowo.")
    if pro_metrics["blind_rate"] + 0.10 < my_metrics["blind_rate"]:
        lessons.append("- Lepiej reaguje na utility przeciwnika i rzadziej umiera od razu po flashu.")
    if pro_metrics["airborne_rate"] + 0.10 < my_metrics["airborne_rate"]:
        lessons.append("- Mniej skacze w sytuacjach bojowych i czesciej walczy po pelnym ustawieniu.")
    if pro_metrics["hit_ratio"] > my_metrics["hit_ratio"] + 0.04:
        lessons.append("- Lepszy jest nie tylko mechanicznie, ale tez czesciej walczy z gotowym crosshairem.")
    if not lessons:
        lessons.append("- Roznica nie lezy w jednej liczbie. Bardziej wyglada to na lepsza regularnosc, spacing i mniej ryzykownych decyzji.")
    lines.extend(lessons)

    lines.append("")
    lines.append("Co kopiowac od lepszego gracza:")
    action_items = []
    if pro_metrics["early_rate"] + 0.15 < my_metrics["early_rate"]:
        action_items.append("- Pierwsze 20 sekund graj wolniej: najpierw info, flash albo smoke, dopiero potem pelen kontakt.")
    if pro_metrics["utility_per_round"] > my_metrics["utility_per_round"] + 0.25:
        action_items.append("- Na kazdej mapie przygotuj 2-3 proste zagrania utility pod swoje najczestsze wejscia.")
    if pro_metrics["avg_alive_seconds"] > my_metrics["avg_alive_seconds"] + 15:
        action_items.append("- Po pierwszych 2-3 kulach resetuj pojedynek, zamiast stac w otwartym miejscu.")
    if pro_metrics["airborne_rate"] + 0.10 < my_metrics["airborne_rate"]:
        action_items.append("- Odetnij skoki w pelnych duelach. Skacz tylko po info albo reposition.")
    if pro_metrics["hit_ratio"] > my_metrics["hit_ratio"] + 0.04:
        action_items.append("- Trenuj pre-aim i pierwszy strzal na mapach, na ktorych najczesciej giniesz.")
    if not action_items:
        action_items.append("- Rozpisz 5 rund z obu dem i porownaj tempo wejscia, uzycie utility i moment odwrotu z walki.")
    lines.extend(action_items)

    lines.append("")
    lines.append("Powtarzajace sie slabosci po Twojej stronie:")
    my_issues = get_issue_flags(my_report)
    pro_issues = set(get_issue_flags(pro_report))
    unique_my_issues = [issue for issue in my_issues if issue not in pro_issues]
    if unique_my_issues:
        lines.extend(f"- {issue}" for issue in unique_my_issues)
    else:
        lines.append("- Najwieksza roznica nie lezy w jednym bledzie, tylko w jakosci decyzji runda po rundzie.")
    return "\n".join(lines)


def run_cli(args):
    try:
        demo_path = resolve_demo_path(args)
    except (FileNotFoundError, ValueError) as error:
        print(error)
        print("Uzyj --demo SCIEZKA_DO_PLIKU.dem, uruchom z --pick-demo albo wlacz GUI przez --gui.")
        return

    parser, header, players = load_parser(demo_path)
    if not args.player:
        print(f"Mapa: {header.get('map_name', 'unknown')}")
        print(format_players(players))
        print("Uruchom ponownie z --player TWOJ_NICK")
        return

    report = build_report_data(parser, demo_path, players, args.player, args.top_deaths, header=header)
    print(render_report_text(report))


class LiveCoachWindow:
    def __init__(self, app):
        self.app = app
        self.root = tk.Toplevel(app.root)
        self.root.title("Live Coach Beta")
        self.root.geometry("1180x760")
        self.root.configure(bg=THEMES[app.current_theme_name]["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.queue = Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.session_data = None
        self.session_path = None
        self.capture_region = None
        self.history_items = []
        self.session_armed = False

        self.interval_var = tk.StringVar(value="1.0")
        self.status_var = tk.StringVar(value="Gotowe do startu. Beta czeka na cs2.exe i zaczyna zbierac dane dopiero po wejsciu do aktywnej sceny.")

        self.build_ui()
        self.refresh_history()
        self.poll_queue()

    def build_ui(self):
        palette = THEMES[self.app.current_theme_name]
        wrapper = tk.Frame(self.root, bg=palette["bg"])
        wrapper.pack(fill="both", expand=True, padx=14, pady=14)
        wrapper.grid_columnconfigure(1, weight=1)
        wrapper.grid_rowconfigure(1, weight=1)

        top = ttk.LabelFrame(wrapper, text="Live Coach Beta", style="Card.TLabelframe", padding=12)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        tk.Label(top, text="Bezpieczna analiza obrazu z ekranu. Beta uzbraja sie po kliknieciu Start, czeka na cs2.exe i rusza dopiero po aktywnym obrazie z gry.", fg=palette["muted"], bg=palette["bg"]).grid(row=0, column=0, columnspan=6, sticky="w")
        tk.Label(top, text="Interwal probkowania (s)", fg=palette["text"], bg=palette["bg"]).grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(top, from_=0.5, to=5.0, increment=0.5, width=6, textvariable=self.interval_var).grid(row=1, column=1, sticky="w", padx=(8, 14), pady=(10, 0))
        ttk.Button(top, text="Start sesji", style="Accent.TButton", command=self.start_session).grid(row=1, column=2, padx=(0, 8), pady=(10, 0))
        ttk.Button(top, text="Stop", command=self.stop_session).grid(row=1, column=3, padx=(0, 8), pady=(10, 0))
        ttk.Button(top, text="Odswiez historie", command=self.refresh_history).grid(row=1, column=4, padx=(0, 8), pady=(10, 0))
        ttk.Button(top, text="Usun zaznaczona analize", command=self.delete_selected_session).grid(row=1, column=5, padx=(0, 8), pady=(10, 0))
        tk.Label(top, textvariable=self.status_var, fg=palette["muted"], bg=palette["bg"]).grid(row=2, column=0, columnspan=6, sticky="w", pady=(10, 0))
        tk.Label(top, text="Interwal probkowania = co ile sekund Live Coach robi probe obrazu. Mniejsza wartosc daje wiecej detali, ale bardziej obciaza komputer.", fg=palette["muted"], bg=palette["bg"]).grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))

        history_frame = ttk.LabelFrame(wrapper, text="Historia sesji", style="Card.TLabelframe", padding=10)
        history_frame.grid(row=1, column=0, sticky="nsw", padx=(0, 10))
        self.history_list = tk.Listbox(history_frame, width=38, height=28, bg=palette["panel_alt"], fg=palette["text"], selectbackground=palette["accent"], relief="flat")
        self.history_list.pack(fill="both", expand=True)
        self.history_list.bind("<<ListboxSelect>>", self.on_history_select)

        right = ttk.Frame(wrapper)
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        timeline_frame = ttk.LabelFrame(right, text="Biezacy timeline", style="Card.TLabelframe", padding=10)
        timeline_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        timeline_frame.grid_columnconfigure(0, weight=1)
        timeline_frame.grid_rowconfigure(0, weight=1)
        self.timeline_text = tk.Text(timeline_frame, wrap="word", bg=palette["panel_alt"], fg=palette["text"], insertbackground=palette["text"], relief="flat", font=("Cascadia Mono", 10))
        self.timeline_text.grid(row=0, column=0, sticky="nsew")
        self.timeline_text.insert("1.0", "Po starcie sesji program bedzie dopisywal probki i zdarzenia do timeline.")
        self.timeline_text.config(state="disabled")
        scroll_1 = ttk.Scrollbar(timeline_frame, orient="vertical", command=self.timeline_text.yview)
        scroll_1.grid(row=0, column=1, sticky="ns")
        self.timeline_text.configure(yscrollcommand=scroll_1.set)

        detail_frame = ttk.LabelFrame(right, text="Podglad zapisanej sesji", style="Card.TLabelframe", padding=10)
        detail_frame.grid(row=1, column=0, sticky="nsew")
        detail_frame.grid_columnconfigure(0, weight=1)
        detail_frame.grid_rowconfigure(0, weight=1)
        self.detail_text = tk.Text(detail_frame, wrap="word", bg=palette["panel_alt"], fg=palette["text"], insertbackground=palette["text"], relief="flat", font=("Cascadia Mono", 10))
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        self.detail_text.insert("1.0", "Tutaj zobaczysz zapis sesji z historii.")
        self.detail_text.config(state="disabled")
        scroll_2 = ttk.Scrollbar(detail_frame, orient="vertical", command=self.detail_text.yview)
        scroll_2.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=scroll_2.set)

    def set_text_widget(self, widget, text):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    def append_timeline(self, text):
        self.timeline_text.config(state="normal")
        self.timeline_text.insert("end", text + "\n")
        self.timeline_text.see("end")
        self.timeline_text.config(state="disabled")

    def refresh_history(self):
        self.history_items = list_live_coach_sessions()
        self.history_list.delete(0, "end")
        for item in self.history_items:
            summary = item.get("summary", {})
            suffix = ""
            if summary:
                suffix = f" | zdarzenia {summary.get('events', 0)}"
            self.history_list.insert("end", item["label"] + suffix)

    def on_history_select(self, _event=None):
        selection = self.history_list.curselection()
        if not selection:
            return
        item = self.history_items[selection[0]]
        try:
            data = load_live_coach_session(item["path"])
        except Exception as error:
            self.set_text_widget(self.detail_text, f"Nie udalo sie wczytac sesji.\n\n{error}")
            return
        summary = data.get("summary", {})
        lines = [
            f"Sesja: {data.get('session_label', 'brak')}",
            f"Start: {data.get('started_at', 'brak')}",
            f"Koniec: {data.get('ended_at', 'brak')}",
            f"Probki: {summary.get('samples', 0)}",
            f"Zdarzenia: {summary.get('events', 0)}",
            f"Sredni ruch obrazu: {summary.get('avg_motion', 0.0)}",
            f"Srednia jasnosc: {summary.get('avg_brightness', 0.0)}",
            f"Srednia pewnosc gameplayu: {summary.get('avg_confidence', 0.0)}",
            "",
            "Ostatnie zdarzenia:",
        ]
        for event in data.get("events", [])[-15:]:
            lines.append(f"- {event.get('time', '?')}: {event.get('message', '')}")
        self.set_text_widget(self.detail_text, "\n".join(lines))

    def delete_selected_session(self):
        selection = self.history_list.curselection()
        if not selection:
            messagebox.showwarning("Brak wyboru", "Zaznacz analize z historii Live Coach.", parent=self.root)
            return
        item = self.history_items[selection[0]]
        try:
            Path(item["path"]).unlink(missing_ok=True)
            self.refresh_history()
            self.set_text_widget(self.detail_text, "Usunieto zaznaczona analize z historii Live Coach.")
            self.status_var.set("Usunieto zaznaczona analize.")
        except Exception as error:
            messagebox.showerror("Nie udalo sie usunac", str(error), parent=self.root)

    def start_session(self):
        if self.worker and self.worker.is_alive():
            self.status_var.set("Sesja juz trwa.")
            return
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            messagebox.showwarning("Blad", "Interwal musi byc liczba.", parent=self.root)
            return
        if interval < 0.5:
            interval = 0.5
            self.interval_var.set("0.5")

        ensure_live_coach_dir()
        now = datetime.now()
        self.session_path = LIVE_COACH_DIR / f"session_{now.strftime('%Y%m%d_%H%M%S')}.json"
        self.session_data = {
            "session_label": f"Live Coach {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "started_at": now.isoformat(timespec="seconds"),
            "ended_at": "",
            "interval_seconds": interval,
            "capture_started_at": "",
            "samples": [],
            "events": [],
            "summary": {},
        }
        self.session_path.write_text(json.dumps(self.session_data, ensure_ascii=True, indent=2), encoding="utf-8")
        self.set_text_widget(self.timeline_text, "Sesja uzbrojona. Czekam na uruchomienie cs2.exe i wejscie do aktywnej sceny.\n")
        self.session_armed = True
        self.stop_event.clear()
        self.worker = threading.Thread(target=self.capture_loop, args=(interval,), daemon=True)
        self.worker.start()
        self.status_var.set("Sesja uzbrojona. Uruchom CS2 i wejdz na mape lub do meczu.")

    def stop_session(self):
        self.stop_event.set()
        self.session_armed = False
        self.status_var.set("Zatrzymywanie sesji...")

    def finalize_session(self):
        if not self.session_data or not self.session_path:
            return
        self.session_armed = False
        self.session_data["ended_at"] = datetime.now().isoformat(timespec="seconds")
        self.session_data["summary"] = summarize_live_session(self.session_data)
        self.session_path.write_text(json.dumps(self.session_data, ensure_ascii=True, indent=2), encoding="utf-8")
        self.refresh_history()
        self.status_var.set("Sesja zakonczona i zapisana do historii.")

    def capture_loop(self, interval):
        prev_gray = None
        state = {}
        sample_counter = 0
        timeline_sample_counter = 0
        capture_active = False
        waiting_announced = False
        activation_announced = False
        paused_reason = ""
        active_scene_counter = 0
        try:
            while not self.stop_event.is_set():
                if not is_cs2_running():
                    prev_gray = None
                    capture_active = False
                    active_scene_counter = 0
                    if not waiting_announced:
                        self.queue.put(("info", "Czekam na uruchomienie cs2.exe..."))
                        waiting_announced = True
                    time.sleep(1.0)
                    continue

                if waiting_announced:
                    self.queue.put(("info", "Wykryto cs2.exe. Czekam, az wejdziesz na mape treningowa albo do meczu."))
                    waiting_announced = False

                window_state = get_foreground_window_state()
                if not window_state["is_cs2_foreground"]:
                    prev_gray = None
                    capture_active = False
                    active_scene_counter = 0
                    if paused_reason != "background":
                        self.queue.put(("info", "CS2 nie jest teraz aktywnym oknem albo jest zminimalizowany. Pauzuje analize."))
                        paused_reason = "background"
                    time.sleep(1.0)
                    continue
                if paused_reason:
                    self.queue.put(("info", "CS2 znowu jest na pierwszym planie. Wracam do oczekiwania na aktywna scene gry."))
                    paused_reason = ""

                _, gray = capture_live_frame()
                result = analyze_live_frame(prev_gray, gray, state)
                timestamp = datetime.now().strftime("%H:%M:%S")

                if not capture_active:
                    active_scene = result["gameplay_confidence"] >= 0.62 and result["spectate_risk"] <= 0.62
                    if not active_scene:
                        active_scene_counter = 0
                        if not activation_announced:
                            self.queue.put(("info", "CS2 dziala, ale jeszcze nie widze aktywnej sceny. Wejdz na mape lub do meczu."))
                            activation_announced = True
                        prev_gray = gray
                        time.sleep(interval)
                        continue
                    active_scene_counter += 1
                    if active_scene_counter < 4:
                        prev_gray = gray
                        time.sleep(interval)
                        continue
                    capture_active = True
                    activation_announced = False
                    self.session_data["capture_started_at"] = datetime.now().isoformat(timespec="seconds")
                    self.queue.put(("info", "Wykryto aktywna scene gry. Start zbierania danych Live Coach."))

                sample = {
                    "time": timestamp,
                    "motion": result["motion"],
                    "center_motion": result["center_motion"],
                    "avg_motion_4": result["avg_motion_4"],
                    "avg_center_4": result["avg_center_4"],
                    "hud_score": result["hud_score"],
                    "gameplay_confidence": result["gameplay_confidence"],
                    "spectate_risk": result["spectate_risk"],
                    "brightness": result["brightness"],
                    "center_brightness": result["center_brightness"],
                }
                self.session_data["samples"].append(sample)
                timeline_sample_counter += 1
                if timeline_sample_counter % 3 == 0:
                    self.queue.put(("sample", sample))
                emitted_tags = state.setdefault("recent_tags", {})
                for tag, message in result["events"]:
                    if time.time() - emitted_tags.get(tag, 0.0) < 18:
                        continue
                    emitted_tags[tag] = time.time()
                    event = {"time": timestamp, "tag": tag, "message": message}
                    self.session_data["events"].append(event)
                    self.queue.put(("event", event))
                sample_counter += 1
                if sample_counter % 5 == 0:
                    self.session_path.write_text(json.dumps(self.session_data, ensure_ascii=True, indent=2), encoding="utf-8")
                prev_gray = gray
                time.sleep(interval)
        except Exception as error:
            self.queue.put(("error", str(error)))
        finally:
            self.queue.put(("done", None))

    def poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "sample":
                    self.append_timeline(
                        f"[{payload['time']}] motion={payload['motion']:.2f} center={payload['center_motion']:.2f} avg4={payload['avg_motion_4']:.2f} conf={payload['gameplay_confidence']:.2f} spectate={payload['spectate_risk']:.2f}"
                    )
                elif kind == "info":
                    self.append_timeline(payload)
                    self.status_var.set(payload)
                elif kind == "event":
                    self.append_timeline(f"  -> {payload['message']}")
                elif kind == "error":
                    self.append_timeline(f"BLAD: {payload}")
                    self.status_var.set(f"Live Coach zatrzymany przez blad: {payload}")
                elif kind == "done":
                    self.finalize_session()
        except Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(400, self.poll_queue)

    def on_close(self):
        self.stop_event.set()
        self.root.destroy()


class DemoAnalyzerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Demo Analiz")
        self.root.geometry("1240x860")
        self.current_theme_name = "Grafit"
        self.theme_var = tk.StringVar(value=self.current_theme_name)
        self.root.configure(bg=THEMES[self.current_theme_name]["bg"])
        self.apply_style()
        self.app_config = load_app_config()

        self.demo_path = None
        self.parser = None
        self.header = None
        self.players = None
        self.player_lookup = {}
        self.multi_demo_paths = []
        self.multi_player_lookup = {}
        self.library_items = []
        self.library_lookup = {}
        self.compare_my_demo_path = None
        self.compare_pro_demo_path = None
        self.compare_my_players = None
        self.compare_pro_players = None
        self.compare_my_lookup = {}
        self.compare_pro_lookup = {}
        self.live_window = None

        self.demo_var = tk.StringVar(value="Nie wybrano dema")
        self.player_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Wybierz demo i gracza.")
        self.top_deaths_var = tk.StringVar(value="5")

        self.multi_status_var = tk.StringVar(value="Dodaj kilka dem z tego samego gracza.")
        self.multi_player_var = tk.StringVar()
        self.multi_top_deaths_var = tk.StringVar(value="3")
        self.library_status_var = tk.StringVar(value="Biblioteka dem przechowuje pliki lokalnie, zeby nie wybierac ich od nowa.")
        self.compare_status_var = tk.StringVar(value="Porownaj dwa dema: Twoje i lepszego gracza.")
        self.compare_my_demo_var = tk.StringVar(value="Nie wybrano Twojego dema")
        self.compare_pro_demo_var = tk.StringVar(value="Nie wybrano dema lepszego gracza")
        self.compare_my_player_var = tk.StringVar()
        self.compare_pro_player_var = tk.StringVar()
        self.compare_top_deaths_var = tk.StringVar(value="5")
        self.github_owner_var = tk.StringVar(value=self.app_config.get("github_owner", DEFAULT_GITHUB_OWNER))
        self.github_repo_var = tk.StringVar(value=self.app_config.get("github_repo", DEFAULT_GITHUB_REPO))
        self.github_asset_var = tk.StringVar(value=self.app_config.get("github_asset_name", DEFAULT_RELEASE_ASSET_NAME))
        self.update_status_var = tk.StringVar(value=f"Wersja programu: {APP_VERSION}")
        self.page_frames = {}
        self.nav_buttons = {}
        self.active_page = None

        self.build_ui()
        self.refresh_library_list()

    def apply_style(self):
        palette = THEMES[self.current_theme_name]
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=palette["bg"], foreground=palette["text"], fieldbackground=palette["input_bg"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("Card.TLabelframe", background=palette["card"], foreground=palette["text"])
        style.configure("Card.TLabelframe.Label", background=palette["card"], foreground=palette["text"], font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TButton", background=palette["accent"], foreground="white", padding=8)
        style.map("Accent.TButton", background=[("active", palette["accent_hover"])])
        style.configure("TButton", background=palette["panel"], foreground=palette["text"], padding=8)
        style.map("TButton", background=[("active", palette["nav_active"])])
        style.configure("TEntry", fieldbackground=palette["input_bg"], foreground=palette["input_text"], insertcolor=palette["input_text"])
        style.configure("TCombobox", fieldbackground=palette["input_bg"], foreground=palette["input_text"], arrowsize=16)
        style.map("TCombobox",
                  fieldbackground=[("readonly", palette["input_bg"])],
                  foreground=[("readonly", palette["input_text"])],
                  selectforeground=[("readonly", palette["input_text"])],
                  selectbackground=[("readonly", "#d9e8ff")])
        style.configure("TSpinbox", fieldbackground=palette["input_bg"], foreground=palette["input_text"], arrowsize=14)
        self.root.configure(bg=palette["bg"])

    def build_ui(self):
        palette = THEMES[self.current_theme_name]
        wrapper = ttk.Frame(self.root, padding=14)
        wrapper.pack(fill="both", expand=True)
        wrapper.columnconfigure(1, weight=1)
        wrapper.rowconfigure(1, weight=1)

        hero = ttk.Frame(wrapper)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        tk.Label(hero, text="Demo Analiz", font=("Segoe UI Semibold", 20), fg=palette["text"], bg=palette["bg"]).pack(anchor="w")
        tk.Label(hero, text="Ladniejszy dashboard, analiza kilku dem i raport prostszym jezykiem.", fg=palette["muted"], bg=palette["bg"]).pack(anchor="w", pady=(4, 0))

        sidebar = tk.Frame(wrapper, bg=palette["panel"], width=220)
        sidebar.grid(row=1, column=0, sticky="nsw", padx=(0, 12))
        sidebar.grid_propagate(False)
        tk.Label(sidebar, text="Sekcje", font=("Segoe UI Semibold", 13), fg=palette["text"], bg=palette["panel"]).pack(anchor="w", padx=14, pady=(14, 10))
        live_button = tk.Button(
            sidebar,
            text="Live Coach Beta",
            anchor="w",
            relief="flat",
            bd=0,
            padx=14,
            pady=10,
            font=("Segoe UI Semibold", 11),
            fg="white",
            bg=palette["accent"],
            activeforeground="white",
            activebackground=palette["accent_hover"],
            command=self.open_live_coach_window,
        )
        live_button.pack(fill="x", padx=10, pady=(0, 12))

        content = ttk.Frame(wrapper)
        content.grid(row=1, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.content_container = content

        page_specs = [
            ("single", "Pojedyncze demo", self.build_single_tab),
            ("multi", "Kilka dem", self.build_multi_tab),
            ("compare", "Porownanie 2 dem", self.build_compare_tab),
            ("library", "Biblioteka dem", self.build_library_tab),
            ("settings", "Ustawienia", self.build_settings_tab),
            ("glossary", "Slownik", self.build_glossary_tab),
        ]
        self.page_order = [key for key, _, _ in page_specs]

        for key, label, builder in page_specs:
            button = tk.Button(
                sidebar,
                text=label,
                anchor="w",
                relief="flat",
                bd=0,
                padx=14,
                pady=10,
                font=("Segoe UI", 11),
                fg=palette["text"],
                bg=palette["nav_idle"],
                activeforeground=palette["text"],
                activebackground=palette["nav_active"],
                command=lambda page_key=key: self.select_page(page_key),
            )
            button.pack(fill="x", padx=10, pady=4)
            self.nav_buttons[key] = button

            frame = ttk.Frame(content)
            frame.grid(row=0, column=0, sticky="nsew")
            self.page_frames[key] = frame
            builder(frame)

        self.select_page("single")

    def build_single_tab(self, parent):
        palette = THEMES[self.current_theme_name]
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        top = ttk.LabelFrame(parent, text="Demo", style="Card.TLabelframe", padding=12)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        top.columnconfigure(1, weight=1)
        ttk.Button(top, text="Wybierz .dem", style="Accent.TButton", command=self.choose_demo).grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(top, textvariable=self.demo_var, state="readonly").grid(row=0, column=1, sticky="ew")
        tk.Label(top, textvariable=self.status_var, fg=palette["muted"], bg=palette["bg"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        controls = ttk.LabelFrame(parent, text="Analiza", style="Card.TLabelframe", padding=12)
        controls.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        controls.columnconfigure(1, weight=1)
        tk.Label(controls, text="Gracz", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.player_combo = ttk.Combobox(controls, textvariable=self.player_var, state="disabled", values=[])
        self.player_combo.grid(row=0, column=1, sticky="ew")
        tk.Label(controls, text="Top zgonow", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Spinbox(controls, from_=1, to=15, width=5, textvariable=self.top_deaths_var).grid(row=0, column=3, sticky="w")
        ttk.Button(controls, text="Analizuj mecz", style="Accent.TButton", command=self.run_analysis).grid(row=0, column=4, padx=(12, 0))
        tk.Label(controls, text="Top zgonow = ile smierci rozpisac szczegolowo w raporcie.", fg=palette["muted"], bg=palette["bg"]).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        meta = ttk.LabelFrame(parent, text="Info", style="Card.TLabelframe", padding=12)
        meta.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        self.meta_label = tk.Label(meta, text="Mapa: -", fg=palette["text"], bg=palette["bg"])
        self.meta_label.pack(anchor="w")

        report_frame = ttk.LabelFrame(parent, text="Raport", style="Card.TLabelframe", padding=10)
        report_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=8)
        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)
        self.report_text = tk.Text(report_frame, wrap="word", bg=palette["panel_alt"], fg=palette["text"], insertbackground=palette["text"], relief="flat", font=("Cascadia Mono", 10))
        self.report_text.grid(row=0, column=0, sticky="nsew")
        self.report_text.insert("1.0", "Raport pojawi sie tutaj po wczytaniu dema.")
        self.report_text.config(state="disabled")
        scrollbar = ttk.Scrollbar(report_frame, orient="vertical", command=self.report_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.report_text.configure(yscrollcommand=scrollbar.set)

    def build_multi_tab(self, parent):
        palette = THEMES[self.current_theme_name]
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        picker = ttk.LabelFrame(parent, text="Kilka dem naraz", style="Card.TLabelframe", padding=12)
        picker.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        picker.columnconfigure(1, weight=1)
        ttk.Button(picker, text="Dodaj dema", style="Accent.TButton", command=self.choose_multiple_demos).grid(row=0, column=0, padx=(0, 10))
        tk.Label(picker, textvariable=self.multi_status_var, fg=palette["muted"], bg=palette["bg"]).grid(row=0, column=1, sticky="w")

        controls = ttk.LabelFrame(parent, text="Ustawienia serii", style="Card.TLabelframe", padding=12)
        controls.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        controls.columnconfigure(1, weight=1)
        tk.Label(controls, text="Gracz", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.multi_player_combo = ttk.Combobox(controls, textvariable=self.multi_player_var, state="disabled", values=[])
        self.multi_player_combo.grid(row=0, column=1, sticky="ew")
        tk.Label(controls, text="Top zgonow", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Spinbox(controls, from_=1, to=10, width=5, textvariable=self.multi_top_deaths_var).grid(row=0, column=3, sticky="w")
        ttk.Button(controls, text="Analizuj serie", style="Accent.TButton", command=self.run_multi_analysis).grid(row=0, column=4, padx=(12, 0))
        tk.Label(controls, text="Top zgonow dotyczy liczby najbardziej wartosciowych smierci opisanych nizej.", fg=palette["muted"], bg=palette["bg"]).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        body = ttk.Frame(parent)
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(body, text="Wybrane pliki", style="Card.TLabelframe", padding=10)
        list_frame.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        self.multi_list = tk.Listbox(list_frame, width=42, height=24, bg=palette["panel_alt"], fg=palette["text"], selectbackground=palette["accent"], relief="flat")
        self.multi_list.pack(fill="both", expand=True)

        result_frame = ttk.LabelFrame(body, text="Raport zbiorczy", style="Card.TLabelframe", padding=10)
        result_frame.grid(row=0, column=1, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self.multi_report_text = tk.Text(result_frame, wrap="word", bg=palette["panel_alt"], fg=palette["text"], insertbackground=palette["text"], relief="flat", font=("Cascadia Mono", 10))
        self.multi_report_text.grid(row=0, column=0, sticky="nsew")
        self.multi_report_text.insert("1.0", "Tutaj pojawi sie raport z kilku dem i lista powtarzajacych sie bledow.")
        self.multi_report_text.config(state="disabled")
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.multi_report_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.multi_report_text.configure(yscrollcommand=scrollbar.set)

    def build_glossary_tab(self, parent):
        palette = THEMES[self.current_theme_name]
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        frame = ttk.LabelFrame(parent, text="Wyjasnienie slow", style="Card.TLabelframe", padding=10)
        frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        glossary = tk.Text(frame, wrap="word", bg=palette["panel_alt"], fg=palette["text"], relief="flat", font=("Segoe UI", 11))
        glossary.grid(row=0, column=0, sticky="nsew")
        glossary.insert("1.0", GLOSSARY_TEXT)
        glossary.config(state="disabled")

    def build_library_tab(self, parent):
        palette = THEMES[self.current_theme_name]
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(parent, text="Stala biblioteka dem", style="Card.TLabelframe", padding=12)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ttk.Button(top, text="Dodaj dema do biblioteki", style="Accent.TButton", command=self.import_to_library).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(top, text="Usun zaznaczone", command=self.remove_selected_library_items).grid(row=0, column=1, padx=(0, 10))
        ttk.Button(top, text="Odswiez", command=self.refresh_library_list).grid(row=0, column=2, padx=(0, 10))
        tk.Label(top, textvariable=self.library_status_var, fg=palette["muted"], bg=palette["bg"]).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        body = ttk.Frame(parent)
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(body, text="Pliki w bibliotece", style="Card.TLabelframe", padding=10)
        list_frame.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        self.library_list = tk.Listbox(list_frame, width=44, height=25, bg=palette["panel_alt"], fg=palette["text"], selectbackground=palette["accent"], relief="flat", selectmode="extended")
        self.library_list.pack(fill="both", expand=True)
        self.library_list.bind("<<ListboxSelect>>", self.on_library_select)

        detail = ttk.LabelFrame(body, text="Akcje i szczegoly", style="Card.TLabelframe", padding=10)
        detail.grid(row=0, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(0, weight=1)
        self.library_detail_text = tk.Text(detail, wrap="word", bg=palette["panel_alt"], fg=palette["text"], insertbackground=palette["text"], relief="flat", font=("Cascadia Mono", 10))
        self.library_detail_text.grid(row=0, column=0, sticky="nsew")
        self.library_detail_text.insert("1.0", "Dodaj dema do biblioteki, zeby trzymac je stale w programie.\n\nMozesz potem ladowac je do pojedynczej analizy, serii lub porownania 2 dem.")
        self.library_detail_text.config(state="disabled")
        scrollbar = ttk.Scrollbar(detail, orient="vertical", command=self.library_detail_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.library_detail_text.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(detail)
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Wczytaj do pojedynczej analizy", command=self.load_library_item_to_single).pack(side="left")
        ttk.Button(actions, text="Dodaj zaznaczone do serii", command=self.add_library_items_to_multi).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Ustaw jako moje demo", command=self.set_library_item_as_compare_my).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Ustaw jako pro demo", command=self.set_library_item_as_compare_pro).pack(side="left", padx=(10, 0))

    def build_compare_tab(self, parent):
        palette = THEMES[self.current_theme_name]
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        demos = ttk.LabelFrame(parent, text="Dwa dema do porownania", style="Card.TLabelframe", padding=12)
        demos.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        demos.columnconfigure(1, weight=1)
        demos.columnconfigure(4, weight=1)
        ttk.Button(demos, text="Wybierz moje demo", style="Accent.TButton", command=self.choose_compare_my_demo).grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(demos, textvariable=self.compare_my_demo_var, state="readonly").grid(row=0, column=1, sticky="ew")
        ttk.Button(demos, text="Wybierz demo pro", style="Accent.TButton", command=self.choose_compare_pro_demo).grid(row=0, column=3, padx=(16, 10))
        ttk.Entry(demos, textvariable=self.compare_pro_demo_var, state="readonly").grid(row=0, column=4, sticky="ew")
        tk.Label(demos, textvariable=self.compare_status_var, fg=palette["muted"], bg=palette["bg"]).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        players = ttk.LabelFrame(parent, text="Gracze i analiza", style="Card.TLabelframe", padding=12)
        players.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        players.columnconfigure(1, weight=1)
        players.columnconfigure(3, weight=1)
        tk.Label(players, text="Ty", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.compare_my_player_combo = ttk.Combobox(players, textvariable=self.compare_my_player_var, state="disabled", values=[])
        self.compare_my_player_combo.grid(row=0, column=1, sticky="ew")
        tk.Label(players, text="Lepszy gracz", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=2, sticky="w", padx=(12, 8))
        self.compare_pro_player_combo = ttk.Combobox(players, textvariable=self.compare_pro_player_var, state="disabled", values=[])
        self.compare_pro_player_combo.grid(row=0, column=3, sticky="ew")
        tk.Label(players, text="Top zgonow", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=4, sticky="w", padx=(12, 8))
        ttk.Spinbox(players, from_=1, to=10, width=5, textvariable=self.compare_top_deaths_var).grid(row=0, column=5, sticky="w")
        ttk.Button(players, text="Porownaj", style="Accent.TButton", command=self.run_compare_analysis).grid(row=0, column=6, padx=(12, 0))
        tk.Label(players, text="Porownanie pokazuje, co lepszy gracz robi w podobnych sytuacjach i jak to przeniesc do Twojej gry.", fg=palette["muted"], bg=palette["bg"]).grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))

        result = ttk.LabelFrame(parent, text="Raport porownawczy", style="Card.TLabelframe", padding=10)
        result.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        result.columnconfigure(0, weight=1)
        result.rowconfigure(0, weight=1)
        self.compare_report_text = tk.Text(result, wrap="word", bg=palette["panel_alt"], fg=palette["text"], insertbackground=palette["text"], relief="flat", font=("Cascadia Mono", 10))
        self.compare_report_text.grid(row=0, column=0, sticky="nsew")
        self.compare_report_text.insert("1.0", "Tutaj pojawi sie porownanie Twojego dema z demem lepszego gracza.")
        self.compare_report_text.config(state="disabled")
        scrollbar = ttk.Scrollbar(result, orient="vertical", command=self.compare_report_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.compare_report_text.configure(yscrollcommand=scrollbar.set)

    def build_settings_tab(self, parent):
        palette = THEMES[self.current_theme_name]
        parent.columnconfigure(0, weight=1)
        settings = ttk.LabelFrame(parent, text="Wyglad i motyw", style="Card.TLabelframe", padding=14)
        settings.grid(row=0, column=0, sticky="new", padx=8, pady=8)
        settings.columnconfigure(1, weight=1)
        tk.Label(settings, text="Motyw", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.theme_combo = ttk.Combobox(settings, textvariable=self.theme_var, state="readonly", values=list(THEMES.keys()))
        self.theme_combo.grid(row=0, column=1, sticky="ew")
        ttk.Button(settings, text="Zastosuj motyw", style="Accent.TButton", command=self.change_theme).grid(row=0, column=2, padx=(12, 0))
        preview = tk.Label(
            settings,
            text="Zmiana motywu odswieza caly interfejs programu i poprawia czytelnosc pol wyboru.",
            fg=palette["muted"],
            bg=palette["bg"],
        )
        preview.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        updates = ttk.LabelFrame(parent, text="Aktualizacje programu", style="Card.TLabelframe", padding=14)
        updates.grid(row=1, column=0, sticky="new", padx=8, pady=(0, 8))
        updates.columnconfigure(1, weight=1)
        tk.Label(updates, text="Wersja", fg=palette["text"], bg=palette["bg"]).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Label(updates, text=APP_VERSION, fg=palette["text"], bg=palette["bg"]).grid(row=0, column=1, sticky="w")
        tk.Label(updates, text="GitHub owner", fg=palette["text"], bg=palette["bg"]).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Entry(updates, textvariable=self.github_owner_var).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        tk.Label(updates, text="Repozytorium", fg=palette["text"], bg=palette["bg"]).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Entry(updates, textvariable=self.github_repo_var).grid(row=2, column=1, sticky="ew", pady=(10, 0))
        tk.Label(updates, text="Plik w release", fg=palette["text"], bg=palette["bg"]).grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Entry(updates, textvariable=self.github_asset_var).grid(row=3, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(updates, text="Zapisz GitHub", command=self.save_update_settings).grid(row=3, column=2, padx=(12, 0), pady=(10, 0))
        ttk.Button(updates, text="Sprawdz aktualizacje", style="Accent.TButton", command=self.check_for_updates).grid(row=4, column=2, padx=(12, 0), pady=(10, 0))
        tk.Label(updates, textvariable=self.update_status_var, fg=palette["muted"], bg=palette["bg"]).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(
            updates,
            text="Po ustawieniu ownera i repo program sprawdza najnowszy GitHub Release, pobiera nowe .exe i podmienia je po zamknieciu aplikacji.",
            fg=palette["muted"],
            bg=palette["bg"],
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def select_page(self, page_key):
        if page_key not in self.page_frames:
            return
        self.active_page = page_key
        self.page_frames[page_key].tkraise()
        palette = THEMES[self.current_theme_name]
        for key, button in self.nav_buttons.items():
            active = key == page_key
            button.configure(
                bg=palette["nav_active"] if active else palette["nav_idle"],
                fg=palette["text"],
                activebackground=palette["nav_active"],
                activeforeground=palette["text"],
            )

    def rebuild_interface(self):
        for child in self.root.winfo_children():
            child.destroy()
        self.page_frames = {}
        self.nav_buttons = {}
        self.apply_style()
        self.build_ui()
        self.refresh_library_list()
        if self.demo_path:
            try:
                self.load_demo_into_single(self.demo_path)
            except Exception:
                pass
        if self.multi_demo_paths:
            for path in self.multi_demo_paths:
                if hasattr(self, "multi_list"):
                    self.multi_list.insert("end", Path(path).name)
            self.refresh_multi_player_choices()
        if self.compare_my_demo_path:
            try:
                self.load_compare_demo(self.compare_my_demo_path, "my")
            except Exception:
                pass
        if self.compare_pro_demo_path:
            try:
                self.load_compare_demo(self.compare_pro_demo_path, "pro")
            except Exception:
                pass
        if self.active_page:
            self.select_page(self.active_page)

    def change_theme(self):
        selected = self.theme_var.get().strip()
        if selected not in THEMES:
            return
        self.current_theme_name = selected
        self.rebuild_interface()

    def save_update_settings(self):
        self.app_config["github_owner"] = self.github_owner_var.get().strip()
        self.app_config["github_repo"] = self.github_repo_var.get().strip()
        self.app_config["github_asset_name"] = self.github_asset_var.get().strip() or DEFAULT_RELEASE_ASSET_NAME
        save_app_config(self.app_config)
        self.update_status_var.set("Zapisano ustawienia GitHub Releases.")

    def check_for_updates(self):
        owner = self.github_owner_var.get().strip()
        repo = self.github_repo_var.get().strip()
        asset_name = self.github_asset_var.get().strip() or DEFAULT_RELEASE_ASSET_NAME
        if not owner or not repo:
            messagebox.showwarning("Brak danych", "Najpierw wpisz ownera i repozytorium GitHub w Ustawieniach.", parent=self.root)
            return
        self.save_update_settings()
        try:
            release_data = fetch_github_latest_release(owner, repo)
        except Exception as error:
            messagebox.showerror("Aktualizacja nieudana", f"Nie udalo sie pobrac danych najnowszego releasu z GitHub.\n\n{error}", parent=self.root)
            self.update_status_var.set("Nie udalo sie pobrac informacji o aktualizacji.")
            return

        latest_version = extract_release_version(release_data)
        notes = str(release_data.get("body", "")).strip()
        asset = find_release_asset(release_data, asset_name)
        download_url = str((asset or {}).get("browser_download_url", "")).strip()
        if not latest_version:
            messagebox.showerror("Bledny release", "GitHub Release nie ma poprawnego tagu albo nazwy wersji.", parent=self.root)
            self.update_status_var.set("Release na GitHubie jest niepoprawny.")
            return
        if not download_url:
            messagebox.showerror("Brak pliku", f"W najnowszym release nie znaleziono pliku '{asset_name}'.", parent=self.root)
            self.update_status_var.set("Brakuje pliku .exe w najnowszym release.")
            return

        if parse_version_text(latest_version) <= parse_version_text(APP_VERSION):
            self.update_status_var.set(f"Brak nowszej wersji. Masz juz {APP_VERSION}.")
            messagebox.showinfo("Aktualizacje", f"Masz juz najnowsza wersje: {APP_VERSION}.", parent=self.root)
            return

        question = f"Znaleziono nowa wersje: {latest_version}\nObecna wersja: {APP_VERSION}"
        if notes:
            question += f"\n\nZmiany:\n{notes}"
        question += "\n\nPobrac i zainstalowac po zamknieciu programu?"
        if not messagebox.askyesno("Nowa aktualizacja", question, parent=self.root):
            self.update_status_var.set("Aktualizacja znaleziona, ale nie zostala pobrana.")
            return

        try:
            self.update_status_var.set(f"Pobieram wersje {latest_version}...")
            self.root.update_idletasks()
            downloaded = download_update_file(download_url)
        except Exception as error:
            messagebox.showerror("Pobieranie nieudane", f"Nie udalo sie pobrac nowej wersji.\n\n{error}", parent=self.root)
            self.update_status_var.set("Pobieranie aktualizacji nie powiodlo sie.")
            return

        if not getattr(sys, "frozen", False):
            self.update_status_var.set(f"Pobrano nowy plik do: {downloaded}")
            messagebox.showinfo("Pobrano aktualizacje", f"Pobrano nowa wersje do pliku:\n{downloaded}\n\nW trybie Python nie podmieniam automatycznie programu.", parent=self.root)
            return

        current_exe = current_app_executable()
        apply_downloaded_update(downloaded, current_exe)
        self.update_status_var.set(f"Gotowe. Program zamknie sie i uruchomi wersje {latest_version}.")
        messagebox.showinfo("Aktualizacja gotowa", "Program zamknie sie teraz, podmieni plik .exe i uruchomi nowa wersje.", parent=self.root)
        self.root.after(300, self.root.destroy)

    def open_live_coach_window(self):
        if self.live_window and self.live_window.root.winfo_exists():
            self.live_window.root.lift()
            self.live_window.root.focus_force()
            return
        self.live_window = LiveCoachWindow(self)

    def set_text_widget(self, widget, text):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    def load_demo_into_single(self, demo_path):
        parser, header, players = load_parser(demo_path)
        self.demo_path = Path(demo_path).resolve()
        self.parser = parser
        self.header = header
        self.players = players
        self.demo_var.set(str(self.demo_path))
        self.meta_label.config(text=f"Mapa: {header.get('map_name', 'unknown')} | Serwer: {header.get('server_name', 'unknown')}")
        values, lookup = build_player_option_maps(players)
        self.player_lookup = lookup
        self.player_combo.config(values=values, state="readonly")
        if values:
            self.player_combo.current(0)
        self.status_var.set(f"Wczytano demo. Graczy: {len(values)}.")
        self.set_text_widget(self.report_text, format_players(players))

    def refresh_library_list(self):
        self.library_items = load_demo_library_index()
        self.library_lookup = {}
        if hasattr(self, "library_list"):
            self.library_list.delete(0, "end")
            for item in self.library_items:
                label = item["name"]
                self.library_list.insert("end", label)
                self.library_lookup[label] = item
        self.library_status_var.set(f"Biblioteka zawiera {len(self.library_items)} dem.")

    def get_selected_library_items(self):
        selected = []
        if not hasattr(self, "library_list"):
            return selected
        for idx in self.library_list.curselection():
            if 0 <= idx < len(self.library_items):
                selected.append(self.library_items[idx])
        return selected

    def on_library_select(self, _event=None):
        items = self.get_selected_library_items()
        if not items:
            self.set_text_widget(self.library_detail_text, "Wybierz demo z listy, aby zobaczyc szczegoly i zaladowac je do analiz.")
            return
        lines = []
        for item in items:
            lines.append(f"Plik: {item['name']}")
            lines.append(f"Sciezka: {item['path']}")
            if item.get("source"):
                lines.append(f"Zrodlo: {item['source']}")
            lines.append("")
        self.set_text_widget(self.library_detail_text, "\n".join(lines).strip())

    def import_to_library(self):
        selected = pick_demo_files(self.root)
        if not selected:
            return
        added, skipped = import_demos_to_library(selected)
        self.refresh_library_list()
        if added:
            self.library_status_var.set(f"Dodano {len(added)} nowych dem do biblioteki.")
        elif skipped:
            self.library_status_var.set("Te dema byly juz w bibliotece albo nie nadawaly sie do importu.")

    def remove_selected_library_items(self):
        items = self.get_selected_library_items()
        if not items:
            messagebox.showwarning("Brak wyboru", "Zaznacz przynajmniej jedno demo w bibliotece.", parent=self.root)
            return
        removed = remove_demo_library_items([item["path"] for item in items])
        self.refresh_library_list()
        self.set_text_widget(self.library_detail_text, "Usunieto wybrane dema z biblioteki.")
        self.library_status_var.set(f"Usunieto {len(removed)} dem z biblioteki.")

    def load_library_item_to_single(self):
        items = self.get_selected_library_items()
        if not items:
            messagebox.showwarning("Brak wyboru", "Zaznacz jedno demo w bibliotece.", parent=self.root)
            return
        try:
            self.load_demo_into_single(Path(items[0]["path"]))
            self.select_page("single")
        except Exception as error:
            messagebox.showerror("Blad wczytywania dema", str(error), parent=self.root)

    def add_library_items_to_multi(self):
        items = self.get_selected_library_items()
        if not items:
            messagebox.showwarning("Brak wyboru", "Zaznacz przynajmniej jedno demo w bibliotece.", parent=self.root)
            return
        existing = {str(path) for path in self.multi_demo_paths}
        added = 0
        for item in items:
            path = Path(item["path"]).resolve()
            if str(path) in existing:
                continue
            existing.add(str(path))
            self.multi_demo_paths.append(path)
            added += 1
        self.multi_list.delete(0, "end")
        for path in self.multi_demo_paths:
            self.multi_list.insert("end", path.name)
        self.refresh_multi_player_choices()
        self.multi_status_var.set(f"Dodano {added} dem z biblioteki. Lacznie w serii: {len(self.multi_demo_paths)}.")
        self.select_page("multi")

    def load_compare_demo(self, demo_path, side):
        parser, header, players = load_parser(demo_path)
        values, lookup = build_player_option_maps(players)
        if side == "my":
            self.compare_my_demo_path = Path(demo_path).resolve()
            self.compare_my_players = players
            self.compare_my_lookup = lookup
            self.compare_my_demo_var.set(str(self.compare_my_demo_path))
            self.compare_my_player_combo.config(values=values, state="readonly")
            if values:
                self.compare_my_player_combo.current(0)
        else:
            self.compare_pro_demo_path = Path(demo_path).resolve()
            self.compare_pro_players = players
            self.compare_pro_lookup = lookup
            self.compare_pro_demo_var.set(str(self.compare_pro_demo_path))
            self.compare_pro_player_combo.config(values=values, state="readonly")
            if values:
                self.compare_pro_player_combo.current(0)
        return parser, header, players

    def choose_compare_my_demo(self):
        selected = pick_demo_file(self.root)
        if not selected:
            return
        try:
            self.load_compare_demo(Path(selected).resolve(), "my")
            self.compare_status_var.set("Wczytano Twoje demo do porownania.")
        except Exception as error:
            messagebox.showerror("Blad wczytywania dema", str(error), parent=self.root)

    def choose_compare_pro_demo(self):
        selected = pick_demo_file(self.root)
        if not selected:
            return
        try:
            self.load_compare_demo(Path(selected).resolve(), "pro")
            self.compare_status_var.set("Wczytano demo lepszego gracza do porownania.")
        except Exception as error:
            messagebox.showerror("Blad wczytywania dema", str(error), parent=self.root)

    def set_library_item_as_compare_my(self):
        items = self.get_selected_library_items()
        if not items:
            messagebox.showwarning("Brak wyboru", "Zaznacz demo w bibliotece.", parent=self.root)
            return
        try:
            self.load_compare_demo(Path(items[0]["path"]), "my")
            self.compare_status_var.set("Ustawiono Twoje demo z biblioteki.")
            self.select_page("compare")
        except Exception as error:
            messagebox.showerror("Blad wczytywania dema", str(error), parent=self.root)

    def set_library_item_as_compare_pro(self):
        items = self.get_selected_library_items()
        if not items:
            messagebox.showwarning("Brak wyboru", "Zaznacz demo w bibliotece.", parent=self.root)
            return
        try:
            self.load_compare_demo(Path(items[0]["path"]), "pro")
            self.compare_status_var.set("Ustawiono demo lepszego gracza z biblioteki.")
            self.select_page("compare")
        except Exception as error:
            messagebox.showerror("Blad wczytywania dema", str(error), parent=self.root)

    def choose_demo(self):
        selected = pick_demo_file(self.root)
        if not selected:
            return
        try:
            demo_path = Path(selected).resolve()
            self.load_demo_into_single(demo_path)
        except Exception as error:
            messagebox.showerror("Blad wczytywania dema", str(error), parent=self.root)
            self.status_var.set("Nie udalo sie wczytac dema.")
            return

    def run_analysis(self):
        if self.parser is None or self.players is None or self.demo_path is None:
            messagebox.showwarning("Brak dema", "Najpierw wybierz plik .dem.", parent=self.root)
            return
        selected_label = self.player_var.get().strip()
        if not selected_label:
            messagebox.showwarning("Brak gracza", "Wybierz gracza z listy.", parent=self.root)
            return
        try:
            top_deaths = int(self.top_deaths_var.get())
        except ValueError:
            messagebox.showwarning("Blad", "Top zgonow musi byc liczba.", parent=self.root)
            return

        player_name = self.player_lookup.get(selected_label, selected_label)
        try:
            self.status_var.set(f"Analizuje mecz gracza {player_name}...")
            self.root.update_idletasks()
            report = build_report_data(self.parser, self.demo_path, self.players, player_name, top_deaths, header=self.header)
            self.set_text_widget(self.report_text, render_report_text(report))
            self.status_var.set(f"Gotowe. Analiza zakonczona dla {player_name}.")
        except Exception as error:
            messagebox.showerror("Analiza nie powiodla sie", str(error), parent=self.root)
            self.status_var.set("Analiza nie powiodla sie.")

    def choose_multiple_demos(self):
        selected = pick_demo_files(self.root)
        if not selected:
            return
        existing = {str(path) for path in self.multi_demo_paths}
        added = 0
        for raw_path in selected:
            path = Path(raw_path).resolve()
            if str(path) in existing:
                continue
            self.multi_demo_paths.append(path)
            existing.add(str(path))
            added += 1
        self.multi_list.delete(0, "end")
        for path in self.multi_demo_paths:
            self.multi_list.insert("end", path.name)
        self.refresh_multi_player_choices()
        if added:
            self.multi_status_var.set(f"Dodano {added} plikow. Lacznie w serii: {len(self.multi_demo_paths)}.")
        else:
            self.multi_status_var.set(f"Te dema byly juz dodane. Lacznie w serii: {len(self.multi_demo_paths)}.")

    def refresh_multi_player_choices(self):
        player_counts = {}
        player_rows = {}
        failed = 0
        for demo_path in self.multi_demo_paths:
            try:
                parser = DemoParser(str(demo_path))
                players = load_players(parser)
            except Exception:
                failed += 1
                continue
            seen_in_demo = set()
            for _, row in players.iterrows():
                name = str(row["name"])
                key = name.lower()
                if key in seen_in_demo:
                    continue
                seen_in_demo.add(key)
                player_counts[name] = player_counts.get(name, 0) + 1
                player_rows.setdefault(name, row)

        if not player_rows:
            self.multi_player_lookup = {}
            self.multi_player_combo.config(values=[], state="disabled")
            self.multi_player_var.set("")
            if self.multi_demo_paths:
                self.multi_status_var.set("Dodano dema, ale nie udalo sie odczytac list graczy.")
            return

        ordered_rows = pd.DataFrame(player_rows.values()).sort_values(["name"]).reset_index(drop=True)
        counts_text = {name: f"w {count}/{len(self.multi_demo_paths)} demach" for name, count in player_counts.items()}
        values, lookup = build_player_option_maps(ordered_rows, with_counts=counts_text)
        self.multi_player_lookup = lookup
        self.multi_player_combo.config(values=values, state="readonly")

        current = self.multi_player_var.get().strip()
        if current not in lookup:
            best_name = max(player_counts.items(), key=lambda item: (item[1], item[0].lower()))[0]
            for label, mapped_name in lookup.items():
                if mapped_name == best_name:
                    self.multi_player_var.set(label)
                    break

        if failed:
            self.multi_status_var.set(
                f"Dodano {len(self.multi_demo_paths)} dem. Liste graczy zbudowano z pominieciem {failed} uszkodzonych plikow."
            )

    def run_multi_analysis(self):
        if not self.multi_demo_paths:
            messagebox.showwarning("Brak dem", "Najpierw dodaj kilka plikow .dem.", parent=self.root)
            return
        selected_label = self.multi_player_var.get().strip()
        if not selected_label:
            messagebox.showwarning("Brak gracza", "Wybierz gracza z listy.", parent=self.root)
            return
        try:
            top_deaths = int(self.multi_top_deaths_var.get())
        except ValueError:
            messagebox.showwarning("Blad", "Top zgonow musi byc liczba.", parent=self.root)
            return

        player_name = self.multi_player_lookup.get(selected_label, selected_label)
        reports = []
        failed = []
        self.multi_status_var.set("Analizuje serie dem...")
        self.root.update_idletasks()
        for demo_path in self.multi_demo_paths:
            try:
                parser, header, players = load_parser(demo_path)
                report = build_report_data(parser, demo_path, players, player_name, top_deaths, header=header)
                reports.append(report)
            except Exception as error:
                failed.append(f"{demo_path.name}: {error}")

        chunks = []
        if reports:
            chunks.append(build_multi_report_text(reports, player_name))
        if failed:
            chunks.append("")
            chunks.append("Pliki, ktorych nie udalo sie przeanalizowac:")
            chunks.extend(f"- {item}" for item in failed)
        self.set_text_widget(self.multi_report_text, "\n".join(chunks) if chunks else "Brak danych.")
        self.multi_status_var.set(f"Gotowe. Sukces: {len(reports)}, bledy: {len(failed)}.")

    def run_compare_analysis(self):
        if self.compare_my_demo_path is None or self.compare_pro_demo_path is None:
            messagebox.showwarning("Brak dem", "Najpierw wybierz oba dema do porownania.", parent=self.root)
            return
        my_label = self.compare_my_player_var.get().strip()
        pro_label = self.compare_pro_player_var.get().strip()
        if not my_label or not pro_label:
            messagebox.showwarning("Brak gracza", "Wybierz obu graczy do porownania.", parent=self.root)
            return
        try:
            top_deaths = int(self.compare_top_deaths_var.get())
        except ValueError:
            messagebox.showwarning("Blad", "Top zgonow musi byc liczba.", parent=self.root)
            return

        my_player_name = self.compare_my_lookup.get(my_label, my_label)
        pro_player_name = self.compare_pro_lookup.get(pro_label, pro_label)
        try:
            self.compare_status_var.set("Analizuje oba dema i buduje porownanie...")
            self.root.update_idletasks()
            my_parser, my_header, my_players = load_parser(self.compare_my_demo_path)
            pro_parser, pro_header, pro_players = load_parser(self.compare_pro_demo_path)
            my_report = build_report_data(my_parser, self.compare_my_demo_path, my_players, my_player_name, top_deaths, header=my_header)
            pro_report = build_report_data(pro_parser, self.compare_pro_demo_path, pro_players, pro_player_name, top_deaths, header=pro_header)
            self.set_text_widget(self.compare_report_text, build_compare_report_text(my_report, pro_report))
            self.compare_status_var.set("Gotowe. Porownanie dwoch dem zakonczone.")
        except Exception as error:
            messagebox.showerror("Porownanie nie powiodlo sie", str(error), parent=self.root)
            self.compare_status_var.set("Porownanie nie powiodlo sie.")

    def run(self):
        self.root.mainloop()


def main():
    args = parse_args()
    if args.gui or (not args.demo and not args.pick_demo and not args.player):
        DemoAnalyzerApp().run()
        return
    run_cli(args)


if __name__ == "__main__":
    main()
