"""Vertical tab routes, sharing the existing process lock, console and cancellation."""
import json
import os
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest

from src.utils import new_run_dir
from src.vertical import VIDEO_EXTENSIONS, VerticalOptions, probe, validate_social_url


def register_vertical(app, backend):
    bp = Blueprint('vertical', __name__, url_prefix='/api/vertical')
    root = backend.PROJECT_ROOT
    gallery = root / 'assets' / 'vertical'
    uploads = backend.DATA_DIR / 'vertical_uploads'
    runs = backend.CLIPS_DIR / 'vertical'

    @bp.get('/presets')
    def presets():
        return jsonify([{'name': p.name, 'id': p.name} for p in sorted(gallery.glob('*'))
                        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS])

    @bp.post('/upload')
    def upload():
        # Bound uploads to 2 GiB, including requests without Content-Length.
        request.max_content_length = 2 * 1024**3
        file = request.files.get('video')
        if not file or Path(file.filename or '').suffix.lower() not in VIDEO_EXTENSIONS:
            return jsonify(error='Envie MP4, MOV, MKV, WebM ou M4V.'), 400
        directory = new_run_dir(uploads)
        path = directory / ('video' + Path(file.filename).suffix.lower())
        file.save(path)
        try:
            probe(path)
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(id=str(path.relative_to(uploads)))

    def local_asset(directory, value):
        path = (directory / value).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError('Arquivo complementar inválido.')
        return str(path)

    @bp.get('/frame_preview')
    def frame_preview():
        source = request.args.get('source', '').strip()
        ts_param = request.args.get('timestamp')
        if not source:
            return jsonify(error='Fonte não informada'), 400

        preview_dir = (uploads / 'frame_previews').resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)
        import hashlib

        # Caso 1: Fonte local (upload ou arquivo)
        local_path = None
        if not source.startswith(('http://', 'https://')):
            candidate = (uploads / source).resolve()
            if candidate.is_file():
                local_path = candidate
            elif Path(source).is_file():
                local_path = Path(source).resolve()

        if local_path and local_path.is_file():
            try:
                meta = probe(local_path)
                duration = meta.get('duration', 10.0)
            except Exception:
                duration = 10.0

            if ts_param is not None:
                try:
                    ts = float(ts_param)
                except ValueError:
                    ts = 3.0
            else:
                # Frame não inicial: 25% da duração ou 3.0 segundos
                ts = min(max(2.5, duration * 0.25), max(1.0, duration - 1.0))

            h = hashlib.md5(f"{local_path}_{ts:.2f}".encode()).hexdigest()
            thumb = preview_dir / f"frame_{h}.jpg"
            if not thumb.exists():
                cmd = ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", str(local_path),
                       "-vframes", "1", "-q:v", "2", str(thumb)]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if thumb.exists():
                return send_from_directory(preview_dir, thumb.name, mimetype='image/jpeg')

        # Caso 2: URL externa
        if source.startswith(('http://', 'https://')):
            h = hashlib.md5(source.encode()).hexdigest()
            thumb = preview_dir / f"url_{h}.jpg"
            if not thumb.exists():
                cmd = ["yt-dlp", "--skip-download", "--write-thumbnail", "--convert-thumbnails", "jpg",
                       "-o", str(preview_dir / f"url_{h}.%(ext)s"), source]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if thumb.exists():
                return send_from_directory(preview_dir, thumb.name, mimetype='image/jpeg')

        return jsonify(error='Não foi possível extrair o frame'), 404

    @bp.post('/run')
    def run():
        try:
            data = request.get_json()
            options = VerticalOptions(**data.get('options', {}))
            options.validate()
            base_raw = data.get('base', '').strip()
            if base_raw.startswith(('http://', 'https://')):
                base = validate_social_url(base_raw)
            else:
                base = local_asset(uploads, base_raw)

            if options.subtitles_only:
                secondary = None
                options.layout = 'full_screen'
                options.captions = True
            else:
                mode = data.get('secondary_mode', 'none')
                value = data.get('secondary', '')
                secondary = None
                if mode == 'url':
                    secondary = validate_social_url(value)
                elif mode in ('preset', 'upload'):
                    secondary = local_asset(gallery if mode == 'preset' else uploads, value)
                elif mode != 'none':
                    raise ValueError('Origem complementar inválida.')
                if secondary is None:
                    options.layout = 'full_screen'
        except (TypeError, ValueError, AttributeError, BadRequest) as exc:
            return jsonify(error=str(exc)), 400
        with backend._proc_lock:
            if backend._proc is not None and backend._proc.poll() is None:
                return jsonify(error='Um processo já está em execução.'), 409
            work = new_run_dir(runs)
            payload = work / 'request.json'
            from dataclasses import asdict
            payload.write_text(json.dumps(dict(base=base, secondary=secondary, options=asdict(options))))
            cmd = [str(backend.PYTHON_BIN), '-m', 'src.vertical', '--request', str(payload)]
            with backend._log_queue.mutex:
                backend._log_queue.queue.clear()
            try:
                proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1, start_new_session=True,
                                        env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            except OSError as exc:
                return jsonify(error=str(exc)), 500
            backend._proc = proc
            def reader():
                for line in proc.stdout:
                    backend._log_queue.put({'type': backend._classify_line(line), 'text': line})
                rc = proc.wait()
                backend._log_queue.put({'type': 'done', 'code': rc,
                                        'text': f'\n[Vídeo vertical finalizado: código {rc}]\n'})
            threading.Thread(target=reader, daemon=True).start()
        return jsonify(ok=True, run=work.name, output=f'/api/vertical/result/{work.name}/vertical.mp4')

    @bp.get('/result/<run_id>/<filename>')
    def result(run_id, filename):
        if not run_id.startswith('run-') or Path(run_id).name != run_id or filename not in (
            'vertical.mp4', 'captions.ass', 'captions.srt', 'vertical.command.txt', 'vertical.filter.txt', 'timeline.json'
        ):
            return jsonify(error='Arquivo inválido'), 404
        return send_from_directory(runs / run_id, filename)

    app.register_blueprint(bp)
