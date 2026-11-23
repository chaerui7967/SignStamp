import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .ui_main import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SignStamp")
    app.setFont(QFont("Malgun Gothic", 10))
    # exe 로 묶이면 _MEIPASS 아래, 소스 실행이면 프로젝트 루트의 icon.ico
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    icon = base / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_document(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
