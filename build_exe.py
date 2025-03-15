import os
import sys
import subprocess
import shutil

def build_executable():
    """Build executable with PyInstaller"""
    print("Building LTE Manager executable...")

    # Check if PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Create spec file
    spec_content = """
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('phone_done.png', '.'), ('README.md', '.')],
    hiddenimports=['PyQt5.sip'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LTE_Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='phone_done.png',
)
    """

    with open("lte_manager.spec", "w") as f:
        f.write(spec_content)

    # Run PyInstaller
    print("Running PyInstaller...")
    subprocess.check_call([
        sys.executable,
        "-m",
        "PyInstaller",
        "lte_manager.spec",
        "--clean"
    ])

    # Create a simple README file (if it doesn't exist)
    if not os.path.exists("README.md"):
        with open("README.md", "w") as f:
            f.write("""# LTE Manager

一个用于管理LTE模块的应用程序，支持电话和短信功能。

## 功能

- 电话呼叫管理（拨打/接听电话）
- 短信管理（发送/接收/解码短信，包括中文）
- 模块配置和状态监控
- 串口配置

## 使用方法

1. 连接LTE模块到计算机
2. 在设置选项卡中配置串口设置
3. 连接到模块
4. 使用电话和短信功能

## 系统托盘

应用程序可以最小化到系统托盘。双击托盘图标可以显示/隐藏主窗口。
""")

    print("Executable built successfully!")
    print("You can find it in the 'dist' folder.")

if __name__ == "__main__":
    build_executable()