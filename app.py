"""
CoastTideX 桌面主程序启动入口
运行方式:
    python app.py
"""

import os
import sys

# 将项目根目录加入模块检索路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 关键机制：在 Windows 下必须在加载 PyQt6 前预先加载 pyfes C++ 动态链接库，避免 Qt6 运行时内存冲突
import pyfes
import rasterio

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from gui.main_window import MainWindow


def main():
    # 启用高 DPI 缩放支持
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("CoastTideX")
    app.setOrganizationName("CoastTideX-Team")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
