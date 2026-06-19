from __future__ import annotations

import json
import hashlib
import math
import mimetypes
import os
import ctypes
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
ROOT = APP_ROOT
STATIC = BUNDLE_ROOT / "static"
DATA = Path(os.environ.get("LOCALAPPDATA", APP_ROOT)) / "GeViMastering" if FROZEN else APP_ROOT / ".suno-mastering"
UPLOADS = DATA / "uploads"
MEDIA = DATA / "media"
PRESETS_FILE = DATA / "web-presets.json"
HISTORY_FILE = DATA / "export-history.json"
ANALYSIS_CACHE_FILE = DATA / "analysis-cache.json"
SESSION_FILE = DATA / "session.json"
EXPORTS = Path.home() / "Documents" / "GeViMastering" / "exports" if FROZEN else APP_ROOT / "exports"
FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
DEFAULT_EQ_PRESETS = {
    "Plano": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Rock": [2, 1, 0, -1, -1, 0, 1, 2, 3, 2],
    "Hip-Hop": [4, 3, 2, 0, -1, 0, 1, 1, 2, 1],
    "Pop": [2, 1, 0, -1, 0, 1, 2, 2, 2, 1],
    "Electrónica": [4, 3, 1, 0, -1, 0, 1, 2, 3, 2],
    "Metal": [2, 1, 0, -1, -2, 0, 2, 3, 2, 1],
    "Jazz": [1, 1, 0, 0, 0, 1, 1, 1, 2, 2],
    "Acústico": [0, 0, 0, 1, 1, 2, 2, 1, 1, 0],
    "Reggaetón": [4, 3, 2, 0, -1, 0, 1, 2, 2, 1],
    "Graves cálidos": [3, 3, 2, 1, 0, 0, 0, 0, 0, 0],
    "Claridad vocal": [0, 0, -1, -1, 0, 1, 2, 2, 1, 0],
}
DEFAULT_PRESET = {
    "preamp": 0, "eq": [0] * 10, "target_lufs": -10, "true_peak": -1,
    "preview_start": 30, "compression": False,
    "filename": {"track": True, "title": True, "album": False, "artist": False, "separator": " - "},
}
DEFAULT_PRESETS = {name: {**DEFAULT_PRESET, "eq": values} for name, values in DEFAULT_EQ_PRESETS.items()}
JOB_LOCK = threading.Lock()
ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
CANCELLED_JOBS: set[str] = set()
CACHE_LOCK = threading.Lock()
SESSION_LOCK = threading.Lock()
INSTANCE_MUTEX = None


def find_binary(name: str) -> str | None:
    for local in (BUNDLE_ROOT / "tools" / f"{name}.exe", APP_ROOT / "tools" / f"{name}.exe"):
        if local.exists():
            return str(local)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    try:
        matches = list(winget.glob(f"Gyan.FFmpeg*/ffmpeg-*-full_build/bin/{name}.exe"))
        return str(matches[-1]) if matches else None
    except OSError:
        return None


FFMPEG = find_binary("ffmpeg")


def reset_job(job_id: str) -> None:
    if not job_id:
        return
    with JOB_LOCK:
        CANCELLED_JOBS.discard(job_id)


def finish_job(job_id: str) -> None:
    if not job_id:
        return
    with JOB_LOCK:
        ACTIVE_PROCESSES.pop(job_id, None)
        CANCELLED_JOBS.discard(job_id)


def cancel_job(job_id: str) -> bool:
    if not job_id:
        return False
    with JOB_LOCK:
        CANCELLED_JOBS.add(job_id)
        process = ACTIVE_PROCESSES.get(job_id)
    if process and process.poll() is None:
        try:
            process.terminate()
            return True
        except OSError:
            return False
    return False


def run_ffmpeg(args: list[str], job_id: str = "") -> subprocess.CompletedProcess[str]:
    if not FFMPEG:
        raise RuntimeError("No se encontró FFmpeg. Instálalo o colócalo en tools/ffmpeg.exe.")
    with JOB_LOCK:
        if job_id and job_id in CANCELLED_JOBS:
            raise RuntimeError("Exportación cancelada.")
    process = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-y", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if job_id:
        with JOB_LOCK:
            ACTIVE_PROCESSES[job_id] = process
            cancelled = job_id in CANCELLED_JOBS
        if cancelled:
            process.terminate()
    stdout, stderr = process.communicate()
    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    if job_id:
        with JOB_LOCK:
            if ACTIVE_PROCESSES.get(job_id) is process:
                ACTIVE_PROCESSES.pop(job_id, None)
            cancelled = job_id in CANCELLED_JOBS
        if cancelled:
            raise RuntimeError("Exportación cancelada.")
    if result.returncode:
        raise RuntimeError("FFmpeg no pudo completar el proceso:\n" + "\n".join(result.stderr.splitlines()[-12:]))
    return result


def normalize_preset(value: object) -> dict:
    if isinstance(value, list):
        value = {"eq": value}
    if not isinstance(value, dict):
        value = {}
    eq = [max(-12.0, min(12.0, float(item))) for item in value.get("eq", [0] * 10)][:10]
    if len(eq) != 10:
        eq = [0.0] * 10
    filename = value.get("filename", {})
    if not isinstance(filename, dict):
        filename = {}
    separator = str(filename.get("separator", " - "))
    if separator not in {" - ", "_", " "}:
        separator = " - "
    return {
        "preamp": max(-12.0, min(12.0, float(value.get("preamp", 0)))),
        "eq": eq,
        "target_lufs": max(-16.0, min(-7.0, float(value.get("target_lufs", -10)))),
        "true_peak": max(-3.0, min(-0.5, float(value.get("true_peak", -1)))),
        "preview_start": max(0.0, float(value.get("preview_start", 30))),
        "compression": bool(value.get("compression", False)),
        "filename": {
            "track": bool(filename.get("track", True)),
            "title": bool(filename.get("title", True)),
            "album": bool(filename.get("album", False)),
            "artist": bool(filename.get("artist", False)),
            "separator": separator,
        },
    }


def load_presets() -> dict[str, dict]:
    custom = {}
    if PRESETS_FILE.exists():
        try:
            custom = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if not isinstance(custom, dict):
        custom = {}
    return {**DEFAULT_PRESETS, **{name: normalize_preset(value) for name, value in custom.items()}}


def save_presets(presets: dict[str, dict]) -> None:
    DATA.mkdir(exist_ok=True)
    custom = {k: v for k, v in presets.items() if k not in DEFAULT_PRESETS}
    PRESETS_FILE.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def add_history_entry(entry: dict) -> list[dict]:
    history = load_history()
    clean = {
        "created_at": str(entry.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        "album": str(entry.get("album", "")).strip(),
        "artist": str(entry.get("artist", "")).strip(),
        "metadata": entry.get("metadata") or load_session().get("metadata", {}),
        "format": str(entry.get("format", "")).lower(),
        "tracks": max(0, int(entry.get("tracks", 0))),
        "output_dir": str(entry.get("output_dir", "")),
        "outputs": [str(item) for item in entry.get("outputs", [])][:200],
        "settings": entry.get("settings", {}),
        "filename": entry.get("filename") or load_session().get("filename", {}),
    }
    history.insert(0, clean)
    history = history[:50]
    DATA.mkdir(exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def valid_uploaded_file(value: object, folder: str) -> Path | None:
    if not value:
        return None
    try:
        path = Path(str(value)).resolve()
        root = (UPLOADS / folder).resolve()
        return path if path.is_file() and path.is_relative_to(root) else None
    except OSError:
        return None


def normalize_session(value: object) -> dict:
    if not isinstance(value, dict):
        value = {}
    tracks = []
    for index, item in enumerate(value.get("tracks", [])[:200]):
        if not isinstance(item, dict):
            continue
        path = valid_uploaded_file(item.get("path"), "audio")
        if not path:
            continue
        tracks.append({
            "id": str(item.get("id") or f"restored-{index}"), "path": str(path),
            "name": str(item.get("name") or path.name), "title": str(item.get("title") or path.stem),
            "track": max(1, int(item.get("track", index + 1))),
        })
    cover = valid_uploaded_file(value.get("cover_path"), "covers")
    metadata = value.get("metadata", {}) if isinstance(value.get("metadata"), dict) else {}
    profile = normalize_preset({**(value.get("settings", {}) if isinstance(value.get("settings"), dict) else {}), "filename": value.get("filename", {})})
    output_format = str(value.get("output_format", "wav")).lower()
    return {
        "tracks": tracks, "current_track_id": str(value.get("current_track_id", "")),
        "cover_path": str(cover) if cover else "", "cover_name": str(value.get("cover_name", cover.name if cover else "")),
        "metadata": {key: str(metadata.get(key, "")) for key in ("artist", "album", "date", "genre")},
        "settings": {key: profile[key] for key in ("preamp", "eq", "target_lufs", "true_peak", "preview_start", "compression")},
        "filename": profile["filename"], "output_dir": str(value.get("output_dir", EXPORTS)),
        "output_format": output_format if output_format in {"wav", "flac", "mp3"} else "wav",
    }


def load_session() -> dict:
    if not SESSION_FILE.exists():
        return {}
    try:
        return normalize_session(json.loads(SESSION_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_session(value: object) -> dict:
    session = normalize_session(value)
    DATA.mkdir(exist_ok=True)
    with SESSION_LOCK:
        temporary = SESSION_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SESSION_FILE)
    return session


def clear_session() -> None:
    with SESSION_LOCK:
        SESSION_FILE.unlink(missing_ok=True)


def session_file_paths() -> set[Path]:
    session = load_session()
    values = [item.get("path") for item in session.get("tracks", [])]
    values.append(session.get("cover_path"))
    return {Path(value).resolve() for value in values if value}


def load_analysis_cache() -> dict:
    if not ANALYSIS_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(ANALYSIS_CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def analysis_cache_key(audio: Path, operation: str, options: dict) -> str:
    stat = audio.stat()
    payload = {
        "path": str(audio.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "operation": operation, "options": options,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def cached_analysis(audio: Path, operation: str, options: dict) -> dict | None:
    key = analysis_cache_key(audio, operation, options)
    with CACHE_LOCK:
        value = load_analysis_cache().get(key)
    return value if isinstance(value, dict) else None


def save_cached_analysis(audio: Path, operation: str, options: dict, value: dict) -> None:
    key = analysis_cache_key(audio, operation, options)
    with CACHE_LOCK:
        cache = load_analysis_cache()
        cache[key] = value
        if len(cache) > 500:
            cache = dict(list(cache.items())[-500:])
        DATA.mkdir(exist_ok=True)
        ANALYSIS_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def processing_filter(settings: dict) -> str:
    filters: list[str] = []
    preamp = float(settings.get("preamp", 0))
    if abs(preamp) >= 0.05:
        filters.append(f"volume={math.pow(10, preamp / 20):.8f}")
    gains = settings.get("eq", [0] * 10)
    for frequency, gain in zip(FREQUENCIES, gains):
        gain = float(gain)
        if abs(gain) >= 0.05:
            filters.append(f"equalizer=f={frequency}:width_type=o:width=1:g={gain:.2f}")
    if settings.get("compression"):
        filters.append("acompressor=threshold=-14dB:ratio=1.5:attack=25:release=180:makeup=1dB")
    return ",".join(filters)


def loudness_data(audio: Path, processing: str = "", target: float = -14, peak: float = -1, job_id: str = "") -> dict:
    norm = f"loudnorm=I={target:.1f}:LRA=11:TP={peak:.1f}:print_format=json"
    audio_filter = ",".join(filter(None, [processing, norm]))
    result = run_ffmpeg(["-i", str(audio), "-af", audio_filter, "-f", "null", "NUL"], job_id)
    blocks = re.findall(r"\{[\s\S]*?\}", result.stderr)
    if not blocks:
        raise RuntimeError("No se pudieron medir los niveles del audio.")
    return json.loads(blocks[-1])


def cached_loudness_data(audio: Path, processing: str = "", target: float = -14, peak: float = -1, job_id: str = "") -> dict:
    options = {"processing": processing, "target": target, "peak": peak}
    cached = cached_analysis(audio, "loudness", options)
    if cached is not None:
        return cached
    result = loudness_data(audio, processing, target, peak, job_id)
    save_cached_analysis(audio, "loudness", options, result)
    return result


def metadata_args(metadata: dict) -> list[str]:
    args: list[str] = []
    for key, value in metadata.items():
        value = str(value).strip()
        if value:
            args.extend(["-metadata", f"{key}={value}"])
    return args


def temporary_storage_info() -> dict:
    files = 0
    size = 0
    for root in (UPLOADS, MEDIA):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    files += 1
                    size += path.stat().st_size
                except OSError:
                    pass
    return {"files": files, "bytes": size}


def cleanup_temporary_files(max_age_hours: float | None = None, preserve_session: bool = True) -> dict:
    cutoff = time.time() - max_age_hours * 3600 if max_age_hours is not None else None
    protected = session_file_paths() if preserve_session else set()
    removed_files = 0
    removed_bytes = 0
    for root in (UPLOADS, MEDIA):
        if not root.exists():
            continue
        resolved_root = root.resolve()
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(resolved_root):
                    continue
                if resolved in protected:
                    continue
                stat = resolved.stat()
                if cutoff is not None and stat.st_mtime >= cutoff:
                    continue
                resolved_bytes = stat.st_size
                resolved.unlink()
                removed_files += 1
                removed_bytes += resolved_bytes
            except OSError:
                pass
        for directory in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
    return {"removed_files": removed_files, "removed_bytes": removed_bytes, "remaining": temporary_storage_info()}


def select_output_directory(initial: str = "") -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        return filedialog.askdirectory(
            parent=root,
            title="Elegir carpeta de exportación",
            initialdir=initial if Path(initial).is_dir() else str(EXPORTS),
        )
    finally:
        root.destroy()


def second_pass_filter(audio: Path, settings: dict, job_id: str = "") -> str:
    target = float(settings.get("target_lufs", -10))
    peak = float(settings.get("true_peak", -1))
    process = processing_filter(settings)
    levels = cached_loudness_data(audio, process, target, peak, job_id)
    normalize = (
        f"loudnorm=I={target:.1f}:LRA=11:TP={peak:.1f}"
        f":measured_I={levels['input_i']}:measured_TP={levels['input_tp']}"
        f":measured_LRA={levels['input_lra']}:measured_thresh={levels['input_thresh']}"
        f":offset={levels['target_offset']}:linear=true:print_format=summary"
    )
    return ",".join(filter(None, [process, normalize]))


def mastered_loudness_data(audio: Path, settings: dict) -> dict:
    relevant = {key: settings.get(key) for key in ("preamp", "eq", "target_lufs", "true_peak", "compression")}
    options = {"settings": relevant}
    cached = cached_analysis(audio, "mastered-loudness", options)
    if cached is not None:
        return cached
    with tempfile.TemporaryDirectory(prefix="gevi-analysis-") as temporary:
        master = Path(temporary) / "master.wav"
        final_filter = second_pass_filter(audio, settings)
        run_ffmpeg(["-i", str(audio), "-map_metadata", "-1", "-af", final_filter, "-ar", "48000", "-c:a", "pcm_s24le", str(master)])
        result = loudness_data(master)
    save_cached_analysis(audio, "mastered-loudness", options, result)
    return result


def make_preview(audio: Path, settings: dict) -> tuple[Path, Path]:
    MEDIA.mkdir(parents=True, exist_ok=True)
    original = MEDIA / "preview-original.wav"
    mastered = MEDIA / "preview-master.wav"
    start = max(0, float(settings.get("preview_start", 30)))
    # El EQ y el preamp se aplican en vivo en el navegador. El clip base conserva
    # el resto del mastering para poder mover las bandas sin regenerar audio.
    preview_settings = dict(settings)
    preview_settings["preamp"] = 0
    preview_settings["eq"] = [0] * len(FREQUENCIES)
    audio_filter = second_pass_filter(audio, preview_settings)
    run_ffmpeg(["-ss", str(start), "-t", "60", "-i", str(audio), "-ar", "48000", "-c:a", "pcm_s16le", str(original)])
    run_ffmpeg(["-ss", str(start), "-t", "60", "-i", str(audio), "-af", audio_filter, "-ar", "48000", "-c:a", "pcm_s16le", str(mastered)])
    return original, mastered


def safe_output_stem(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "master"


def export_audio(audio: Path, cover: Path | None, settings: dict, metadata: dict, output_dir: Path, output_format: str, output_stem: str = "", job_id: str = "") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = output_format.lower()
    if output_format not in {"wav", "flac", "mp3"}:
        raise ValueError("El formato de salida debe ser WAV, FLAC o MP3.")
    title = str(metadata.get("title", "")).strip() or audio.stem
    safe = safe_output_stem(output_stem or title)
    final_filter = second_pass_filter(audio, settings, job_id)
    tags = metadata_args(metadata)
    output = output_dir / f"{safe}.{output_format}"

    with tempfile.TemporaryDirectory(prefix="suno-mastering-") as temporary:
        master = Path(temporary) / "master.wav"
        encoded = Path(temporary) / f"output.{output_format}"
        run_ffmpeg(["-i", str(audio), "-map_metadata", "-1", "-af", final_filter, "-ar", "48000", "-c:a", "pcm_s24le", str(master)], job_id)
        if output_format == "wav":
            run_ffmpeg(["-i", str(master), "-map_metadata", "-1", *tags, "-ar", "48000", "-c:a", "pcm_s24le", str(encoded)], job_id)
        else:
            command = ["-i", str(master)]
            if cover and cover.is_file():
                command += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-disposition:v", "attached_pic", "-metadata:s:v", "title=Album cover"]
            if output_format == "flac":
                command += ["-map_metadata", "-1", *tags, "-ar", "48000", "-c:a", "flac", "-sample_fmt", "s32", "-compression_level", "8", "-c:v", "copy", str(encoded)]
            else:
                command += ["-map_metadata", "-1", *tags, "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3", "-c:v", "copy", str(encoded)]
            run_ffmpeg(command, job_id)
        encoded.replace(output)
    return output


class Handler(BaseHTTPRequestHandler):
    server_version = "GeViMastering/1.0"

    def log_message(self, format: str, *args) -> None:
        pass

    def send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/state":
            self.send_json({"presets": load_presets(), "history": load_history(), "session": load_session(), "output_dir": str(EXPORTS), "ffmpeg": bool(FFMPEG), "temporary": temporary_storage_info()})
            return
        if parsed.path == "/session-cover":
            cover = load_session().get("cover_path")
            if cover:
                self.serve_file(Path(cover))
            else:
                self.send_error(404)
            return
        if parsed.path.startswith("/media/"):
            self.serve_file(MEDIA / Path(parsed.path).name)
            return
        relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
        self.serve_file(STATIC / relative)

    def serve_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            allowed = any(resolved.is_relative_to(root.resolve()) for root in (STATIC, MEDIA, UPLOADS))
            if not resolved.is_file() or not allowed:
                self.send_error(404)
                return
            data = resolved.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/upload":
                cleanup_temporary_files(max_age_hours=24)
                query = urllib.parse.parse_qs(parsed.query)
                kind = query.get("kind", ["audio"])[0]
                original = Path(query.get("name", ["archivo"])[0]).name
                safe = re.sub(r"[^\w.() -]+", "_", original, flags=re.UNICODE)
                target_dir = UPLOADS / ("covers" if kind == "cover" else "audio")
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / safe
                remaining = int(self.headers.get("Content-Length", "0"))
                with target.open("wb") as output:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        output.write(chunk)
                        remaining -= len(chunk)
                self.send_json({"ok": True, "path": str(target), "name": original, "temporary": temporary_storage_info()})
                return
            if parsed.path == "/api/cleanup":
                self.read_json()
                clear_session()
                result = cleanup_temporary_files(preserve_session=False)
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/session":
                session = save_session(self.read_json())
                self.send_json({"ok": True, "session": session})
                return
            if parsed.path == "/api/analyze":
                body = self.read_json()
                data = cached_loudness_data(Path(body["audio_path"]))
                self.send_json({"ok": True, "analysis": data})
                return
            if parsed.path == "/api/analyze-album":
                body = self.read_json()
                tracks = body.get("tracks", [])
                settings = body.get("settings", {})
                if not tracks:
                    raise ValueError("Añade al menos una canción para medir el álbum.")
                results = []
                for track in tracks[:200]:
                    levels = mastered_loudness_data(Path(track["path"]), settings)
                    results.append({
                        "id": str(track.get("id", "")),
                        "title": str(track.get("title", "")),
                        "track": int(track.get("track", 0)),
                        "lufs": float(levels["input_i"]),
                        "true_peak": float(levels["input_tp"]),
                        "lra": float(levels["input_lra"]),
                    })
                center = sum(item["lufs"] for item in results) / len(results)
                for item in results:
                    item["difference"] = round(item["lufs"] - center, 1)
                spread = max(item["lufs"] for item in results) - min(item["lufs"] for item in results)
                self.send_json({"ok": True, "tracks": results, "average_lufs": round(center, 1), "spread": round(spread, 1), "mode": "post-master"})
                return
            if parsed.path == "/api/cancel-export":
                body = self.read_json()
                job_id = str(body.get("job_id", ""))
                active = cancel_job(job_id)
                self.send_json({"ok": True, "cancelled": True, "active_process_stopped": active})
                return
            if parsed.path == "/api/start-export-job":
                body = self.read_json()
                reset_job(str(body.get("job_id", "")))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/finish-export-job":
                body = self.read_json()
                finish_job(str(body.get("job_id", "")))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/preflight":
                body = self.read_json()
                output_dir = Path(body.get("output_dir") or EXPORTS)
                output_format = str(body.get("output_format", "wav")).lower()
                if output_format not in {"wav", "flac", "mp3"}:
                    raise ValueError("Formato de salida inválido.")
                existing = []
                duplicates = []
                seen: set[str] = set()
                for stem in body.get("stems", [])[:200]:
                    candidate = output_dir / f"{safe_output_stem(str(stem))}.{output_format}"
                    normalized = str(candidate).casefold()
                    if normalized in seen:
                        duplicates.append(str(candidate))
                    seen.add(normalized)
                    if candidate.exists():
                        existing.append(str(candidate))
                self.send_json({"ok": True, "existing": existing, "duplicates": duplicates})
                return
            if parsed.path == "/api/history":
                body = self.read_json()
                history = add_history_entry(body)
                self.send_json({"ok": True, "history": history})
                return
            if parsed.path == "/api/open-folder":
                body = self.read_json()
                raw_path = str(body.get("path", "")).strip()
                if not raw_path:
                    raise ValueError("El historial no conserva una carpeta para esta exportación.")
                path = Path(raw_path).expanduser()
                target = path if path.is_dir() else path.parent
                if not target.is_dir():
                    raise ValueError("La carpeta ya no existe.")
                os.startfile(str(target))
                self.send_json({"ok": True, "path": str(target)})
                return
            if parsed.path == "/api/select-output-dir":
                body = self.read_json()
                selected = select_output_directory(str(body.get("initial", "")))
                self.send_json({"ok": True, "path": selected})
                return
            if parsed.path == "/api/preview":
                body = self.read_json()
                original, mastered = make_preview(Path(body["audio_path"]), body["settings"])
                version = str(max(os.path.getmtime(original), os.path.getmtime(mastered)))
                self.send_json({"ok": True, "original_url": "/media/preview-original.wav?v=" + version, "master_url": "/media/preview-master.wav?v=" + version})
                return
            if parsed.path == "/api/export":
                body = self.read_json()
                job_id = str(body.get("job_id", ""))
                cover = Path(body["cover_path"]) if body.get("cover_path") else None
                output = export_audio(Path(body["audio_path"]), cover, body["settings"], body["metadata"], Path(body.get("output_dir") or EXPORTS), body.get("output_format", "wav"), str(body.get("output_stem", "")), job_id)
                self.send_json({"ok": True, "output": str(output)})
                return
            if parsed.path == "/api/presets":
                body = self.read_json()
                name = str(body["name"]).strip()
                if not name:
                    raise ValueError("El preset necesita un nombre.")
                if name in DEFAULT_PRESETS:
                    raise ValueError("Elige otro nombre; los presets incluidos no se sobrescriben.")
                profile = normalize_preset(body.get("profile", body.get("values", [])))
                presets = load_presets()
                presets[name] = profile
                save_presets(presets)
                self.send_json({"ok": True, "presets": presets})
                return
            self.send_error(404)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/presets/"):
            name = urllib.parse.unquote(parsed.path.removeprefix("/api/presets/"))
            if name in DEFAULT_PRESETS:
                self.send_json({"ok": False, "error": "Los presets incluidos no se pueden eliminar."}, 400)
                return
            presets = load_presets()
            presets.pop(name, None)
            save_presets(presets)
            self.send_json({"ok": True, "presets": load_presets()})
            return
        self.send_error(404)


def claim_single_instance() -> bool:
    global INSTANCE_MUTEX
    if os.name != "nt" or not FROZEN or os.environ.get("GEVI_ALLOW_MULTIPLE") == "1":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.SetLastError(0)
    INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "Local\\GeViMasteringDesktop")
    if kernel32.GetLastError() != 183:
        return True
    user32 = ctypes.windll.user32
    window = user32.FindWindowW(None, "GeVi Mastering")
    if window:
        user32.ShowWindow(window, 9)
        user32.SetForegroundWindow(window)
    kernel32.CloseHandle(INSTANCE_MUTEX)
    INSTANCE_MUTEX = None
    return False


def run_native_window(server: ThreadingHTTPServer, url: str) -> bool:
    try:
        import webview
    except ImportError:
        return False
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        webview.create_window("GeVi Mastering", url, width=1440, height=920, min_size=(900, 650))
        webview.start()
    except Exception as exc:
        try:
            (DATA / "desktop-error.log").write_text(str(exc), encoding="utf-8")
        except OSError:
            pass
        webbrowser.open(url)
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
    finally:
        server.shutdown()
        server.server_close()
        cleanup_temporary_files()
    return True


def main() -> None:
    DATA.mkdir(exist_ok=True)
    if not claim_single_instance():
        return
    cleanup_temporary_files(max_age_hours=24)
    preferred_port = int(os.environ.get("GEVI_PORT", "8765"))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", preferred_port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"GeVi Mastering: {url}")
    if FROZEN and os.environ.get("GEVI_NO_BROWSER") != "1" and run_native_window(server, url):
        return
    if os.environ.get("GEVI_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        cleanup_temporary_files()


if __name__ == "__main__":
    main()
