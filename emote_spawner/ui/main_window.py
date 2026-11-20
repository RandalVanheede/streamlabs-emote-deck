"""Main window: an on-screen stream deck of emotes. Click a key to spawn it on the overlay."""

import random
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from emote_spawner.services.library import ANIMATIONS, LibraryError

TILE = QSize(128, 136)
ADD_KEY = "__add__"  # UserRole marker for the trailing "+" key

STYLE = """
QMainWindow, QWidget#central { background: #141518; }
QLabel { color: #9a9ca3; }
QLabel#title { color: #f2f3f5; font-size: 20px; font-weight: 600; }
QLabel#ok { color: #3ddc84; } QLabel#err { color: #ff5c5c; }
QLineEdit { background: #1e2024; color: #c9cbd1; border: 1px solid #2b2e34;
            border-radius: 8px; padding: 6px 10px; }
QPushButton { background: #24262c; color: #f2f3f5; border: none; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #2e3138; }
QPushButton:pressed, QPushButton#accent { background: palette(highlight); }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { background: #202227; color: #d6d8dd; border: 1px solid #2b2e34;
                    border-radius: 16px; padding-top: 14px; }
QListWidget::item:hover { background: #2a2d33; border-color: #3a3e46; }
QListWidget::item:selected { background: #2a2d33; border: 2px solid palette(highlight); }
QStatusBar { color: #6f7279; }
"""


class MainWindow(QMainWindow):
    def __init__(self, config, server):
        super().__init__()
        self.config = config
        self.server = server
        self.library = server.library

        self.setWindowTitle("Emote Deck")
        self.resize(820, 620)
        self.setStyleSheet(STYLE)

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 8)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(QLabel("Emote Deck", objectName="title"))
        if server.running:
            header.addWidget(QLabel("● Live", objectName="ok"))
        else:
            header.addWidget(QLabel(f"● Server failed (port {config.port} in use?)", objectName="err"))
        header.addStretch()
        btn_random = QPushButton("🎲  Random", objectName="accent")
        btn_random.clicked.connect(self._spawn_random)
        header.addWidget(btn_random)
        layout.addLayout(header)

        # Overlay URL to paste into a Streamlabs/OBS Browser Source
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Browser Source"))
        self.txt_overlay = QLineEdit(f"{server.base_url}/overlay", readOnly=True)
        url_row.addWidget(self.txt_overlay)
        btn_copy = QPushButton("Copy")
        btn_copy.clicked.connect(self._copy_url)
        url_row.addWidget(btn_copy)
        btn_folder = QPushButton("Open Folder")
        btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.library_dir))))
        url_row.addWidget(btn_folder)
        layout.addLayout(url_row)

        # The deck
        self.deck = QListWidget()
        self.deck.setViewMode(QListWidget.ViewMode.IconMode)
        self.deck.setIconSize(QSize(88, 88))
        self.deck.setSpacing(12)
        self.deck.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.deck.setMovement(QListWidget.Movement.Static)
        self.deck.setUniformItemSizes(True)
        self.deck.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deck.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.deck.itemClicked.connect(self._on_key)
        self.deck.customContextMenuRequested.connect(self._key_menu)
        layout.addWidget(self.deck)

        self.statusBar().showMessage("Click a key to spawn · right-click for sound, animation, remove")
        self._refresh()

    def _refresh(self):
        self.deck.clear()
        for img in self.library.list_images():
            name = img["filename"]
            label = ("🔊 " if img["sound"] else "") + Path(name).stem
            item = QListWidgetItem(QIcon(str(self.library.library_dir / name)), label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(TILE)
            item.setToolTip(f"{name}\nSound: {img['sound'] or 'none'}\nAnimation: {img['animation']}")
            self.deck.addItem(item)
        add = QListWidgetItem(QIcon.fromTheme("list-add"), "＋ Add emotes")
        add.setData(Qt.ItemDataRole.UserRole, ADD_KEY)
        add.setSizeHint(TILE)
        self.deck.addItem(add)

    def _on_key(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name == ADD_KEY:
            self._add_images()
        else:
            self._spawn(name)
        QTimer.singleShot(180, self.deck.clearSelection)  # brief "pressed" flash, like a hardware key

    def _key_menu(self, pos):
        item = self.deck.itemAt(pos)
        name = item and item.data(Qt.ItemDataRole.UserRole)
        if not name or name == ADD_KEY:
            return
        menu = QMenu(self)
        menu.addAction("Set sound…", lambda: self._set_sound(name))
        if self.library.sound_for(name):
            menu.addAction("Remove sound", lambda: (self.library.remove_sound(name), self._refresh()))
        anim_menu = menu.addMenu("Animation")
        current = self.library.get_animation(name)
        for anim in ANIMATIONS:
            act = anim_menu.addAction(anim.replace("-", " ").capitalize(), lambda a=anim: self._set_animation(name, a))
            act.setCheckable(True)
            act.setChecked(anim == current)
        menu.addSeparator()
        menu.addAction("Remove emote", lambda: self._remove(name))
        menu.exec(self.deck.viewport().mapToGlobal(pos))

    def _spawn(self, name: str):
        self.server.spawn(name)
        self.statusBar().showMessage(f"Spawned {name}", 2500)

    def _spawn_random(self):
        images = self.library.list_images()
        if images:
            self._spawn(random.choice(images)["filename"])

    def _copy_url(self):
        QApplication.clipboard().setText(self.txt_overlay.text())
        self.statusBar().showMessage("Overlay URL copied", 2500)

    def _add_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add Emotes", "", "Images (*.png *.gif *.jpg *.jpeg *.webp)")
        errors = []
        for f in files:
            try:
                self.library.add_image(Path(f))
            except LibraryError as e:
                errors.append(f"{Path(f).name}: {e}")
        if errors:
            QMessageBox.warning(self, "Some images were skipped", "\n".join(errors))
        self._refresh()

    def _set_sound(self, name: str):
        f, _ = QFileDialog.getOpenFileName(self, f"Sound for {name}", "", "Audio (*.mp3 *.wav *.ogg)")
        if not f:
            return
        try:
            self.library.set_sound(name, Path(f))
        except LibraryError as e:
            QMessageBox.warning(self, "Sound not set", str(e))
        self._refresh()

    def _set_animation(self, name: str, animation: str):
        self.library.set_animation(name, animation)
        self._refresh()
        self._spawn(name)  # preview it on the overlay right away

    def _remove(self, name: str):
        ok = QMessageBox.question(self, "Remove emote", f"Remove '{name}' and its sound from the deck?")
        if ok == QMessageBox.StandardButton.Yes:
            self.library.delete_image(name)
            self._refresh()

    def closeEvent(self, event):
        self.server.stop()
        event.accept()
