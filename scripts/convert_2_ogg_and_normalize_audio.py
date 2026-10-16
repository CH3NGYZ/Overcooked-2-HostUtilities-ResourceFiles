import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

# ============================================================
# 配置
# ============================================================

# MOD 根目录，里面应当包含 Resources 文件夹
ROOT_DIR = Path(__file__).resolve().parent.parent

# 输入文件夹名称
INPUT_DIR_NAME = "Resources"

# 输出文件夹名称
OUTPUT_DIR_NAME = "Resources_OGG"

# 长音频目标响度
TARGET_LUFS = -12.0

# 短音频目标 RMS 响度
TARGET_RMS_DBFS = -14.0

# 短音频判断阈值，单位秒
SHORT_AUDIO_SECONDS = 3.0

# 峰值限制，单位 dBFS
MAX_PEAK_DBFS = -1.0

# 低于此 RMS 的文件视为静音，不增加音量
SILENCE_THRESHOLD_DBFS = -55.0

# 最大放大和衰减幅度
MAX_GAIN_DB = 18.0
MIN_GAIN_DB = -18.0

# OGG Vorbis 质量：0～10，越高音质越好、文件越大
# 4 通常适合游戏音效；可尝试 3～5
OGG_QUALITY = 4

# OGG 编码器输出采样率。None 表示保持原采样率
OUTPUT_SAMPLE_RATE = None

# 是否递归处理子目录
RECURSIVE = True


# ============================================================
# FFmpeg 工具
# ============================================================


def find_executable(name):
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"未找到 {name}，请安装 FFmpeg 并将其加入 PATH。")
    return path


FFMPEG = find_executable("ffmpeg")
FFPROBE = find_executable("ffprobe")


def run_command(args):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ============================================================
# 音频分析与响度处理
# ============================================================


def db_to_linear(db):
    return 10.0 ** (db / 20.0)


def measure_rms_dbfs(audio):
    """计算整段音频的 RMS 响度，单位 dBFS。"""
    audio = np.asarray(audio, dtype=np.float64)

    if audio.size == 0:
        return -120.0

    rms = np.sqrt(np.mean(audio**2))

    if rms <= 1e-12:
        return -120.0

    return 20.0 * math.log10(rms)


def measure_peak_dbfs(audio):
    """计算采样峰值，单位 dBFS。"""
    if audio.size == 0:
        return -120.0

    peak = float(np.max(np.abs(audio)))

    if peak <= 1e-12:
        return -120.0

    return 20.0 * math.log10(peak)


def limit_peak(audio):
    """按比例限制采样峰值，不进行硬削波。"""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    peak_limit = db_to_linear(MAX_PEAK_DBFS)

    if peak > peak_limit:
        audio = audio * (peak_limit / peak)

    return audio


def apply_gain(audio, gain_db):
    """调整音量并保护峰值。"""
    audio = audio.astype(np.float64) * db_to_linear(gain_db)
    return limit_peak(audio)


def normalize_audio(audio, sample_rate):
    """
    短音频使用 RMS，长音频使用 FFmpeg loudnorm。
    返回：处理后的音频、处理说明。
    """
    audio = np.asarray(audio, dtype=np.float64)

    if audio.ndim == 1:
        audio = audio.reshape(-1, 1)

    duration = len(audio) / sample_rate

    if duration <= 0:
        raise ValueError("音频长度为零")

    if not np.all(np.isfinite(audio)):
        raise ValueError("音频包含 NaN 或无穷值")

    rms_db = measure_rms_dbfs(audio)

    # 静音文件不放大，避免把底噪抬高
    if rms_db < SILENCE_THRESHOLD_DBFS:
        return limit_peak(audio), "静音保护"

    if duration < SHORT_AUDIO_SECONDS:
        gain_db = TARGET_RMS_DBFS - rms_db

        gain_db = min(gain_db, MAX_GAIN_DB)
        gain_db = max(gain_db, MIN_GAIN_DB)

        output = apply_gain(audio, gain_db)

        return output, (f"短音频 RMS={rms_db:.1f} dBFS，增益={gain_db:+.1f} dB")

    # 长音频转交 FFmpeg loudnorm 进行两遍响度标准化。
    # 这里先返回原始音频，长音频由后续 FFmpeg 处理。
    return None, "长音频使用 FFmpeg LUFS 标准化"


def analyze_loudness(audio_file):
    """使用 FFmpeg 分析长音频的响度。"""
    result = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio_file),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            (f"loudnorm=I={TARGET_LUFS}:TP={MAX_PEAK_DBFS}:LRA=7:print_format=json"),
            "-f",
            "null",
            "-",
        ]
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])

    matches = re.findall(
        r'\{\s*"input_i"\s*:.*?\}',
        result.stderr,
        flags=re.DOTALL,
    )

    if not matches:
        raise RuntimeError("无法提取 FFmpeg 响度分析结果")

    data = json.loads(matches[-1])

    required_fields = (
        "input_i",
        "input_tp",
        "input_lra",
        "input_thresh",
        "target_offset",
    )

    for field in required_fields:
        if field not in data:
            raise RuntimeError(f"缺少响度分析字段：{field}")

        if data[field] == "-inf":
            raise RuntimeError("音频没有有效响度")

    return data


# ============================================================
# 转换为 OGG
# ============================================================


def get_duration(audio_file):
    result = run_command(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(audio_file),
        ]
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])

    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def encode_short_audio(input_file, output_file, temp_wav):
    """短音频：Python RMS 标准化后，编码为 OGG。"""
    audio, sample_rate = sf.read(
        str(input_file),
        dtype="float64",
        always_2d=True,
    )

    normalized, message = normalize_audio(audio, sample_rate)

    if normalized is None:
        raise RuntimeError("短音频处理逻辑异常")

    sf.write(
        str(temp_wav),
        normalized,
        sample_rate,
        subtype="PCM_24",
        format="WAV",
    )

    encode_ogg(temp_wav, output_file, sample_rate)

    return message


def encode_long_audio(input_file, output_file, temp_wav=None):
    """长音频：FFmpeg 两遍 LUFS 标准化并编码为 OGG。"""
    measurements = analyze_loudness(input_file)

    audio_filter = (
        f"loudnorm=I={TARGET_LUFS}:"
        f"TP={MAX_PEAK_DBFS}:"
        "LRA=7:"
        f"measured_I={measurements['input_i']}:"
        f"measured_TP={measurements['input_tp']}:"
        f"measured_LRA={measurements['input_lra']}:"
        f"measured_thresh={measurements['input_thresh']}:"
        f"offset={measurements['target_offset']}:"
        "linear=true:print_format=summary"
    )

    args = [
        FFMPEG,
        "-hide_banner",
        "-nostats",
        "-y",
        "-i",
        str(input_file),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        audio_filter,
    ]

    if OUTPUT_SAMPLE_RATE is not None:
        args += ["-ar", str(OUTPUT_SAMPLE_RATE)]

    args += [
        "-c:a",
        "libvorbis",
        "-q:a",
        str(OGG_QUALITY),
        str(output_file),
    ]

    result = run_command(args)

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])

    return "长音频 LUFS 标准化"


def encode_ogg(input_file, output_file, sample_rate):
    """将 WAV 编码为 OGG Vorbis。"""
    args = [
        FFMPEG,
        "-hide_banner",
        "-nostats",
        "-y",
        "-i",
        str(input_file),
        "-map",
        "0:a:0",
        "-vn",
    ]

    if OUTPUT_SAMPLE_RATE is not None:
        args += ["-ar", str(OUTPUT_SAMPLE_RATE)]
    else:
        args += ["-ar", str(sample_rate)]

    args += [
        "-c:a",
        "libvorbis",
        "-q:a",
        str(OGG_QUALITY),
        str(output_file),
    ]

    result = run_command(args)

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])


def verify_ogg(path):
    """检查输出 OGG 是否可以正常解码。"""
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("OGG 输出文件不存在或为空")

    result = run_command([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"])

    if result.returncode != 0:
        raise RuntimeError("OGG 文件验证失败：" + result.stderr[-1000:])


# ============================================================
# 单文件处理
# ============================================================


def convert_file(input_file, output_file):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    duration = get_duration(input_file)

    # 临时 WAV 放在系统临时目录，不污染资源目录
    with tempfile.TemporaryDirectory(prefix="audio_convert_") as temp_dir:
        temp_wav = Path(temp_dir) / "normalized.wav"

        if duration < SHORT_AUDIO_SECONDS:
            message = encode_short_audio(
                input_file,
                output_file,
                temp_wav,
            )
        else:
            message = encode_long_audio(
                input_file,
                output_file,
            )

    verify_ogg(output_file)

    return message


# ============================================================
# 主程序
# ============================================================


def main():
    input_dir = ROOT_DIR / INPUT_DIR_NAME
    output_dir = ROOT_DIR / OUTPUT_DIR_NAME

    if not input_dir.is_dir():
        print(f"错误：找不到输入目录：{input_dir}")
        sys.exit(1)

    if input_dir.resolve() == output_dir.resolve():
        print("错误：输入目录和输出目录不能相同。")
        sys.exit(1)

    iterator = input_dir.rglob("*") if RECURSIVE else input_dir.glob("*")

    files = sorted(path for path in iterator if path.is_file() and path.suffix.lower() == ".wav")

    if not files:
        print(f"没有找到 WAV 文件：{input_dir}")
        return

    print(f"输入目录：{input_dir}")
    print(f"输出目录：{output_dir}")
    print(f"发现 WAV：{len(files)} 个")
    print(f"短音频目标 RMS：{TARGET_RMS_DBFS} dBFS")
    print(f"长音频目标 LUFS：{TARGET_LUFS} LUFS")
    print(f"峰值限制：{MAX_PEAK_DBFS} dBFS")
    print(f"OGG 质量：{OGG_QUALITY}")
    print()

    success = 0
    failed = 0
    total_input_size = 0
    total_output_size = 0

    for index, input_file in enumerate(files, 1):
        relative_path = input_file.relative_to(input_dir)
        # 仅去除文件名中的空格，保留文件夹名称和扩展名
        new_filename = relative_path.stem.replace(" ", "") + relative_path.suffix
        output_file = output_dir / relative_path.parent / new_filename
        output_file = output_file.with_suffix(".ogg")

        print(f"[{index}/{len(files)}] {relative_path}")

        try:
            total_input_size += input_file.stat().st_size

            message = convert_file(input_file, output_file)

            output_size = output_file.stat().st_size
            total_output_size += output_size

            print(f"  完成：{message}")
            print(f"  大小：{input_file.stat().st_size / 1024:.1f} KB -> {output_size / 1024:.1f} KB")

            success += 1

        except Exception as exc:
            print(f"  失败：{exc}")
            failed += 1

    print("\n========== 处理完成 ==========")
    print(f"成功：{success}")
    print(f"失败：{failed}")

    if total_input_size > 0:
        print(f"输入总大小：{total_input_size / 1024 / 1024:.2f} MB")
        print(f"输出总大小：{total_output_size / 1024 / 1024:.2f} MB")

        reduction = (1.0 - total_output_size / total_input_size) * 100.0

        print(f"体积变化：减少 {reduction:.1f}%")

    print(f"\nOGG 文件位于：{output_dir}")
    print("原始 WAV 未修改。")


if __name__ == "__main__":
    main()
