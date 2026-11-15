import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from concurrent.futures import ThreadPoolExecutor
import requests
import collections
import glob
import hashlib
import json
import os
import queue
import random
import re
import gc
import shutil
import subprocess
import threading
import time
from flask import Flask, Response, jsonify, request, send_from_directory
from ytmusicapi import YTMusic
from ytmusicapi.auth.oauth import OAuthCredentials
from ytmusicapi.helpers import sapisid_from_cookie, get_authorization
from ytmusicapi.models.content.enums import LikeStatus
import yt_dlp

# static_folder='static': раздаём ТОЛЬКО папку static (css/js) —
# browser.json, session_*.json, token_*_oauth.json в корне по-прежнему не отдаются
app = Flask(__name__,
            static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
            static_url_path='/static',
            template_folder='.')
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"ok": False, "error": "Запрос слишком большой"}), 413

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_path(fpath):
    if not fpath:
        return ""
    if os.path.isabs(fpath):
        return fpath
    return os.path.join(BASE_DIR, fpath)
CACHE_DIR = os.environ.get("RADIO_CACHE_DIR", r"E:\ets2_radio_cache").strip()
if not CACHE_DIR:
    CACHE_DIR = os.path.join(BASE_DIR, "ets2_radio_cache")
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except OSError:
    CACHE_DIR = os.path.join(BASE_DIR, "ets2_radio_cache")
    os.makedirs(CACHE_DIR, exist_ok=True)

PROFILES_FILE = os.path.join(BASE_DIR, "profiles.json")
ACTIVE_PROFILE_FILE = os.path.join(BASE_DIR, "active_profile.txt")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = resolve_path("history.json")
FAVORITES_FILE = resolve_path("favorites.json")
PLAYLISTS_FILE = resolve_path("playlists.json")
STATIONS_FILE = resolve_path("stations.json")
history_lock = threading.RLock()
favorites_lock = threading.RLock()
playlists_lock = threading.RLock()
history_store = {"profiles": {}}
favorites_store = {"profiles": {}}
playlists_store = {"profiles": {}}

app_config = {
    "cache_limit": 10,
    "bitrate": "128k",
    "volume_boost": "1.0",
    "discord_rpc": False,
    "cache_enabled": False,  # стрим напрямую без записи на диск
    "auto_pause_game": True  # радио паузится при паузе в игре (ETS2/ATS телеметрия)
}

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            app_config.update(json.load(f))
    except Exception:
        pass

try:
    app_config["cache_limit"] = max(1, min(100, int(app_config.get("cache_limit", 10))))
except (TypeError, ValueError):
    app_config["cache_limit"] = 10
if app_config.get("bitrate") not in {"128k", "192k", "256k"}:
    app_config["bitrate"] = "128k"
try:
    app_config["volume_boost"] = str(round(max(0.5, min(2.0, float(app_config.get("volume_boost", "1.0")))), 2))
except (TypeError, ValueError):
    app_config["volume_boost"] = "1.0"

def atomic_json_dump(path, data, indent=2):
    """Атомарная запись JSON через временный файл: при сбое/гонке файл не портится."""
    with json_write_lock:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(tmp, path)

def save_config():
    try:
        with state_lock:
            snapshot = dict(app_config)
        atomic_json_dump(CONFIG_FILE, snapshot)
    except Exception:
        pass

# Автодетекция JS runtime (yt-dlp ожидает словарь {runtime: {config}})
def get_js_runtimes():
    runtimes = {}
    if shutil.which("node"):
        runtimes["node"] = {}
    if shutil.which("deno"):
        runtimes["deno"] = {}
    if shutil.which("bun"):
        runtimes["bun"] = {}
    return runtimes

JS_RUNTIMES = get_js_runtimes()

# Надежные дефолтные опции для yt-dlp (легкий аудиопоток m4a, устойчивый клиент ios/mweb)
def get_ydl_base_opts(extra=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 10,
        'retries': 3,
        'format': 'ba[ext=m4a]/ba/b',
        'extractor_args': {
            'youtube': {
                'player_client': ['android_music', 'ios_music', 'android', 'tv_embedded']
            }
        },
    }
    if JS_RUNTIMES:
        opts['js_runtimes'] = JS_RUNTIMES
    if extra:
        opts.update(extra)
    return opts

profiles_data = {}
DEFAULT_PROFILE_NAME = "Мой профиль"
active_profile_name = DEFAULT_PROFILE_NAME
active_account_info = None
is_active_authenticated = False

clean_yt = YTMusic()
yt = clean_yt

# 9 точных треков со скриншота "Швидкий вибір" Олега Ананасова
ANANASOV_REAL_PICKS = [
    {
        "videoId": "buGWqDJLw0I",
        "title": "Культурный кот",
        "artist": "Нежное Это",
        "thumbnail": "https://yt3.googleusercontent.com/-ce9GkttdgFpFnDSsm-7gttoWwaYdPccDqv9cEBiU5e4pP6AgespK86QVrDoUu2DUXNi-l7yHjb9ah3N=w544-h544-l90-rj"
    },
    {
        "videoId": "kuOkbex2oJ4",
        "title": "Почему котикам...",
        "artist": "zhanulka",
        "thumbnail": "https://yt3.googleusercontent.com/-1d2cCgjRt9TcU3IzR_HN3JCF0WLqtxGBX67Df73pkYmWbMRxUDPREUZTA9TX-W2Y1DSK2YvsanKsupr=w544-h544-l90-rj"
    },
    {
        "videoId": "-2RdJ5Zadcg",
        "title": "Банкомат",
        "artist": "DEAD BLONDE",
        "thumbnail": "https://yt3.googleusercontent.com/COJx_RAdhydgxcy7lfS3admq1m5f9Ri6TGK5UKVneNsszYQ7LYXwhAWGE2E1YQ6LcjPbtcxIRR5DDDkNVA=w544-h544-l90-rj"
    },
    {
        "videoId": "KLWrNPw-Rek",
        "title": "забита голова",
        "artist": "zhanulka / МУЗЫКА КОШКИ БИЛЬЯРД",
        "thumbnail": "https://yt3.googleusercontent.com/EXUmNVtP3zJorCfWT-8Etd0hIcKCQHx-Fvbszz0HVMEsym8p7RNkz1r5wmfqiTPbGcio1zssJLq6ASrz=w544-h544-l90-rj"
    },
    {
        "videoId": "NFKTXuykXBI",
        "title": "Ход конём",
        "artist": "Комбинация",
        "thumbnail": "https://yt3.googleusercontent.com/Y0l19QnsF9CpQzTmTyllno1iab3eY4w10vmFlEA7SUOOdzBKSuxwzHYufIALQUxR8y_qAxrl6UZqXghg=w544-h544-l90-rj"
    },
    {
        "videoId": "MOMbIxFR0Y8",
        "title": "Я не интроверт",
        "artist": "nyan.mp3 / KSB muzic",
        "thumbnail": "https://yt3.googleusercontent.com/8huldfIY6aJ3KyzC_gO_X_-pp2b0MI82jp4TwgOvJJMRmT0xycioQ0FJPXYJItUZO_EgWu-MAUaPjnFG=w544-h544-l90-rj"
    },
    {
        "videoId": "VGM7V9t3Q2k",
        "title": "альтушка",
        "artist": "Neverlove",
        "thumbnail": "https://yt3.googleusercontent.com/LJsfLGc29chA4guzVWdby8gLM-5A_ZY5LOlgOwhZNNGQpXLBVNTz3wU7FUiWP0CMDApuZC8GSwtUQV4a=w544-h544-l90-rj"
    },
    {
        "videoId": "TbihvQ3GjTw",
        "title": "У батарей",
        "artist": "nyan.mp3",
        "thumbnail": "https://yt3.googleusercontent.com/aX9GvLB5UeLdJCrTfWApLIPy7ke7ab5_0_jXmu1-4wrgeTkiTRJnMdSeYT_JhuHmQdCd7GCwiGOBkVm-=w544-h544-l90-rj"
    },
    {
        "videoId": "mrdEwk7PJwA",
        "title": "На твиче",
        "artist": "нексюша / Монеточка",
        "thumbnail": "https://yt3.googleusercontent.com/3ivyKP-8fI9o_pseVgdM8C1DNPR3LhEhHgmnRteLatl0zXAJyCIoXl6LEnpnyy6YVhZysAlELS0Zgz33=w544-h544-l90-rj"
    }
]

# Вайб профиля "пк": первым идёт альбом целиком, дальше синглы
PK_ALBUM = {
    "playlistId": "OLAK5uy_nSLjYS9H9r-NKyI9cGkDIsdpMqjNwTdi4",
    "title": "чел, тебе 30",
    "artist": "СТИНТ"
}

def _pk_track(vid, title, artist):
    return {
        "videoId": vid,
        "title": title,
        "artist": artist,
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    }

PK_TRACK_PICKS = [
    _pk_track("blL2YoqOJaw", "Пить / Тупить", "СТИНТ, bratishkin"),
    _pk_track("GYrs93kJypw", "Выходные", "СТИНТ"),
    _pk_track("2Ja3Qqs9vGU", "БСЛ", "СТИНТ"),
    _pk_track("5Bw3a5yG5J4", "Педагогиня", "СТИНТ"),
    _pk_track("NFKTXuykXBI", "Ход конем", "Комбинация"),
    _pk_track("nKmEKbwAcEU", "Девочка с тиктока", "FirstFeel, mzlff, ШАРЫ"),
    _pk_track("hecVKBoso9M", "в пряничном домике", "mzlff"),
    _pk_track("NdKf-MSv6Kc", "Новая муза", "mzlff"),
]

PK_SEED_VIDS = ["blL2YoqOJaw", "GYrs93kJypw", "2Ja3Qqs9vGU", "NFKTXuykXBI", "5Bw3a5yG5J4"]

# Фильтр регионального поп-спама и трендов
def is_generic_chart_track(title, artist=""):
    bad_markers = [
        "daily top music", "trending 20", "top 100", "biggest ukrainian hits", 
        "ukrainian hits", "небо чисте, голубе", "мені тебе неможна", "о, господи!", 
        "анна трінчер", "dj kaifar", "muf modern", "і у темную ніч", "мама казала",
        "подоляночка", "красива жінка незаміжня", "ой на горі", "та мила чому ти",
        "vila", "mila pazik", "коломийка", "volkanov", "skylerr", "parfeniuk",
        "додому", "червона рута", "дике поле", "апероль", "бучинськ", "kler",
        "повільне диско", "irena", "покохав", "surov", "одна душа на двох",
        "oxyna", "чом - чом", "mister mask", "її закони", "trap mafia house",
        "mafia style", "simo sent", "dream chaos", "roma dj", "freed from desire"
    ]
    combined = f"{title} {artist}".lower()
    for b in bad_markers:
        marker = b.strip().lower()
        if not marker:
            continue
        if " " in marker:
            # Фраза из нескольких слов — ищем как подстроку
            if marker in combined:
                return True
        else:
            # Одиночное слово — только по границам слов, иначе
            # "vila" выкидывает условную "Avila", а "kler" — "Backler"
            if re.search(r'(?<!\w)' + re.escape(marker) + r'(?!\w)', combined):
                return True
    return False

current_track_info = {
    "title": "Культурный кот",
    "artist": "Нежное Это",
    "album": "",
    "video_id": "buGWqDJLw0I",
    "thumbnail": "https://i.ytimg.com/vi/buGWqDJLw0I/hqdefault.jpg",
    "duration": 180,
    "duration_known": False,
    "current_time": 0,
    "is_paused": False,
    "status": "ready"
}

upcoming_queue = []
current_oauth_flow = None

cmd_queue = queue.Queue()
active_listeners = []
current_proc = None
skip_event = threading.Event()
seek_event = threading.Event()
target_seek_time = 0

class BoundedDict(dict):
    """dict с ограничением размера: при переполнении вытесняются самые старые записи."""
    def __init__(self, max_entries=500):
        super().__init__()
        self._max_entries = max_entries
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        overflow = len(self) - self._max_entries
        if overflow > 0:
            for old_key in list(self)[:overflow]:
                super().pop(old_key, None)

meta_cache = BoundedDict(600)
stream_url_cache = BoundedDict(300)
picks_cache = BoundedDict(120)

# Ключи, которые нельзя отдавать клиентам (OAuth-креды Device Flow)
CONFIG_SECRET_KEYS = ("oauth_client_id", "oauth_client_secret")

def sanitize_config(cfg):
    return {k: v for k, v in cfg.items() if k not in CONFIG_SECRET_KEYS}
status_cache = {}
CACHE_TTL_SEC = 60
AUTH_CHECK_INTERVAL_SEC = 120
auth_fail_streak = 0

# Общие мьютексы и константы
state_lock = threading.RLock()
listeners_lock = threading.Lock()
proc_lock = threading.Lock()
json_write_lock = threading.RLock()
picks_lock = threading.RLock()
recommendation_slots = threading.BoundedSemaphore(8)
downloading_lock = threading.Lock()
downloading_vids = set()
bg_download_pool = ThreadPoolExecutor(max_workers=1)
play_history = collections.deque(maxlen=60)
played_tracks_history = collections.deque(maxlen=50)
current_playing_item = None
playback_profile_name = ""
track_queue_list = []
profile_queues = {}
played_vids = set()
dismissed_vids = set()
playback_generation = 0
# Real MP3 silence frames (128 kbps, 44.1 kHz, Joint Stereo) - 417 bytes each
# Sending valid MP3 silence frames prevents decoders (FMOD in ETS2 / browser) from losing sync or stuttering
MP3_SILENCE_FRAME = (
    b'\xff\xfb\x90d\x00\x0f\xf0\x00\x00i\x00\x00\x00\x08\x00\x00\r \x00\x00\x01\x00\x00\x01\xa4\x00\x00\x00 \x00\x004\x80\x00\x00\x04LAME3.100'
    + b'U' * 175
    + b'LAME3.100'
    + b'U' * 188
)
SILENCE_CHUNK = MP3_SILENCE_FRAME * 10
KEEPALIVE_CHUNK = MP3_SILENCE_FRAME
radio_inflight = 0
MIN_CACHE_SIZE = 100 * 1024
STREAM_URL_TTL_SEC = 3600
ALLOWED_BITRATES = {"128k", "192k", "256k"}
CACHE_EXTS = ("m4a", "webm", "mp4", "opus", "ogg")
# Мусорные расширения: обрывки недокачанных закачек yt-dlp (.part, .ytdl).
# Они не играются и только жрут диск — выметаем всегда, не считая лимитом.
JUNK_EXTS = ("part", "ytdl", "temp")

def flush_listeners(pad_silence=True):
    with listeners_lock:
        listeners = list(active_listeners)
    for q in listeners:
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break
        if pad_silence:
            try:
                q.put_nowait(MP3_SILENCE_FRAME * 4)
            except Exception:
                pass

def push_chunk(chunk):
    if skip_event.is_set() or seek_event.is_set():
        return
    with listeners_lock:
        listeners = list(active_listeners)
    for q in listeners:
        try:
            q.put_nowait(chunk)
        except queue.Full:
            try:
                q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(chunk)
            except Exception:
                pass

def kill_current_proc():
    global current_proc
    with proc_lock:
        p = current_proc
    if not p:
        return
    try:
        p.kill()
    except Exception:
        pass
    try:
        p.wait(timeout=1)
    except Exception:
        pass
    with proc_lock:
        if current_proc is p:
            current_proc = None

# --- РЕАЛЬНЫЕ ИНТЕРНЕТ-РАДИОСТАНЦИИ ---

station_state = None  # dict(id, name, url) когда играет интернет-станция

DEFAULT_STATIONS = [
    # --- Русские федеральные (официальные потоки hostingradio.ru / Record) ---
    {"id": "rr-main", "name": "Радио Рекорд", "url": "https://radiorecord.hostingradio.ru/rr96.aacp", "genre": "Танцевальное"},
    {"id": "rr-trfm", "name": "Рекорд ТРФМ", "url": "https://radiorecord.hostingradio.ru/trfm96.aacp", "genre": "Танцевальное РФ"},
    {"id": "rr-gop", "name": "Рекорд Gop FM", "url": "https://radiorecord.hostingradio.ru/gop96.aacp", "genre": "Гоп-хиты"},
    {"id": "rr-pirate", "name": "Рекорд Пиратская", "url": "https://radiorecord.hostingradio.ru/pirate96.aacp", "genre": "Пиратская станция"},
    {"id": "rr-chill", "name": "Рекорд Chill-Out", "url": "https://radiorecord.hostingradio.ru/chil96.aacp", "genre": "Chill"},
    {"id": "rr-rock", "name": "Рекорд Рок", "url": "https://radiorecord.hostingradio.ru/rock96.aacp", "genre": "Рок"},
    {"id": "rusradio", "name": "Русское Радио", "url": "https://rusradio.hostingradio.ru/rusradio96.aacp", "genre": "Русские хиты"},
    {"id": "europaplus", "name": "Europa Plus", "url": "http://ep256.hostingradio.ru/ep256.mp3", "genre": "Поп"},
    {"id": "dfm", "name": "DFM", "url": "http://dfm.hostingradio.ru/dfm96.aacp", "genre": "Электронное"},
    {"id": "dfm-dance", "name": "DFM Dance", "url": "http://dfm.hostingradio.ru/dfmdance96.aacp", "genre": "Dance"},
    {"id": "nashe", "name": "Наше Радио", "url": "https://nashe1.hostingradio.ru/nashe-128.mp3", "genre": "Русский рок"},
    {"id": "hitfm", "name": "Хит FM", "url": "http://hitfm.hostingradio.ru/hitfm96.aacp", "genre": "Хиты"},
    {"id": "energy", "name": "Energy", "url": "https://energy1.hostingradio.ru/energy96.aacp", "genre": "Поп/Танцевальное"},
    {"id": "retro", "name": "Ретро FM", "url": "http://retro.hostingradio.ru/retro128.mp3", "genre": "Ретро"},
    {"id": "chanson", "name": "Радио Шансон", "url": "http://chanson.hostingradio.ru/chanson96.aacp", "genre": "Шансон"},
    {"id": "autoradio", "name": "Авторадио", "url": "http://autoradio.hostingradio.ru/autoradio96.aacp", "genre": "Авто-хиты"},
    {"id": "dorognoe", "name": "Дорожное радио", "url": "https://dorognoe.hostingradio.ru/dorognoe96.aacp", "genre": "Для дороги"},
    {"id": "vanya", "name": "Ваня Радио", "url": "http://vanaradio.hostingradio.ru/vanya96.aacp", "genre": "Русский поп"},
    {"id": "comedy", "name": "Comedy Radio", "url": "http://comedyradio.hostingradio.ru/comedy96.aacp", "genre": "Юмор"},
    {"id": "humor", "name": "Юмор FM", "url": "http://humorfm.hostingradio.ru/humorfm96.aacp", "genre": "Юмор"},
    {"id": "love", "name": "Love Radio", "url": "http://loveradio.hostingradio.ru/loveradio96.aacp", "genre": "Поп"},
    {"id": "keks", "name": "Кекс FM", "url": "http://keksfm.hostingradio.ru/keksfm96.aacp", "genre": "Весёлое"},
    {"id": "dacha", "name": "Радио Дача", "url": "http://dacha.hostingradio.ru/dacha96.aacp", "genre": "Дачное"},
    {"id": "newradio", "name": "Новое Радио", "url": "http://newradio.hostingradio.ru/newradio96.aacp", "genre": "Хиты 90-2000-х"},
    {"id": "marshal", "name": "Радио Маршал", "url": "http://marshal.hostingradio.ru/marshal96.aacp", "genre": "Авто"},
    # --- Зарубежные (проверены, работают везде) ---
    {"id": "soma-groove", "name": "SomaFM Groove Salad", "url": "https://ice1.somafm.com/groovesalad-128-mp3", "genre": "Chill/Downtempo"},
    {"id": "soma-drone", "name": "SomaFM Drone Zone", "url": "https://ice1.somafm.com/dronezone-128-mp3", "genre": "Ambient"},
    {"id": "soma-defcon", "name": "SomaFM DEF CON", "url": "https://ice1.somafm.com/defcon-128-mp3", "genre": "Electro/Hacker"},
    {"id": "rp-main", "name": "Radio Paradise Main Mix", "url": "https://stream.radioparadise.com/mp3-128", "genre": "Eclectic Rock"},
    {"id": "rp-mellow", "name": "Radio Paradise Mellow Mix", "url": "https://stream.radioparadise.com/mellow-128", "genre": "Mellow Mix"},
]

# Проверка доступности станций (кэшируется на 5 минут)
stations_health_cache = {"t": 0.0, "alive": {}}

def check_stations_health():
    """Параллельно пингует потоки: кто отвечает байтами — тот живой.
    Из-за гео-блокировки часть потоков может быть недоступна конкретно у тебя."""
    now = time.time()
    if now - stations_health_cache["t"] < 300:
        return stations_health_cache["alive"]
    stations = get_stations()

    def _probe(st):
        try:
            r = requests.get(st["url"], stream=True, timeout=4,
                             headers={"User-Agent": "ETS2RadioDJ/1.0"})
            got = 0
            for chunk in r.iter_content(8192):
                got += len(chunk)
                if got >= 8192:
                    break
            r.close()
            return got > 4096
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=min(10, len(stations))) as ex:
        flags = list(ex.map(_probe, stations))
    alive = {st["id"]: ok for st, ok in zip(stations, flags)}
    stations_health_cache["t"] = now
    stations_health_cache["alive"] = alive
    return alive

def load_stations():
    if os.path.exists(STATIONS_FILE):
        try:
            with open(STATIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            print("[Stations] Error reading stations.json:", e)
    atomic_json_dump(STATIONS_FILE, DEFAULT_STATIONS)
    return [dict(s) for s in DEFAULT_STATIONS]

def get_stations():
    return load_stations()

def play_station_loop(station):
    """Крутит реальный интернет-стрим через тот же пайп /radio.mp3.
    Блокирует поток (вызывается из dj_thread). Очередь YouTube не трогает."""
    global station_state
    bitrate = app_config.get("bitrate", "128k")
    boost = app_config.get("volume_boost", "1.0")
    ff_log_path = os.path.join(CACHE_DIR, 'ffmpeg_last.log')

    cmd = [
        'ffmpeg', '-nostdin',
        '-reconnect', '1', '-reconnect_streamed', '1',
        '-reconnect_at_eof', '1', '-reconnect_delay_max', '5',
        '-rw_timeout', '15000000',
        '-probesize', '65536', '-analyzeduration', '500000',
        '-i', station["url"],
        '-af', f'volume={boost}',
        '-f', 'mp3', '-acodec', 'libmp3lame', '-b:a', bitrate,
        '-ar', '44100', '-ac', '2',
        'pipe:1'
    ]
    try:
        ff_log_file = open(ff_log_path, 'wb')
    except Exception:
        ff_log_file = subprocess.DEVNULL
    try:
        with proc_lock:
            current_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ff_log_file)
    finally:
        try:
            ff_log_file.close()
        except Exception:
            pass

    ff_start = time.time()
    while True:
        if skip_event.is_set() or not cmd_queue.empty():
            break
        # seek на живом эфире бессмыслен — гасим событие, чтобы оно
        # не всплыло в середине следующего трека
        if seek_event.is_set():
            seek_event.clear()
        if current_track_info.get("is_paused"):
            # тишина темпом битрейта, как у треков
            while current_track_info.get("is_paused"):
                if skip_event.is_set() or not cmd_queue.empty():
                    break
                kbps = int(re.sub(r'\D', '', str(bitrate)) or 128)
                push_chunk(MP3_SILENCE_FRAME * max(256, int(kbps * 128 / 8)))
                time.sleep(1.0)
            if skip_event.is_set() or not cmd_queue.empty():
                break
        with proc_lock:
            proc = current_proc
        if not proc or not proc.stdout:
            break
        chunk = proc.stdout.read(4096)
        if not chunk:
            # станция отвалилась — перезапускаем через 2с, если её не остановили
            if skip_event.is_set() or not cmd_queue.empty():
                break
            kill_current_proc()
            if time.time() - ff_start < 5:
                # поток не ожил за 5 секунд — скорее всего мёртвый URL или
                # гео-блок. Не крутимся вечно: останавливаем станцию.
                try:
                    with open(ff_log_path, 'r', encoding='utf-8', errors='replace') as lf:
                        print(f"[STATION {station.get('name')}] ffmpeg stderr:\n{lf.read()[-400:]}", flush=True)
                except Exception:
                    pass
                with state_lock:
                    if station_state and station_state.get("id") == station.get("id"):
                        station_state = None
                current_track_info.update({
                    "title": station.get("name", "Радио"),
                    "artist": "Станция недоступна (проверь здоровье в списке станций)",
                    "status": "Ошибка станции",
                    "is_station": False,
                    "current_time": 0
                })
                skip_event.clear()
                return
            time.sleep(2.0)
            return play_station_loop(station)
        if skip_event.is_set() or not cmd_queue.empty():
            break
        current_track_info["current_time"] = int(time.time() - ff_start)
        push_chunk(chunk)
    kill_current_proc()

def clear_picks_cache():
    with picks_lock:
        picks_cache.clear()

def is_pk_profile_name(name=None):
    return str(name if name is not None else active_profile_name).strip().casefold() == "пк"

def reset_profile_runtime():
    global playback_generation, radio_inflight, current_playing_item, playback_profile_name
    with state_lock:
        if active_profile_name:
            profile_queues[active_profile_name] = [dict(item) for item in track_queue_list]
        playback_generation += 1
        track_queue_list.clear()
        upcoming_queue.clear()
        played_vids.clear()
        dismissed_vids.clear()
        radio_inflight = 0
        current_playing_item = None
        playback_profile_name = ""
    while True:
        try:
            cmd_queue.get_nowait()
        except queue.Empty:
            break
    play_history.clear()
    played_tracks_history.clear()

def publish_queue_snapshot():
    with state_lock:
        upcoming_queue[:] = [{
            "title": item.get("title", "Трек"),
            "artist": item.get("artist", ""),
            "album": item.get("album", ""),
            "videoId": item.get("videoId"),
            "thumbnail": item.get("thumbnail", "")
        } for item in list(track_queue_list[:8])]

def fill_queue_after_dismissal(generation, seed_vid, seed_artist):
    candidates = []
    try:
        with recommendation_slots:
            radio = safe_get_watch_playlist(seed_vid, radio=True) if seed_vid else {}
            for item in (radio or {}).get("tracks", []):
                candidate = format_track_pick(item)
                if candidate:
                    candidates.append(candidate)
            if len(candidates) < 4 and seed_artist:
                try:
                    results = yt.search(seed_artist, filter="songs") or []
                except Exception:
                    results = []
                for item in results[:3]:
                    candidate = format_track_pick(item)
                    if candidate:
                        candidates.append(candidate)
            if not candidates:
                if is_active_authenticated and profiles_data.get(active_profile_name, {}).get("type") == "oauth":
                    candidates.extend(get_oauth_liked_songs(limit=30))
                if not candidates:
                    candidates.extend(get_persisted_history(limit=30))
                if not candidates:
                    candidates.extend(PK_TRACK_PICKS if is_pk_profile_name() else ANANASOV_REAL_PICKS)
    except Exception as ex:
        print("[QUEUE] Не удалось подобрать замену:", ex)

    random.shuffle(candidates)
    added = []
    with state_lock:
        if generation != playback_generation:
            return []
        known = set(dismissed_vids) | set(play_history) | {item.get("videoId") for item in track_queue_list}
        if current_playing_item:
            known.add(current_playing_item.get("videoId"))
        known.discard(None)
        for candidate in candidates:
            vid = candidate.get("videoId")
            if not vid or vid in known:
                continue
            known.add(vid)
            track_queue_list.append({
                "videoId": vid,
                "title": candidate.get("title", "Трек"),
                "artist": candidate.get("artist", ""),
                "album": "Рекомендации (Замена)",
                "thumbnail": candidate.get("thumbnail", "")
            })
            added.append(vid)
            if len(track_queue_list) >= 8:
                break
        upcoming_queue[:] = [{
            "title": item.get("title", "Трек"),
            "artist": item.get("artist", ""),
            "album": item.get("album", ""),
            "videoId": item.get("videoId"),
            "thumbnail": item.get("thumbnail", "")
        } for item in list(track_queue_list[:8])]
    return added

def safe_get_watch_playlist(video_id, radio=True):
    p_info = profiles_data.get(active_profile_name) or {}
    instances = [yt, clean_yt]
    for inst in instances:
        try:
            res = inst.get_watch_playlist(videoId=video_id, radio=radio)
            if res and res.get("tracks"):
                return res
        except Exception:
            pass
    return {}

def safe_get_album(album_id):
    for inst in [yt, clean_yt]:
        try:
            res = inst.get_album(album_id)
            if res and res.get("tracks"):
                return res
        except Exception:
            pass
    return {}

def safe_get_playlist(playlist_id, limit=200):
    p_info = profiles_data.get(active_profile_name) or {}
    instances = [yt, clean_yt]
    for inst in instances:
        try:
            res = inst.get_playlist(playlist_id, limit=limit)
            if res and res.get("tracks"):
                return res
        except Exception:
            pass

    # OAuth API fallback for private/unlisted playlists
    if p_info.get("type") == "oauth":
        token_file = resolve_path(p_info.get("token_file"))
        if token_file and os.path.exists(token_file):
            try:
                with open(token_file, 'r', encoding='utf-8') as f:
                    tok = json.load(f)
                access_token = tok.get("access_token")
                if access_token:
                    url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={playlist_id}&maxResults=50"
                    r = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=6)
                    if r.status_code == 200:
                        items = r.json().get("items", [])
                        tracks = []
                        for it in items:
                            s = it.get("snippet", {})
                            vid = s.get("resourceId", {}).get("videoId")
                            if vid:
                                tracks.append({
                                    "videoId": vid,
                                    "title": s.get("title", "Трек"),
                                    "artists": [{"name": s.get("videoOwnerChannelTitle", "") or s.get("channelTitle", "")}],
                                    "thumbnails": [{"url": (s.get("thumbnails", {}).get("high") or {}).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")}]
                                })
                        if tracks:
                            return {"title": "Плейліст", "tracks": tracks}
            except Exception:
                pass

    try:
        ydl_opts = {'quiet': True, 'extract_flat': True, 'dump_single_json': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/playlist?list={playlist_id}", download=False)
            entries = info.get("entries", [])
            if entries:
                tracks = []
                for e in entries:
                    vid = e.get("id") or e.get("url")
                    if vid:
                        tracks.append({
                            "videoId": vid,
                            "title": e.get("title") or "Трек",
                            "artists": [{"name": e.get("uploader") or e.get("channel") or ""}],
                            "thumbnails": [{"url": e.get("thumbnails", [{}])[-1].get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg") if e.get("thumbnails") else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}]
                        })
                return {"title": info.get("title", "Плейліст"), "tracks": tracks}
    except Exception:
        pass
    return {}

def probe_duration(path):
    """Точная длительность локального файла через ffprobe (сек) или None."""
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            timeout=8, stderr=subprocess.DEVNULL)
        return float(out.decode('utf-8', 'ignore').strip())
    except Exception:
        return None

def find_cached_file(vid):
    """Найти файл кеша для videoId (любое известное расширение)."""
    for ext in CACHE_EXTS:
        p = os.path.join(CACHE_DIR, f"{vid}.{ext}")
        try:
            if os.path.exists(p) and os.path.getsize(p) > MIN_CACHE_SIZE:
                return p
        except OSError:
            pass
    return None

def clean_old_cache(protect_vid=None):
    try:
        with downloading_lock:
            busy_vids = set(downloading_vids)
        for jext in JUNK_EXTS:
            for jf in glob.glob(os.path.join(CACHE_DIR, f"*.{jext}")):
                jbase = os.path.basename(jf)
                if any(jbase.startswith(f"{bv}.") for bv in busy_vids):
                    continue
                try:
                    os.remove(jf)
                except Exception:
                    pass

        max_files = int(app_config.get("cache_limit", 10))
        if not app_config.get("cache_enabled", False):
            # кеш выключен — держим папку пустой (кроме защищённых треков)
            max_files = 0
        max_files = max(0, max_files)
        files = []
        for ext in CACHE_EXTS:
            files.extend(glob.glob(os.path.join(CACHE_DIR, f"*.{ext}")))
        files.sort(key=os.path.getmtime)

        protected_vids = set()
        if protect_vid:
            protected_vids.add(protect_vid)
        try:
            with state_lock:
                for q_item in track_queue_list[:6]:
                    q_vid = q_item.get("videoId")
                    if q_vid:
                        protected_vids.add(q_vid)
        except Exception:
            pass

        while len(files) > max_files:
            removable = None
            for candidate in files:
                base = os.path.basename(candidate)
                with downloading_lock:
                    busy = any(base.startswith(f"{dv}.") for dv in downloading_vids)
                if not busy and not any(base.startswith(f"{pv}.") for pv in protected_vids):
                    removable = candidate
                    break
            if removable is None:
                break
            files.remove(removable)
            try:
                os.remove(removable)
            except Exception:
                pass
    except Exception:
        pass

def save_profiles():
    try:
        with state_lock:
            snapshot = {name: dict(info) for name, info in profiles_data.items()}
        atomic_json_dump(PROFILES_FILE, snapshot)
    except Exception as e:
        print("[ERROR] Не удалось сохранить profiles.json:", e)

def load_profiles():
    global profiles_data, active_profile_name
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                profiles_data = json.load(f)
        except Exception:
            profiles_data = {}

    if os.path.exists(ACTIVE_PROFILE_FILE):
        try:
            with open(ACTIVE_PROFILE_FILE, "r", encoding="utf-8-sig") as f:
                active_profile_name = f.read().strip().lstrip('\ufeff')
        except Exception:
            active_profile_name = "пк"

    if not active_profile_name or active_profile_name not in profiles_data:
        active_profile_name = "пк" if "пк" in profiles_data else (list(profiles_data.keys())[0] if profiles_data else DEFAULT_PROFILE_NAME)

    set_active_yt_instance(active_profile_name)

def sanitize_oauth_input(text):
    if not text:
        return ""
    text = str(text).strip().strip("'\"`")
    for d in ['\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2015', '\u2212', '–', '—', '−']:
        text = text.replace(d, '-')
    text = re.sub(r'[\u200b\u200c\u200d\ufeff\u00a0]', '', text)
    return text.strip()

def get_oauth_account_info(token_file, creds=None):
    token_file = resolve_path(token_file)
    try:
        with open(token_file, 'r', encoding='utf-8') as f:
            tok = json.load(f)
        access_token = tok.get("access_token")
        refresh_token = tok.get("refresh_token")

        res = requests.get(
            'https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=5
        )
        if res.status_code == 401 and creds and refresh_token:
            new_tok = creds.refresh_token(refresh_token)
            if new_tok and "access_token" in new_tok:
                tok.update(new_tok)
                atomic_json_dump(token_file, tok)
                access_token = tok["access_token"]
                res = requests.get(
                    'https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
                    headers={'Authorization': f'Bearer {access_token}'},
                    timeout=5
                )

        if res.status_code == 200:
            items = res.json().get("items", [])
            if items:
                snip = items[0].get("snippet", {})
                title = snip.get("title", "Google User")
                thumb = snip.get("thumbnails", {}).get("default", {}).get("url", "")
                custom_url = snip.get("customUrl", "")
                return {
                    "accountName": title,
                    "channelHandle": custom_url or title,
                    "thumbnail": thumb,
                    "accountPhotoUrl": thumb
                }
    except Exception as e:
        print("[OAuth] Error verifying account info:", e)
    return None

NON_MUSIC_KEYWORDS = (
    "смотрит", "реакция", "нарезка", "нарезки", "стрим", "stream",
    "прохождение", "gameplay", "геймплей", "обзор", "интервью",
    "новости", "майнкрафт", "minecraft", "mrbeast", "мистер бист",
    "shorts", "шортс", "диплом", "нейросеть", "подкаст", "podcast"
)

def is_non_music_video(title, artist=""):
    t_l = (title or "").lower()
    a_l = (artist or "").lower()
    if "topic" in a_l or "records" in a_l or "music" in a_l:
        return False
    return any(kw in t_l or kw in a_l for kw in NON_MUSIC_KEYWORDS)

def get_oauth_liked_songs(limit=50):
    profile_name = active_profile_name
    p_info = profiles_data.get(profile_name) or {}
    if p_info.get("type") != "oauth" or not is_active_authenticated:
        return []
    token_file = resolve_path(p_info.get("token_file"))
    if not token_file or not os.path.exists(token_file):
        return []

    try:
        liked = yt.get_liked_songs(limit=limit)
        yt_picks = []
        for item in (liked or {}).get("tracks", []):
            pick = format_track_pick(item)
            if pick:
                yt_picks.append(pick)
        if yt_picks:
            return yt_picks
    except Exception:
        pass

    try:
        with open(token_file, 'r', encoding='utf-8') as f:
            tok = json.load(f)
        access_token = tok.get("access_token")
        refresh_token = tok.get("refresh_token")
        client_id = sanitize_oauth_input(p_info.get("client_id", ""))
        client_secret = sanitize_oauth_input(p_info.get("client_secret", ""))

        def do_request(url):
            nonlocal access_token, tok
            r = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=6)
            if r.status_code == 401 and refresh_token and client_id and client_secret:
                creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
                new_tok = creds.refresh_token(refresh_token)
                if new_tok and "access_token" in new_tok:
                    tok.update(new_tok)
                    atomic_json_dump(token_file, tok)
                    access_token = tok["access_token"]
                    r = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=6)
            return r

        results = []
        for playlist_code in ["LM", "LL"]:
            page_token = ""
            while len(results) < limit:
                batch_size = min(50, limit - len(results))
                url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId={playlist_code}&maxResults={batch_size}"
                if page_token:
                    url += f"&pageToken={page_token}"
                res = do_request(url)
                if res.status_code != 200:
                    break
                data = res.json()
                items = data.get("items", [])
                if not items:
                    break
                for item in items:
                    snippet = item.get("snippet", {})
                    vid = snippet.get("resourceId", {}).get("videoId") or item.get("contentDetails", {}).get("videoId")
                    if not vid:
                        continue
                    title = snippet.get("title", "Трек")
                    artist = snippet.get("videoOwnerChannelTitle", "") or snippet.get("channelTitle", "")
                    if artist.endswith(" - Topic"):
                        artist = artist[:-8]
                    if is_non_music_video(title, artist):
                        continue
                    thumb_dict = snippet.get("thumbnails", {})
                    thumb_url = (thumb_dict.get("maxres") or thumb_dict.get("high") or thumb_dict.get("medium") or thumb_dict.get("default") or {}).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")

                    pick = {
                        "videoId": vid,
                        "title": title,
                        "artist": artist,
                        "thumbnail": thumb_url
                    }
                    meta_cache[vid] = pick
                    results.append(pick)
                    if len(results) >= limit:
                        break
                page_token = data.get("nextPageToken")
                if not page_token or len(results) >= limit:
                    break
            if results:
                break
        return results
    except Exception as ex:
        print("[OAuth] Error fetching liked songs:", ex)
        return []

def get_oauth_user_playlist_seeds(limit_playlists=3, tracks_per_pl=6):
    """Отримує сиди з власних плейлістів користувача (Remix tracks, down тощо)."""
    p_info = profiles_data.get(active_profile_name) or {}
    if not is_active_authenticated or p_info.get("type") != "oauth":
        return []
    token_file = resolve_path(p_info.get("token_file"))
    if not token_file or not os.path.exists(token_file):
        return []
    try:
        with open(token_file, 'r', encoding='utf-8') as f:
            tok = json.load(f)
        access_token = tok.get("access_token")
        if not access_token:
            return []
        
        url_pl = "https://www.googleapis.com/youtube/v3/playlists?part=id,snippet&mine=true&maxResults=10"
        r_pl = requests.get(url_pl, headers={'Authorization': f'Bearer {access_token}'}, timeout=5)
        if r_pl.status_code == 401:
            refresh_token = tok.get("refresh_token")
            client_id = sanitize_oauth_input(p_info.get("client_id", ""))
            client_secret = sanitize_oauth_input(p_info.get("client_secret", ""))
            if refresh_token and client_id and client_secret:
                creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
                new_tok = creds.refresh_token(refresh_token)
                if new_tok and "access_token" in new_tok:
                    tok.update(new_tok)
                    atomic_json_dump(token_file, tok)
                    access_token = tok["access_token"]
                    r_pl = requests.get(url_pl, headers={'Authorization': f'Bearer {access_token}'}, timeout=5)
        if r_pl.status_code != 200:
            return []
        
        seeds = []
        for pl_item in r_pl.json().get("items", [])[:limit_playlists]:
            pl_id = pl_item.get("id")
            if not pl_id:
                continue
            url_items = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={pl_id}&maxResults={tracks_per_pl}"
            r_items = requests.get(url_items, headers={'Authorization': f'Bearer {access_token}'}, timeout=5)
            if r_items.status_code == 200:
                for it in r_items.json().get("items", []):
                    s = it.get("snippet", {})
                    vid = s.get("resourceId", {}).get("videoId")
                    title = s.get("title", "")
                    artist = s.get("videoOwnerChannelTitle", "") or s.get("channelTitle", "")
                    if vid and not is_non_music_video(title, artist):
                        if artist.endswith(" - Topic"):
                            artist = artist[:-8]
                        thumb_dict = s.get("thumbnails", {})
                        thumb_url = (thumb_dict.get("maxres") or thumb_dict.get("high") or thumb_dict.get("medium") or thumb_dict.get("default") or {}).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
                        meta_cache[vid] = {
                            "videoId": vid,
                            "title": title,
                            "artist": artist,
                            "thumbnail": thumb_url
                        }
                        seeds.append(vid)
        return seeds
    except Exception as e:
        print("[OAuth] Error fetching playlist seeds:", e)
        return []

def load_history_store():
    global history_store
    with history_lock:
        if not os.path.exists(HISTORY_FILE):
            history_store = {"profiles": {}}
            return
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
                history_store = {"profiles": data["profiles"]}
            elif isinstance(data, list):
                history_store = {"profiles": {active_profile_name: data}}
                atomic_json_dump(HISTORY_FILE, history_store)
            else:
                history_store = {"profiles": {}}
        except Exception as e:
            print("[History] Error reading history.json:", e)
            history_store = {"profiles": {}}

def get_persisted_history(limit=27, profile_name=None):
    profile = profile_name or active_profile_name
    with history_lock:
        items = list(history_store.get("profiles", {}).get(profile, []))
    result = []
    for item in items[:limit]:
        if isinstance(item, dict) and item.get("videoId"):
            meta_cache[item["videoId"]] = item
            result.append(item)
    return result

def get_recently_played_vids(limit=25):
    """ВидеоId последних прослушанных треков — чтобы рекомендации не совали
    только что услышанное."""
    recent = []
    try:
        with state_lock:
            for item in played_tracks_history[-8:]:
                vid = item.get("videoId")
                if vid:
                    recent.append(vid)
            cur = current_playing_item.get("videoId") if current_playing_item else None
            if cur:
                recent.append(cur)
    except Exception:
        pass
    for item in get_persisted_history(limit=limit):
        vid = item.get("videoId")
        if vid:
            recent.append(vid)
    seen = set()
    ordered = []
    for vid in recent:
        if vid not in seen:
            seen.add(vid)
            ordered.append(vid)
    return ordered[:limit]

def record_history_item(vid, title, artist, thumb, profile_name=None):
    if not vid:
        return
    profile = profile_name or active_profile_name
    try:
        with history_lock:
            items = [
                x for x in history_store.get("profiles", {}).get(profile, [])
                if isinstance(x, dict) and x.get("videoId") != vid
            ]
            new_item = {
                "videoId": vid,
                "title": title or "Трек",
                "artist": artist or "",
                "thumbnail": thumb or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
            }
            items.insert(0, new_item)
            history_store.setdefault("profiles", {})[profile] = items[:60]
            atomic_json_dump(HISTORY_FILE, history_store)
    except Exception as e:
        print("[History] Error saving history item:", e)

def load_favorites_store():
    global favorites_store
    with favorites_lock:
        if not os.path.exists(FAVORITES_FILE):
            favorites_store = {"profiles": {}}
            return
        try:
            with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
                favorites_store = {"profiles": data["profiles"]}
            elif isinstance(data, list):
                favorites_store = {"profiles": {active_profile_name: data}}
                atomic_json_dump(FAVORITES_FILE, favorites_store)
            else:
                favorites_store = {"profiles": {}}
        except Exception:
            favorites_store = {"profiles": {}}

def get_favorites(profile_name=None, limit=100):
    profile = profile_name or active_profile_name
    with favorites_lock:
        items = [dict(item) for item in favorites_store.get("profiles", {}).get(profile, []) if isinstance(item, dict)]
    return items[:max(1, min(200, int(limit)))]

# --- СОБСТВЕННЫЕ ПЛЕЙЛИСТЫ (playlists.json) ---

def load_playlists_store():
    global playlists_store
    with playlists_lock:
        if not os.path.exists(PLAYLISTS_FILE):
            playlists_store = {"profiles": {}}
            return
        try:
            with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
                playlists_store = {"profiles": data["profiles"]}
            else:
                playlists_store = {"profiles": {}}
        except Exception as e:
            print("[Playlists] Error reading playlists.json:", e)
            playlists_store = {"profiles": {}}

def save_playlists_store():
    with playlists_lock:
        atomic_json_dump(PLAYLISTS_FILE, playlists_store)

def get_playlists(profile_name=None):
    profile = profile_name or active_profile_name
    with playlists_lock:
        items = playlists_store.get("profiles", {}).get(profile, [])
        return [dict(pl) for pl in items if isinstance(pl, dict)]

def find_playlist(playlist_id, profile_name=None):
    profile = profile_name or active_profile_name
    with playlists_lock:
        for pl in playlists_store.get("profiles", {}).get(profile, []):
            if isinstance(pl, dict) and pl.get("id") == playlist_id:
                return pl
    return None

def is_favorite(video_id, profile_name=None):
    if not video_id:
        return False
    profile = profile_name or active_profile_name
    with favorites_lock:
        return any(item.get("videoId") == video_id for item in favorites_store.get("profiles", {}).get(profile, []))

def toggle_favorite(video_id, title="", artist="", thumbnail="", album="", profile_name=None):
    if not video_id:
        return False, []
    profile = profile_name or active_profile_name
    with favorites_lock:
        items = [dict(item) for item in favorites_store.get("profiles", {}).get(profile, []) if isinstance(item, dict)]
        was_favorite = any(item.get("videoId") == video_id for item in items)
        filtered = [item for item in items if item.get("videoId") != video_id]
        added = not was_favorite
        if added:
            filtered.insert(0, {
                "videoId": video_id,
                "title": title or "Трек",
                "artist": artist or "",
                "album": album or "",
                "thumbnail": thumbnail or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            })
        filtered = filtered[:100]
        if added or was_favorite:
            favorites_store.setdefault("profiles", {})[profile] = filtered
            atomic_json_dump(FAVORITES_FILE, favorites_store)
        return added, filtered

def set_favorite(video_id, liked, title="", artist="", thumbnail="", album="", profile_name=None):
    if not video_id:
        return False, []
    profile = profile_name or active_profile_name
    with favorites_lock:
        items = [dict(item) for item in favorites_store.get("profiles", {}).get(profile, []) if isinstance(item, dict)]
        has = any(item.get("videoId") == video_id for item in items)
        if has == bool(liked):
            return has, [dict(item) for item in items]
        filtered = [item for item in items if item.get("videoId") != video_id]
        if liked:
            filtered.insert(0, {
                "videoId": video_id,
                "title": title or "Трек",
                "artist": artist or "",
                "album": album or "",
                "thumbnail": thumbnail or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            })
        filtered = filtered[:100]
        favorites_store.setdefault("profiles", {})[profile] = filtered
        atomic_json_dump(FAVORITES_FILE, favorites_store)
        return bool(liked), [dict(item) for item in filtered]

def set_active_yt_instance(name):
    global yt, active_profile_name, active_account_info, is_active_authenticated, auth_fail_streak, playback_profile_name
    name = str(name).strip().lstrip('\ufeff')
    reset_profile_runtime()
    # Глобальные имя/экземпляр меняем атомарно: читатели (dj_thread, history,
    # рекомендации) снапшотят active_profile_name и параллельно юзают yt.
    with state_lock:
        active_profile_name = name
        playback_profile_name = name
        track_queue_list[:] = [dict(item) for item in profile_queues.get(name, [])]
        active_account_info = None
        is_active_authenticated = False
        auth_fail_streak = 0
    publish_queue_snapshot()

    try:
        with open(ACTIVE_PROFILE_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except Exception:
        pass

    if name in profiles_data:
        p_info = profiles_data[name]

        # 1. OAuth
        if p_info.get("type") == "oauth":
            token_file = resolve_path(p_info.get("token_file"))
            client_id = sanitize_oauth_input(p_info.get("client_id", ""))
            client_secret = sanitize_oauth_input(p_info.get("client_secret", ""))
            if token_file and os.path.exists(token_file):
                try:
                    with open(token_file, "r", encoding="utf-8") as tf:
                        t_data = json.load(tf)
                    if "error" not in t_data and ("access_token" in t_data or "refresh_token" in t_data):
                        creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
                        new_yt = None
                        try:
                            channel_id = p_info.get("channel_id")
                            if channel_id:
                                new_yt = YTMusic(token_file, user=channel_id, oauth_credentials=creds)
                            else:
                                new_yt = YTMusic(token_file, oauth_credentials=creds)
                            oauth_loaded = True
                        except Exception as e:
                            new_yt = None
                            oauth_loaded = False
                            print(f"[WARN] OAuth YTMusic не загружен для '{name}': {e}")
                        oauth_info = get_oauth_account_info(token_file, creds)
                        try:
                            yt_info = (new_yt or clean_yt).get_account_info() or {}
                        except Exception:
                            yt_info = {}
                        # yt + статус аутентификации меняем атомарно
                        with state_lock:
                            yt = new_yt if new_yt else clean_yt
                            if oauth_info or yt_info:
                                active_account_info = {**(oauth_info or {}), **yt_info}
                            else:
                                active_account_info = {"accountName": name}
                            is_active_authenticated = oauth_loaded
                        if is_active_authenticated:
                            print(f"[OK] OAuth авторизован для '{name}' ({active_account_info.get('accountName')})")
                        else:
                            print(f"[WARN] OAuth токен не прошёл проверку для '{name}'")
                        return
                except Exception as e:
                    print(f"[ERROR] Сбой загрузки OAuth '{name}':", e)

        # 2. Browser
        elif p_info.get("type") == "browser" or "session_file" in p_info or "session" in p_info:
            session_file = resolve_path(p_info.get("session_file"))
            if not session_file or not os.path.exists(session_file):
                safe_id = hashlib.md5(name.encode('utf-8')).hexdigest()[:10]
                session_file = os.path.join(BASE_DIR, f"session_{safe_id}.json")
                if "session" in p_info:
                    atomic_json_dump(session_file, p_info["session"])
                    p_info["session_file"] = f"session_{safe_id}.json"
                    save_profiles()

            if session_file and os.path.exists(session_file):
                try:
                    test_yt = make_channel_instance(session_file, p_info.get("channel_id"))
                    try:
                        yt_info = test_yt.get_account_info()
                        yt_authed = True
                        if yt_info.get("channelHandle"):
                            p_info["channel_handle"] = yt_info["channelHandle"]
                    except Exception:
                        yt_info = {"accountName": name}
                        yt_authed = False
                    with state_lock:
                        yt = test_yt
                        active_account_info = yt_info
                        is_active_authenticated = yt_authed
                    if is_active_authenticated:
                        print(f"[OK] Сессия Browser активна для '{name}'")
                    else:
                        print(f"[WARN] Cookie-сессия '{name}' не прошла проверку аккаунта")
                    return
                except Exception as e:
                    print(f"[ERROR] Сбой Browser сессии '{name}':", e)

    with state_lock:
        yt = clean_yt
        is_active_authenticated = False
    print(f"[INFO] Режим пользователя '{name}' (ожидает подтверждения входа)")

def verify_active_auth():
    global active_account_info, is_active_authenticated, auth_fail_streak
    profile_name = active_profile_name
    p_info = profiles_data.get(profile_name)
    if not p_info:
        return
    if p_info.get("type") == "oauth":
        token_file = resolve_path(p_info.get("token_file"))
        client_id = sanitize_oauth_input(p_info.get("client_id", ""))
        client_secret = sanitize_oauth_input(p_info.get("client_secret", ""))
        if token_file and os.path.exists(token_file):
            creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
            oauth_info = get_oauth_account_info(token_file, creds)
            if active_profile_name != profile_name:
                return
            if oauth_info:
                with state_lock:
                    active_account_info = oauth_info
                    auth_fail_streak = 0
                    is_active_authenticated = True
            else:
                with state_lock:
                    auth_fail_streak += 1
                    if auth_fail_streak >= 3:
                        is_active_authenticated = False
        return
    try:
        inst = yt
        info = inst.get_account_info()
        if active_profile_name != profile_name:
            return
        with state_lock:
            if info and info.get("accountName"):
                active_account_info = info
            auth_fail_streak = 0
            if not is_active_authenticated:
                is_active_authenticated = True
    except Exception:
        with state_lock:
            auth_fail_streak += 1
            if auth_fail_streak >= 3 and is_active_authenticated:
                is_active_authenticated = False

def auth_watchdog():
    while True:
        time.sleep(AUTH_CHECK_INTERVAL_SEC)
        try:
            verify_active_auth()
        except Exception as e:
            print("[ERROR] Ошибка watchdog авторизации:", e)

CACHE_CLEAN_INTERVAL_SEC = 60

def cache_watchdog():
    """Автоочистка кеша: каждые 60 сек подрезает старые файлы, если их больше
    cache_limit. clean_old_cache сам защищает текущий трек (protect_vid) и
    файлы, которые сейчас докачиваются (downloading_vids)."""
    while True:
        time.sleep(CACHE_CLEAN_INTERVAL_SEC)
        try:
            clean_old_cache(protect_vid=current_track_info.get("video_id"))
        except Exception as e:
            print("[ERROR] Ошибка автоочистки кеша:", e)

game_paused_by_radio = False  # радио само поставило паузу из-за паузы игры

def game_pause_watchdog():
    """Пауза радио при паузе в игре: если телеметрия говорит paused и
    auto_pause_game включён — тишину; отпустили — продолжаем. Ручная пауза
    пользователя не трогается (she помечается, кто поставил паузу)."""
    global game_paused_by_radio
    while True:
        time.sleep(2)
        try:
            tele = telemetry_cache.get("data") or {}
            if not app_config.get("auto_pause_game", True) or not tele.get("connected"):
                # связь с игрой пропала — снять нашу паузу, если она была
                if game_paused_by_radio and current_track_info.get("is_paused"):
                    current_track_info["is_paused"] = False
                game_paused_by_radio = False
                continue
            game_paused = bool(tele.get("paused"))
            currently_paused = current_track_info.get("is_paused", False)
            if game_paused and not currently_paused:
                current_track_info["is_paused"] = True
                game_paused_by_radio = True
            elif game_paused_by_radio and not game_paused and currently_paused:
                current_track_info["is_paused"] = False
                game_paused_by_radio = False
        except Exception as e:
            print("[ERROR] game_pause_watchdog:", e)

load_profiles()
load_history_store()
load_favorites_store()
load_playlists_store()

def parse_input_to_track_queue(query_or_url, meta_hint=None):
    global current_playing_item
    q = str(query_or_url).strip()
    if not q:
        return [], False

    # 1. Специфический Video ID в URL: v=... или youtu.be/... или shorts/...
    target_vid = None
    if "v=" in q:
        m = re.search(r'v=([a-zA-Z0-9_-]{11})', q)
        if m: target_vid = m.group(1)
    elif "youtu.be/" in q:
        m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', q)
        if m: target_vid = m.group(1)
    elif "shorts/" in q:
        m = re.search(r'shorts/([a-zA-Z0-9_-]{11})', q)
        if m: target_vid = m.group(1)

    # 2. Проверяем, переданы ли лайки (LM / LL / list=LM / list=LL)
    is_likes = (q.upper() in ("LM", "LL", "VLLM", "VLLL") 
                or bool(re.search(r'list=(?:LM|LL|VLLM|VLLL)(?:&|$)', q, re.IGNORECASE))
                or q.lower() in ("likes", "liked", "лайки", "вподобані", "любимые"))
    if is_likes:
        p_info = profiles_data.get(active_profile_name) or {}
        tracks = []
        if p_info.get("type") == "oauth":
            tracks = get_oauth_liked_songs(limit=100)
        if not tracks and is_active_authenticated and p_info.get("type") != "oauth":
            try:
                liked = yt.get_liked_songs(limit=100)
                for t in liked.get("tracks", []):
                    p = format_track_pick(t)
                    if p: tracks.append(p)
            except Exception:
                pass
        if not tracks:
            tracks = get_persisted_history(limit=50)

        if tracks:
            for t in tracks:
                t["album"] = "Избранные треки"
                meta_cache[t["videoId"]] = t
            if target_vid:
                target_idx = next((i for i, t in enumerate(tracks) if t.get("videoId") == target_vid), None)
                if target_idx is not None:
                    tracks.insert(0, tracks.pop(target_idx))
                else:
                    tracks.insert(0, {"videoId": target_vid, "title": meta_hint.get("title", "Трек") if meta_hint else "Трек", "artist": meta_hint.get("artist", "") if meta_hint else "", "album": "Избранные треки"})
            return tracks, False

    # 3. Плейлист по ID или URL: list=...
    playlist_id = None
    if "list=" in q:
        match = re.search(r'list=([a-zA-Z0-9_-]+)', q)
        if match: playlist_id = match.group(1)
    elif re.match(r'^(?:PL|RD|OLAK5uy_)[a-zA-Z0-9_-]+$', q):
        playlist_id = q

    if playlist_id and playlist_id not in ("LM", "LL", "VLLM", "VLLL"):
        pl = safe_get_playlist(playlist_id, limit=250)
        if pl and pl.get("tracks"):
            is_alb = playlist_id.startswith("OLAK5uy_") or pl.get("type") in ["Album", "Single", "EP"]
            pl_title = pl.get("title", "Альбом" if is_alb else "Плейліст")
            author = pl.get("author", {}).get("name", "") if isinstance(pl.get("author"), dict) else ""
            tracks = []
            for t in pl["tracks"]:
                vid = t.get("videoId")
                if vid:
                    t_art = t.get("artists", [{}])[0].get("name", "") if t.get("artists") else author
                    t_thumb = (t.get("thumbnails", [{}]) or [{}])[-1].get("url", "")
                    track_obj = {
                        "videoId": vid,
                        "title": t.get("title", "Трек"),
                        "artist": t_art,
                        "album": pl_title,
                        "thumbnail": t_thumb
                    }
                    meta_cache[vid] = track_obj
                    tracks.append(track_obj)
            if tracks:
                if target_vid:
                    target_idx = next((i for i, tr in enumerate(tracks) if tr.get("videoId") == target_vid), None)
                    if target_idx is not None:
                        tracks.insert(0, tracks.pop(target_idx))
                    else:
                        tracks.insert(0, {"videoId": target_vid, "title": meta_hint.get("title", "Трек") if meta_hint else "Трек", "artist": meta_hint.get("artist", "") if meta_hint else author, "album": pl_title})
                return tracks, is_alb

    # 4. Если в запросе был конкретный Video ID (прямой 11-символьный ID или ссылка v=...)
    if target_vid or re.match(r'^[a-zA-Z0-9_-]{11}$', q):
        vid_to_use = target_vid or q
        item = {"videoId": vid_to_use}
        if meta_hint:
            item.update(meta_hint)
            meta_cache[vid_to_use] = meta_hint
        elif vid_to_use in meta_cache:
            item.update(meta_cache[vid_to_use])
        return [item], False

    # 5. Browse album ID: MPREb_...
    mpre_match = re.search(r'(MPREb_[a-zA-Z0-9_-]+)', q)
    if mpre_match:
        album_id = mpre_match.group(1)
        album = safe_get_album(album_id)
        if album and album.get("tracks"):
            album_title = album.get("title", "Альбом")
            artist_name = album.get("artists", [{}])[0].get("name", "") if album.get("artists") else ""
            album_thumb = (album.get("thumbnails", [{}]) or [{}])[-1].get("url", "")
            tracks = []
            for t in album["tracks"]:
                vid = t.get("videoId")
                if vid:
                    t_art = t.get("artists", [{}])[0].get("name", "") if t.get("artists") else artist_name
                    t_thumb = (t.get("thumbnails", [{}]) or [{}])[-1].get("url", "") or album_thumb
                    meta_cache[vid] = {
                        "title": t.get("title", "Трек"),
                        "artist": t_art,
                        "album": album_title,
                        "thumbnail": t_thumb
                    }
                    tracks.append({
                        "videoId": vid,
                        "title": t.get("title", "Трек"),
                        "artist": t_art,
                        "album": album_title,
                        "thumbnail": t_thumb
                    })
            if tracks:
                return tracks, True

    # 6. Поиск альбома по ключевому слову "альбом ..."
    album_keyword_match = re.match(r'^(?:альбом|album|диск|сборник)[:\s]+(.+)$', q, re.IGNORECASE)
    if album_keyword_match:
        album_search_term = album_keyword_match.group(1).strip()
        for inst in [yt, clean_yt]:
            try:
                res = inst.search(album_search_term, filter="albums")
                if res:
                    browse_id = res[0].get("browseId")
                    playlist_id = res[0].get("playlistId")
                    if browse_id:
                        album = safe_get_album(browse_id)
                        if album and album.get("tracks"):
                            album_title = album.get("title", res[0].get("title", "Альбом"))
                            tracks = []
                            for t in album["tracks"]:
                                vid = t.get("videoId")
                                if vid:
                                    t_art = t.get("artists", [{}])[0].get("name", "") if t.get("artists") else ""
                                    tracks.append({
                                        "videoId": vid,
                                        "title": t.get("title", "Трек"),
                                        "artist": t_art,
                                        "album": album_title,
                                        "thumbnail": (t.get("thumbnails") or album.get("thumbnails", [{}]))[-1].get("url", "")
                                    })
                            if tracks:
                                return tracks, True
                    elif playlist_id:
                        pl = safe_get_playlist(playlist_id, limit=200)
                        if pl and pl.get("tracks"):
                            album_title = pl.get("title", res[0].get("title", "Альбом"))
                            tracks = []
                            for t in pl["tracks"]:
                                vid = t.get("videoId")
                                if vid:
                                    t_art = t.get("artists", [{}])[0].get("name", "") if t.get("artists") else ""
                                    tracks.append({
                                        "videoId": vid,
                                        "title": t.get("title", "Трек"),
                                        "artist": t_art,
                                        "album": album_title,
                                        "thumbnail": (t.get("thumbnails") or [{}])[-1].get("url", "")
                                    })
                            if tracks:
                                return tracks, True
            except Exception:
                pass

    # 7. Текстовый поиск через YTMusic
    for engine in [yt, clean_yt]:
        try:
            res = engine.search(q, filter="songs")
            if res:
                first = res[0]
                vid = first.get("videoId") or first.get("id")
                if vid:
                    item = {
                        "videoId": vid,
                        "title": first.get("title", ""),
                        "artist": first.get("artists", [{}])[0].get("name", "") if first.get("artists") else "",
                        "thumbnail": (first.get("thumbnails") or [{}])[-1].get("url", "")
                    }
                    meta_cache[item["videoId"]] = item
                    return [item], False
        except Exception:
            pass

    for engine in [yt, clean_yt]:
        try:
            res = engine.search(q, filter="videos")
            if res:
                first = res[0]
                vid = first.get("videoId") or first.get("id")
                if vid:
                    item = {
                        "videoId": vid,
                        "title": first.get("title", ""),
                        "artist": first.get("artists", [{}])[0].get("name", "") if first.get("artists") else "",
                        "thumbnail": (first.get("thumbnails") or [{}])[-1].get("url", "")
                    }
                    meta_cache[item["videoId"]] = item
                    return [item], False
        except Exception:
            pass

    # 8. Финальный fallback: поиск через yt-dlp flat extraction (находит ВСЁ!)
    try:
        ydl_opts = get_ydl_base_opts({'extract_flat': True, 'default_search': 'ytsearch1'})
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch1:{q}", download=False)
            entries = res.get("entries", [])
            if entries:
                first = entries[0]
                vid = first.get("id") or first.get("url")
                if vid:
                    item = {
                        "videoId": vid,
                        "title": first.get("title", "Трек"),
                        "artist": first.get("uploader") or first.get("channel") or "",
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    }
                    meta_cache[vid] = item
                    return [item], False
    except Exception as ex:
        print(f"[parse_input] yt-dlp search fallback error: {ex}")

    return [], False

def download_track_bg(vid, done_callback=None, wait_if_in_progress=False):
    cached = find_cached_file(vid)
    if cached:
        if done_callback:
            done_callback(cached)
        return cached

    with downloading_lock:
        in_progress = vid in downloading_vids
        if not in_progress:
            downloading_vids.add(vid)

    if in_progress:
        if wait_if_in_progress:
            for _ in range(60):
                time.sleep(0.1)
                cached = find_cached_file(vid)
                if cached:
                    if done_callback:
                        done_callback(cached)
                    return cached
                with downloading_lock:
                    if vid not in downloading_vids:
                        break
            return find_cached_file(vid)
        return None

    ydl_opts = get_ydl_base_opts({
        'format': 'ba[ext=m4a]/ba/b',
        'outtmpl': os.path.join(CACHE_DIR, f"{vid}.%(ext)s"),
        'noplaylist': True,
        'quiet': True
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={vid}"])
        gc.collect()  # yt_dlp разборы страниц прожорливы — не держим мусор в RSS
        clean_old_cache(protect_vid=vid)
        cached = find_cached_file(vid)
        if cached and done_callback:
            done_callback(cached)
        return cached
    except Exception as e:
        print(f"[ERROR] Ошибка загрузки {vid}:", e)
    finally:
        with downloading_lock:
            downloading_vids.discard(vid)
    return find_cached_file(vid)

def get_direct_stream_url(vid):
    now = time.time()
    if vid in stream_url_cache:
        exp, url, info = stream_url_cache[vid]
        if now < exp and url:
            return url, info
        stream_url_cache.pop(vid, None)

    try:
        ydl_opts = get_ydl_base_opts({'format': 'ba[ext=m4a]/ba/b', 'noplaylist': True})
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            url = info.get('url')
            if url:
                stream_url_cache[vid] = (now + STREAM_URL_TTL_SEC, url, info)
            gc.collect()
            return url, info
    except Exception as e:
        print(f"[ERROR] Не удалось получить стрим {vid}:", e)
        return None, None

def dj_thread():
    global current_proc, target_seek_time, upcoming_queue, radio_inflight, track_queue_list, current_playing_item, played_vids, playback_profile_name, station_state

    while True:
        try:
            task = None
            if not track_queue_list:
                try:
                    task = cmd_queue.get(timeout=2.0)
                except queue.Empty:
                    task = None
            else:
                try:
                    task = cmd_queue.get_nowait()
                except queue.Empty:
                    task = None

            if task:
                # --- Режим интернет-радиостанции: крутим прямой стрим,
                # очередь YouTube не трогаем (после остановки продолжится сама)
                if task.get("station"):
                    skip_event.clear()
                    st = task["station"]
                    with state_lock:
                        station_state = st
                    current_track_info.update({
                        "title": st.get("name", "Радио"),
                        "artist": st.get("genre") or "Інтернет-радіо",
                        "album": "",
                        "thumbnail": "",
                        "duration": 0,
                        "duration_known": False,
                        "current_time": 0,
                        "is_paused": False,
                        "status": "Радио",
                        "video_id": f"station:{st.get('id', '')}",
                        "favorite": False,
                        "is_station": True
                    })
                    play_station_loop(st)
                    kill_current_proc()
                    skip_event.clear()
                    # станция кончилась (skip/stop/новая задача) — выходим,
                    # иначе цикл снова подхватит очередь старым video_id
                    with state_lock:
                        if station_state and station_state.get("id") == st.get("id"):
                            station_state = None
                    current_track_info["is_station"] = False
                    continue

                # --- Возврат из станции к обычной очереди: просто выходим,
                # внешний цикл сам продолжит играть track_queue_list
                if task.get("resume_queue"):
                    skip_event.clear()
                    with state_lock:
                        station_state = None
                    current_track_info["is_station"] = False
                    continue

                skip_event.clear()
                query = task.get("query", "")
                start_sec = task.get("offset", 0)
                meta_hint = task.get("meta")

                # Готовый список треков (игр своих плейлистов) — без парсинга
                preset_tracks = task.get("tracks")
                if preset_tracks:
                    new_tracks = [dict(t) for t in preset_tracks if isinstance(t, dict) and t.get("videoId")]
                    is_album = False
                else:
                    new_tracks, is_album = parse_input_to_track_queue(query, meta_hint)
                if not new_tracks:
                    current_track_info["title"] = "Трек не найден!"
                    current_track_info["album"] = ""
                    current_track_info["status"] = "Ошибка поиска"
                    current_track_info["current_time"] = 0
                    continue

                # Под локом: HTTP-эндпоинты мутируют очередь из других потоков
                with state_lock:
                    track_queue_list = new_tracks
                played_vids.clear()
                is_current_album = is_album
            else:
                start_sec = 0
                is_current_album = False

            while True:
                if not cmd_queue.empty():
                    break

                # pop под локом, чтобы не гоняться с reorder/remove из эндпоинтов
                with state_lock:
                    item = track_queue_list.pop(0) if track_queue_list else None
                if item is None:
                    break
                vid = item.get("videoId")
                if not vid:
                    continue
                if not item.get("force_play") and vid in played_vids:
                    continue

                played_vids.add(vid)
                dismissed_vids.discard(vid)
                playback_profile_name = active_profile_name
                track_profile_name = playback_profile_name
                skip_event.clear()

                with state_lock:
                    if current_playing_item and current_playing_item.get("videoId"):
                        if not played_tracks_history or played_tracks_history[-1].get("videoId") != current_playing_item.get("videoId"):
                            played_tracks_history.append(dict(current_playing_item))
                    current_playing_item = dict(item)

                # Обновляем очередь для клиентов (под локом — иначе читатели
                # /api/status могут поймать список наполовину пересобранным)
                with state_lock:
                    upcoming_queue = [{
                        "title": q_item.get("title", "Трек"),
                        "artist": q_item.get("artist", ""),
                        "album": q_item.get("album", ""),
                        "videoId": q_item.get("videoId"),
                        "thumbnail": q_item.get("thumbnail", "")
                    } for q_item in list(track_queue_list[:8])]

                try:
                    cache_on = bool(app_config.get("cache_enabled", False))
                    if cache_on:
                        local_file = find_cached_file(vid)
                        if not local_file:
                            local_file = download_track_bg(vid, wait_if_in_progress=True)
                    else:
                        # Режим стрима: без записи на диск, только прямой URL
                        local_file = None

                    for q_item in track_queue_list[:2]:
                        next_vid = q_item.get("videoId")
                        if next_vid and not find_cached_file(next_vid):
                            if cache_on:
                                bg_download_pool.submit(download_track_bg, next_vid)
                            threading.Thread(target=get_direct_stream_url, args=(next_vid,), daemon=True).start()

                    thumb_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    is_from_cache = bool(local_file)

                    # Мгновенно выставляем метаданные для моментального отклика UI.
                    # ВАЖНО: не подставляем title/artist предыдущего трека — лучше
                    # честные плейсхолдеры, чем чужая обложка на кешированном треке.
                    cached_meta = meta_cache.get(vid) or {}
                    current_track_info["video_id"] = vid
                    current_track_info["title"] = cached_meta.get("title") or item.get("title") or "Трек"
                    current_track_info["artist"] = cached_meta.get("artist") or item.get("artist") or "YouTube Music"
                    current_track_info["thumbnail"] = cached_meta.get("thumbnail") or item.get("thumbnail") or thumb_url
                    current_track_info["album"] = item.get("album", "")
                    current_track_info["duration"] = int(cached_meta.get("duration") or item.get("duration") or 180)
                    current_track_info["duration_known"] = bool(cached_meta.get("duration") or item.get("duration"))
                    current_track_info["current_time"] = start_sec

                    if is_from_cache:
                        input_source = local_file
                        current_track_info["status"] = f"Кеш ({os.path.basename(CACHE_DIR)})"
                        # Точная длительность из самого файла (иначе seek упирается
                        # в фиктивные 180 сек)
                        real_dur = probe_duration(local_file)
                        if real_dur and real_dur > 1:
                            current_track_info["duration"] = int(real_dur)
                            current_track_info["duration_known"] = True
                            cached_meta["duration"] = int(real_dur)
                            meta_cache[vid] = {**cached_meta, **{
                                "title": current_track_info["title"],
                                "artist": current_track_info["artist"],
                                "thumbnail": current_track_info["thumbnail"],
                                "duration": int(real_dur)
                            }}
                    else:
                        current_track_info["status"] = "Live Stream"
                        input_source, info = get_direct_stream_url(vid)
                        if info:
                            current_track_info["title"] = info.get('title') or current_track_info["title"]
                            current_track_info["artist"] = info.get('uploader') or info.get('artist') or current_track_info["artist"]
                            current_track_info["thumbnail"] = info.get('thumbnail') or current_track_info["thumbnail"]
                            current_track_info["duration"] = int(info.get('duration') or 180)
                            current_track_info["duration_known"] = bool(info.get('duration'))
                            meta_cache[vid] = {
                                "title": current_track_info["title"],
                                "artist": current_track_info["artist"],
                                "thumbnail": current_track_info["thumbnail"],
                                "duration": current_track_info["duration"]
                            }

                    if not input_source:
                        print(f"[WARN] Источник для {vid} не получен, пропускаем...")
                        current_track_info["status"] = "Ошибка источника"
                        current_track_info["current_time"] = 0
                        skip_event.clear()
                        # сбрасываем seek-офсет, иначе следующий трек стартанет
                        # с чужой позиции (сброс ниже по коду сюда не доходит)
                        start_sec = 0
                        continue

                    play_history.append(vid)
                    try:
                        record_history_item(vid, current_track_info.get("title"), current_track_info.get("artist"), current_track_info.get("thumbnail"), profile_name=track_profile_name)
                    except Exception:
                        pass

                    # ФОНОВАЯ ПОДГРУЗКА РЕКОМЕНДАЦИЙ (НЕ БЛОКИРУЕТ СТАРТ ТРЕКА!)
                    # Исправлено: дедупликация по истории воспроизведения (а не
                    # только по текущему проходу), добивка волной от артиста,
                    # фоллбэк уважает активный профиль, очередь под локом.
                    generation = playback_generation
                    def bg_expand_recommendations(seed_vid, seed_artist, was_album, generation):
                        nonlocal is_current_album
                        global track_queue_list
                        global upcoming_queue
                        if generation != playback_generation:
                            return
                        with state_lock:
                            qlen = len(track_queue_list)
                        # Если играет альбом, расширяем когда осталось 3 трека
                        if was_album and qlen > 3:
                            return
                        # Держим запас очереди в 8 треков
                        if qlen >= 8:
                            return

                        try:
                            recent_played = set(list(play_history)[-12:])
                            if current_playing_item and current_playing_item.get("videoId"):
                                recent_played.add(current_playing_item["videoId"])
                            with state_lock:
                                known = recent_played | {t.get("videoId") for t in track_queue_list}
                            known.discard(None)

                            def _push_r(rvid, rtitle, rartist, rthumb, tag, dur=None):
                                if generation != playback_generation:
                                    return False
                                with state_lock:
                                    blocked = rvid in dismissed_vids
                                if (not rvid or rvid in known
                                        or blocked
                                        or is_generic_chart_track(rtitle, rartist)): 
                                    return False
                                known.add(rvid)
                                meta_cache[rvid] = {
                                    "title": rtitle, "artist": rartist,
                                    "thumbnail": rthumb, "duration": dur
                                }
                                with state_lock:
                                    if generation != playback_generation:
                                        return False
                                    if any(t.get("videoId") == rvid for t in track_queue_list):
                                        return False
                                    track_queue_list.append({
                                        "videoId": rvid, "title": rtitle,
                                        "artist": rartist, "album": tag,
                                        "thumbnail": rthumb
                                    })
                                return True

                            def _feed_radio(seed):
                                n = 0
                                radio = safe_get_watch_playlist(seed, radio=True)
                                for r in radio.get("tracks", []):
                                    if _push_r(r.get("videoId"), r.get("title", ""),
                                               (r.get("artists", [{}])[0].get("name", "") if r.get("artists") else ""),
                                               (r.get("thumbnails", [{}]) or [{}])[-1].get("url", ""),
                                               "Рекомендации (Радио)",
                                               dur=r.get("duration_seconds")):
                                        n += 1
                                    with state_lock:
                                        full = len(track_queue_list) >= 12
                                    if full:
                                        break
                                return n

                            # 1) Радио-волна от самого трека
                            added = _feed_radio(seed_vid)

                            # 2) Радио слабое -> добиваем волной от других треков
                            #    того же артиста (раньше сид был только один, поэтому
                            #    реки быстро сходили на нет)
                            with state_lock:
                                qlen = len(track_queue_list)
                            if added < 4 and qlen < 6 and seed_artist:
                                try:
                                    res = yt.search(seed_artist, filter="songs") or []
                                except Exception:
                                    try:
                                        res = clean_yt.search(seed_artist, filter="songs") or []
                                    except Exception:
                                        res = []
                                for es in [x.get("videoId") for x in res[:3]
                                           if x.get("videoId") and x.get("videoId") != seed_vid][:2]:
                                    added += _feed_radio(es)
                                    with state_lock:
                                        if len(track_queue_list) >= 10:
                                            break

                            if was_album and added > 0:
                                is_current_album = False

                            # 3) Фоллбэк: профильный пул (пк -> PK, остальные ->
                            #    Ананасов), вперемешку и без повторов истории
                            with state_lock:
                                qlen = len(track_queue_list)
                            if qlen < 4:
                                user_pool = []
                                try:
                                    p_info = profiles_data.get(active_profile_name) or {}
                                    if p_info.get("type") == "oauth":
                                        user_pool = get_oauth_liked_songs(limit=30)
                                    if not user_pool:
                                        user_pool = get_persisted_history(limit=30)
                                except Exception:
                                    pass

                                if not user_pool:
                                    user_pool = PK_TRACK_PICKS if is_pk_profile_name() else ANANASOV_REAL_PICKS

                                seeds = [s for s in user_pool if s.get("videoId") and s["videoId"] not in known]
                                random.shuffle(seeds)
                                for seed in seeds[:5]:
                                    _push_r(seed["videoId"], seed.get("title", "Трек"), seed.get("artist", ""),
                                            seed.get("thumbnail", ""), "Рекомендации (Вайб)")
                        except Exception as ex:
                            print("[WARN] Ошибка расширения радио:", ex)

                        if generation != playback_generation:
                            return
                        with state_lock:
                            if generation != playback_generation:
                                return
                            upcoming_queue[:] = [{
                                "title": q_item.get("title", "Трек"),
                                "artist": q_item.get("artist", ""),
                                "album": q_item.get("album", ""),
                                "videoId": q_item.get("videoId"),
                                "thumbnail": q_item.get("thumbnail", "")
                            } for q_item in list(track_queue_list[:8])]

                    def bg_expand_wrapped(seed_vid, seed_artist, was_album, generation):
                        global radio_inflight
                        try:
                            bg_expand_recommendations(seed_vid, seed_artist, was_album, generation)
                        finally:
                            with state_lock:
                                if generation == playback_generation:
                                    radio_inflight = max(0, radio_inflight - 1)

                    with state_lock:
                        if generation == playback_generation:
                            radio_inflight += 1
                    seed_artist = current_track_info.get("artist", "")
                    threading.Thread(target=bg_expand_wrapped, args=(vid, seed_artist, is_current_album, generation), daemon=True).start()

                    # докачиваем играющий трек только если кеш включён
                    if not is_from_cache and app_config.get("cache_enabled", False):
                        bg_download_pool.submit(download_track_bg, vid)

                    skip_event.clear()
                    seek_event.clear()
                    flush_listeners()

                    current_seek = start_sec
                    start_sec = 0

                    while True:
                        if skip_event.is_set() or not cmd_queue.empty():
                            break

                        bitrate = app_config.get("bitrate", "128k")
                        boost = app_config.get("volume_boost", "1.0")

                        cmd = [
                            'ffmpeg',
                            '-nostdin',
                        ]
                        if str(input_source).startswith('http'):
                            cmd.extend([
                                '-reconnect', '1',
                                '-reconnect_streamed', '1',
                                '-reconnect_at_eof', '1',
                                '-reconnect_delay_max', '3',
                                '-rw_timeout', '15000000',
                            ])
                        cmd.extend([
                            '-probesize', '65536',
                            '-analyzeduration', '200000',
                            '-ss', str(current_seek),
                            '-re',
                            '-i', input_source,
                            '-af', f'volume={boost}',
                            '-f', 'mp3',
                            '-acodec', 'libmp3lame',
                            '-b:a', bitrate,
                            '-ar', '44100',
                            '-ac', '2',
                            'pipe:1'
                        ])
                        ff_log_path = os.path.join(CACHE_DIR, 'ffmpeg_last.log')
                        try:
                            ff_log_file = open(ff_log_path, 'wb')
                        except Exception:
                            ff_log_file = subprocess.DEVNULL

                        try:
                            with proc_lock:
                                current_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ff_log_file)
                        finally:
                            try:
                                ff_log_file.close()
                            except Exception:
                                pass
                        seek_event.clear()

                        ff_start = time.time()
                        start_play_time = time.time() - current_seek

                        while True:
                            if skip_event.is_set():
                                print(f"[DEBUG] Break: skip_event for {vid}")
                                kill_current_proc()
                                flush_listeners()
                                break
                            if not cmd_queue.empty():
                                print(f"[DEBUG] Break: cmd_queue not empty for {vid}")
                                kill_current_proc()
                                flush_listeners()
                                break
                            if seek_event.is_set():
                                print(f"[DEBUG] Break: seek_event for {vid}")
                                kill_current_proc()
                                flush_listeners()
                                break

                            if current_track_info["is_paused"]:
                                p_start = time.time()
                                while current_track_info["is_paused"]:
                                    if skip_event.is_set() or not cmd_queue.empty() or seek_event.is_set():
                                        break
                                    # Тишина темпом битрейта (а не 4КБ каждые 0.1с
                                    # в лоб) — очереди слушателей не переполняются
                                    kbps = int(re.sub(r'\D', '', str(bitrate)) or 128)
                                    pause_chunk = max(256, int(kbps * 128 / 8))
                                    push_chunk(MP3_SILENCE_FRAME * 38)
                                    time.sleep(1.0)
                                start_play_time += (time.time() - p_start)
                                flush_listeners()
                                if skip_event.is_set() or not cmd_queue.empty() or seek_event.is_set():
                                    kill_current_proc()
                                    break

                            with proc_lock:
                                proc = current_proc
                            if not proc or not proc.stdout:
                                break
                            chunk = proc.stdout.read(4096)
                            if not chunk:
                                elapsed = time.time() - ff_start
                                print(f"[DEBUG] Break: not chunk for {vid}, time={elapsed:.2f}s, poll={proc.poll()}", flush=True)
                                if elapsed < 3:
                                    try:
                                        with open(ff_log_path, 'r', encoding='utf-8', errors='replace') as lf:
                                            print(f"[FFMPEG STDERR for {vid}]:\n{lf.read()[-600:]}", flush=True)
                                    except Exception:
                                        pass
                                # ffmpeg упал в первые секунды на http-источнике —
                                # скорее всего протухший URL из кеша: сбрасываем его
                                if str(input_source).startswith('http') and time.time() - ff_start < 3:
                                    stream_url_cache.pop(vid, None)
                                break

                            if skip_event.is_set() or not cmd_queue.empty() or seek_event.is_set():
                                kill_current_proc()
                                flush_listeners()
                                break

                            current_track_info["current_time"] = int(time.time() - start_play_time)

                            push_chunk(chunk)

                        kill_current_proc()

                        if seek_event.is_set():
                            flush_listeners()
                            current_seek = target_seek_time
                            current_track_info["current_time"] = current_seek
                            continue
                        else:
                            break

                    clean_old_cache(protect_vid=vid)

                except Exception as e:
                    print(f"[ERROR] Ошибка воспроизведения трека {vid}:", e)
                    time.sleep(0.5)
                    continue

            # Очередь исчерпана: ГАРАНТИРОВАННОЕ БЕСКОНЕЧНОЕ РАДИО! Эфир НИКОГДА не встает!
            # skip_event тут НЕ проверяем: после скипа последнего трека он
            # застревал в set() и радио молчало вечно — cmd_queue.empty()
            # уже покрывает случай "сейчас придёт команда".
            while not track_queue_list and cmd_queue.empty():
                print("[RADIO] Очередь пуста! Автоматически восстанавливаем радио-поток...")
                for _ in range(6):
                    if track_queue_list or not cmd_queue.empty() or skip_event.is_set():
                        break
                    time.sleep(0.5)
                if track_queue_list or not cmd_queue.empty() or skip_event.is_set():
                    break

                seed_to_use = (current_playing_item.get("videoId") if current_playing_item else None) or "6Pr1yeLw8XI"
                emergency_tracks = []
                try:
                    radio = safe_get_watch_playlist(seed_to_use, radio=True)
                    for r in radio.get("tracks", []):
                        rvid = r.get("videoId")
                        rtitle = r.get("title", "")
                        rartist = (r.get("artists", [{}])[0].get("name", "") if r.get("artists") else "")
                        if rvid and rvid not in played_vids and rvid not in dismissed_vids and not is_non_music_video(rtitle, rartist):
                            emergency_tracks.append({
                                "videoId": rvid,
                                "title": rtitle,
                                "artist": rartist,
                                "album": "Рекомендации (Радио)",
                                "thumbnail": (r.get("thumbnails", [{}]) or [{}])[-1].get("url", "")
                            })
                except Exception as ex:
                    print("[RADIO] Emergency radio wave failed:", ex)

                if not emergency_tracks:
                    pool = get_oauth_liked_songs(limit=30) or get_persisted_history(limit=30)
                    for item in pool:
                        if item.get("videoId") and item.get("videoId") not in played_vids and item.get("videoId") not in dismissed_vids and not is_non_music_video(item.get("title", ""), item.get("artist", "")): 
                            emergency_tracks.append({
                                "videoId": item["videoId"],
                                "title": item.get("title", "Трек"),
                                "artist": item.get("artist", ""),
                                "album": "Избранное",
                                "thumbnail": item.get("thumbnail", "")
                            })

                if emergency_tracks:
                    existing_ids = set(played_vids)
                    emergency_tracks = [t for t in emergency_tracks if t.get("videoId") not in existing_ids]
                    random.shuffle(emergency_tracks)
                    with state_lock:
                        track_queue_list.extend(emergency_tracks[:12])
                        upcoming_queue[:] = [{
                            "title": q_item.get("title", "Трек"),
                            "artist": q_item.get("artist", ""),
                            "album": q_item.get("album", ""),
                            "videoId": q_item.get("videoId"),
                            "thumbnail": q_item.get("thumbnail", "")
                        } for q_item in list(track_queue_list[:8])]
                    print(f"[RADIO] Радио успешно подпитано: +{len(emergency_tracks[:12])} треков!")
                    break
                else:
                    print("[RADIO] Ожидание доступных треков (повтор через 2с)...")
                    time.sleep(2.0)

        except Exception as e:
            print("[ERROR] Ошибка в DJ-потоке:", e)
            time.sleep(1)

@app.route('/')
def home():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/overlay')
def overlay_page():
    return send_from_directory(BASE_DIR, 'overlay.html')

# --- DISCORD RICH PRESENCE ---

def discord_rpc_thread():
    """Показывает текущий трек в статусе Discord. pypresence опционален:
    не установлен или Discord не запущен — фича тихо выключается."""
    try:
        from pypresence import Presence
    except ImportError:
        print("[Discord] pypresence не установлен (pip install pypresence) — Rich Presence выключен")
        return

    rpc = None
    rpc_connected = False
    last_update = 0.0
    last_signature = None

    while True:
        time.sleep(5)
        if not app_config.get("discord_rpc", False):
            if rpc_connected and rpc:
                try:
                    rpc.close()
                except Exception:
                    pass
                rpc_connected = False
                last_signature = None
            continue

        track = current_track_info
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        if not title or title in ("Трек не найден!", "Ошибка поиска") or track.get("is_station"):
            # станция: нечего показывать — очистим
            if rpc_connected and rpc:
                try:
                    rpc.clear()
                except Exception:
                    pass
                last_signature = None
            continue

        paused = bool(track.get("is_paused"))
        dur = track.get("duration") or 0
        try:
            dur = int(dur)
        except (TypeError, ValueError):
            dur = 0
        cur = track.get("current_time") or 0
        try:
            cur = int(cur)
        except (TypeError, ValueError):
            cur = 0
        signature = (title, artist, paused, dur)

        # Discord RPC не любит обновления чаще ~15с, но смена трека/паузы — сразу
        now = time.time()
        if signature == last_signature and now - last_update < 15:
            continue

        try:
            if not rpc_connected:
                rpc = Presence(client_id="1299153000000000000")  # локальное приложение, id неважен
                rpc.connect()
                rpc_connected = True
        except Exception:
            rpc_connected = False
            continue  # Discord не запущен — попробуем позже

        try:
            if paused:
                rpc.update(details=title[:128], state=artist[:128] if artist else "Пауза",
                           large_text="ETS2 Radio DJ", small_text="paused")
            elif dur and cur <= dur:
                rpc.update(details=title[:128], state=artist[:128] if artist else "",
                           large_text="ETS2 Radio DJ",
                           start=int(now - cur), end=int(now - cur + dur))
            else:
                rpc.update(details=title[:128], state=artist[:128] if artist else "",
                           large_text="ETS2 Radio DJ", start=int(now))
            last_signature = signature
            last_update = now
        except Exception:
            try:
                rpc.close()
            except Exception:
                pass
            rpc_connected = False

@app.route('/api/status')
def status():
    # Статистика диска кешируется на 10 сек: иначе glob+getsize по всему
    # HDD-кешу выполняется каждую секунду на каждую открытую вкладку
    now = time.time()
    with state_lock:
        cached_stats = status_cache.get("stats")
    if cached_stats and now - cached_stats[0] < 10:
        cache_files_count, total_mb = cached_stats[1], cached_stats[2]
    else:
        cache_files = []
        for ext in CACHE_EXTS:
            cache_files.extend(glob.glob(os.path.join(CACHE_DIR, f"*.{ext}")))
        total_bytes = 0
        for f in cache_files:
            try:
                if os.path.exists(f):
                    total_bytes += os.path.getsize(f)
            except OSError:
                pass
        cache_files_count = len(cache_files)
        total_mb = round(total_bytes / (1024 * 1024), 1)
        with state_lock:
            status_cache["stats"] = (now, cache_files_count, total_mb)

    with state_lock:
        track_snapshot = dict(current_track_info)
        track_snapshot["favorite"] = is_favorite(track_snapshot.get("video_id"))
        queue_snapshot = [dict(item) for item in upcoming_queue]
        for item in queue_snapshot:
            item["favorite"] = is_favorite(item.get("videoId"))
        profile_snapshot = list(profiles_data.keys())
        account_snapshot = {n: (i.get("user_name") or n) for n, i in profiles_data.items()}
        if isinstance(active_account_info, dict) and active_account_info.get("accountName"):
            account_snapshot[active_profile_name] = active_account_info["accountName"]
        active_snapshot = active_profile_name
        auth_snapshot = is_active_authenticated
        account_info_snapshot = dict(active_account_info) if isinstance(active_account_info, dict) else active_account_info
        config_snapshot = sanitize_config(app_config)

    return jsonify({
        "track": track_snapshot,
        "active_profile": active_snapshot,
        "account_info": account_info_snapshot,
        "is_auth": auth_snapshot,
        "profiles": profile_snapshot,
        "profile_accounts": account_snapshot,
        "cache_dir": CACHE_DIR,
        "cache_count": cache_files_count,
        "cache_mb": total_mb,
        "queue": queue_snapshot,
        "config": config_snapshot
    })

# --- OAUTH АВТОРИЗАЦИЯ ---

# Креды OAuth-клиента Device Flow. Публичные креды ytmusicapi Google отозвал
# (invalid_client), поэтому храним свои в config.json: вводятся один раз через
# UI или файл, дальше сервер сам их подставляет при каждом входе.
@app.route('/api/oauth/client', methods=['GET'])
def oauth_client_get():
    return jsonify({
        "ok": True,
        "client_id": app_config.get("oauth_client_id", ""),
        "client_secret": app_config.get("oauth_client_secret", ""),
        "configured": bool(app_config.get("oauth_client_id") and app_config.get("oauth_client_secret"))
    })

@app.route('/api/oauth/start', methods=['POST'])
def oauth_start():
    global current_oauth_flow
    data = request.get_json(silent=True) or {}
    client_id = sanitize_oauth_input(data.get("client_id", "")) or app_config.get("oauth_client_id", "")
    client_secret = sanitize_oauth_input(data.get("client_secret", "")) or app_config.get("oauth_client_secret", "")

    if not client_id or not client_secret:
        return jsonify({
            "ok": False,
            "error": "Нет OAuth-кредов. Один раз создай OAuth-клиент типа 'TV and Limited Input devices' "
                     "в Google Cloud Console (console.cloud.google.com → APIs & Services → Credentials) "
                     "и вставь Client ID + Client Secret в поля ниже. Дальше сервер запомнит их навсегда."
        }), 400

    try:
        creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
        code_data = creds.get_code()

        current_oauth_flow = {
            "creds": creds,
            "device_code": code_data["device_code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "profile_name": str(data.get("custom_name") or '').strip() or DEFAULT_PROFILE_NAME
        }

        return jsonify({
            "ok": True,
            "user_code": code_data.get("user_code"),
            "verification_url": code_data.get("verification_url", "https://www.google.com/device"),
            "interval": code_data.get("interval", 5)
        })
    except Exception as e:
        # иначе /api/oauth/poll начнёт опрашивать протухший старый device_code
        current_oauth_flow = None
        return jsonify({"ok": False, "error": f"Ошибка Google API: {str(e)}"}), 500

@app.route('/api/oauth/poll', methods=['POST'])
def oauth_poll():
    global current_oauth_flow
    if not current_oauth_flow:
        return jsonify({"status": "error", "error": "Сессия устарела"}), 400

    try:
        token = current_oauth_flow["creds"].token_from_code(current_oauth_flow["device_code"])
        
        if isinstance(token, dict):
            if "error" in token:
                err = token.get("error", "")
                if err in ["authorization_pending", "slow_down"]:
                    return jsonify({"status": "pending", "slow_down": err == "slow_down"})
                err_desc = token.get("error_description", err)
                current_oauth_flow = None
                return jsonify({"status": "error", "error": f"Ошибка Google: {err_desc}"})

            if "access_token" in token or "refresh_token" in token:
                token_dict = dict(token)
                token_dict.pop("refresh_token_expires_in", None)

                profile_name = current_oauth_flow["profile_name"]
                safe_id = hashlib.md5(profile_name.encode('utf-8')).hexdigest()[:10]
                token_file = os.path.join(BASE_DIR, f"token_{safe_id}_oauth.json")

                atomic_json_dump(token_file, token_dict)

                profiles_data[profile_name] = {
                    "type": "oauth",
                    "client_id": current_oauth_flow["client_id"],
                    "client_secret": current_oauth_flow["client_secret"],
                    "token_file": token_file,
                    "user_name": profile_name
                }
                # Запоминаем рабочие креды клиента — следующий вход без их ввода
                app_config["oauth_client_id"] = current_oauth_flow["client_id"]
                app_config["oauth_client_secret"] = current_oauth_flow["client_secret"]
                save_config()
                save_profiles()
                set_active_yt_instance(profile_name)
                clear_picks_cache()
                current_oauth_flow = None
                return jsonify({
                    "status": "success",
                    "profile_name": profile_name,
                    "account": active_account_info
                })

        return jsonify({"status": "pending"})
    except Exception as e:
        err = str(e).lower()
        if "authorization_pending" in err or "slow_down" in err:
            return jsonify({"status": "pending", "slow_down": "slow_down" in err})
        return jsonify({"status": "error", "error": str(e)})

# --- ВХОД ЧЕРЕЗ COOKIE / HEADERS ---

def parse_and_validate_cookie_or_headers(text):
    text = text.strip()
    if not text:
        raise ValueError("Пустая строка")

    cookie_str = ""
    authuser = "0"
    if text.startswith("{") and text.endswith("}"):
        try:
            data = json.loads(text)
            if "cookie" in data:
                cookie_str = data["cookie"]
            elif "Cookie" in data:
                cookie_str = data["Cookie"]
            for key in ("x-goog-authuser", "X-Goog-Authuser", "authuser", "authUser"):
                if key in data and str(data[key]).strip().isdigit():
                    authuser = str(data[key]).strip()
                    break
        except Exception:
            cookie_str = ""

    if "cookie:" in text.lower() or "user-agent:" in text.lower() or "x-goog-authuser:" in text.lower():
        for line in text.split("\n"):
            line = line.strip()
            low = line.lower()
            if not cookie_str and low.startswith("cookie:"):
                cookie_str = line.split(":", 1)[1].strip()
            elif low.startswith("x-goog-authuser:"):
                val = line.split(":", 1)[1].strip()
                if val.isdigit():
                    authuser = val

    if not cookie_str:
        cookie_str = text.replace("\r", "").replace("\n", " ").strip()

    try:
        sapisid = sapisid_from_cookie(cookie_str)
    except Exception:
        m = re.search(r'(__Secure-3PAPISID|SAPISID)=([^;]+)', cookie_str)
        if m:
            sapisid = m.group(2).strip('"')
        else:
            raise ValueError("У куках не знайдено __Secure-3PAPISID или SAPISID. Убедись, что ты авторизован в music.youtube.com")

    # Если authuser не передали явно, пытаемся вытащить его из самих кук
    # (Google хранит активный канал в AUTHUSER / __Secure-...AUTHUSER)
    if authuser == "0":
        m_au = re.search(r'(?:^|;\s*)(?:__Secure-\d+P?)?AUTHUSER=(\d+)', cookie_str)
        if m_au:
            authuser = m_au.group(1)

    origin = "https://music.youtube.com"
    auth_hash = get_authorization(f"{sapisid} {origin}")

    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept": "*/*",
        "accept-encoding": "gzip, deflate",
        "content-type": "application/json",
        "origin": origin,
        "cookie": cookie_str,
        "authorization": auth_hash,
        "x-goog-authuser": authuser
    }
    return headers


def _find_account_items(node, out):
    """Рекурсивно ищет accountItem в JSON-дереве ответа accounts_list."""
    if isinstance(node, dict):
        if "accountItem" in node and isinstance(node["accountItem"], dict):
            out.append(node["accountItem"])
        for v in node.values():
            _find_account_items(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_account_items(v, out)

def _runs_text(field):
    try:
        return "".join(r.get("text", "") for r in field.get("runs", []))
    except Exception:
        return ""

def list_account_channels(test_yt):
    """Список brand-каналов аккаунта: [{'name','handle','user_id','selected'}].
    У одного Google-аккаунта может быть несколько каналов (у тебя 'Олег' и 'пк') —
    pageId/gaiaId и есть тот самый user для YTMusic(user=...)."""
    channels = []
    try:
        resp = test_yt._send_request('account/accounts_list', {})
        items = []
        _find_account_items(resp, items)
        for it in items:
            name = _runs_text(it.get("accountName", {}))
            handle = _runs_text(it.get("channelHandle", {}))
            selected = bool(it.get("isSelected"))
            # pageIdToken (brand-канал) или obfuscatedGaiaId (дефолтный)
            tokens = (it.get("serviceEndpoint", {})
                        .get("selectActiveIdentityEndpoint", {})
                        .get("supportedTokens", []))
            page_id = gaia_id = None
            for tok in tokens:
                if "pageIdToken" in tok:
                    page_id = tok["pageIdToken"].get("pageId")
                if "accountStateToken" in tok:
                    gaia_id = tok["accountStateToken"].get("obfuscatedGaiaId") or gaia_id
            channels.append({
                "name": name,
                "handle": handle,
                "user_id": page_id or gaia_id,
                "is_brand": bool(page_id),
                "selected": selected
            })
    except Exception as e:
        print("[WARN] Не удалось получить список каналов:", e)
    return channels

def make_channel_instance(session_file, channel_id=None):
    """YTMusic для конкретного brand-канала. Если у аккаунта несколько каналов
    (например 'Мой профиль'), по умолчанию ytmusicapi берёт дефолтный.
    Передаём channel_id в 'user' — и получаем реки/историю/лайки нужного канала."""
    if channel_id:
        try:
            return YTMusic(session_file, user=channel_id)
        except Exception as e:
            print(f"[WARN] Канал user={channel_id} не принят:", e)
            raise
    return YTMusic(session_file)

def account_fingerprint(cookie_str):
    """Короткий отпечаток аккаунта по SAPISID: два канала на одном Google-логине
    имеют разный SAPISID. Нужен, чтобы не плодить два профиля на один канал."""
    m = re.search(r'(?:__Secure-3PAPISID|SAPISID)=([^;]+)', cookie_str)
    if not m:
        return ""
    return hashlib.md5(m.group(1).strip().encode('utf-8')).hexdigest()[:8]

@app.route('/api/auth/browser', methods=['POST'])
def auth_browser():
    data = request.get_json(silent=True) or {}
    raw_input = str(data.get("input") or '').strip()
    profile_name = str(data.get("name") or '').strip() or DEFAULT_PROFILE_NAME

    if not raw_input:
        return jsonify({"ok": False, "error": "Вставь Cookie или заголовки!"}), 400

    try:
        headers_dict = parse_and_validate_cookie_or_headers(raw_input)
        safe_id = hashlib.md5(profile_name.encode('utf-8')).hexdigest()[:10]
        session_file = os.path.join(BASE_DIR, f"session_{safe_id}.json")

        atomic_json_dump(session_file, headers_dict)

        test_yt = YTMusic(session_file)
        account_name = profile_name
        real_channel = ""
        try:
            info = test_yt.get_account_info()
            if info.get("accountName"):
                account_name = info["accountName"]
                real_channel = info["accountName"]
        except Exception:
            pass

        # Автопривязка brand-канала: если имя профиля совпадает с названием
        # одного из каналов аккаунта — привязываем его channel_id, чтобы реки
        # были именно этого канала, а не дефолтного.
        matched_channel = None
        try:
            for ch in list_account_channels(test_yt):
                if ch["name"].strip().lower() == profile_name.strip().lower() and ch.get("user_id"):
                    matched_channel = ch
                    break
        except Exception:
            pass

        # Отпечаток канала: предупредим, если вставили куки того же аккаунта,
        # что уже сохранён под другим профилем (иначе реки будут одинаковые)
        fp = account_fingerprint(headers_dict.get("cookie", ""))
        duplicate_of = None
        if fp:
            for n, inf in profiles_data.items():
                if n != profile_name and inf.get("fp") == fp:
                    duplicate_of = n
                    break

        profiles_data[profile_name] = {
            "type": "browser",
            "session_file": session_file,
            "user_name": account_name,
            "authuser": headers_dict.get("x-goog-authuser", "0"),
            "fp": fp
        }
        if matched_channel:
            profiles_data[profile_name]["channel_id"] = matched_channel["user_id"]
            profiles_data[profile_name]["channel_handle"] = matched_channel["handle"]
        save_profiles()
        set_active_yt_instance(profile_name)
        clear_picks_cache()

        resp = {
            "ok": True,
            "profile_name": profile_name,
            "account": active_account_info,
            "authuser": headers_dict.get("x-goog-authuser", "0"),
            "real_channel": real_channel
        }
        if matched_channel:
            resp["bound_channel"] = matched_channel["name"]
            resp["channel_handle"] = matched_channel["handle"]
        if duplicate_of:
            resp["warning"] = (f"Это тот же Google-аккаунт, что и в профиле "
                               f"\"{duplicate_of}\"! Рекомендации/история будут одинаковыми. "
                               f"Чтобы получить второй канал — в браузере переключи аккаунт "
                               f"(правый верхний угол YouTube) и только потом копируй Cookie.")
        return jsonify(resp)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ошибка авторизации: {str(e)}"}), 400

@app.route('/api/auth/import_browser_json', methods=['POST'])
def auth_import_browser_json():
    browser_path = resolve_path("browser.json")
    if not os.path.exists(browser_path):
        return jsonify({"ok": False, "error": "Файл browser.json не найден в папке проекта"}), 404

    try:
        with open(browser_path, "r", encoding="utf-8") as f:
            content = f.read()

        headers_dict = parse_and_validate_cookie_or_headers(content)
        profile_name = DEFAULT_PROFILE_NAME + " (browser)"
        safe_id = hashlib.md5(profile_name.encode('utf-8')).hexdigest()[:10]
        session_file = os.path.join(BASE_DIR, f"session_{safe_id}.json")

        atomic_json_dump(session_file, headers_dict)

        test_yt = YTMusic(session_file)
        account_name = profile_name
        try:
            info = test_yt.get_account_info()
            if info.get("accountName"):
                account_name = info["accountName"]
        except Exception:
            pass

        matched_channel = None
        try:
            for channel in list_account_channels(test_yt):
                if channel.get("name", "").strip().casefold() == profile_name.strip().casefold() and channel.get("user_id"):
                    matched_channel = channel
                    break
        except Exception:
            pass

        profiles_data[profile_name] = {
            "type": "browser",
            "session_file": session_file,
            "user_name": account_name,
            "authuser": headers_dict.get("x-goog-authuser", "0"),
            "fp": account_fingerprint(headers_dict.get("cookie", ""))
        }
        if matched_channel:
            profiles_data[profile_name]["channel_id"] = matched_channel["user_id"]
            profiles_data[profile_name]["channel_handle"] = matched_channel["handle"]
        save_profiles()
        set_active_yt_instance(profile_name)
        clear_picks_cache()

        response = {
            "ok": True,
            "profile_name": profile_name,
            "account": active_account_info
        }
        if matched_channel:
            response["bound_channel"] = matched_channel["name"]
            response["channel_handle"] = matched_channel["handle"]
        return jsonify(response)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ошибка импорта browser.json: {str(e)}"}), 400

# --- РЕКОМЕНДАЦИИ И МИКСЫ ОЛЕГА АНАНАСОВА ---

def format_track_pick(item):
    vid = item.get("videoId")
    if not vid:
        return None
    thumb = ""
    thumbs = item.get("thumbnails", [])
    if thumbs:
        thumb = thumbs[-1].get("url", "")
    if not thumb:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

    artist = ""
    if item.get("artists"):
        artist = item["artists"][0].get("name", "")
    elif item.get("uploader"):
        artist = item["uploader"]

    return {
        "videoId": vid,
        "title": item.get("title", "Трек"),
        "artist": artist,
        "thumbnail": thumb
    }

def build_home_picks(base_picks, seed_vids, exclude_recent=True):
    """База + розумно перемежована хвиля рекомендацій з кількох сидів (до 27 штук).
    Паралельне завантаження, round-robin між сидами, різноманіття артистів
    (не більше 2 треків підряд одного виконавця) і фільтр щойно прослуханого."""
    picks = []
    seen = set()

    recent_vids = set(get_recently_played_vids(20)) if exclude_recent else set()
    artist_run = {}  # artist -> скільки підряд у кінці списку

    def _can_append(p):
        """Не даємо одному артисту захопити весь екран: максимум 2 підряд."""
        artist = (p.get("artist") or "").strip().lower()
        if not artist:
            return True
        return artist_run.get(artist, 0) < 2

    def _append(p):
        artist = (p.get("artist") or "").strip().lower()
        # перед вставкою нового артиста скидаємо серію всіх інших
        for a in list(artist_run):
            if a != artist:
                artist_run.pop(a, None)
        artist_run[artist] = artist_run.get(artist, 0) + 1
        picks.append(p)

    for p in base_picks:
        key = p.get("videoId") or ("pl:" + p.get("playlistId", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        if p.get("videoId"):
            meta_cache[p["videoId"]] = p
        picks.append(dict(p))
        artist = (p.get("artist") or "").strip().lower()
        if artist:
            artist_run[artist] = artist_run.get(artist, 0) + 1

    if not seed_vids:
        return picks

    def _fetch_seed_wave(svid):
        try:
            with recommendation_slots:
                res = safe_get_watch_playlist(svid, radio=True)
            wave = []
            for r in res.get("tracks", []):
                p = format_track_pick(r)
                if p and not is_generic_chart_track(p["title"], p["artist"]) and not is_non_music_video(p["title"], p["artist"]):
                    if p.get("videoId") in recent_vids:
                        continue  # свіжість: не рекомендуємо щойно прослухане
                    wave.append(p)
            random.shuffle(wave)  # різні сиди дають різні старти при кожному оновленні
            return wave
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(seed_vids))) as executor:
        seed_waves = list(executor.map(_fetch_seed_wave, seed_vids[:8]))

    seed_waves = [w for w in seed_waves if w]

    # Round-robin між сидами + різноманіття артистів
    deferred = []  # треки, відкладені через ліміт артиста — повертаються пізніше
    while len(picks) < 27 and (seed_waves or deferred):
        added_any = False
        for wave in list(seed_waves):
            while wave:
                candidate = wave.pop(0)
                cid = candidate.get("videoId")
                if not cid or cid in seen:
                    continue
                if not _can_append(candidate):
                    deferred.append(candidate)
                    continue
                seen.add(cid)
                meta_cache[cid] = candidate
                _append(candidate)
                added_any = True
                break
            if len(picks) >= 27:
                break
        # повертаємо відкладені, коли інших варіантів немає
        if not added_any:
            while deferred and len(picks) < 27:
                candidate = deferred.pop(0)
                cid = candidate.get("videoId")
                if cid and cid not in seen:
                    seen.add(cid)
                    meta_cache[cid] = candidate
                    _append(candidate)
                    added_any = True
                    break
        if not added_any:
            break

    # Якщо ще є вільне місце (менше 27 треків) — добираємо з перевіреної бази
    if len(picks) < 27:
        pool = PK_TRACK_PICKS if is_pk_profile_name() else ANANASOV_REAL_PICKS
        for p in pool:
            vid = p.get("videoId")
            if vid and vid not in seen and vid not in recent_vids:
                seen.add(vid)
                meta_cache[vid] = p
                picks.append(dict(p))
                if len(picks) >= 27:
                    break

    return picks[:27]

def get_ananasov_home_picks():
    """Отримує персональний 'Швидкий вибір' Олега Ананасова зі збалансованою хвилею схожих артистів"""
    return build_home_picks(
        ANANASOV_REAL_PICKS,
        ["mrdEwk7PJwA", "kuOkbex2oJ4", "-2RdJ5Zadcg", "VGM7V9t3Q2k", "MOMbIxFR0Y8", "buGWqDJLw0I", "vKnFwBsYW2U", "eQq7foGyMgc"]
    )

def get_personal_home_picks():
    """Персональний вайб АКТИВНОГО профілю:
    Глибоко персоналізовані сиди з вподобаних треків YouTube Music (LM), власних плейлістів та історії.
    Перша сторінка містить улюблені треки користувача, а наступні сторінки —
    розумно підібрані схожі треки з паралельним перемежовуванням."""
    seeds = []
    seen_vid = set()

    # 1) Лайки активного профілю (YouTube Music LM)
    p_info = profiles_data.get(active_profile_name) or {}
    if p_info.get("type") == "oauth":
        try:
            oauth_liked = get_oauth_liked_songs(limit=50)
            for t in oauth_liked:
                v = t.get("videoId")
                if v and v not in seen_vid and not is_non_music_video(t.get("title", ""), t.get("artist", "")):
                    seen_vid.add(v)
                    seeds.append(v)
        except Exception:
            pass

    # 2) Власні плейлісти користувача (Remix tracks, down тощо)
    try:
        pl_seeds = get_oauth_user_playlist_seeds(limit_playlists=3, tracks_per_pl=6)
        for v in pl_seeds:
            if v and v not in seen_vid:
                seen_vid.add(v)
                seeds.append(v)
    except Exception:
        pass

    # 3) Реальна історія прослуховування (тільки музичні треки)
    try:
        hist_items = get_persisted_history(limit=30)
        for t in hist_items:
            v = t.get("videoId")
            if v and v not in seen_vid and not is_non_music_video(t.get("title", ""), t.get("artist", "")):
                seen_vid.add(v)
                seeds.append(v)
    except Exception:
        pass

    if is_active_authenticated and p_info.get("type") != "oauth":
        try:
            hist = yt.get_history()
            for t in (hist or [])[:20]:
                v = t.get("videoId")
                if v and v not in seen_vid and not is_non_music_video(t.get("title", ""), t.get("artist", "")):
                    seen_vid.add(v)
                    seeds.append(v)
        except Exception:
            pass

    if not seeds:
        return []

    # Формуємо базові треки (до 6 топових улюблених треків прямо на початок)
    base_tracks = []
    base_seen = set()
    for vid in seeds:
        info = meta_cache.get(vid)
        if info and not is_non_music_video(info.get("title", ""), info.get("artist", "")):
            base_tracks.append({
                "videoId": vid,
                "title": info.get("title", ""),
                "artist": info.get("artist", ""),
                "thumbnail": info.get("thumbnail", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
            })
            base_seen.add(vid)
            if len(base_tracks) >= 6:
                break

    # Перемішуємо решту сидів, щоб при кожному оновленні рекомендації звучали свіжо
    remaining_seeds = [s for s in seeds if s not in base_seen]
    random.shuffle(remaining_seeds)
    chosen_seeds = ([track["videoId"] for track in base_tracks] + remaining_seeds)[:10]

    return build_home_picks(base_tracks, chosen_seeds)

def get_mix_picks():
    """Мікс: бленд улюблених треків + хвиль від випадкових треків історії.
    Кожен виклик (сіль кешу 10 хв) дає нові комбінації — не нудно."""
    seed_vids = []
    base_tracks = []
    seen = set()

    def _add_base(track):
        vid = track.get("videoId")
        if not vid or vid in seen:
            return
        seen.add(vid)
        base_tracks.append({
            "videoId": vid,
            "title": track.get("title", "Трек"),
            "artist": track.get("artist", ""),
            "thumbnail": track.get("thumbnail", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
        })

    def _add_seed(vid):
        if vid and vid not in seen:
            seen.add(vid)
            seed_vids.append(vid)

    # 1) 3 случайных трека из недавней истории — от них строим волны
    hist = get_persisted_history(limit=25)
    music_hist = [t for t in hist if not is_non_music_video(t.get("title", ""), t.get("artist", ""))]
    random.shuffle(music_hist)
    wave_seeds = []
    for t in music_hist[:3]:
        wave_seeds.append(t.get("videoId"))

    # 2) 1-2 случайных лайка — база (сразу видно "твоё") + сид для волны
    liked = get_oauth_liked_songs(limit=40) if is_active_authenticated else []
    if not liked:
        try:
            with favorites_lock:
                liked = [dict(x) for x in favorites_store.get("profiles", {}).get(active_profile_name, [])]
        except Exception:
            liked = []
    music_liked = [t for t in liked if not is_non_music_video(t.get("title", ""), t.get("artist", ""))]
    random.shuffle(music_liked)
    for t in music_liked[:2]:
        _add_base(t)
        _add_seed(t.get("videoId"))

    # 3) база дополнена парой треков истории (свежесть + узнаваемость)
    for t in music_hist[:2]:
        _add_base(t)

    # 4) сид из чужого "открытия": случайный трек сохранённых пиков
    pool = PK_TRACK_PICKS if is_pk_profile_name() else ANANASOV_REAL_PICKS
    if pool:
        extra = random.choice(pool)
        _add_seed(extra.get("videoId"))

    seeds = wave_seeds + seed_vids
    random.shuffle(seeds)
    if not seeds and not base_tracks:
        return []
    return build_home_picks(base_tracks, seeds[:6])

def get_pk_home_picks():
    """Вайб профиля 'пк': первой карточкой альбом СТИНТа целиком, дальше синглы и похожая волна."""
    album_thumb = ""
    try:
        pl = safe_get_playlist(PK_ALBUM["playlistId"], limit=1)
        thumbs = pl.get("thumbnails", []) or []
        if thumbs:
            album_thumb = thumbs[-1].get("url", "")
    except Exception:
        pass
    if not album_thumb:
        album_thumb = "https://i.ytimg.com/vi/blL2YoqOJaw/hqdefault.jpg"
    base = [dict(PK_ALBUM, thumbnail=album_thumb)] + PK_TRACK_PICKS
    return build_home_picks(base, PK_SEED_VIDS)

@app.route('/api/quick_picks')
def get_quick_picks():
    mode = request.args.get("mode", "vibe")
    allowed_modes = {"vibe", "radio", "charts", "liked", "history", "mix"}
    if mode not in allowed_modes:
        return jsonify({"picks": [], "mode": mode, "error": f"Неизвестный режим: {mode}"}), 400
    force = request.args.get("force", "") == "1"
    curr_vid = current_track_info.get("video_id") or "buGWqDJLw0I"
    # mix живёт 10 минут: соль по времени => каждое обновление даёт свежий микс
    mix_salt = str(int(time.time() // 600)) if mode == "mix" else ""
    cache_key = f"{mode}_{active_profile_name}_{curr_vid if mode == 'radio' else ''}_{mix_salt}"

    now = time.time()
    with picks_lock:
        cached_entry = picks_cache.get(cache_key)
    if not force and cached_entry:
        cached_time, cached_picks, cached_fallback = cached_entry
        if now - cached_time < CACHE_TTL_SEC and cached_picks:
            with state_lock:
                dismissed_snapshot = set(dismissed_vids)
            visible_picks = [p for p in cached_picks if p.get("videoId") not in dismissed_snapshot]
            return jsonify({
                "picks": visible_picks,
                "mode": mode,
                "cached": True,
                "fallback": cached_fallback,
                "user_name": active_account_info.get("accountName", active_profile_name) if active_account_info else active_profile_name,
                "is_auth": is_active_authenticated
            })

    picks = []
    fallback_used = False
    fallback_msg = ""

    # 1. Швидкий вибір (Мій Вайб) — персональный по активному каналу.
    # Авторизованный канал -> волна из его лайков/истории. Иначе хардкод-фоллбэк.
    if mode == "vibe":
        if is_pk_profile_name():
            picks = get_pk_home_picks()
        else:
            picks = get_personal_home_picks()
        if not picks:
            picks = get_pk_home_picks() if is_pk_profile_name() else get_ananasov_home_picks()

    # 2. Хвиля треку
    elif mode == "radio":
        try:
            radio = safe_get_watch_playlist(curr_vid, radio=True)
            for r in radio.get("tracks", []):
                p = format_track_pick(r)
                if p and not is_generic_chart_track(p["title"], p["artist"]):
                    meta_cache[p["videoId"]] = p
                    picks.append(p)
                if len(picks) >= 27:
                    break
        except Exception:
            pass

        if not picks:
            try:
                radio = safe_get_watch_playlist("buGWqDJLw0I", radio=True)
                for r in radio.get("tracks", []):
                    p = format_track_pick(r)
                    if p and not is_generic_chart_track(p["title"], p["artist"]):
                        meta_cache[p["videoId"]] = p
                        picks.append(p)
                if picks:
                    fallback_used = True
                    fallback_msg = "Волну текущего трека получить не удалось — показаны похожие треки"
            except Exception:
                pass

    # 3. Лайки
    elif mode == "liked":
        p_info = profiles_data.get(active_profile_name) or {}
        if is_active_authenticated and p_info.get("type") == "oauth":
            picks = get_oauth_liked_songs(limit=27)
            if not picks:
                return jsonify({
                    "picks": [],
                    "mode": mode,
                    "is_auth": is_active_authenticated,
                    "message": "Не удалось загрузить лайки OAuth-профиля. Попробуй переподключить профиль."
                })
        elif not picks:
            try:
                liked = yt.get_liked_songs(limit=27)
                for r in liked.get("tracks", []):
                    p = format_track_pick(r)
                    if p:
                        meta_cache[p["videoId"]] = p
                        picks.append(p)
            except Exception:
                pass
        if not picks:
            return jsonify({
                "picks": [],
                "mode": mode,
                "is_auth": is_active_authenticated,
                "message": "Чтобы увидеть лайки, войди в свой Google / YouTube Music аккаунт через OAuth или Cookie"
            })

    # 4. Історія
    elif mode == "history":
        if is_active_authenticated:
            try:
                hist = yt.get_history()
                if hist:
                    for r in hist[:27]:
                        p = format_track_pick(r)
                        if p:
                            meta_cache[p["videoId"]] = p
                            picks.append(p)
            except Exception as e:
                print("[WARN] InnerTube history failed, falling back to local:", e)

        # Якщо через InnerTube не отримано (наприклад OAuth або InnerTube 400), беремо збережену історію
        if not picks:
            picks = get_persisted_history(limit=27)

        if not picks:
            return jsonify({
                "picks": [],
                "mode": mode,
                "is_auth": is_active_authenticated,
                "message": "История пуста. Включи любой трек или подключи профиль."
            })

    # 5. Вайб-мікс новинок
    elif mode == "charts":
        chart_seeds = ["KLWrNPw-Rek", "lulVPmcNCzI", "eQq7foGyMgc", "tcwuKGwdciM", "qyrDASgwAI8"]
        picks = build_home_picks([], chart_seeds)

    # 6. Мікс — лучший бленд: лайки + волны от случайных треков истории
    elif mode == "mix":
        picks = get_mix_picks()
        if not picks:
            picks = get_persisted_history(limit=27)
            if picks:
                random.shuffle(picks)
                fallback_used = True
                fallback_msg = "Микс временно недоступен — показана перемешанная история"

    if not picks and mode in ("vibe", "radio", "charts"):
        picks = get_pk_home_picks() if is_pk_profile_name() else get_ananasov_home_picks()
        if mode != "vibe":
            fallback_used = True
            fallback_msg = "Рекомендации временно недоступны — показана подборка по умолчанию"

    if mode in ("vibe", "radio", "charts", "mix"):
        with state_lock:
            dismissed_snapshot = set(dismissed_vids)
        picks = [pick for pick in picks if pick.get("videoId") not in dismissed_snapshot]

    with picks_lock:
        picks_cache[cache_key] = (now, picks[:27], fallback_used)
    return jsonify({
        "picks": picks[:27],
        "mode": mode,
        "cached": False,
        "fallback": fallback_used,
        "message": fallback_msg or None,
        "user_name": active_account_info.get("accountName", active_profile_name) if active_account_info else active_profile_name,
        "is_auth": is_active_authenticated
    })

@app.route('/api/search')
def search_tracks():
    query = str(request.args.get("q", "")).strip()
    if not query:
        return jsonify({"ok": True, "query": "", "results": []})
    results = []
    seen = set()
    for searcher in (yt, clean_yt):
        try:
            raw_results = searcher.search(query, filter="songs") or []
        except Exception:
            raw_results = []
        for raw in raw_results:
            pick = format_track_pick(raw)
            if not pick or pick.get("videoId") in seen:
                continue
            seen.add(pick["videoId"])
            meta_cache[pick["videoId"]] = pick
            results.append(pick)
            if len(results) >= 24:
                break
        if results:
            break
    return jsonify({"ok": True, "query": query, "results": results})

@app.route('/api/favorites')
def get_favorites_endpoint():
    return jsonify({"ok": True, "favorites": get_favorites(limit=100), "active_profile": active_profile_name})

@app.route('/api/favorites/toggle', methods=['POST'])
def toggle_favorite_endpoint():
    data = request.get_json(silent=True) or {}
    video_id = str(data.get("video_id", "")).strip()
    if not video_id:
        video_id = str(current_track_info.get("video_id", "")).strip()
    if not video_id:
        return jsonify({"ok": False, "error": "Нет video_id"}), 400
    added, favorites = toggle_favorite(
        video_id,
        data.get("title", current_track_info.get("title", "")),
        data.get("artist", current_track_info.get("artist", "")),
        data.get("thumbnail", current_track_info.get("thumbnail", "")),
        data.get("album", current_track_info.get("album", ""))
    )
    return jsonify({"ok": True, "favorited": added, "video_id": video_id, "favorites": favorites})

@app.route('/api/like', methods=['POST'])
def account_like():
    data = request.get_json(silent=True) or {}
    video_id = str(data.get("video_id", "")).strip()
    if not video_id:
        video_id = str(current_track_info.get("video_id", "")).strip()
    if not video_id:
        return jsonify({"ok": False, "error": "Нет video_id"}), 400
    if "like" in data and data.get("like") is not None:
        target = bool(data.get("like"))
    else:
        target = not is_favorite(video_id)
    account_ok = False
    account_message = ""
    if is_active_authenticated:
        try:
            yt.rate_song(video_id, LikeStatus.LIKE if target else LikeStatus.INDIFFERENT)
            account_ok = True
        except Exception as ex:
            account_message = str(ex)[:300]
            if not target and "invalid argument" in account_message.lower():
                account_ok = True
                account_message = ""
    else:
        account_message = "Профиль не авторизован"
    liked, _favorites = set_favorite(
        video_id,
        target,
        data.get("title", current_track_info.get("title", "")),
        data.get("artist", current_track_info.get("artist", "")),
        data.get("thumbnail", current_track_info.get("thumbnail", "")),
        data.get("album", current_track_info.get("album", ""))
    )
    result = {
        "ok": True,
        "liked": liked,
        "video_id": video_id,
        "account": account_ok,
        "profile": active_profile_name
    }
    if account_message and not account_ok:
        result["message"] = account_message
    return jsonify(result)

@app.route('/api/history')
def get_history_endpoint():
    try:
        limit = max(1, min(100, int(request.args.get("limit", 60))))
    except (TypeError, ValueError):
        limit = 60
    return jsonify({"ok": True, "history": get_persisted_history(limit=limit), "active_profile": active_profile_name})

@app.route('/api/queue/reorder', methods=['POST'])
def reorder_queue():
    data = request.get_json(silent=True) or {}
    order = data.get("video_ids")
    if order is None and "from_index" in data and "to_index" in data:
        try:
            from_index = max(0, int(data["from_index"]))
            to_index = max(0, int(data["to_index"]))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Индексы должны быть числами"}), 400
        with state_lock:
            current_order = [item.get("videoId") for item in track_queue_list]
        if from_index >= len(current_order) or to_index >= len(current_order):
            return jsonify({"ok": False, "error": "Индекс вне очереди"}), 400
        current_order.insert(to_index, current_order.pop(from_index))
        order = current_order
    if not isinstance(order, list):
        return jsonify({"ok": False, "error": "video_ids должен быть списком"}), 400
    normalized = [str(value).strip() for value in order if str(value).strip()]
    with state_lock:
        # Позиционное сопоставление: videoId может повторяться в очереди,
        # поэтому каждому id из order отдаем следующий ещё не занятый элемент.
        # Список order должен быть перестановкой всей очереди (клиент строит
        # его из полного снапшота); иначе — 409, ничего не мутируем.
        available = list(track_queue_list)
        selected = []
        for video_id in normalized:
            match_idx = next((i for i, item in enumerate(available)
                              if item.get("videoId") and str(item.get("videoId")) == video_id), None)
            if match_idx is None:
                return jsonify({"ok": False, "error": "Очередь изменилась, обновите список"}), 409
            selected.append(available.pop(match_idx))
        if available:
            # порядок покрывает не всю очередь (например, клиент видел только
            # видимую часть) — прикрепляем остаток в конец, ничего не теряем
            selected.extend(available)
        track_queue_list[:] = selected
    publish_queue_snapshot()
    return jsonify({"ok": True, "queue": [dict(item) for item in upcoming_queue]})

# --- ИНТЕРНЕТ-РАДИОСТАНЦИИ ---

@app.route('/api/stations')
@app.route('/api/stations')
def stations_list():
    stations = get_stations()
    with state_lock:
        active = dict(station_state) if station_state else None
    health = check_stations_health() if request.args.get("health", "") == "1" else {}
    return jsonify({
        "ok": True,
        "stations": stations,
        "active": active.get("id") if active else None,
        "health": health
    })

@app.route('/api/station/play', methods=['POST'])
def station_play():
    global playback_generation, radio_inflight
    data = request.get_json(silent=True) or {}
    station_id = str(data.get("id") or '')
    station = next((s for s in get_stations() if s.get("id") == station_id), None)
    if not station:
        return jsonify({"ok": False, "error": "Станция не найдена"}), 404
    with state_lock:
        playback_generation += 1
        radio_inflight = 0
    kill_current_proc()
    skip_event.set()
    cmd_queue.put({"station": dict(station)})
    return jsonify({"ok": True, "station": station})

@app.route('/api/station/stop', methods=['POST'])
def station_stop():
    global playback_generation, radio_inflight
    with state_lock:
        playback_generation += 1
        radio_inflight = 0
    kill_current_proc()
    skip_event.set()
    cmd_queue.put({"resume_queue": True})
    return jsonify({"ok": True})

# --- СОБСТВЕННЫЕ ПЛЕЙЛИСТЫ ---

def _make_playlist_id():
    return "pl_" + hashlib.md5((active_profile_name + str(time.time()) + str(random.random())).encode('utf-8')).hexdigest()[:10]

@app.route('/api/playlists')
def playlists_list():
    return jsonify({"ok": True, "playlists": get_playlists(), "active_profile": active_profile_name})

@app.route('/api/playlists/create', methods=['POST'])
def playlists_create():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or '').strip()[:60]
    if not name:
        return jsonify({"ok": False, "error": "Укажи название плейлиста"}), 400
    with playlists_lock:
        items = playlists_store.setdefault("profiles", {}).setdefault(active_profile_name, [])
        pl = {"id": _make_playlist_id(), "name": name, "tracks": [], "created": int(time.time())}
        items.insert(0, pl)
        save_playlists_store()
    return jsonify({"ok": True, "playlist": pl})

@app.route('/api/playlists/add', methods=['POST'])
def playlists_add():
    data = request.get_json(silent=True) or {}
    playlist_id = str(data.get("playlist_id") or '')
    pl = find_playlist(playlist_id)
    if not pl:
        return jsonify({"ok": False, "error": "Плейлист не найден"}), 404
    vid = str(data.get("video_id") or '').strip()
    meta = data.get("meta") or {}
    if not vid:
        vid = current_track_info.get("video_id") or ''
        if vid.startswith("station:"):
            vid = ""
        if not vid:
            return jsonify({"ok": False, "error": "Нет текущего трека и video_id"}), 400
        meta = {
            "title": current_track_info.get("title"),
            "artist": current_track_info.get("artist"),
            "thumbnail": current_track_info.get("thumbnail"),
            "duration": current_track_info.get("duration")
        }
    with playlists_lock:
        tracks = [t for t in pl.get("tracks", []) if t.get("videoId") != vid]
        tracks.insert(0, {
            "videoId": vid,
            "title": str(meta.get("title") or meta_cache.get(vid, {}).get("title") or "Трек"),
            "artist": str(meta.get("artist") or meta_cache.get(vid, {}).get("artist") or ""),
            "thumbnail": meta.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "duration": meta.get("duration") or meta_cache.get(vid, {}).get("duration")
        })
        pl["tracks"] = tracks[:200]
        save_playlists_store()
    return jsonify({"ok": True, "playlist": pl})

@app.route('/api/playlists/remove_track', methods=['POST'])
def playlists_remove_track():
    data = request.get_json(silent=True) or {}
    pl = find_playlist(str(data.get("playlist_id") or ''))
    if not pl:
        return jsonify({"ok": False, "error": "Плейлист не найден"}), 404
    vid = str(data.get("video_id") or '')
    pl["tracks"] = [t for t in pl.get("tracks", []) if t.get("videoId") != vid]
    save_playlists_store()
    return jsonify({"ok": True, "playlist": pl})

@app.route('/api/playlists/delete', methods=['POST'])
def playlists_delete():
    data = request.get_json(silent=True) or {}
    playlist_id = str(data.get("playlist_id") or '')
    with playlists_lock:
        items = playlists_store.get("profiles", {}).get(active_profile_name, [])
        playlists_store["profiles"][active_profile_name] = [pl for pl in items if pl.get("id") != playlist_id]
        save_playlists_store()
    return jsonify({"ok": True})

@app.route('/api/playlists/play', methods=['POST'])
def playlists_play():
    global playback_generation, radio_inflight
    data = request.get_json(silent=True) or {}
    pl = find_playlist(str(data.get("playlist_id") or ''))
    if not pl:
        return jsonify({"ok": False, "error": "Плейлист не найден"}), 404
    tracks = [dict(t) for t in pl.get("tracks", []) if t.get("videoId")]
    if not tracks:
        return jsonify({"ok": False, "error": "Плейлист пуст"}), 400
    with state_lock:
        playback_generation += 1
        radio_inflight = 0
        current_track_info["is_paused"] = False
    kill_current_proc()
    skip_event.set()
    cmd_queue.put({"tracks": tracks})
    return jsonify({"ok": True, "playlist": pl, "count": len(tracks)})

# --- ТЕКСТ ПЕСНИ (LRCLIB) ---

lyrics_cache = BoundedDict(200)
lyrics_lock = threading.RLock()

@app.route('/api/lyrics')
def lyrics_get():
    vid = str(request.args.get("video_id") or '').strip()
    if not vid or vid.startswith("station:"):
        return jsonify({"ok": False, "error": "Нет трека"}), 400

    with lyrics_lock:
        cached = lyrics_cache.get(vid)
    if cached:
        return jsonify({"ok": True, **cached, "cached": True})

    meta = meta_cache.get(vid) or {}
    title = str(meta.get("title") or current_track_info.get("title") or '').strip()
    artist = str(meta.get("artist") or current_track_info.get("artist") or '').strip()
    if not title:
        return jsonify({"ok": False, "error": "Неизвестно, что играет"}), 400

    def _lrc_request(params):
        try:
            r = requests.get("https://lrclib.net/api/get", params=params,
                             headers={"User-Agent": "ETS2RadioDJ/1.0 (personal player)"},
                             timeout=6)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    result = None
    dur = meta.get("duration") or current_track_info.get("duration") or 0
    try:
        dur = int(dur)
    except (TypeError, ValueError):
        dur = 0
    # 1) точное совпадение с длительностью, 2) без длительности, 3) поиск
    result = None
    if dur:
        result = _lrc_request({"artist_name": artist, "track_name": title, "album_name": meta.get("album") or "", "duration": dur})
    if not result:
        result = _lrc_request({"artist_name": artist, "track_name": title})
    if not result:
        try:
            r = requests.get("https://lrclib.net/api/search",
                             params={"track_name": title, "artist_name": artist},
                             headers={"User-Agent": "ETS2RadioDJ/1.0 (personal player)"},
                             timeout=6)
            if r.status_code == 200:
                items = r.json() or []
                for it in items:
                    if it.get("syncedLyrics") or it.get("plainLyrics"):
                        result = it
                        break
        except Exception:
            pass

    cleaned_title = re.sub(r'\s*[([](feat|ft|remix|remaster)[^)\]]*\)?]?$', '', title, flags=re.I)
    if not result and cleaned_title != title:
        result = _lrc_request({"artist_name": artist, "track_name": cleaned_title})

    if not result:
        resp = {"synced": False, "plain": "", "artist": artist, "title": title}
        with lyrics_lock:
            lyrics_cache[vid] = resp
        return jsonify({"ok": False, "error": "Текст не найден", **resp})

    resp = {
        "synced": bool(result.get("syncedLyrics")),
        "plain": result.get("plainLyrics") or "",
        "lrc": result.get("syncedLyrics") or "",
        "artist": result.get("artistName") or artist,
        "title": result.get("trackName") or title
    }
    with lyrics_lock:
        lyrics_cache[vid] = resp
    return jsonify({"ok": True, **resp})

# --- ETS2 ТЕЛЕМЕТРИЯ (ets2-telemetry-server на 25555) ---

telemetry_cache = {"t": 0.0, "data": {"connected": False}}
telemetry_lock = threading.Lock()

@app.route('/api/telemetry')
def telemetry_get():
    now = time.time()
    with telemetry_lock:
        if now - telemetry_cache["t"] < 1.0:
            return jsonify(telemetry_cache["data"])
    data = {"connected": False}
    try:
        r = requests.get("http://127.0.0.1:25555/api/ets2/telemetry.json", timeout=1.5)
        if r.status_code == 200:
            raw = r.json()
            tele = raw.get("telemetry", {}) or {}
            truck = raw.get("truck", {}) or {}
            game = raw.get("game", {}) or {}
            data = {
                "connected": True,
                "speed_kmh": round(abs(float(truck.get("speedKmh", 0) or 0)), 1),
                "gear": truck.get("gear", 0),
                "paused": bool(game.get("paused", False)),
                "game": game.get("gameName") or "",
                "truck": raw.get("truck", {}).get("truckName") or "",
                "odometer_km": round(float(truck.get("odometer", 0) or 0), 1),
                "fuel_pct": round(float(truck.get("fuelPercentage", 0) or 0), 1),
                "location": (raw.get("navigation", {}) or {}).get("estimatedTimeMinutes", None)
            }
    except Exception:
        pass
    with telemetry_lock:
        telemetry_cache["t"] = now
        telemetry_cache["data"] = data
    return jsonify(data)

# --- ПРОПИСАНИЕ РАДИО В ETS2/ATS (live_streams.sii) ---

STREAM_ENTRY = "http://127.0.0.1:5000/radio.mp3|YT Automix DJ|Custom|RU|128|1"

def find_game_stream_files():
    """Ищет live_streams.sii у ETS2/ATS: обычные Documents и OneDrive-варианты."""
    results = []
    doc_roots = []
    profile = os.environ.get("USERPROFILE", "")
    candidates = [
        os.path.join(profile, "Documents"),
        os.path.join(os.environ.get("OneDrive", ""), "Documents") if os.environ.get("OneDrive") else None,
    ]
    doc_roots = [c for c in candidates if c]
    game_dirs = ["Euro Truck Simulator 2", "American Truck Simulator"]
    for root in doc_roots:
        for gd in game_dirs:
            p = os.path.join(root, gd, "live_streams.sii")
            game = "ETS2" if "Euro" in gd else "ATS"
            results.append({"game": game, "path": p, "exists": os.path.exists(p)})
    return results

def _inject_stream(path):
    """Вставляет нашу станцию в live_streams.sii (с бэкапом). Идемпотентно."""
    entry = STREAM_ENTRY
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    if entry in content:
        return "already"
    if not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")
    lines = content.splitlines()
    new_lines = []
    inserted = False
    count_bumped = False
    for line in lines:
        m = re.search(r'live_streams:\s*\(\s*(\d+)\s*,', line)
        if m and not inserted:
            # счётчик потоков в шапке SII — увеличиваем на 1
            line = re.sub(r'(live_streams:\s*\(\s*)(\d+)', lambda mm: mm.group(1) + str(int(mm.group(2)) + 1), line)
            count_bumped = True
            new_lines.append(line)
            new_lines.append('\t.stream_data[]: "%s",' % entry)
            inserted = True
            continue
        new_lines.append(line)
    if not inserted:
        # формат без счётчика/шапки — вставляем перед закрывающей скобкой
        for i in range(len(new_lines) - 1, -1, -1):
            if '.stream_data[]:' in new_lines[i]:
                new_lines.insert(i + 1, '\t.stream_data[]: "%s",' % entry)
                inserted = True
                break
    if not inserted:
        return "no_anchor"
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(new_lines) + "\n")
    return "installed" if not count_bumped else "installed+count"

@app.route('/api/ets2/check')
def ets2_check():
    files = find_game_stream_files()
    found = []
    for f in files:
        if f["exists"]:
            try:
                with open(f["path"], "r", encoding="utf-8", errors="replace") as fh:
                    f["has_stream"] = STREAM_ENTRY in fh.read()
            except Exception:
                f["has_stream"] = False
            found.append(f)
    return jsonify({
        "ok": True,
        "all": files,
        "found_any": any(f["exists"] for f in files),
        "installed_any": any(f.get("has_stream") for f in found)
    })

@app.route('/api/ets2/install_stream', methods=['POST'])
def ets2_install_stream():
    files = find_game_stream_files()
    results = []
    for f in files:
        if not f["exists"]:
            results.append({"game": f["game"], "path": f["path"], "result": "not_found"})
            continue
        try:
            results.append({"game": f["game"], "path": f["path"], "result": _inject_stream(f["path"])})
        except Exception as e:
            results.append({"game": f["game"], "path": f["path"], "result": "error", "error": str(e)})
    if not results:
        return jsonify({"ok": False, "error": "Папки ETS2/ATS в Documents не найдены", "results": results}), 404
    installed = [r for r in results if r["result"].startswith("installed")]
    return jsonify({"ok": True, "results": results, "installed": len(installed)})

@app.route('/api/change', methods=['POST'])
def change_track():
    global current_proc, playback_generation, radio_inflight
    data = request.get_json(silent=True) or {}
    query = str(data.get('query') or '').strip()
    try:
        offset = int(data.get('offset', 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "offset должен быть числом (секунды)"}), 400
    offset = max(0, offset)
    title = str(data.get('title') or '').strip()
    artist = str(data.get('artist') or '').strip()
    thumbnail = str(data.get('thumbnail') or '').strip()

    if query:
        current_track_info["is_paused"] = False
        with state_lock:
            playback_generation += 1
            radio_inflight = 0
            track_queue_list.clear()
            profile_queues.pop(active_profile_name, None)
            upcoming_queue.clear()
            played_vids.clear()
        with state_lock:
            if current_playing_item and current_playing_item.get("videoId"):
                if not played_tracks_history or played_tracks_history[-1].get("videoId") != current_playing_item.get("videoId"):
                    played_tracks_history.append(dict(current_playing_item))
        if title:
            current_track_info["title"] = title
        if artist:
            current_track_info["artist"] = artist
        if thumbnail:
            current_track_info["thumbnail"] = thumbnail

        skip_event.set()
        flush_listeners()
        kill_current_proc()

        meta_hint = {}
        if title:
            meta_hint["title"] = title
        if artist:
            meta_hint["artist"] = artist
        if thumbnail:
            meta_hint["thumbnail"] = thumbnail

        cmd_queue.put({"query": query, "offset": offset, "meta": meta_hint if meta_hint else None})
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Пустой запрос"}), 400

@app.route('/api/queue/remove', methods=['POST'])
def remove_queue_track():
    data = request.get_json(silent=True) or {}
    video_id = str(data.get("video_id", "")).strip()
    remove_current = bool(data.get("current"))
    if remove_current:
        with state_lock:
            video_id = (current_playing_item or {}).get("videoId") or current_track_info.get("video_id", "")
            if not video_id:
                return jsonify({"ok": False, "error": "Нет текущего трека"}), 404
            dismissed_vids.add(video_id)
        skip_event.set()
        flush_listeners()
        kill_current_proc()
        removed = 1
    else:
        if not video_id:
            return jsonify({"ok": False, "error": "Не указан video_id"}), 400
        with state_lock:
            dismissed_vids.add(video_id)
            before = len(track_queue_list)
            track_queue_list[:] = [item for item in track_queue_list if item.get("videoId") != video_id]
            removed = before - len(track_queue_list)
            publish_queue_snapshot()
        if not removed:
            return jsonify({"ok": False, "error": "Трек не найден в очереди"}), 404

    with state_lock:
        generation = playback_generation
        seed_item = dict(current_playing_item) if current_playing_item else {}
        if not seed_item and track_queue_list:
            seed_item = dict(track_queue_list[0])
        seed_vid = seed_item.get("videoId") or current_track_info.get("video_id") or "6Pr1yeLw8XI"
        seed_artist = seed_item.get("artist") or current_track_info.get("artist", "")
    threading.Thread(
        target=fill_queue_after_dismissal,
        args=(generation, seed_vid, seed_artist),
        daemon=True
    ).start()
    return jsonify({
        "ok": True,
        "removed": removed,
        "video_id": video_id,
        "replacement_pending": True
    })

@app.route('/api/control', methods=['POST'])
def player_control():
    global target_seek_time, current_proc, current_playing_item, played_vids
    data = request.get_json(silent=True) or {}
    action = data.get('action')

    if action == "skip_to":
        video_id = str(data.get("video_id", "")).strip()
        if not video_id:
            return jsonify({"ok": False, "error": "Не указан video_id"}), 400

        selected = None
        with state_lock:
            for index, item in enumerate(track_queue_list):
                if str(item.get("videoId", "")).strip() == video_id:
                    selected = track_queue_list.pop(index)
                    selected["force_play"] = True
                    track_queue_list.insert(0, selected)
                    break

        if selected is None:
            return jsonify({"ok": False, "error": "Трек не найден в очереди"}), 404

        publish_queue_snapshot()
        skip_event.set()
        flush_listeners()
        kill_current_proc()
        return jsonify({
            "ok": True,
            "action": "skip_to",
            "track": {
                "videoId": selected.get("videoId"),
                "title": selected.get("title", "Трек"),
                "artist": selected.get("artist", "")
            }
        })

    elif action == "skip":
        skip_event.set()
        flush_listeners()
        kill_current_proc()
        return jsonify({"ok": True})

    elif action in ("previous", "prev"):
        with state_lock:
            if played_tracks_history:
                prev_item = played_tracks_history.pop()
                prev_item["force_play"] = True
                # Restore current track to queue so Next returns to it
                if current_playing_item and current_playing_item.get("videoId"):
                    curr_to_restore = dict(current_playing_item)
                    curr_to_restore["force_play"] = True
                    track_queue_list.insert(0, curr_to_restore)
                # Insert previous track at top of queue to play next immediately
                track_queue_list.insert(0, dict(prev_item))
                if prev_item.get("videoId"):
                    played_vids.discard(prev_item.get("videoId"))
                # Clear current_playing_item so it doesn't duplicate into history on skip
                current_playing_item = None
                skip_event.set()
                flush_listeners()
                kill_current_proc()
                return jsonify({"ok": True, "action": "previous", "track": prev_item})
            else:
                target_seek_time = 0
                seek_event.set()
                flush_listeners()
                kill_current_proc()
                return jsonify({"ok": True, "action": "seek_zero"})

    elif action == "pause":
        current_track_info["is_paused"] = not current_track_info["is_paused"]
        if not current_track_info["is_paused"]:
            flush_listeners()
        return jsonify({"ok": True, "is_paused": current_track_info["is_paused"]})

    elif action == "seek":
        try:
            target = int(data.get('time', 0))
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "time должен быть числом"}), 400
        # Клампим по duration только если она точно известна: фиктивные 180 сек
        # раньше обрезали seek на длинных треках из кеша
        if current_track_info.get("duration_known"):
            target_seek_time = max(0, min(target, current_track_info["duration"]))
        else:
            target_seek_time = max(0, target)
        seek_event.set()
        flush_listeners()
        kill_current_proc()
        return jsonify({"ok": True, "seek_to": target_seek_time})

    return jsonify({"ok": False}), 400

@app.route('/api/profiles/switch', methods=['POST'])
def switch_profile():
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    if name in profiles_data or name == "Гость" or name == DEFAULT_PROFILE_NAME:
        skip_event.set()
        flush_listeners()
        kill_current_proc()
        set_active_yt_instance(name)
        clear_picks_cache()
        status_cache.pop("stats", None)
        # Переключение профиля снимает застрявшую паузу, чтобы эфир не молчал
        if current_track_info.get("is_paused"):
            current_track_info["is_paused"] = False
            flush_listeners()
        return jsonify({
            "ok": True,
            "active": name,
            "is_auth": is_active_authenticated,
            "account": active_account_info
        })
    return jsonify({"ok": False, "error": "Профиль не найден"}), 404

@app.route('/api/profiles/channels', methods=['POST'])
def list_profile_channels():
    """Список brand-каналов, доступных активному аккаунту (для выбора в UI)."""
    if not is_active_authenticated:
        return jsonify({"ok": False, "error": "Профиль не авторизован"}), 400
    chans = []
    current_id = profiles_data.get(active_profile_name, {}).get("channel_id")
    current_info = active_account_info if isinstance(active_account_info, dict) else {}
    if current_info.get("accountName"):
        chans.append({
            "name": current_info.get("accountName", active_profile_name),
            "handle": current_info.get("channelHandle", ""),
            "user_id": current_id,
            "is_brand": bool(current_id),
            "selected": True
        })
    try:
        discovered = list_account_channels(yt)
    except Exception:
        discovered = []
    known_channels = {(c.get("name", ""), c.get("user_id", "")) for c in chans}
    for channel in discovered:
        key = (channel.get("name", ""), channel.get("user_id", ""))
        if key not in known_channels:
            chans.append(channel)
            known_channels.add(key)
    return jsonify({
        "ok": True,
        "channels": chans,
        "active_profile": active_profile_name,
        "current_channel_id": profiles_data.get(active_profile_name, {}).get("channel_id")
    })

@app.route('/api/profiles/set_channel', methods=['POST'])
def set_profile_channel():
    """Привязать профиль к конкретному brand-каналу -> свои реки/история/лайки."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", active_profile_name)
    channel_id = data.get("channel_id")  # None/"" -> дефолтный канал
    if name not in profiles_data:
        return jsonify({"ok": False, "error": "Профиль не найден"}), 404
    if channel_id:
        channel_id = str(channel_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,256}", channel_id):
            return jsonify({"ok": False, "error": "Неверный channel_id"}), 400
        profiles_data[name]["channel_id"] = channel_id
    else:
        profiles_data[name].pop("channel_id", None)
        profiles_data[name].pop("channel_handle", None)
    save_profiles()
    # Переподключаем активный инстанс, если меняли его
    if name == active_profile_name:
        set_active_yt_instance(name)
    clear_picks_cache()
    info = profiles_data[name]
    return jsonify({
        "ok": True,
        "profile": name,
        "channel_id": info.get("channel_id"),
        "channel_handle": info.get("channel_handle"),
        "is_auth": is_active_authenticated
    })

@app.route('/api/profiles/delete', methods=['POST'])
def delete_profile():
    global active_profile_name
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    if name in profiles_data:
        p_info = profiles_data[name]
        for fkey in ["token_file", "session_file"]:
            fpath = p_info.get(fkey)
            fpath = resolve_path(fpath)
            if not fpath or not os.path.exists(fpath):
                continue
            # Удаляем файл только если он не используется другими профилями
            shared = any(
                n != name and resolve_path(info.get(fkey)) == fpath
                for n, info in profiles_data.items()
            )
            if shared:
                continue
            try:
                os.remove(fpath)
            except Exception:
                pass
        del profiles_data[name]
        with history_lock:
            history_store.get("profiles", {}).pop(name, None)
            atomic_json_dump(HISTORY_FILE, history_store)
        with favorites_lock:
            favorites_store.get("profiles", {}).pop(name, None)
            atomic_json_dump(FAVORITES_FILE, favorites_store)
        profile_queues.pop(name, None)
        save_profiles()
        clear_picks_cache()
        if active_profile_name == name:
            new_active = list(profiles_data.keys())[0] if profiles_data else DEFAULT_PROFILE_NAME
            set_active_yt_instance(new_active)
        return jsonify({"ok": True, "active": active_profile_name})
    return jsonify({"ok": False, "error": "Не найдено"}), 404

@app.route('/api/config', methods=['POST'])
def update_config():
    data = request.get_json(silent=True) or {}
    if "cache_limit" in data:
        try:
            lim = int(data["cache_limit"])
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "cache_limit должен быть числом"}), 400
        app_config["cache_limit"] = max(1, min(100, lim))
    if "bitrate" in data:
        b = str(data["bitrate"])
        if b not in ALLOWED_BITRATES:
            return jsonify({"ok": False, "error": f"bitrate должен быть одним из {sorted(ALLOWED_BITRATES)}"}), 400
        app_config["bitrate"] = b
    boost_changed = False
    if "volume_boost" in data:
        try:
            v = float(data["volume_boost"])
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "volume_boost должен быть числом"}), 400
        v = max(0.5, min(2.0, v))
        boost_changed = abs(float(app_config.get("volume_boost", "1.0")) - v) > 0.001
        app_config["volume_boost"] = str(round(v, 2))
    cache_was = bool(app_config.get("cache_enabled", False))
    if "cache_enabled" in data:
        app_config["cache_enabled"] = bool(data["cache_enabled"])
    if "auto_pause_game" in data:
        app_config["auto_pause_game"] = bool(data["auto_pause_game"])
    if "discord_rpc" in data:
        app_config["discord_rpc"] = bool(data["discord_rpc"])
    save_config()
    clean_old_cache(protect_vid=current_track_info.get("video_id"))
    status_cache.pop("stats", None)  # UI сразу увидит эффект очистки

    # Мгновенное применение громкости/бирейта: перезапускаем ffmpeg на текущей
    # позиции через штатный seek-механизм (трек не прерывается).
    global target_seek_time
    if (boost_changed or cache_was != bool(app_config.get("cache_enabled", False))) and current_proc and not current_track_info.get("is_station"):
        try:
            cur = int(current_track_info.get("current_time") or 0)
        except (TypeError, ValueError):
            cur = 0
        with state_lock:
            target_seek_time = max(0, cur)
        seek_event.set()
    return jsonify({"ok": True, "config": sanitize_config(app_config)})

@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    try:
        curr_vid = current_track_info.get("video_id")
        files = []
        for ext in CACHE_EXTS + JUNK_EXTS:
            files.extend(glob.glob(os.path.join(CACHE_DIR, f"*.{ext}")))
        deleted = 0
        for f in files:
            if curr_vid and os.path.basename(f).startswith(f"{curr_vid}."):
                continue
            with downloading_lock:
                busy = any(os.path.basename(f).startswith(f"{vid}.") for vid in downloading_vids)
            if busy:
                continue
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
        status_cache.pop("stats", None)
        return jsonify({"ok": True, "deleted": deleted})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/cache/clean', methods=['POST'])
def clean_cache_endpoint():
    data = request.get_json(silent=True) or {}
    try:
        max_age_days = max(0, min(3650, float(data.get("max_age_days", 30))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "max_age_days должен быть числом"}), 400
    if "max_files" in data:
        try:
            app_config["cache_limit"] = max(1, min(100, int(data["max_files"])))
            save_config()
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "max_files должен быть числом"}), 400

    current_vid = current_track_info.get("video_id")
    protected = {current_vid} if current_vid else set()
    with state_lock:
        protected.update(item.get("videoId") for item in track_queue_list[:6] if item.get("videoId"))
    cutoff = time.time() - (max_age_days * 86400 if max_age_days else 0)
    deleted = 0
    for ext in CACHE_EXTS + JUNK_EXTS:
        for path in glob.glob(os.path.join(CACHE_DIR, f"*.{ext}")):
            base = os.path.basename(path)
            video_id = base.split(".", 1)[0]
            if video_id in protected:
                continue
            with downloading_lock:
                busy = any(base.startswith(f"{value}.") for value in downloading_vids)
            if busy:
                continue
            try:
                # max_age_days == 0 значит "очистить всё" (UI semantics)
                if not max_age_days or os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    deleted += 1
            except OSError:
                pass
    clean_old_cache(protect_vid=current_vid)
    status_cache.pop("stats", None)
    return jsonify({"ok": True, "deleted": deleted, "cache_dir": CACHE_DIR, "max_age_days": max_age_days})

@app.route('/radio.mp3')
def stream_audio():
    def client_stream():
        listener_q = queue.Queue(maxsize=128)
        with listeners_lock:
            active_listeners.append(listener_q)
        try:
            listener_q.put_nowait(SILENCE_CHUNK)
            while True:
                try:
                    # Таймаут: после 30с тишины шлём keep-alive. Это заставляет
                    # генератор выполнить yield -> Werkzeug замечает разрыв TCP,
                    # и поток мёртвого клиента завершается (раньше висел вечно)
                    yield listener_q.get(timeout=30)
                except queue.Empty:
                    yield KEEPALIVE_CHUNK
        except GeneratorExit:
            pass
        finally:
            with listeners_lock:
                if listener_q in active_listeners:
                    active_listeners.remove(listener_q)

    resp = Response(client_stream(), mimetype='audio/mpeg')
    resp.headers["icy-name"] = "ETS2 Radio DJ"
    resp.headers["icy-genre"] = "Mixed"
    resp.headers["icy-pub"] = "1"
    return resp

@app.route('/api/user_playlists')
def get_user_playlists_endpoint():
    playlists = []
    if is_active_authenticated:
        playlists.append({
            "id": "LM",
            "title": "Избранные треки (Лайки)",
            "url": "https://music.youtube.com/playlist?list=LM",
            "type": "likes",
            "thumbnail": "https://www.gstatic.com/youtube/media/ytm/images/auto_playlists/liked_songs.png"
        })
    seen_ids = {"LM"}

    p_info = profiles_data.get(active_profile_name) or {}
    if p_info.get("type") != "oauth" or not is_active_authenticated:
        return jsonify({"ok": True, "playlists": playlists})
    token_file = p_info.get("token_file")
    client_id = sanitize_oauth_input(p_info.get("client_id", ""))
    client_secret = sanitize_oauth_input(p_info.get("client_secret", ""))

    token_file = resolve_path(token_file) if token_file else ""
    if token_file and os.path.exists(token_file):
        try:
            with open(token_file, 'r', encoding='utf-8') as f:
                tok = json.load(f)
            access_token = tok.get("access_token")
            refresh_token = tok.get("refresh_token")
            url = "https://www.googleapis.com/youtube/v3/playlists?part=snippet,contentDetails&mine=true&maxResults=50"
            res = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=6)

            if res.status_code == 401 and refresh_token and client_id and client_secret:
                creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
                new_tok = creds.refresh_token(refresh_token)
                if new_tok and "access_token" in new_tok:
                    tok.update(new_tok)
                    atomic_json_dump(token_file, tok)
                    access_token = tok["access_token"]
                    res = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=6)

            if res.status_code == 200:
                for item in res.json().get("items", []):
                    pid = item.get("id")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        snip = item.get("snippet", {})
                        title = snip.get("title", "Плейліст")
                        count = item.get("contentDetails", {}).get("itemCount", 0)
                        thumb = (snip.get("thumbnails", {}).get("default") or {}).get("url", "")
                        playlists.append({
                            "id": pid,
                            "title": title,
                            "count": count,
                            "thumbnail": thumb,
                            "url": f"https://music.youtube.com/playlist?list={pid}",
                            "type": "playlist"
                        })
        except Exception as e:
            print("[OAuth] Error fetching user playlists:", e)

    return jsonify({"ok": True, "playlists": playlists})


if __name__ == '__main__':
    for pattern in ("_temp_session_*.json", "_temp_setup.json"):
        for f in glob.glob(os.path.join(BASE_DIR, pattern)):
            try:
                os.remove(f)
            except Exception:
                pass

    threading.Thread(target=dj_thread, daemon=True).start()
    threading.Thread(target=auth_watchdog, daemon=True).start()
    threading.Thread(target=cache_watchdog, daemon=True).start()
    threading.Thread(target=game_pause_watchdog, daemon=True).start()
    threading.Thread(target=discord_rpc_thread, daemon=True).start()
    radio_host = os.environ.get("RADIO_HOST", "127.0.0.1")
    if radio_host not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("RADIO_ALLOW_LAN") != "1":
        print("[WARN] Сетевой доступ запрещён. Для включения задайте RADIO_ALLOW_LAN=1.")
        radio_host = "127.0.0.1"
    app.run(host=radio_host, port=5000, threaded=True)