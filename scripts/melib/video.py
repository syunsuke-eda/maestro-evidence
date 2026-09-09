"""録画原本をレビュー・PR投稿に使える動画へ整える。

freeze 検出は fail-open にしている。判定に失敗したときは切り詰めずに原本を使う方が、
誤って操作の冒頭を落とすより証跡として安全なため。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .errors import MeError
from .proc import RunCommand

MAX_REVIEW_VIDEO_BYTES = 9_000_000
MAX_REVIEW_WIDTH = 886
# 1回目で収まらなかったときだけ落とす幅
FALLBACK_REVIEW_WIDTH = 720
CONTACT_SHEET_FRAMES = 12

# ffmpeg freezedetect の許容ノイズ。UI待機中の圧縮ノイズを「静止でない」と
# 誤判定しないための値
FREEZE_DETECT_NOISE = 0.003
FREEZE_DETECT_MIN_DURATION_SECONDS = 1.5
# 録画開始からこの秒数以内に始まる静止だけを「先頭の静止」とみなす
LEADING_FREEZE_START_TOLERANCE_SECONDS = 1.5
# 静止終端ぎりぎりで切ると直後の操作の立ち上がりが欠けるため残す余裕
LEADING_TRIM_SAFETY_MARGIN_SECONDS = 1.0
MAX_LEADING_TRIM_SECONDS = 90.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    run_command: RunCommand = subprocess.run,
) -> "subprocess.CompletedProcess":
    result = run_command(list(command), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise MeError(
            "{0} による動画処理に失敗しました: {1}".format(
                Path(command[0]).name, (result.stderr or "").strip()
            )
        )
    return result


def probe_video(path: Path, *, run_command: RunCommand = subprocess.run) -> Dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise MeError("動画が存在しないか0 byteです: {0}".format(path))
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        run_command=run_command,
    )
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        container = payload["format"]
        duration = float(container["duration"])
        size = int(container["size"])
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise MeError("ffprobe結果を解釈できません: {0}".format(path)) from error
    if not math.isfinite(duration) or duration <= 0:
        raise MeError("動画durationが不正です: {0}".format(duration))
    if width <= 0 or height <= 0:
        raise MeError("動画解像度が不正です: {0}x{1}".format(width, height))
    return {
        "codec": str(stream.get("codec_name", "")),
        "pixel_format": str(stream.get("pix_fmt", "")),
        "width": width,
        "height": height,
        "average_frame_rate": str(stream.get("avg_frame_rate", "")),
        "duration_seconds": round(duration, 3),
        "size_bytes": size,
        "sha256": sha256_file(path),
    }


def detect_leading_freeze_trim_seconds(
    video: Path, duration: float, *, run_command: RunCommand = subprocess.run
) -> "tuple":
    """先頭の静止区間の切り詰め秒数を推定する。

    判定対象は freezedetect が検出した最初の静止区間だけで、後続の静止区間は
    連結しない。UI操作の録画は短い遷移を挟んで静止が続くのが普通であり、
    連結すると本編まで削ってしまうため。解析失敗・先頭が静止でない・切り詰め量が
    過大なときは 0 を返す。戻り値は (秒数, status)。
    """

    try:
        result = run_command(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(video),
                "-vf",
                "freezedetect=n={0}:d={1}".format(
                    FREEZE_DETECT_NOISE, FREEZE_DETECT_MIN_DURATION_SECONDS
                ),
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        return 0.0, "skipped:freezedetectを実行できませんでした({0})".format(error)
    stderr = result.stderr or ""
    starts = [float(value) for value in re.findall(r"freeze_start:\s*([0-9.]+)", stderr)]
    ends = [float(value) for value in re.findall(r"freeze_end:\s*([0-9.]+)", stderr)]
    if result.returncode != 0 and not starts:
        return 0.0, "skipped:freezedetectの解析に失敗しました"
    if not starts or starts[0] > LEADING_FREEZE_START_TOLERANCE_SECONDS:
        return 0.0, "not_needed"
    if not ends:
        return 0.0, "skipped:先頭静止区間の終端を検出できませんでした"
    trim = ends[0] - LEADING_TRIM_SAFETY_MARGIN_SECONDS
    if trim <= 0.0:
        return 0.0, "not_needed"
    # 上限へ丸めると「どれだけ本編を削ったか」が記録から分からなくなるため、
    # 過大な場合は切り詰め自体を諦める
    trim_limit = min(MAX_LEADING_TRIM_SECONDS, duration - 5.0)
    if trim > trim_limit:
        return 0.0, "skipped:切り詰め量が動画長に対して大きすぎます"
    return round(trim, 3), "applied"


def _target_bitrate(duration: float) -> int:
    return max(250_000, min(1_600_000, int((MAX_REVIEW_VIDEO_BYTES * 8 * 0.88) / duration)))


def transcode_command(source: Path, output: Path, duration: float, width: int) -> list:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        "scale=w='min({0},iw)':h=-2,fps=30".format(width),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-b:v",
        str(_target_bitrate(duration)),
        "-maxrate",
        str(_target_bitrate(duration)),
        "-bufsize",
        str(_target_bitrate(duration) * 2),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        "-y",
        str(output),
    ]


def extract_images(
    video: Path,
    contact_sheet: Path,
    final_frame: Path,
    duration: float,
    *,
    run_command: RunCommand = subprocess.run,
) -> None:
    contact_sheet.parent.mkdir(parents=True, exist_ok=True)
    sample_fps = max(CONTACT_SHEET_FRAMES / duration, 0.01)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            "fps={0:.8f},scale=240:-2,tile=4x3:padding=2:margin=2".format(sample_fps),
            "-frames:v",
            "1",
            "-y",
            str(contact_sheet),
        ],
        run_command=run_command,
    )
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-sseof",
            "-0.5",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-y",
            str(final_frame),
        ],
        run_command=run_command,
    )


def prepare_review_video(
    source: Path,
    output: Path,
    contact_sheet: Path,
    final_frame: Path,
    *,
    trim_leading_freeze: bool = True,
    run_command: RunCommand = subprocess.run,
) -> Dict[str, Any]:
    source.chmod(0o600)
    source_probe = probe_video(source, run_command=run_command)
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = float(source_probe["duration_seconds"])

    with tempfile.TemporaryDirectory(prefix="maestro-evidence-video-") as temporary:
        working = source
        trimmed_seconds = 0.0
        trim_status = "skipped:disabled"
        if trim_leading_freeze:
            trimmed_seconds, trim_status = detect_leading_freeze_trim_seconds(
                source, duration, run_command=run_command
            )
            if trimmed_seconds > 0.0:
                trimmed = Path(temporary) / "trimmed-input.mp4"
                # -i の後に -ss を置き、切り詰め位置以降を1度decode/encodeし直すことで
                # keyframe単位の粗いseekによる誤差を避ける
                _run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source),
                        "-ss",
                        "{0:.3f}".format(trimmed_seconds),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "18",
                        "-an",
                        "-y",
                        str(trimmed),
                    ],
                    run_command=run_command,
                )
                working = trimmed
                duration = float(probe_video(trimmed, run_command=run_command)["duration_seconds"])

        _run(transcode_command(working, output, duration, MAX_REVIEW_WIDTH), run_command=run_command)
        review = probe_video(output, run_command=run_command)
        width_used = MAX_REVIEW_WIDTH
        if review["size_bytes"] > MAX_REVIEW_VIDEO_BYTES:
            _run(
                transcode_command(working, output, duration, FALLBACK_REVIEW_WIDTH),
                run_command=run_command,
            )
            review = probe_video(output, run_command=run_command)
            width_used = FALLBACK_REVIEW_WIDTH

    output.chmod(0o644)
    if review["codec"] != "h264" or review["pixel_format"] != "yuv420p":
        raise MeError("投稿用動画がH.264/yuv420pではありません")
    if review["size_bytes"] > MAX_REVIEW_VIDEO_BYTES:
        raise MeError("投稿用動画が{0} byteを超えています".format(MAX_REVIEW_VIDEO_BYTES))
    extract_images(
        output,
        contact_sheet,
        final_frame,
        float(review["duration_seconds"]),
        run_command=run_command,
    )
    return {
        "status": "pass",
        "source": source_probe,
        "review": review,
        "encoded_width_limit": width_used,
        "trimmed_leading_seconds": trimmed_seconds,
        "trim_status": trim_status,
        "contact_sheet": contact_sheet.name,
        "final_frame": final_frame.name,
    }
