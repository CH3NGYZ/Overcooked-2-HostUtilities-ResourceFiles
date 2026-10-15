import shutil
import subprocess
import tempfile
import os
from pathlib import Path

import numpy as np
import soundfile as sf
import pyloudnorm as pyln

# 修改为你的 MOD 根目录
ROOT_DIR = Path(r"../")

# 长音频目标响度
TARGET_LUFS = -18.0

# 短音频目标 RMS 响度
TARGET_RMS_DBFS = -20.0

# 小于此时长的音频按短音频处理
SHORT_AUDIO_SECONDS = 3.0

# 峰值限制
MAX_PEAK_DBFS = -1.0

# 低于此 RMS 的音频视为静音，不处理
SILENCE_THRESHOLD_DBFS = -55.0


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
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0

    if peak <= 1e-12:
        return -120.0

    return 20.0 * np.log10(peak)


def apply_gain(audio, gain_db):
    """应用增益，并限制采样峰值。"""
    gain = db_to_linear(gain_db)
    peak_limit = db_to_linear(MAX_PEAK_DBFS)

    audio = audio.astype(np.float64) * gain

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

    peak_db = measure_peak_dbfs(audio)

    if duration < SHORT_AUDIO_SECONDS:
        # 短音效：根据 RMS 调整，减少 LUFS 在极短音频上的不稳定性
        gain_db = TARGET_RMS_DBFS - rms_db

        # 限制最大放大量，避免把微弱底噪放得过大
        gain_db = min(gain_db, 18.0)

        # 限制最大衰减，防止音效被压得太小
        gain_db = max(gain_db, -18.0)

        output = apply_gain(audio, gain_db)

        return output, (f"短音频 RMS {rms_db:.1f} dBFS，增益 {gain_db:+.1f} dB")

    # 长音频：使用 LUFS 测量
    meter = pyln.Meter(sample_rate)

    # pyloudnorm 需要至少约 400ms 的有效音频
    loudness = meter.integrated_loudness(audio)

    if not np.isfinite(loudness):
        gain_db = TARGET_RMS_DBFS - rms_db
        gain_db = min(max(gain_db, -18.0), 18.0)
        output = apply_gain(audio, gain_db)
        return output, f"LUFS 不可用，回退 RMS，增益 {gain_db:+.1f} dB"

    gain_db = TARGET_LUFS - loudness

    # 限制最大增益，避免极小声文件被大幅放大
    gain_db = min(gain_db, 18.0)
    gain_db = max(gain_db, -18.0)

    output = apply_gain(audio, gain_db)

    return output, (f"LUFS {loudness:.1f}，增益 {gain_db:+.1f} dB")


def normalize_file(path):
    """处理单个 WAV，成功后覆盖原文件。"""
    audio, sample_rate = sf.read(
        str(path),
        dtype="float64",
        always_2d=True,
    )

    if audio.size == 0:
        raise ValueError("空文件")

    normalized, message = normalize_audio(audio, sample_rate)

    # 先在原目录创建临时文件，写入失败不影响原文件
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix=path.stem + "_",
            suffix=".wav",
            dir=path.parent,
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)

        sf.write(
            str(temp_path),
            normalized,
            sample_rate,
            subtype="PCM_24",
            format="WAV",
        )

        # 验证临时文件可以读取
        check, check_sr = sf.read(
            str(temp_path),
            dtype="float64",
            always_2d=True,
        )

        if check_sr != sample_rate or check.shape != normalized.shape:
            raise ValueError("输出验证失败")

        if not np.all(np.isfinite(check)):
            raise ValueError("输出存在 NaN 或无穷值")

        # 替换原文件
        os.replace(temp_path, path)
        temp_path = None

        return message

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    resources = ROOT_DIR / "Resources"

    if not resources.is_dir():
        print(f"找不到目录：{resources}")
        return

    files = sorted(path for path in resources.rglob("*") if path.is_file() and path.suffix.lower() == ".wav")

    if not files:
        print("没有找到 WAV 文件")
        return

    print(f"共找到 {len(files)} 个 WAV 文件\n")

    success = 0
    failed = 0

    for index, path in enumerate(files, 1):
        print(f"[{index}/{len(files)}] {path.relative_to(resources)}")

        try:
            message = normalize_file(path)
            print(f"  完成：{message}")
            success += 1
        except Exception as exc:
            print(f"  失败：{exc}")
            failed += 1

    print(f"\n处理结束：成功 {success} 个，失败 {failed} 个")


if __name__ == "__main__":
    main()
