from __future__ import annotations

import os
import sys
import json
import re
import atexit
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    Qt,
    QUrl,
    QTimer,
    QDateTime,
    QSize,
    QCoreApplication,
    pyqtSignal,
    QObject,
    QPropertyAnimation,
    QEasingCurve,
    QStandardPaths,
    QByteArray,
)
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QSplitter,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QLineEdit,
    QToolBar,
    QMenu,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QTextEdit,
    QSizePolicy,
    QGraphicsOpacityEffect,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineDownloadRequest,
    QWebEngineScript,
)


# ----------------- SETTINGS -----------------
APP_NAME = "Multi Steam"
APP_ORG = "Multi Steam"
DEFAULT_HOME_URL = "https://steamcommunity.com/"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
TRUST_HOSTS = {
    "steamcommunity.com",
    "store.steampowered.com",
    "help.steampowered.com",
    "login.steampowered.com",
}


# ----------------- LOGGING -----------------
from io import TextIOWrapper

_log_file: Optional[TextIOWrapper] = None


def appDataDir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    root = Path(base)
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    return str(root)


def configPath() -> str:
    return str(Path(appDataDir()) / "config.json")


def rotateLogs(log_dir: str, keep: int = 10) -> None:
    p = Path(log_dir)
    if not p.exists():
        return
    files = sorted(p.glob("app_*.log"), key=lambda x: x.stat().st_mtime)
    while len(files) > keep:
        try:
            files[0].unlink()
        except Exception:
            pass
        files.pop(0)


def initLogging() -> None:
    global _log_file
    base = Path(appDataDir())
    logdir = base / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    rotateLogs(str(logdir))
    fname = logdir / f"app_{QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')}.log"
    _log_file = open(fname, "a", encoding="utf-8", buffering=1)
    atexit.register(_closeLog)
    info(f"Logging started to {fname}")


def _closeLog() -> None:
    global _log_file
    try:
        if _log_file and not _log_file.closed:
            _log_file.flush()
            _log_file.close()
    except Exception:
        pass


def _log(prefix: str, msg: str) -> None:
    global _log_file
    if not _log_file:
        return
    ts = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss.zzz")
    _log_file.write(f"{ts} [{prefix}] - {msg}\n")
    _log_file.flush()


def info(msg: str) -> None:
    _log("I", msg)


def warn(msg: str) -> None:
    _log("W", msg)


# ----------------- UTILS -----------------
def isSteamUrl(u: QUrl | str) -> bool:
    if isinstance(u, str):
        u = QUrl(u)
    host = u.host().lower()
    return any(
        host.endswith(h)
        for h in (
            "steamcommunity.com",
            "store.steampowered.com",
            "help.steampowered.com",
            "login.steampowered.com",
        )
    )


def urlIsLogin(u: QUrl | str) -> bool:
    if isinstance(u, str):
        u = QUrl(u)
    return ("login" in u.toString().lower()) and u.host().endswith("steamcommunity.com")


# ----------------- RENDERER CONFIG -----------------
def configureRenderer() -> str:
    if sys.platform == "win32":
        flags = [
            "--ignore-gpu-blocklist",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--disable-quic",
            "--log-level=3",
            "--disable-logging",
            "--use-angle=d3d11",
        ]
        os.environ["QT_OPENGL"] = "angle"
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)
        return "d3d11"
    else:
        flags = [
            "--ignore-gpu-blocklist",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--disable-quic",
            "--log-level=3",
            "--disable-logging",
        ]
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)
        return "hw"


def fade_in(widget: QWidget, duration=220):
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


# ----------------- UI core classes -----------------
class PopupWindow(QMainWindow):
    def __init__(self, parent: Optional[QWidget], view: QWebEngineView, title: str = "Steam Web Chat") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(980, 720)
        self.setCentralWidget(view)


class Page(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, owner_view: QWebEngineView):
        super().__init__(profile)
        self.owner_view = owner_view
        self.last_fix_ms = 0

    def javaScriptConsoleMessage(self, level, msg: str, line: int, src: str) -> None:
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            warn(f"[JS-ERROR] {src}:{line} — {msg}")
        if any(s in msg for s in ["ChunkLoadError", "jQuery is not defined", "Prototype is not defined"]):
            owner_obj = self.owner_view.property("_owner_ptr")
            cur = owner_obj.property("current_name") if owner_obj else ""
            my = self.owner_view.property("_profile_name")
            if cur != my:
                return
            now = QDateTime.currentMSecsSinceEpoch()
            if now - self.last_fix_ms > 5000:
                self.last_fix_ms = now
                QTimer.singleShot(150, lambda: self.triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache))


# -------- Импорт log:pass контроллер --------
class ImportLogPassController(QObject):
    stopped = pyqtSignal()

    def __init__(self, owner: 'MultiBrowser', lines: List[Tuple[str, str]], start_index: int = 0):
        super().__init__(owner)
        self.owner = owner
        self.lines = lines
        self.index = max(0, min(start_index, len(lines)))
        self.active = True

        owner.profileAdded.connect(self.onProfileAdded)
        owner.currentUrlChanged.connect(self.onUrlChanged)
        owner.statusBar().showMessage(
            f"Импорт log:pass запущен (с {self.index + 1}-й строки). Создайте новый профиль.",
            5000,
        )

    def stop(self):
        if not self.active:
            return
        self.active = False
        try:
            self.owner.profileAdded.disconnect(self.onProfileAdded)
            self.owner.currentUrlChanged.disconnect(self.onUrlChanged)
        except Exception:
            pass
        self.stopped.emit()
        self.owner.statusBar().showMessage("Импорт log:pass остановлен.", 4000)

    def onProfileAdded(self, name: str):
        if not self.active or self.index >= len(self.lines):
            return
        view = self.owner.browsers.get(name)
        if not view:
            return
        if not urlIsLogin(view.url()):
            view.setUrl(QUrl("https://steamcommunity.com/login/home/?goto="))

        login, pwd = self.lines[self.index]
        text_line = f"{login}:{pwd}"

        mb = QMessageBox(self.owner)
        mb.setWindowTitle("Импорт log:pass")
        mb.setText(f"Скопировать логин/пароль для записи #{self.index + 1}?\n\n{login}:{'*' * len(pwd)}")
        btnCopy = mb.addButton("Скопировать", QMessageBox.ButtonRole.AcceptRole)
        btnSkip = mb.addButton("Пропустить", QMessageBox.ButtonRole.ActionRole)
        btnStop = mb.addButton("Стоп", QMessageBox.ButtonRole.RejectRole)
        mb.exec()

        if mb.clickedButton() == btnStop:
            self.stop()
            return
        if mb.clickedButton() == btnSkip:
            self.index += 1
            if self.index >= len(self.lines):
                self.finishAll()
            else:
                self.owner.statusBar().showMessage(
                    f"Пропущено. Осталось {len(self.lines) - self.index}. Создайте следующий профиль.",
                    4000,
                )
            return

        QApplication.clipboard().setText(text_line)
        self.owner.statusBar().showMessage("Скопировано в буфер: " + text_line, 4000)

    def onUrlChanged(self, url: QUrl):
        if not self.active or self.index >= len(self.lines):
            return
        if isSteamUrl(url) and not urlIsLogin(url):
            self.index += 1
            self.owner._last_import_index = self.index
            if self.index >= len(self.lines):
                self.finishAll()
            else:
                self.owner.statusBar().showMessage(
                    f"Вход подтверждён. Готово {self.index}/{len(self.lines)}. Создайте следующий профиль.",
                    6000,
                )

    def finishAll(self):
        QMessageBox.information(self.owner, "Импорт log:pass", "Все записи обработаны.")
        self.stop()


def parse_logpass_file(path: str) -> List[Tuple[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    raw = p.read_text("utf-8", errors="ignore")
    out: List[Tuple[str, str]] = []
    for ln in re.split(r"[\r\n]+", raw):
        line = ln.strip()
        if not line or line.startswith('#'):
            continue
        if ":" not in line:
            continue
        login, pwd = line.split(":", 1)
        login, pwd = login.strip(), pwd.strip()
        if login and pwd:
            out.append((login, pwd))
    return out


# ----------------- Main window -----------------
class MultiBrowser(QMainWindow):
    profileAdded = pyqtSignal(str)  # name
    currentUrlChanged = pyqtSignal(QUrl)  # url of active view

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(QSize(1400, 900))
        self.setProperty("current_name", "")

        self.profiles: List[str] = []
        self.lastActive: str = ""
        self.currentName: str = ""
        self.browsers: Dict[str, BrowserView] = {}
        self.popupsByProfile: Dict[str, List[PopupWindow]] = {}
        self.lastUrls: Dict[str, str] = {}

        # импорт log:pass
        self._importCtl: Optional[ImportLogPassController] = None
        self._last_import_file: Optional[str] = None
        self._last_import_lines: List[Tuple[str, str]] = []
        self._last_import_index: int = 0

        # UI state
        self.accountsPanelDefaultWidth = 320
        self.accountsVisible = False
        self.leftBarWidth = 160  # widened so buttons fit

        self.loadConfig()

        # --- Splitter: [leftBar | accountsPanel | center]
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.splitter)

        # LEFT BAR (wider so text fits)
        self.leftBar = self._buildLeftBar()
        self.splitter.addWidget(self.leftBar)
        self.leftBar.setMinimumWidth(self.leftBarWidth)
        self.leftBar.setMaximumWidth(self.leftBarWidth)

        # ACCOUNTS PANEL
        self.accountsPanel = self._buildAccountsPanel()
        self.splitter.addWidget(self.accountsPanel)
        self.accountsPanel.setVisible(self.accountsVisible)

        # CENTER
        self.centerWidget = QWidget()
        cl = QVBoxLayout(self.centerWidget)
        cl.setContentsMargins(0, 0, 0, 0)

        self.toolbar = QToolBar()
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)

        commAct = QAction("👥", self)
        commAct.setToolTip("Steam Community")
        commAct.triggered.connect(lambda: self.navigateCurrent(DEFAULT_HOME_URL))
        self.toolbar.addAction(commAct)
        self.toolbar.addSeparator()

        backAct = QAction("◀", self)
        fwdAct = QAction("▶", self)
        relAct = QAction("⟳", self)
        backAct.triggered.connect(lambda: self.callOnCurrent(lambda v: v.back()))
        fwdAct.triggered.connect(lambda: self.callOnCurrent(lambda v: v.forward()))
        relAct.triggered.connect(lambda: self.callOnCurrent(lambda v: v.reload()))
        self.toolbar.addAction(backAct)
        self.toolbar.addAction(fwdAct)
        self.toolbar.addAction(relAct)
        self.toolbar.addSeparator()

        # Removed cookie import actions per requirement

        self.urlBar = QLineEdit()
        self.urlBar.setPlaceholderText("Введите URL и Enter…")
        self.urlBar.returnPressed.connect(self.navigateToUrl)
        self.toolbar.addWidget(self.urlBar)

        self.browserHolder = QWidget()
        self.browserLayout = QVBoxLayout(self.browserHolder)
        self.browserLayout.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.browserHolder, 1)

        self.splitter.addWidget(self.centerWidget)
        self.splitter.setStretchFactor(2, 1)

        # restore splitter sizing
        self._applySplitterSizes()
        self.splitter.splitterMoved.connect(self._onSplitterMoved)

        # создать браузеры для уже сохранённых профилей
        for name in self.profiles:
            self.createProfile(name, doSwitch=False)
            self._accountListAddItem(name)

        # Do NOT auto-create a profile on first launch
        if self.browsers.get(self.lastActive):
            self.switchAccount(self.lastActive)

        self.applyStyle()
        self.statusBar().setSizeGripEnabled(True)
        self.statusBar().setStyleSheet(
            "QStatusBar{background:#111927;color:#CDE6FF;border-top:1px solid #1e2b3c;}"
        )

        # restore window geometry if present
        if getattr(self, "_restoredGeometry", False):
            pass

    # ---------- Left bar ----------
    def _buildLeftBar(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 12, 8, 12)
        v.setSpacing(10)

        self.btnAccounts = QPushButton("Аккаунты")
        self.btnAccounts.setCheckable(True)
        self.btnAccounts.clicked.connect(self.toggleAccountsPanel)
        self.btnAccounts.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.btnChats = QPushButton("Чаты")
        self.btnChats.setEnabled(True)
        self.btnChats.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        for b in (self.btnAccounts, self.btnChats):
            b.setMinimumHeight(44)

        v.addWidget(self.btnAccounts)
        v.addWidget(self.btnChats)
        v.addStretch(1)
        return w

    # ---------- Accounts panel ----------
    def _buildAccountsPanel(self) -> QWidget:
        side = QWidget()
        sb = QVBoxLayout(side)
        sb.setContentsMargins(12, 12, 12, 12)
        sb.setSpacing(10)

        titleRow = QHBoxLayout()
        title = QLabel("Аккаунты")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#CDE6FF;")
        titleRow.addWidget(title)
        titleRow.addStretch(1)
        sb.addLayout(titleRow)

        self.accountList = QListWidget()
        self.accountList.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.accountList.customContextMenuRequested.connect(self.showAccountsMenu)
        self.accountList.itemClicked.connect(lambda it: self.switchAccount(it.text()) if it else None)
        sb.addWidget(self.accountList, 1)

        btnRow = QHBoxLayout()
        self.btnAddProfile = QPushButton("➕ Профиль")
        self.btnAddProfile.clicked.connect(self.addAccount)

        self.btnImportLogPass = QPushButton("📥 Импорт log:pass")
        self.btnImportLogPass.clicked.connect(self.startImportLogPass)
        # ПКМ — задать стартовую строку
        self.btnImportLogPass.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btnImportLogPass.customContextMenuRequested.connect(self._importLogPassContextMenu)

        btnRow.addWidget(self.btnAddProfile)
        btnRow.addWidget(self.btnImportLogPass)
        sb.addLayout(btnRow)
        return side

    def _importLogPassContextMenu(self, pos):
        menu = QMenu(self)
        actSetStart = menu.addAction("Задать стартовую строку…")
        chosen = menu.exec(self.btnImportLogPass.mapToGlobal(pos))
        if chosen == actSetStart:
            if not self._last_import_lines:
                path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Выберите .txt (login:password)",
                    QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation),
                    "Text files (*.txt);;All files (*.*)",
                )
                if not path:
                    return
                lines = parse_logpass_file(path)
                if not lines:
                    QMessageBox.warning(self, "Импорт log:pass", "Файл пуст или не содержит login:password.")
                    return
                self._last_import_file = path
                self._last_import_lines = lines
                self._last_import_index = 0

            idx, ok = QInputDialog.getInt(
                self,
                "Стартовая строка",
                "Номер (1..N):",
                max(1, self._last_import_index + 1),
                1,
                len(self._last_import_lines),
                1,
            )
            if not ok:
                return
            self._last_import_index = idx - 1
            if self._importCtl and self._importCtl.active:
                self._importCtl.stop()
            self._importCtl = ImportLogPassController(self, self._last_import_lines, start_index=self._last_import_index)
            self._importCtl.stopped.connect(lambda: setattr(self, "_importCtl", None))

    def toggleAccountsPanel(self) -> None:
        self.accountsVisible = not self.accountsVisible
        self.accountsPanel.setVisible(self.accountsVisible)
        self.btnAccounts.setChecked(self.accountsVisible)
        self._applySplitterSizes()
        self.saveConfig()

    def _applySplitterSizes(self) -> None:
        left_w = self.leftBarWidth
        acc_w = self.accountsPanelDefaultWidth if self.accountsVisible else 0
        self.accountsPanel.setMinimumWidth(0 if not self.accountsVisible else 240)
        self.accountsPanel.setMaximumWidth(0 if not self.accountsVisible else 480)
        self.splitter.setSizes([left_w, acc_w, 1000])

    def _onSplitterMoved(self, pos: int, index: int) -> None:
        sizes = self.splitter.sizes()
        # Update accounts panel width only when it's visible
        if self.accountsVisible and len(sizes) >= 2:
            self.accountsPanelDefaultWidth = max(240, min(480, sizes[1]))
            self.saveConfig()

    # ---------- Accounts context menu ----------
    def showAccountsMenu(self, pos) -> None:
        it: QListWidgetItem = self.accountList.itemAt(pos)
        if not it:
            return
        name = it.text()
        menu = QMenu(self)
        actDel = menu.addAction("🗑 Удалить профиль…")
        chosen = menu.exec(self.accountList.mapToGlobal(pos))
        if chosen == actDel:
            self.deleteProfile(name)

    def _accountListAddItem(self, name: str) -> None:
        self.accountList.addItem(name)

    # ---------- Import log:pass ----------
    def startImportLogPass(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите .txt (login:password)",
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation),
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return
        lines = parse_logpass_file(path)
        if not lines:
            QMessageBox.warning(self, "Импорт log:pass", "Файл пуст или не найдено строк вида login:password.")
            return
        self._last_import_file = path
        self._last_import_lines = lines
        self._last_import_index = 0
        if self._importCtl and self._importCtl.active:
            self._importCtl.stop()
        self._importCtl = ImportLogPassController(self, lines, start_index=0)
        self._importCtl.stopped.connect(lambda: setattr(self, "_importCtl", None))

    # ---------- Profiles mgmt ----------
    def addAccount(self) -> None:
        acc = self.nextSteamName()
        self.createProfile(acc, doSwitch=True)
        self._accountListAddItem(acc)
        items = self.accountList.findItems(acc, Qt.MatchFlag.MatchExactly)
        if items:
            self.accountList.setCurrentItem(items[0])
        if acc not in self.profiles:
            self.profiles.append(acc)
        self.lastActive = acc
        self.saveConfig()

        v = self.browsers[acc]
        urlNow = v.url().toString()
        if not urlNow or urlNow == "about:blank":
            restore = self.lastUrls.get(acc, "")
            self.navigateCurrent(restore if restore else DEFAULT_HOME_URL)
        self.profileAdded.emit(acc)

    def deleteProfile(self, name: str) -> None:
        if name not in self.browsers:
            return
        if (
            QMessageBox.question(self, "Удаление", f"Удалить профиль «{name}» и его файлы?")
            != QMessageBox.StandardButton.Yes
        ):
            return

        v = self.browsers.pop(name, None)
        if v:
            for i in range(self.browserLayout.count() - 1, -1, -1):
                w = self.browserLayout.itemAt(i).widget()
                if w is v:
                    w.setParent(None)
                    break
            v.deleteLater()

        if name in self.popupsByProfile:
            for w in self.popupsByProfile[name]:
                if w:
                    w.close()
            self.popupsByProfile.pop(name, None)

        pdir = Path(self.profileDir(name))
        try:
            if pdir.exists():
                import shutil

                shutil.rmtree(pdir)
        except Exception as e:
            warn(f"Failed to remove profile dir: {e}")
            self.statusBar().showMessage(f"Не удалось удалить каталог профиля: {e}", 5000)

        for it in self.accountList.findItems(name, Qt.MatchFlag.MatchExactly):
            self.accountList.takeItem(self.accountList.row(it))

        if name in self.profiles:
            self.profiles.remove(name)
        self.lastUrls.pop(name, None)
        self.lastActive = self.profiles[0] if self.profiles else ""
        self.saveConfig()
        if self.lastActive:
            self.switchAccount(self.lastActive)

    def switchAccount(self, acc: str) -> None:
        if acc not in self.browsers:
            return
        for i in range(self.browserLayout.count() - 1, -1, -1):
            w = self.browserLayout.itemAt(i).widget()
            if w:
                w.setParent(None)
        v = self.browsers[acc]
        self.browserLayout.addWidget(v)
        self.currentName = acc
        self.setProperty("current_name", self.currentName)
        if v.url().isEmpty() or v.url().toString() == "about:blank":
            restore = self.lastUrls.get(acc, "")
            v.setUrl(QUrl(restore if restore else DEFAULT_HOME_URL))
        self.urlBar.setText(v.url().toString())
        v.setFocus()
        fade_in(v)
        items = self.accountList.findItems(acc, Qt.MatchFlag.MatchExactly)
        if items:
            self.accountList.setCurrentItem(items[0])
        self.lastActive = acc
        self.saveConfig()
        self.showPopupsFor(acc)

    # ---------- Navigation ----------
    def navigateCurrent(self, url: str) -> None:
        if not self.currentName:
            return
        self.browsers[self.currentName].setUrl(QUrl(url))
        self.urlBar.setText(url)

    def navigateToUrl(self) -> None:
        if not self.currentName:
            return
        url = self.urlBar.text().strip()
        if not (
            url.startswith("http://") or url.startswith("https://") or url.startswith("chrome://")
        ):
            url = "https://" + url
        self.browsers[self.currentName].setUrl(QUrl(url))

    def callOnCurrent(self, fn) -> None:
        if not self.currentName:
            return
        fn(self.browsers[self.currentName])

    # ---------- Popups mgmt ----------
    def registerPopup(self, profileName: str, win: PopupWindow) -> None:
        self.popupsByProfile.setdefault(profileName, []).append(win)

    def unregisterPopup(self, profileName: str, win: PopupWindow) -> None:
        arr = self.popupsByProfile.get(profileName)
        if not arr:
            return
        if win in arr:
            arr.remove(win)
        if not arr:
            self.popupsByProfile.pop(profileName, None)

    def showPopupsFor(self, profileName: str) -> None:
        for key, wins in list(self.popupsByProfile.items()):
            isActive = key == profileName
            for w in wins:
                if not w:
                    continue
                if isActive:
                    w.showNormal()
                    w.raise_()
                else:
                    w.hide()

    def current_name(self) -> str:
        return self.currentName

    # ---------- Config ----------
    def loadConfig(self) -> None:
        p = Path(configPath())
        if not p.exists():
            return
        try:
            o = json.loads(p.read_text("utf-8"))
        except Exception:
            return
        self.profiles = [str(x) for x in o.get("profiles", [])]
        self.lastActive = str(o.get("last_active", ""))
        self.lastUrls = {str(k): str(v) for k, v in o.get("last_urls", {}).items()}

        # UI state
        self.accountsVisible = bool(o.get("accounts_visible", False))
        self.accountsPanelDefaultWidth = int(o.get("accounts_panel_width", self.accountsPanelDefaultWidth))
        self.leftBarWidth = int(o.get("leftbar_width", self.leftBarWidth))

        # Try restore geometry
        geo_hex = o.get("win_geometry")
        if isinstance(geo_hex, str) and geo_hex:
            try:
                ba = QByteArray.fromHex(bytes(geo_hex, "utf-8"))
                if not ba.isEmpty():
                    self.restoreGeometry(ba)
                    self._restoredGeometry = True
            except Exception:
                pass

    def saveConfig(self) -> None:
        try:
            geo_hex = str(self.saveGeometry().toHex(), "utf-8")
        except Exception:
            geo_hex = ""
        o = {
            "profiles": self.profiles,
            "last_active": self.lastActive,
            "last_urls": self.lastUrls,
            # UI
            "accounts_visible": self.accountsVisible,
            "accounts_panel_width": self.accountsPanelDefaultWidth,
            "leftbar_width": self.leftBarWidth,
            "win_geometry": geo_hex,
        }
        Path(configPath()).write_text(json.dumps(o, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---------- Profile creation ----------
    def profileDir(self, name: str) -> str:
        p = Path(appDataDir()) / "profiles" / name
        (p / "cache").mkdir(parents=True, exist_ok=True)
        (p / "downloads").mkdir(parents=True, exist_ok=True)
        return str(p)

    def nextSteamName(self) -> str:
        n = 1
        while f"Steam {n}" in self.browsers:
            n += 1
        return f"Steam {n}"

    def createProfile(self, name: str, doSwitch: bool) -> None:
        """
        ЖЁСТКАЯ изоляция:
        - создаём НОВЫЙ QWebEngineProfile(self) БЕЗ имени (чтобы Qt не переиспользовал раздел),
        - задаём УНИКАЛЬНЫЙ persistentStoragePath для этого профиля,
        - свой дисковый кэш и политика постоянных куки.
        """
        pdir = self.profileDir(name)
        profile = QWebEngineProfile(self)  # <=== separate engine profile per app profile
        profile.setHttpUserAgent(CHROME_UA)
        profile.setPersistentStoragePath(pdir)
        profile.setCachePath(str(Path(pdir) / "cache"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.setHttpCacheMaximumSize(256 * 1024 * 1024)

        def on_download(req: QWebEngineDownloadRequest) -> None:
            dirp = Path(self.profileDir(name)) / "downloads"
            dirp.mkdir(parents=True, exist_ok=True)
            file_name = Path(req.downloadFileName()).name
            req.setDownloadDirectory(str(dirp))
            req.setDownloadFileName(file_name)
            req.accept()
            self.statusBar().showMessage(f"Загрузка: {file_name} → {dirp}", 1500)

            last_tick = {"ts": 0}

            def progress_update():
                now = QDateTime.currentMSecsSinceEpoch()
                if now - last_tick.get("ts", 0) < 120:
                    return
                last_tick["ts"] = now
                received = req.receivedBytes()
                total = req.totalBytes()
                if total > 0:
                    self.statusBar().showMessage(f"Загрузка {file_name}: {received}/{total}", 150)

            req.receivedBytesChanged.connect(progress_update)
            req.totalBytesChanged.connect(progress_update)

            def on_state(st: QWebEngineDownloadRequest.DownloadState):
                if st == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                    self.statusBar().showMessage(f"Загрузка завершена: {dirp}/{file_name}", 4000)
                elif st in (
                    QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
                    QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
                ):
                    self.statusBar().showMessage(f"Загрузка прервана: {file_name}", 4000)

            req.stateChanged.connect(on_state)

        profile.downloadRequested.connect(on_download)

        view = BrowserView(profile, self, name)
        view.urlChanged.connect(lambda u, v=view: self._onUrlChanged(u, v))
        self.browsers[name] = view

        QTimer.singleShot(
            1000,
            lambda v=view: (
                v.postLoadHealthcheck(False)
                if (v.url().toString() and v.url().toString() != "about:blank")
                else None
            ),
        )

        if doSwitch:
            self.switchAccount(name)

    def _onUrlChanged(self, u: QUrl, view: 'BrowserView') -> None:
        if self.currentName and self.browsers.get(self.currentName) is view:
            self.urlBar.setText(u.toString())
            self.currentUrlChanged.emit(u)
        pname = view.property("_profile_name")
        if pname:
            self.lastUrls[str(pname)] = u.toString()
            self.saveConfig()

    # ---------- Styling ----------
    def applyStyle(self) -> None:
        if sys.platform == "win32":
            f = QFont("Segoe UI", 10)
        elif sys.platform == "darwin":
            f = QFont(".SF NS Text", 12)
        else:
            f = QFont("Inter", 10)
        QApplication.setFont(f)
        self.setStyleSheet(
            """
QMainWindow { background-color:#17212B; color:#E6E9EE; }
QToolBar { background:#0f141a; border:0; padding:8px; }
QToolButton { color:#e6f1ff; font-size:16px; padding:8px 10px; border-radius:12px; }
QToolButton:hover { background:rgba(255,255,255,0.06); }
QPushButton { background:#1a2532; color:#E1ECF4; border-radius:12px; padding:10px 12px; font-weight:600; }
QPushButton:hover { background:#223349; }
QPushButton:checked { background:#2AABEE; color:#ffffff; }
QListWidget { background:#0e1621; border:none; color:#E1ECF4; border-radius:12px; outline:0; padding:6px; }
QListWidget::item { padding:10px 12px; margin:4px; border-radius:10px; }
QListWidget::item:selected { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2AABEE, stop:1 #229ED9); color:#fff; }
QLineEdit, QTextEdit { background:#0b141f; color:#e8f1ff; border:1px solid #1f2b38; border-radius:12px; padding:10px; }
QLabel { color:#CDE6FF; }
QMenu { background:#0e1621; color:#E1ECF4; border:1px solid #1f2b38; }
QMenu::item:selected { background:#18324a; }
"""
        )

    def closeEvent(self, event) -> None:
        try:
            self.saveConfig()
        finally:
            super().closeEvent(event)


class BrowserView(QWebEngineView):
    def __init__(self, profile: QWebEngineProfile, owner: MultiBrowser, profileName: str) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setProperty("_owner_ptr", owner)
        self.setProperty("_profile_name", profileName)

        p = Page(profile, self)
        self.setPage(p)
        self.page().featurePermissionRequested.connect(self._onFeaturePerm)

        s = self.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, True)

        self.loadProgress.connect(self._onProgress)
        self.page().renderProcessTerminated.connect(self._onRenderCrash)

    def _onFeaturePerm(self, url: QUrl, feat: QWebEnginePage.Feature) -> None:
        host = url.host()
        if host in TRUST_HOSTS:
            self.page().setFeaturePermission(
                url, feat, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            )
        else:
            self.page().setFeaturePermission(
                url, feat, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
            )

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEngineView:
        # поп-апы используют ТОТ ЖЕ profile, что и текущий аккаунт (но не делятся с другими профилями)
        child = BrowserView(self.page().profile(), self.owner, str(self.property("_profile_name")))
        popup = PopupWindow(self.owner, child, "Steam Web Chat")
        child.page().windowCloseRequested.connect(popup.close)
        popup.destroyed.connect(lambda: self.owner.unregisterPopup(str(self.property("_profile_name")), popup))
        self.owner.registerPopup(str(self.property("_profile_name")), popup)
        if self.owner and self.owner.current_name() == str(self.property("_profile_name")):
            popup.show()
        else:
            popup.hide()
        return child

    def _onProgress(self, percent: int) -> None:
        if percent == 100:
            QTimer.singleShot(
                800,
                lambda: (
                    self.postLoadHealthcheck(False)
                    if (
                        self.owner and self.owner.current_name() == str(self.property("_profile_name"))
                    )
                    else None
                ),
            )

    def _onRenderCrash(self, status, code: int) -> None:
        if not self.owner or self.owner.current_name() != str(self.property("_profile_name")):
            return
        last = getattr(self, "_lastRenderReloadMs", 0)
        now = QDateTime.currentMSecsSinceEpoch()
        if now - last < 5000:
            return
        self._lastRenderReloadMs = now
        QTimer.singleShot(150, lambda: self.page().triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache))

    def postLoadHealthcheck(self, _force: bool) -> None:
        if not self.owner or self.owner.current_name() != str(self.property("_profile_name")):
            return
        u = self.url().toString()
        if not u or u == "about:blank":
            return

        def after_html(html: str) -> None:
            h = (html or "").strip()
            if h and len(h) >= 50:
                self.setProperty("_blank_fix_tries", 0)
                return
            last = int(self.property("_last_blank_fix_ms") or 0)
            tries = int(self.property("_blank_fix_tries") or 0)
            now = QDateTime.currentMSecsSinceEpoch()
            if tries < 3 and (now - last) > 5000:
                self.setProperty("_last_blank_fix_ms", now)
                self.setProperty("_blank_fix_tries", tries + 1)
                self.page().triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)

        self.page().toHtml(after_html)
        QTimer.singleShot(400, self._postBlankJS)

    def _postBlankJS(self) -> None:
        js = r"""
(function(){
  try{
    var b = document.body; if(!b) return {empty:true};
    var txt = (b.innerText||"").replace(/\s+/g,"");
    var nodes = b.children ? b.children.length : 0;
    var heavy = document.querySelectorAll('img,video,canvas,iframe').length;
    var okay = (txt.length > 20) || (nodes > 5) || (heavy > 0);
    return {empty: !okay};
  }catch(e){ return {empty:false}; }
})();
"""
        self.page().runJavaScript(
            js, QWebEngineScript.ScriptWorldId.ApplicationWorld, self._maybeReloadOnBlank
        )

    def _maybeReloadOnBlank(self, res) -> None:
        isBlank = False
        try:
            if isinstance(res, dict):
                isBlank = bool(res.get("empty"))
        except Exception:
            isBlank = False
        last = int(self.property("_last_blank_fix_ms") or 0)
        tries = int(self.property("_blank_fix_tries") or 0)
        now = QDateTime.currentMSecsSinceEpoch()
        if isBlank and (now - last) > 5000 and tries < 3:
            self.setProperty("_last_blank_fix_ms", now)
            self.setProperty("_blank_fix_tries", tries + 1)
            self.page().triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)


# ----------------- main() -----------------
def main() -> int:
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.*=false;qt.webenginecontext.*=false;qt.webengine.*=false"
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    backend = configureRenderer()
    app = QApplication(sys.argv)
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(APP_ORG)

    initLogging()
    info(f"ANGLE backend selected: {backend}")

    w = MultiBrowser()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

