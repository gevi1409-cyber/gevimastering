from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / ".suno-mastering"
UPLOADS = DATA / "uploads"
MEDIA = DATA / "media"
PRESETS_FILE = DATA / "web-presets.json"
EXPORTS = ROOT / "exports"
FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
DEFAULT_PRESETS = {
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


def find_binary(name: str) -> str | None:
    local = ROOT / "tools" / f"{name}.exe"
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


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    if not FFMPEG:
        raise RuntimeError("No se encontró FFmpeg. Instálalo o colócalo en tools/ffmpeg.exe.")
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-y", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("FFmpeg no pudo completar el proceso:\n" + "\n".join(result.stderr.splitlines()[-12:]))
    return result


def load_presets() -> dict[str, list[float]]:
    custom = {}
    if PRESETS_FILE.exists():
        try:
            custom = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {**DEFAULT_PRESETS, **custom}


def save_presets(presets: dict[str, list[float]]) -> None:
    DATA.mkdir(exist_ok=True)
    custom = {k: v for k, v in presets.items() if k not in DEFAULT_PRESETS}
    PRESETS_FILE.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")


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


def loudness_data(audio: Path, processing: str = "", target: float = -14, peak: float = -1) -> dict:
    norm = f"loudnorm=I={target:.1f}:LRA=11:TP={peak:.1f}:print_format=json"
    audio_filter = ",".join(filter(None, [processing, norm]))
    result = run_ffmpeg(["-i", str(audio), "-af", audio_filter, "-f", "null", "NUL"])
    blocks = re.findall(r"\{[\s\S]*?\}", result.stderr)
    if not blocks:
        raise RuntimeError("No se pudieron medir los niveles del audio.")
    return json.loads(blocks[-1])


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


def cleanup_temporary_files(max_age_hours: float | None = None) -> dict:
    cutoff = time.time() - max_age_hours * 3600 if max_age_hours is not None else None
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


def second_pass_filter(audio: Path, settings: dict) -> str:
    target = float(settings.get("target_lufs", -10))
    peak = float(settings.get("true_peak", -1))
    process = processing_filter(settings)
    levels = loudness_data(audio, process, target, peak)
    normalize = (
        f"loudnorm=I={target:.1f}:LRA=11:TP={peak:.1f}"
        f":measured_I={levels['input_i']}:measured_TP={levels['input_tp']}"
        f":measured_LRA={levels['input_lra']}:measured_thresh={levels['input_thresh']}"
        f":offset={levels['target_offset']}:linear=true:print_format=summary"
    )
    return ",".join(filter(None, [process, normalize]))


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


def export_audio(audio: Path, cover: Path | None, settings: dict, metadata: dict, output_dir: Path, output_format: str, output_stem: str = "") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = output_format.lower()
    if output_format not in {"wav", "flac", "mp3"}:
        raise ValueError("El formato de salida debe ser WAV, FLAC o MP3.")
    title = str(metadata.get("title", "")).strip() or audio.stem
    safe = re.sub(r'[<>:"/\\|?*]+', "_", output_stem or title).strip(" .") or "master"
    final_filter = second_pass_filter(audio, settings)
    tags = metadata_args(metadata)
    output = output_dir / f"{safe}.{output_format}"

    with tempfile.TemporaryDirectory(prefix="suno-mastering-") as temporary:
        master = Path(temporary) / "master.wav"
        run_ffmpeg(["-i", str(audio), "-map_metadata", "-1", "-af", final_filter, "-ar", "48000", "-c:a", "pcm_s24le", str(master)])
        if output_format == "wav":
            run_ffmpeg(["-i", str(master), "-map_metadata", "-1", *tags, "-ar", "48000", "-c:a", "pcm_s24le", str(output)])
        else:
            command = ["-i", str(master)]
            if cover and cover.is_file():
                command += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-disposition:v", "attached_pic", "-metadata:s:v", "title=Album cover"]
            if output_format == "flac":
                command += ["-map_metadata", "-1", *tags, "-ar", "48000", "-c:a", "flac", "-sample_fmt", "s32", "-compression_level", "8", "-c:v", "copy", str(output)]
            else:
                command += ["-map_metadata", "-1", *tags, "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3", "-c:v", "copy", str(output)]
            run_ffmpeg(command)
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
            self.send_json({"presets": load_presets(), "output_dir": str(EXPORTS), "ffmpeg": bool(FFMPEG), "temporary": temporary_storage_info()})
            return
        if parsed.path.startswith("/media/"):
            self.serve_file(MEDIA / Path(parsed.path).name)
            return
        relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
        self.serve_file(STATIC / relative)

    def serve_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            if not resolved.is_file() or not (str(resolved).startswith(str(STATIC.resolve())) or str(resolved).startswith(str(MEDIA.resolve()))):
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
                result = cleanup_temporary_files()
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/analyze":
                body = self.read_json()
                data = loudness_data(Path(body["audio_path"]))
                self.send_json({"ok": True, "analysis": data})
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
                cover = Path(body["cover_path"]) if body.get("cover_path") else None
                output = export_audio(Path(body["audio_path"]), cover, body["settings"], body["metadata"], Path(body.get("output_dir") or EXPORTS), body.get("output_format", "wav"), str(body.get("output_stem", "")))
                self.send_json({"ok": True, "output": str(output)})
                return
            if parsed.path == "/api/presets":
                body = self.read_json()
                name = str(body["name"]).strip()
                values = [float(value) for value in body["values"]][:10]
                if not name or len(values) != 10:
                    raise ValueError("El preset necesita nombre y 10 bandas.")
                presets = load_presets()
                presets[name] = values
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


def main() -> None:
    DATA.mkdir(exist_ok=True)
    cleanup_temporary_files(max_age_hours=24)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("GeVi Mastering: http://127.0.0.1:8765")
    threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        cleanup_temporary_files()


if __name__ == "__main__":
    main()
