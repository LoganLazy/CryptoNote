#!/usr/bin/env python3
"""Build standalone Windows executables for CryptoNote (ARM64 and x86)."""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def build_exe(arch: str):
    """Build exe for given architecture."""
    print(f"\n{'='*60}")
    print(f"Building CryptoNote for {arch}")
    print(f"{'='*60}")

    dist_dir = f"dist/CryptoNote-{arch}"

    # Clean previous build
    if Path(dist_dir).exists():
        shutil.rmtree(dist_dir)

    # PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", f"CryptoNote-{arch}",
        "--distpath", "dist",
        "--buildpath", f"build-{arch}",
        "--specpath", f"build-{arch}",
        "app.py",
    ]
    if Path("icon.ico").exists():
        cmd.insert(3, "--icon=icon.ico")

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        print(f"✓ Build successful: {dist_dir}")
        return True
    else:
        print(f"✗ Build failed for {arch}")
        return False

def main():
    # Check dependencies
    try:
        import PyInstaller
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
        success &= build_exe("ARM64")
    elif current_arch.lower() in ("x86_64", "amd64"):
        success &= build_exe("x86_64")

    print("\n" + "="*60)
    if success:
        print("✓ All builds completed successfully!")
        print(f"Executables are in dist/ directory")
    else:
        print("✗ Some builds failed")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
