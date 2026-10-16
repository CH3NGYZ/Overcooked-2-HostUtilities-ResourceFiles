import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf

# 修改为你的 MOD 根目录
ROOT_DIR = Path(__file__).resolve().parent.parent

# 长音频目标响度
TARGET_LUFS = -18.0

# 短音频目标 RMS 响度
TARGET_RMS_DBFS = -20.0

# 小于此时长的音频按短音频处理
SHORT_AUDIO_SECONDS = 1.0

# 峰值限制
MAX_PEAK_DBFS = -1.0

# 低于此 RMS 的音频视为静音，不处理
SILENCE_THRESHOLD_DBFS = -55.0

# OGG Vorbis 编码质量：0～10，越高质量越好、文件通常越大
OGG_QUALITY = 6

# 输出采样率
OUTPUT_SAMPLE_RATE = 44100

# 是否处理 WAV 文件
PROCESS_WAV = True

# 是否处理 OGG 文件
PROCESS_OGG = True

# 是否在输出验证成功后删除原始 WAV 文件
# True：WAV 转换为 OGG 后删除原 WAV
# False：保留原 WAV
DELETE_ORIGINAL_WAV = False


def db_to_linear(db):
    return 10 ** (db / 20.0)


def measure_rms_dbfs(audio):
    """计算多声道音频的整体 RMS，返回 dBFS。"""
    audio = np.asarray(audio, dtype=np.float64)

    if audio.size == 0:
        return -120.0

    rms = np.sqrt(np.mean(audio**2))

    if rms <= 1e-12:
        return -120.0

    return 20.0 * np.log10(rms)


def measure_peak_dbfs(audio):
    """计算采样峰值，返回 dBFS。"""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0

    if peak <= 1e-12:
        return -120.0

    return 20.0 * np.log10(peak)


def apply_gain(audio, gain_db):
    """应用增益，并限制采样峰值。"""
    audio = audio.astype(np.float64) * db_to_linear(gain_db)
    peak_limit = db_to_linear(MAX_PEAK_DBFS)

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0

    if peak > peak_limit:
        audio *= peak_limit / peak

    return audio


def normalize_audio(audio, sample_rate):
    """长音频使用 LUFS，短音频使用 RMS。"""
    duration = len(audio) / sample_rate

    if duration <= 0:
        return audio, "空音频"

    rms_db = measure_rms_dbfs(audio)

    # 静音或接近静音的文件不做增益处理
    if rms_db < SILENCE_THRESHOLD_DBFS:
        return audio, "静音跳过"

    if duration < SHORT_AUDIO_SECONDS:
        gain_db = TARGET_RMS_DBFS - rms_db
        gain_db = min(max(gain_db, -18.0), 18.0)

        output = apply_gain(audio, gain_db)

        return output, (f"短音频 RMS {rms_db:.1f} dBFS，增益 {gain_db:+.1f} dB")

    # 长音频使用 LUFS
    try:
        meter = pyln.Meter(sample_rate)
        loudness = meter.integrated_loudness(audio)
    except Exception:
        loudness = float("nan")

    if not np.isfinite(loudness):
        gain_db = TARGET_RMS_DBFS - rms_db
        gain_db = min(max(gain_db, -18.0), 18.0)

        output = apply_gain(audio, gain_db)

        return output, (f"LUFS 不可用，回退 RMS，增益 {gain_db:+.1f} dB")

    gain_db = TARGET_LUFS - loudness
    gain_db = min(max(gain_db, -18.0), 18.0)

    output = apply_gain(audio, gain_db)

    return output, (f"LUFS {loudness:.1f}，增益 {gain_db:+.1f} dB")


def get_ffmpeg():
    """获取 FFmpeg 路径。"""
    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，请安装 FFmpeg 并将其添加到 PATH")

    return ffmpeg


def get_ffprobe():
    """获取 FFprobe 路径。"""
    ffprobe = shutil.which("ffprobe")

    if not ffprobe:
        raise RuntimeError("未找到 FFprobe，请安装完整的 FFmpeg 套件并将其添加到 PATH")

    return ffprobe


def verify_vorbis(path, ffprobe):
    """验证输出文件的实际编码确实为 Vorbis。"""
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    import json

    data = json.loads(result.stdout)
    streams = data.get("streams", [])

    if not streams:
        raise ValueError("输出文件中没有有效音频流")

    stream = streams[0]

    if stream.get("codec_name") != "vorbis":
        raise ValueError(f"输出编码错误：{stream.get('codec_name')}，预期为 vorbis")

    if int(stream.get("sample_rate", 0)) != OUTPUT_SAMPLE_RATE:
        raise ValueError("输出采样率验证失败")

    if int(stream.get("channels", 0)) < 1:
        raise ValueError("输出声道数验证失败")


def encode_ogg(audio, sample_rate, output_path, ffmpeg, ffprobe):
    """将处理后的音频统一编码为 OGG Vorbis。"""
    with tempfile.NamedTemporaryFile(
        suffix=".wav",
        delete=False,
    ) as temp:
        wav_path = Path(temp.name)

    try:
        # 先写入临时 WAV，避免直接向 FFmpeg 传递浮点数组
        sf.write(
            str(wav_path),
            audio,
            sample_rate,
            subtype="PCM_24",
            format="WAV",
        )

        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav_path),
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-vn",
                "-sn",
                "-dn",
                "-ar",
                str(OUTPUT_SAMPLE_RATE),
                "-c:a",
                "libvorbis",
                "-q:a",
                str(OGG_QUALITY),
                "-f",
                "ogg",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        # 确认编码器及容器输出符合要求
        verify_vorbis(output_path, ffprobe)

        # 实际解码测试，确保音频不是空流或损坏文件
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-v",
                "error",
                "-i",
                str(output_path),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    except subprocess.CalledProcessError as exc:
        error = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(f"OGG Vorbis 编码或解码验证失败：{error}") from exc

    finally:
        wav_path.unlink(missing_ok=True)


def normalize_file(path, ffmpeg, ffprobe):
    """处理单个 WAV 或 OGG，输出 OGG Vorbis。"""
    suffix = path.suffix.lower()

    if suffix not in {".wav", ".ogg"}:
        raise ValueError(f"不支持的输入格式：{suffix}")

    audio, sample_rate = sf.read(
        str(path),
        dtype="float64",
        always_2d=True,
    )

    if audio.size == 0:
        raise ValueError("空文件")

    normalized, message = normalize_audio(audio, sample_rate)

    # 所有输出统一为 OGG
    output_path = path.with_suffix(".ogg")

    # 目标文件已存在时不覆盖，避免误删其他文件
    if output_path.exists() and output_path.resolve() != path.resolve():
        raise FileExistsError(f"目标文件已存在，为避免覆盖而跳过：{output_path.name}")

    temp_path = None

    try:
        # 临时文件放在目标文件所在目录，保证替换操作在同一文件系统
        with tempfile.NamedTemporaryFile(
            prefix=path.stem + "_",
            suffix=".ogg",
            dir=path.parent,
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)

        encode_ogg(
            normalized,
            sample_rate,
            temp_path,
            ffmpeg,
            ffprobe,
        )

        # 再次验证 FFmpeg 可以读取输出文件
        check, check_sr = sf.read(
            str(temp_path),
            dtype="float64",
            always_2d=True,
        )

        if len(check) == 0:
            raise ValueError("输出音频为空")

        if not np.all(np.isfinite(check)):
            raise ValueError("输出存在 NaN 或无穷值")

        if check.shape[1] != normalized.shape[1]:
            raise ValueError("输出声道数验证失败")

        if check_sr != OUTPUT_SAMPLE_RATE:
            raise ValueError("输出采样率验证失败")

        # 如果是 OGG，直接替换原文件
        if suffix == ".ogg":
            os.replace(temp_path, path)
            temp_path = None

        else:
            # WAV 转 OGG：先确保 OGG 完整写入，再处理原 WAV
            os.replace(temp_path, output_path)
            temp_path = None

            if DELETE_ORIGINAL_WAV:
                path.unlink()

        return f"{message}；输出 OGG Vorbis，{OUTPUT_SAMPLE_RATE} Hz"

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    resources = ROOT_DIR / "Resources"

    if not resources.is_dir():
        print(f"找不到目录：{resources}")
        return

    extensions = set()

    if PROCESS_WAV:
        extensions.add(".wav")

    if PROCESS_OGG:
        extensions.add(".ogg")

    if not extensions:
        print("WAV 和 OGG 均未启用，无需处理")
        return

    files = sorted(path for path in resources.rglob("*") if path.is_file() and path.suffix.lower() in extensions)

    if not files:
        print("没有找到符合条件的音频文件")
        return

    try:
        ffmpeg = get_ffmpeg()
        ffprobe = get_ffprobe()
    except RuntimeError as exc:
        print(exc)
        return

    print(f"共找到 {len(files)} 个音频文件\n")

    success = 0
    failed = 0

    for index, path in enumerate(files, 1):
        relative_path = path.relative_to(resources)

        print(f"[{index}/{len(files)}] {relative_path}")

        try:
            message = normalize_file(path, ffmpeg, ffprobe)
            print(f"  完成：{message}")
            success += 1

        except Exception as exc:
            print(f"  失败：{exc}")
            failed += 1

    print(f"\n处理结束：成功 {success} 个，失败 {failed} 个")


if __name__ == "__main__":
    main()
