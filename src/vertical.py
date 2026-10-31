"""Standalone vertical remix pipeline; shares transcription and caption styling."""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from .config import AppConfig, load_config
from .downloader import YouTubeDownloader

LOG = logging.getLogger(__name__)
LAYOUTS = ('split_vertical_top_bottom', 'split_side_by_side', 'full_screen', 'pip_overlay')
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}


@dataclass
class VerticalOptions:
    layout: str = 'full_screen'
    invert: bool = False
    fps: int = 30
    secondary_volume: float = 0.0
    remove_silence: bool = False
    captions: bool = True
    caption_y: float = 65.0
    safe_zones: bool = True
    subtitles_only: bool = False
    # Caption styling options
    caption_font_size: int | None = None
    caption_force_uppercase: bool | None = None
    caption_font: str | None = None
    caption_color: str | None = None
    caption_active_color: str | None = None
    caption_stroke_color: str | None = None
    caption_stroke_width: float | None = None
    caption_shadow: float | None = None
    caption_glow_blur: float | None = None
    caption_hook_scale: int | None = None
    caption_hook_color: str | None = None
    caption_max_words: int | None = None
    caption_max_chars: int | None = None
    caption_position: str | None = None

    def validate(self):
        if self.layout not in LAYOUTS or self.fps not in (30, 60):
            raise ValueError('Layout inválido ou FPS diferente de 30/60.')
        if not 0 <= self.secondary_volume <= 1 or not 0 <= self.caption_y <= 100:
            raise ValueError('Volume ou posição de legenda fora do intervalo.')
        for field in ('invert', 'remove_silence', 'captions', 'safe_zones', 'subtitles_only'):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f'{field} deve ser booleano.')
        if self.caption_force_uppercase is not None and type(self.caption_force_uppercase) is not bool:
            raise ValueError('caption_force_uppercase deve ser booleano.')
        if self.caption_font_size is not None and not (10 <= self.caption_font_size <= 300):
            raise ValueError('caption_font_size deve estar entre 10 e 300.')
        if self.caption_stroke_width is not None and self.caption_stroke_width < 0:
            raise ValueError('caption_stroke_width não pode ser negativo.')
        if self.caption_shadow is not None and self.caption_shadow < 0:
            raise ValueError('caption_shadow não pode ser negativo.')


def validate_social_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or '').lower()
    domains = ('youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com')
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or not any(host == d or host.endswith('.' + d) for d in domains)):
        raise ValueError('Use uma URL HTTP(S) do YouTube, TikTok ou Instagram.')
    return url.strip()


def execute(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{cmd[0]} falhou:\n{result.stderr[-6000:]}')
    return result


def probe(path: Path) -> dict:
    info = json.loads(execute(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                              '-of', 'json', str(path)]).stdout)
    video = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    if video is None:
        raise ValueError('O arquivo não contém vídeo.')
    duration = float(video.get('duration') or info['format'].get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Duração de vídeo inválida; transmissões ao vivo não são aceitas.')
    return dict(duration=duration, audio=any(s['codec_type'] == 'audio' for s in info['streams']),
                width=video['width'], height=video['height'])


def download_social(url: str, directory: Path, config: AppConfig) -> Path:
    """Best original streams, no resolution cap; retry with platform headers."""
    url = validate_social_url(url)
    directory.mkdir(parents=True, exist_ok=True)
    helper = YouTubeDownloader(config)
    manifest = directory / 'download-path.txt'
    cmd = ['yt-dlp', '--no-playlist', '--no-progress', '--retries', '3',
           '--match-filter', '!is_live', '-f', 'bestvideo*+bestaudio/best',
           '--merge-output-format', 'mkv', '--write-info-json',
           '--print-to-file', 'after_move:filepath', str(manifest),
           '-o', str(directory / 'source.%(ext)s'), *helper._cookie_args()]
    if 'youtu' in urlsplit(url).hostname:
        cmd += [*helper._js_runtime_args(), *helper._remote_components_args(), *helper._extractor_args()]
    for retry in (False, True):
        headers = (['--add-headers', 'User-Agent:Mozilla/5.0', '--add-headers',
                    f'Referer:{urlsplit(url).scheme}://{urlsplit(url).netloc}/'] if retry else [])
        try:
            execute([*cmd, *headers, url])
            candidates = manifest.read_text().splitlines()
            path = Path(candidates[-1]).resolve()
            if path.parent != directory.resolve() or not path.is_file():
                raise RuntimeError('yt-dlp não informou um arquivo de saída válido.')
            probe(path)
            return path
        except RuntimeError:
            if retry:
                raise
            LOG.warning('Tentando novamente com headers da plataforma. Cookies configurados são preservados.')
    raise RuntimeError('Download falhou')


def speech_ranges(log: str, duration: float, fps: int) -> list[tuple[float, float]]:
    """Complement silence intervals, with 50 ms speech handles and frame boundaries."""
    silence = []
    start = None
    for match in re.finditer(r'silence_(start|end):\s*(-?\d+(?:\.\d+)?)', log):
        value = min(duration, max(0.0, float(match[2])))
        if match[1] == 'start':
            start = value
        elif start is not None:
            silence.append((start, value))
            start = None
    if start is not None:
        silence.append((start, duration))
    keep, cursor = [], 0.0
    for a, b in silence:
        end = min(duration, a + .05) if a > 0 else 0
        if end > cursor:
            keep.append((cursor, end))
        cursor = max(cursor, b - .05 if b < duration else duration)
    if cursor < duration:
        keep.append((cursor, duration))
    frames = round(duration * fps)
    rounded = []
    for a, b in keep:
        left, right = max(0, math.floor(a * fps)), min(frames, math.ceil(b * fps))
        if right <= left:
            continue
        if rounded and left <= rounded[-1][1]:
            rounded[-1] = (rounded[-1][0], max(right, rounded[-1][1]))
        else:
            rounded.append((left, right))
    return [(a / fps, b / fps) for a, b in rounded]


def cut_filter(ranges: list[tuple[float, float]], fps: int) -> str:
    """Frame-aligned picture cuts and 5 ms non-overlapping audio crossfades."""
    filters = []
    for i, (a, b) in enumerate(ranges):
        filters += [f'[0:v]trim=start_frame={round(a*fps)}:end_frame={round(b*fps)},setpts=PTS-STARTPTS[v{i}]',
                    f'[0:a]atrim=start={a:.9f}:end={b:.9f},asetpts=PTS-STARTPTS[a{i}]']
    filters.append(''.join(f'[v{i}]' for i in range(len(ranges))) + f'concat=n={len(ranges)}:v=1:a=0[v]')
    previous = 'a0'
    for i in range(1, len(ranges)):
        label = f'joined{i}'
        filters.append(f'[{previous}][a{i}]acrossfade=d=0.005:o=0:c1=tri:c2=tri[{label}]')
        previous = label
    filters.append(f'[{previous}]anull[a]')
    return ';'.join(filters)


def ffmpeg_render(inputs: list[str], graph: str, output: Path, duration: float, fps: int,
                  *, pcm: bool = False):
    script = output.with_suffix('.filter.txt')
    script.write_text(graph)
    cmd = ['ffmpeg', '-hide_banner', '-y', '-filter_complex_threads', '2', *inputs,
           '-filter_complex_script', str(script), '-map', '[v]', '-map', '[a]',
           '-t', f'{duration:.9f}', '-r', str(fps), '-c:v', 'libx264', '-threads', '2',
           '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
           '-c:a', 'pcm_s16le' if pcm else 'aac']
    if not pcm:
        cmd += ['-b:a', '192k', '-movflags', '+faststart']
    cmd += [str(output)]
    output.with_suffix('.command.txt').write_text(shlex.join(cmd) + '\n')
    execute(cmd, cwd=output.parent)


def prepare_base(source: Path, work: Path, options: VerticalOptions) -> tuple[Path, list]:
    meta = probe(source)
    duration = math.ceil(meta['duration'] * options.fps) / options.fps
    normalized = work / 'normalized.mkv'
    inputs = ['-i', str(source)]
    if not meta['audio']:
        inputs += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    audio = '0:a' if meta['audio'] else '1:a'
    graph = (f'[0:v]setpts=PTS-STARTPTS,fps={options.fps},scale=trunc(iw/2)*2:trunc(ih/2)*2,'
             f'tpad=stop_mode=clone:stop_duration=1,trim=duration={duration}[v];'
             f'[{audio}]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,'
             f'apad,atrim=duration={duration}[a]')
    ffmpeg_render(inputs, graph, normalized, duration, options.fps, pcm=True)
    ranges = [(0., duration)]
    if options.remove_silence and meta['audio']:
        LOG.info('Detectando pausas: -30 dB, mínimo de 0,4 s.')
        log = execute(['ffmpeg', '-hide_banner', '-i', str(normalized), '-vn', '-af',
                       'silencedetect=noise=-30dB:d=0.4', '-f', 'null', '-']).stderr
        (work / 'silence.log').write_text(log)
        detected = speech_ranges(log, duration, options.fps)
        if not detected:
            LOG.warning('Vídeo inteiramente silencioso: preservando a duração original.')
        else:
            ranges = detected
        if ranges != [(0., duration)]:
            edited = work / 'edited.mkv'
            ffmpeg_render(['-i', str(normalized)], cut_filter(ranges, options.fps), edited,
                          sum(b-a for a, b in ranges), options.fps, pcm=True)
            return edited, ranges
    return normalized, ranges


def composition_filter(options: VerticalOptions, duration: float, secondary: bool,
                       secondary_audio: bool, ass: bool = False) -> str:
    options.validate()
    layout = 'full_screen' if (options.subtitles_only or not secondary) else options.layout
    def fit(stream, w, h, name):
        return (f'[{stream}:v]setpts=PTS-STARTPTS,fps={options.fps},'
                f'scale=iw*sar:ih,setsar=1,scale={w}:{h}:force_original_aspect_ratio=increase,'
                f'crop={w}:{h},setsar=1,trim=duration={duration:.9f}[{name}]')
    filters = []
    if layout == 'full_screen':
        filters.append(fit(0, 1080, 1920, 'picture'))
    elif layout == 'pip_overlay':
        filters += [fit(0, 1080, 1920, 'base'), fit(1, 324, 576, 'extra'),
                    '[base][extra]overlay=x=54:y=154:shortest=1[picture]']
    else:
        w, h = (1080, 960) if layout == 'split_vertical_top_bottom' else (540, 1920)
        filters += [fit(0, w, h, 'base'), fit(1, w, h, 'extra')]
        order = '[extra][base]' if options.invert else '[base][extra]'
        stack = 'vstack' if layout == 'split_vertical_top_bottom' else 'hstack'
        filters.append(f'{order}{stack}=inputs=2:shortest=1[picture]')
    filters.append('[picture]' + ('ass=captions.ass' if ass else 'null') + '[v]')
    filters.append(f'[0:a]apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[baseaudio]')
    if secondary and secondary_audio and options.secondary_volume > 0 and layout != 'full_screen':
        filters += [f'[1:a]asetpts=PTS-STARTPTS,aresample=48000,apad,atrim=duration={duration:.9f},'
                    f'volume={options.secondary_volume}[extraaudio]',
                    '[baseaudio][extraaudio]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[a]']
    else:
        filters.append('[baseaudio]anull[a]')
    return ';'.join(filters)


def run_vertical(base: str | Path, secondary: str | Path | None, options: VerticalOptions,
                 config: AppConfig, work: Path) -> Path:
    if options.subtitles_only:
        options.layout = 'full_screen'
        options.captions = True
        secondary = None

    options.validate()
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / 'options.json').write_text(json.dumps(asdict(options), indent=2))
    def ingest(value, name):
        if str(value).startswith(('https://', 'http://')):
            return download_social(str(value), work / name, config)
        path = Path(value).resolve()
        probe(path)
        return path
    LOG.info('Ingerindo vídeo base.')
    source = ingest(base, 'base')
    extra = ingest(secondary, 'secondary') if secondary and options.layout != 'full_screen' else None
    LOG.info('Preparando a timeline antes da transcrição.')
    edited, ranges = prepare_base(source, work, options)
    duration = sum(b-a for a, b in ranges)
    (work / 'timeline.json').write_text(json.dumps({'source': str(source), 'ranges': ranges,
                                                  'duration': duration}, indent=2))
    ass = False
    if options.captions and probe(source)['audio']:
        from .captions import BrainRotCaptionBuilder, KineticCaptionStyle, CaptionPlacement
        from .transcriber import WhisperTranscriber
        LOG.info('Transcrevendo áudio já editado com o motor existente.')
        audio = work / 'speech.wav'
        execute(['ffmpeg', '-y', '-i', str(edited), '-vn', '-ac', '1', '-ar', '16000', str(audio)])
        transcriber = WhisperTranscriber(replace(config, whisper_word_timestamps=True))
        try:
            transcript = transcriber.transcribe(audio, work / 'transcript.json')
        finally:
            transcriber.unload_model()
        style = KineticCaptionStyle.from_config(config)
        style.fps = options.fps
        if options.caption_font_size is not None:
            style.font_size = int(options.caption_font_size)
        if options.caption_force_uppercase is not None:
            style.force_uppercase = bool(options.caption_force_uppercase)
        if options.caption_font:
            style.font_name = options.caption_font
        if options.caption_color:
            style.primary_color = options.caption_color
        if options.caption_active_color:
            style.pulse_color = options.caption_active_color
        if options.caption_stroke_color:
            style.outline_color = options.caption_stroke_color
        if options.caption_stroke_width is not None:
            style.outline = float(options.caption_stroke_width)
        if options.caption_shadow is not None:
            style.shadow = float(options.caption_shadow)
        if options.caption_glow_blur is not None:
            style.keyword_glow_blur = float(options.caption_glow_blur)
        if options.caption_hook_scale is not None:
            style.hook_first_scale = int(options.caption_hook_scale)
        if options.caption_hook_color:
            style.hook_first_color = options.caption_hook_color
        if options.caption_max_words is not None:
            style.short_phrase_max_words = int(options.caption_max_words)
        if options.caption_max_chars is not None:
            style.max_chars = int(options.caption_max_chars)
        if options.caption_position:
            style.position = options.caption_position

        LOG.info(
            'Aplicando estilo de legendas: fonte=%s, tamanho=%d, uppercase=%s, cor=%s, '
            'destaque=%s, contorno=%s (%.1fpx), y=%.1f%%',
            style.font_name, style.font_size, style.force_uppercase, style.primary_color,
            style.pulse_color, style.outline_color, style.outline, options.caption_y
        )
        BrainRotCaptionBuilder(style).build(transcript, work / 'captions.ass', 0, duration,
            placement=CaptionPlacement(options.caption_y, options.safe_zones))
        ass = True
    elif options.captions:
        LOG.warning('Vídeo sem faixa de áudio: legendagem ignorada.')
    LOG.info('Compondo layout %s em 1080x1920 a %s FPS.', options.layout, options.fps)
    inputs = ['-i', str(edited)]
    if extra:
        inputs += ['-stream_loop', '-1', '-i', str(extra)]
    graph = composition_filter(options, duration, extra is not None,
                               bool(extra and probe(extra)['audio']), ass)
    output = work / 'vertical.mp4'
    ffmpeg_render(inputs, graph, output, duration, options.fps)
    LOG.info('Concluído: %s', output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.request.read_text())
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    run_vertical(payload['base'], payload.get('secondary'), VerticalOptions(**payload['options']),
                 load_config(), args.request.parent)


if __name__ == '__main__':
    main()
