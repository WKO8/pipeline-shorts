import os
import subprocess
import logging
import random
import tempfile
from pathlib import Path
from src.utils import run_cmd

LOGGER = logging.getLogger("PostZoom")

def apply_cinematic_zoom(
    input_video: str | Path,
    output_video: str | Path,
    tracking_points: list = None,
    eye_line_ratio: float = 0.30
):
    """
    Applies Seamless Cinematic Face-Lock Zoom.
    Uses target eye_line_ratio to maintain vertical alignment during zoom.
    """
    input_video = Path(input_video).resolve()
    output_video = Path(output_video).resolve()
    
    if not input_video.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {input_video}")

    # 1. Get Duration
    dur_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(input_video)
    ]
    duration = float(subprocess.check_output(dur_cmd).decode("utf-8").strip())

    # 2. Generate Randomized Zoom Timeline
    points = []
    t = 0.0
    z_max = 1.35
    
    while t < duration:
        points.append((t, 1.0))
        t += random.uniform(3.0, 10.0)
        if t >= duration: break
        points.append((t, 1.0))
        t += 0.5
        if t >= duration: break
        points.append((t, z_max))
        t += random.uniform(1.0, 3.0)
        if t >= duration: break
        points.append((t, z_max))
        if random.random() < 0.8:
            points.append((t + 0.001, 1.0))
            t += 0.001
        else:
            t_out = random.uniform(0.3, 0.5)
            t += t_out
            if t >= duration: break
            points.append((t, 1.0))
    
    if not points or points[-1][0] < duration:
        points.append((duration, 1.0))

    # 3. Helper to build expressions
    def build_expr(pts, default_val):
        parts = []
        for i in range(len(pts) - 1):
            t0, v0 = pts[i]; t1, v1 = pts[i+1]
            dt = max(0.0001, t1 - t0)
            prog = f"((t-{t0:.3f})/{dt:.3f})"
            if dt > 0.01:
                smooth = f"({prog}*{prog}*(3-2*{prog}))"
                lerp = f"({v0:.3f}+({v1-v0:.3f})*{smooth})"
            else:
                lerp = f"{v1:.3f}"
            cond = f"gte(t,{t0:.3f})*lt(t,{t1:.3f})" if i < len(pts)-2 else f"gte(t,{t0:.3f})*lte(t,{t1:.3f})"
            parts.append(f"{cond}*{lerp}")
        return f"({'+'.join(parts)})" if parts else f"{default_val:.3f}"

    z_expr = build_expr(points, 1.0)
    fx_expr = "0.5"
    fy_expr = str(eye_line_ratio)
    
    # Calculate vertical offset anchor based on eye_line_ratio
    # 1920 * eye_line_ratio
    v_anchor = int(1920 * eye_line_ratio)

    if tracking_points:
        step = max(1, len(tracking_points) // 400) 
        sampled = tracking_points[::step]
        if sampled[-1].t < tracking_points[-1].t:
            sampled.append(tracking_points[-1])
            
        fx_pts = [(p.t, p.fx if p.fx is not None else 0.5) for p in sampled]
        fy_pts = [(p.t, p.fy if p.fy is not None else eye_line_ratio) for p in sampled]
        fx_expr = build_expr(fx_pts, 0.5)
        fy_expr = build_expr(fy_pts, eye_line_ratio)

    # 4. Create Filter Script File
    # Centering math: iw*fx_expr - (target_w/2)
    # Vertical math: ih*fy_expr - (target_h * eye_line_ratio)
    vf_chain = (
        f"scale='1080*{z_expr}':-1:eval=frame,"
        f"crop=1080:1920:'iw*{fx_expr}-540':'ih*{fy_expr}-{v_anchor}',"
        "setsar=1"
    )
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.vf', dir=output_video.parent, delete=False) as f:
        f.write(vf_chain)
        vf_script_path = f.name

    cmd = [
        "ffmpeg", "-y", "-threads", "4", "-v", "warning",
        "-i", str(input_video),
        "-filter_script:v", vf_script_path,
        "-c:v", "h264_nvenc", "-preset", "p5",
        "-b:v", "18M", "-maxrate", "18M", "-bufsize", "20M",
        "-c:a", "copy",
        str(output_video)
    ]

    LOGGER.info(f"Applying Precision Face-Lock Zoom... (Target Height: {eye_line_ratio*100:.0f}%)")
    try:
        try:
            run_cmd(cmd, check=True)
        except RuntimeError as exc:
            if "nvenc" in str(exc).lower() or "encoder" in str(exc).lower():
                LOGGER.warning("NVENC failed during zoom, falling back to libx264: %s", exc)
                fallback_cmd = [
                    "ffmpeg", "-y", "-threads", "4", "-v", "warning",
                    "-i", str(input_video),
                    "-filter_script:v", vf_script_path,
                    "-c:v", "libx264", "-preset", "fast",
                    "-crf", "18",
                    "-c:a", "copy",
                    str(output_video)
                ]
                run_cmd(fallback_cmd, check=True)
            else:
                raise
        LOGGER.info(f"Zoom processing complete: {output_video.name}")
    finally:
        if os.path.exists(vf_script_path):
            try:
                os.remove(vf_script_path)
            except Exception:
                LOGGER.debug("Preserved zoom filter script: %s", vf_script_path)
