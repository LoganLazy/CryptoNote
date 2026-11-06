#!/usr/bin/env python3
"""Build standalone Windows executables for CryptoNote."""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def build_exe(arch):
    """Build exe for given architecture."""
    print("\n" + "="*60)
    print(f"Building CryptoNote for {arch}")
    print("="*60)

    # 实际产物：dist/CryptoNote-{arch}.exe + build-{arch} 工作目录。
    # 注意：不要删 dist/CryptoNote-{arch}——那是目录名，PyInstaller
    # 用 --distpath dist 时从不创建它，删它等于什么都没清理。
    exe_path = Path(f"dist/CryptoNote-{arch}.exe")
    work_dir = Path(f"build-{arch}")

    # Clean previous build outputs
    if exe_path.exists():
        exe_path.unlink()
    if work_dir.exists():
        shutil.rmtree(work_dir)

    # PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", f"CryptoNote-{arch}",
        "--icon", "icon.ico",
        "--distpath", "dist",
        "--workpath", f"build-{arch}",
        "--specpath", f"build-{arch}",
        "app.py",
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        print(f"[SUCCESS] Build successful: {exe_path}")
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024*1024)
            print(f"Executable size: {size_mb:.1f} MB")
        return True
    else:
        print(f"[FAILED] Build failed for {arch}")
        return False

def main():
    # Check dependencies
    try:
        import PyInstaller
        print("PyInstaller found")
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    # Detect current architecture
    import platform
    current_arch = platform.machine()
    print(f"Current system: {platform.system()} {current_arch}")

    success = True

    # Build for current architecture
    if current_arch.lower() in ("arm64", "aarch64"):
        success = build_exe("ARM64")
    elif current_arch.lower() in ("x86_64", "amd64"):
        success = build_exe("x86_64")
    else:
        print(f"Unknown architecture: {current_arch}")
        return 1

    print("\n" + "="*60)
    if success:
        print("[SUCCESS] All builds completed!")
        print("Executables are in dist/ directory")
    else:
        print("[FAILED] Some builds failed")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
