import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .ui_main import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SignStamp")
    app.setFont(QFont("Malgun Gothic", 10))
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_document(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
