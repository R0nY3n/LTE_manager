import sys
import os
from PyQt5.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu, QAction
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtCore import Qt

class TrayIconTest(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tray Icon Test")
        self.resize(300, 200)

        # Create a simple colored icon
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.green)
        self.icon = QIcon(pixmap)

        # Set window icon
        self.setWindowIcon(self.icon)

        # Create system tray icon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.icon)
        self.tray_icon.setToolTip("Tray Icon Test")

        # Create tray menu
        tray_menu = QMenu()

        # Add actions
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        tray_menu.addAction(exit_action)

        # Set tray menu
        self.tray_icon.setContextMenu(tray_menu)

        # Connect signals
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Show tray icon
        self.tray_icon.show()

        print("Tray icon visible:", self.tray_icon.isVisible())
        print("System tray available:", QSystemTrayIcon.isSystemTrayAvailable())

    def on_tray_icon_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()

    def closeEvent(self, event):
        """Handle close event"""
        # Remove tray icon
        if self.tray_icon.isVisible():
            self.tray_icon.hide()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = TrayIconTest()
    window.show()
    sys.exit(app.exec_())