# mural/utils/gpu_monitor.py
# GPL v3 — see LICENSE
"""Lightweight GPU VRAM query using vendor-specific CLI tools (nvidia-smi, rocm-smi)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class GpuMemory:
    total_mb: int
    used_mb: int
    free_mb: int
    vendor: str


def get_gpu_memory() -> GpuMemory | None:
    """Return VRAM stats from nvidia-smi or rocm-smi, or None if neither is available."""
    # NVIDIA
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).decode()
        parts = [p.strip() for p in out.strip().split(",")]
        if len(parts) == 3:
            return GpuMemory(
                total_mb=int(parts[0]),
                used_mb=int(parts[1]),
                free_mb=int(parts[2]),
                vendor="nvidia",
            )
    except Exception:
        pass

    # AMD via rocm-smi
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).decode()
        import json
        data = json.loads(out)
        # rocm-smi JSON: {"card0": {"VRAM Total Memory (B)": ..., "VRAM Total Used Memory (B)": ...}}
        for card_data in data.values():
            total_b = int(card_data.get("VRAM Total Memory (B)", 0))
            used_b = int(card_data.get("VRAM Total Used Memory (B)", 0))
            if total_b > 0:
                total_mb = total_b // (1024 * 1024)
                used_mb = used_b // (1024 * 1024)
                return GpuMemory(
                    total_mb=total_mb,
                    used_mb=used_mb,
                    free_mb=max(0, total_mb - used_mb),
                    vendor="amd",
                )
    except Exception:
        pass

    return None


def is_vram_exhausted(threshold_mb: int = 256) -> bool:
    """Return True if free VRAM is below *threshold_mb* MiB."""
    info = get_gpu_memory()
    if info is None:
        return False
    return info.free_mb < threshold_mb
