import os
import sys

def build():
    # 构建命令
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--windows-disable-console",  # 隐藏控制台
        "--include-package=PySide6",
        "--include-package=loguru",
        "--include-package=psutil",
        "--include-package=send2trash",
        "--include-package=concurrent",
        "--include-package=GPUtil",
        "--include-package=darkdetect",
        "--include-package=ui",
        "--include-package=core",
        "main.py"
    ]
    
    print("执行构建命令:")
    print(" ".join(cmd))
    os.system(" ".join(cmd))

if __name__ == "__main__":
    build()
