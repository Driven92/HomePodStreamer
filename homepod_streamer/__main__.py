# -*- coding: utf-8 -*-
"""包入口：供 `python -m homepod_streamer` 与 PyInstaller 打包后的 exe 使用。"""
from homepod_streamer.main import main

if __name__ == "__main__":
    main()
