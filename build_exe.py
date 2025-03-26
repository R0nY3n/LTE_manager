import os
import sys
import subprocess
import shutil
import glob
import time
from PIL import Image, ImageDraw

def build_executable():
    """Build executable with PyInstaller"""
    print("Building LTE Manager executable...")

    # 尝试终止可能正在运行的LTE_Manager.exe进程
    try:
        print("尝试终止可能正在运行的LTE_Manager.exe进程...")
        subprocess.call("taskkill /F /IM LTE_Manager.exe", shell=True)
        # 等待进程完全终止
        time.sleep(2)
    except Exception as e:
        print(f"终止进程时出错 (这可能是正常的，如果进程不存在): {str(e)}")

    # 保存数据库文件
    db_backup_dir = "_db_backup_temp"
    db_files = []

    # 查找用户主目录下的数据库文件
    user_home = os.path.expanduser('~')
    lte_db_dir = os.path.join(user_home, '.LTE')
    user_db_path = os.path.join(lte_db_dir, 'lte_data.db')

    if os.path.exists("dist") or os.path.exists(user_db_path):
        print("备份数据库文件...")
        # 创建临时备份目录
        if not os.path.exists(db_backup_dir):
            os.makedirs(db_backup_dir)

        # 查找所有SQLite数据库文件
        dist_db_files = glob.glob("dist/*.db") if os.path.exists("dist") else []

        # 备份找到的dist目录中的文件
        for db_file in dist_db_files:
            db_filename = os.path.basename(db_file)
            backup_path = os.path.join(db_backup_dir, db_filename)
            print(f"备份数据库 (旧版位置): {db_filename}")
            shutil.copy2(db_file, backup_path)

        # 备份用户主目录中的数据库
        if os.path.exists(user_db_path):
            backup_path = os.path.join(db_backup_dir, 'lte_data.db')
            print(f"备份数据库 (用户目录): {user_db_path}")
            shutil.copy2(user_db_path, backup_path)

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
    datas=[
        ('default.png', '.'),
        ('running.png', '.'),
        ('error.png', '.'),
        ('README.md', '.'),
        ('incoming_call.py', '.'),
        ('audio.py', '.')
    ],
    hiddenimports=['PyQt5.sip', 'sounddevice', 'numpy'],
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
    icon='default.png',
)
    """

    with open("lte_manager.spec", "w") as f:
        f.write(spec_content)

    # Run PyInstaller
    print("Running PyInstaller...")
    try:
        # 等待一秒确保所有进程都释放了文件
        time.sleep(1)

        # 先检查目标EXE文件是否存在并可以被删除
        target_exe = os.path.join("dist", "LTE_Manager.exe")
        if os.path.exists(target_exe):
            try:
                # 尝试先重命名文件，这是检查文件是否被锁定的一种方法
                temp_name = os.path.join("dist", f"LTE_Manager_old_{int(time.time())}.exe")
                os.rename(target_exe, temp_name)
                # 然后删除重命名后的文件
                os.remove(temp_name)
                print("成功删除旧的可执行文件")
            except Exception as rename_error:
                print(f"无法删除旧的可执行文件: {str(rename_error)}")
                print("尝试使用Python 3.10来构建...")
                # 尝试使用Python 3.10，如果可用
                python310_path = r"G:\Python310\python.exe"
                if os.path.exists(python310_path):
                    subprocess.check_call([
                        python310_path,
                        "-m",
                        "PyInstaller",
                        "lte_manager.spec",
                        "--clean"
                    ])
                    print("使用Python 3.10构建完成")
                    build_success = True
                    return

        # 运行PyInstaller
        subprocess.check_call([
            sys.executable,
            "-m",
            "PyInstaller",
            "lte_manager.spec",
            "--clean"
        ])

        print("PyInstaller执行完成")
        build_success = True
    except Exception as e:
        print(f"构建过程中出错: {str(e)}")
        build_success = False

    # 恢复数据库文件
    if os.path.exists(db_backup_dir):
        print("恢复数据库文件...")
        backups = glob.glob(os.path.join(db_backup_dir, "*.db"))

        for backup_file in backups:
            db_filename = os.path.basename(backup_file)

            # 确保用户目录存在
            if not os.path.exists(lte_db_dir):
                os.makedirs(lte_db_dir)

            # 恢复到用户主目录
            user_db_path = os.path.join(lte_db_dir, db_filename)
            print(f"恢复数据库到用户目录: {user_db_path}")
            shutil.copy2(backup_file, user_db_path)

            # 为了向后兼容，也恢复到dist目录
            if os.path.exists("dist"):
                dist_path = os.path.join("dist", db_filename)
                print(f"恢复数据库到dist目录: {dist_path}")
                shutil.copy2(backup_file, dist_path)

        # 删除临时备份目录
        shutil.rmtree(db_backup_dir)

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
- 来电接听对话框
- PCM音频通话支持

## 使用方法

1. 连接LTE模块到计算机
2. 在设置选项卡中配置串口设置
3. 连接到模块
4. 使用电话和短信功能

## 系统托盘

应用程序可以最小化到系统托盘。双击托盘图标可以显示/隐藏主窗口。
""")

    if build_success:
        print("Executable built successfully!")
        print("You can find it in the 'dist' folder.")
    else:
        print("构建过程未完成，请检查错误信息。")

if __name__ == "__main__":
    build_executable()