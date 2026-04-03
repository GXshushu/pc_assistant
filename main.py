import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    # 创建应用实例
    app = QApplication(sys.argv)
    app.setApplicationName("Personal PCAssistant")
    # 设置关闭窗口时不退出程序，由托盘菜单控制退出
    app.setQuitOnLastWindowClosed(False)

    # 创建并显示主窗口
    window = MainWindow()
    window.show()

    # 运行应用
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
