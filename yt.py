import sys, time, os, re, subprocess

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextBrowser,
    QTableWidget, QTableWidgetItem, QSpinBox, QLabel, QSizePolicy, QSplitter,
    QRadioButton, QButtonGroup, QHeaderView,
    QDialog, QDialogButtonBox, QPlainTextEdit
)
from PyQt6.QtCore import Qt, QUrl, QSettings
from urllib.parse import urlparse, parse_qs
from pytubefix import YouTube, Playlist
from pathlib import Path
from progressbar import ProgressItem, ProgressDelegate


class UserBreakException(Exception):
    pass


def sanitize_filename(name: str) -> str:
    """
    Usuwa emoji i znaki niedozwolone w nazwach plików (Win/Linux/macOS).
    Zachowuje litery narodowe (ą, ę, ü, ñ itp.).
    Limit: 200 znaków (bezpieczny margines dla UTF-8 i rozszerzenia).
    """
    import unicodedata
    result = []
    for ch in name:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):  # znaki sterujące
            continue
        if cat in ("So", "Sm", "Sk"):  # emoji / symbole matematyczne / modyfikatory
            continue
        if ch in r'\/:*?"<>|':  # znaki zakazane na Win/Linux/macOS
            continue
        result.append(ch)
    name = "".join(result).strip(". ")
    # Limit długości — 200 znaków to bezpieczny margines (systemy mają limit 255 bajtów,
    # znaki UTF-8 mogą zajmować do 4 bajtów, plus rozszerzenie np. ".mp3")
    name = name[:200].rstrip(". ")
    return name or "plik"


def safe_name(name: str) -> str:
    return sanitize_filename(name)


def apply_name_format(fmt: str, title: str, nr: str = "", ilosc: str = "", folder: str = "") -> str:
    """Podstawia tokeny do szablonu nazwy pliku.
    {nazwa}  - tytuł filmu
    {nr}     - numer bez paddingu (1, 2, 3...)
    {Nr}     - numer z zerami poprzedzającymi (01, 02... albo 001, 002...)
    {ilość}  - łączna liczba plików
    {folder} - nazwa podfolderu
    """
    if not fmt.strip():
        return sanitize_filename(title)
    width = len(str(ilosc)) if ilosc else 1
    nr_padded = str(nr).zfill(width)
    result = fmt
    result = result.replace("{nazwa}", sanitize_filename(title))
    result = result.replace("{Nr}", nr_padded)  # {Nr} przed {nr} żeby nie podmienić prefiksu
    result = result.replace("{nr}", str(nr))
    result = result.replace("{ilość}", str(ilosc))
    result = result.replace("{folder}", sanitize_filename(folder))
    return sanitize_filename(result)


from dataclasses import dataclass


@dataclass
class Status:
    code: int
    icon: str


MODE_AUDIO = 0
MODE_MP3 = 1
MODE_VIDEO = 2

ROLE_URL = Qt.ItemDataRole.UserRole
ROLE_STATUS = Qt.ItemDataRole.UserRole + 1
ROLE_FOLDER = Qt.ItemDataRole.UserRole + 2
ROLE_PRECENT = Qt.ItemDataRole.UserRole + 3
ROLE_NAME_FORMAT = Qt.ItemDataRole.UserRole + 4
ROLE_NR = Qt.ItemDataRole.UserRole + 5
ROLE_ILOSC = Qt.ItemDataRole.UserRole + 6

STATUS_FINISHED = Status(0, "✅")
STATUS_PENDING = Status(1, "⏳")
STATUS_DOWNLOADING = Status(2, "🔽")
STATUS_CONVERSION = Status(3, "🔄")
STATUS_ERROR = Status(-1, "❌")
STATUS_USER_BREAK = Status(-2, "🛑")
STATUS_SKIPPED = Status(-3, "➖")

BITRATE_MAP = {
    "low": 96,
    "medium": 160,
    "high": 192,
    "hq": 256,
    "max": 320
}


def format_filesize(size: int) -> str:
    """Pokazuje rozmiar pliku z jednostkami"""
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    return f"{size:.1f} {units[unit_index]}"


def open_folder(url: QUrl):
    import subprocess
    if url.scheme() == "file":
        path = url.toLocalFile()
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
    else:
        # http / https — otwórz w przeglądarce
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(url)


def check_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


def get_duration(file: str) -> float:
    import subprocess

    result = subprocess.run([
        "ffprobe",
        "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        file
    ], capture_output=True, text=True)

    return float(result.stdout.strip())


def choose_bitrate(yt_stream) -> int:
    """
    Dobiera bitrate na podstawie jakości źródła
    """

    abr = getattr(yt_stream, "abr", None)  # np. '128kbps'

    if abr is None:
        return 160  # fallback

    abr_value = int(abr.replace("kbps", ""))

    if abr_value <= 96:
        return 128
    elif abr_value <= 128:
        return 160
    elif abr_value <= 192:
        return 192
    elif abr_value <= 256:
        return 256
    else:
        return 320


class BatchDownloadDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pobierz listę linków")
        self.resize(500, 380)

        layout = QVBoxLayout(self)

        # Pole na linki
        lbl_links = QLabel("Lista linków (jeden na linię):")
        layout.addWidget(lbl_links)

        self.links_edit = QPlainTextEdit()
        self.links_edit.setPlaceholderText(
            "https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/watch?v=...\n...")
        layout.addWidget(self.links_edit)

        # Pole na nazwę folderu
        lbl_folder = QLabel("Nazwa podfolderu (opcjonalnie):")
        layout.addWidget(lbl_folder)

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("np. Muzyka 2024")
        layout.addWidget(self.folder_edit)

        # Przyciski Pobierz / Anuluj
        buttons = QDialogButtonBox()
        self.btn_pobierz = buttons.addButton("Pobierz", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_anuluj = buttons.addButton("Anuluj", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_links(self) -> list[str]:
        text = self.links_edit.toPlainText()
        return [line.strip() for line in text.splitlines() if line.strip()]

    def get_folder_name(self) -> str:
        return self.folder_edit.text().strip()


class App(QWidget):

    def __init__(self):
        super().__init__()
        self._msg: str = ''
        self.stop_flag = False
        self.current_row: int | None = None
        self.setWindowTitle("YT Downloader")
        self.setWindowIcon(QIcon('icons/app.png'))
        self.resize(1000, 500)

        self.layout = QVBoxLayout()

        # --- TOP: input + btn_download ---
        top_layout = QHBoxLayout()

        self.btn_batch: QPushButton = QPushButton("☰")
        self.btn_batch.setToolTip("Pobierz listę linków")
        self.btn_batch.setFixedWidth(36)
        self.btn_batch.clicked.connect(self.open_batch_dialog)

        self.input: QLineEdit = QLineEdit()
        self.input.setPlaceholderText("Wpisz link do playlisty lub filu YT...")

        self.btn_download: QPushButton = QPushButton("Pobierz")
        self.btn_download.clicked.connect(self.handle_pobierz)

        # enter jako submit
        self.input.returnPressed.connect(self.handle_pobierz)

        # radio dla konwersji
        self.radio_mp3 = QRadioButton("Konwersja do .mp3")
        self.radio_m4a = QRadioButton("Format oryginalny (.m4a)")
        self.radio_video = QRadioButton("Wideo")

        # sprawdza czy jest ffmpeg i udostępnia konwersję
        if check_ffmpeg():
            self.radio_mp3.setChecked(True)
            self.log("ffpmeg obecne<br>")
        else:
            self.radio_mp3.setEnabled(False)
            self.radio_m4a.setChecked(True)
            self.log("Brak ffmpeg do konwersji .mp3<br>")
            self.log(
                "Pobierz i zainstaluj bibliotekę ręcznie z <a href='https://ffmpeg.org/download.html'>https://ffmpeg.org/download.html</a>")

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_mp3, MODE_MP3)
        self.mode_group.addButton(self.radio_m4a, MODE_AUDIO)
        self.mode_group.addButton(self.radio_video, MODE_VIDEO)

        # tabela
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["id", "Info", "St", "Status"])
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 500)
        self.table.setColumnWidth(2, 20)
        self.table.setColumnWidth(3, 400)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(200)

        # numer startowy
        self.start_input_label = QLabel("Start od")
        self.start_input = QSpinBox()
        self.start_input.setRange(1, 9999)  # 4 cyfry
        self.start_input.setValue(1)
        self.start_input.setFixedWidth(100)
        self.start_input.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)

        # btn_download stopu
        self.stop_btn: QPushButton = QPushButton("STOP")
        self.stop_btn.clicked.connect(self.stop_download)

        top_layout.addWidget(self.btn_batch)
        top_layout.addWidget(self.input)
        top_layout.addWidget(self.start_input_label)
        top_layout.addWidget(self.start_input)
        top_layout.addWidget(self.btn_download)
        top_layout.addWidget(self.stop_btn)

        radio_layout = QHBoxLayout()
        radio_layout.addWidget(self.radio_mp3, alignment=Qt.AlignmentFlag.AlignLeft)
        radio_layout.addSpacing(20)
        radio_layout.addWidget(self.radio_m4a, alignment=Qt.AlignmentFlag.AlignLeft)
        radio_layout.addSpacing(20)
        radio_layout.addWidget(self.radio_video, alignment=Qt.AlignmentFlag.AlignLeft)
        radio_layout.addStretch()

        # --- FORMAT NAZWY (globalny) ---
        self.settings = QSettings("yt-downloader", "yt-downloader")

        format_layout = QHBoxLayout()
        format_label = QLabel("Format nazwy:")
        format_label.setFixedWidth(90)
        self.format_edit = QLineEdit()
        self.format_edit.setText(self.settings.value("name_format", "{nazwa}"))
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_edit)

        hint_label = QLabel(
            "<small>Tokeny: <b>{nazwa}</b> &nbsp; <b>{nr}</b> &nbsp; <b>{Nr}</b> (z zerami) &nbsp; <b>{ilość}</b> &nbsp; <b>{folder}</b> &nbsp;&nbsp; Przykład: <i>{Nr} - {nazwa}</i></small>")
        hint_label.setTextFormat(Qt.TextFormat.RichText)

        # --- RESULT window ---
        self.output: QTextBrowser = QTextBrowser()
        self.output.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.output.setMaximumHeight(300)
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)  # blokuje nawigację po kliknięciu
        self.output.anchorClicked.connect(open_folder)

        # --- LAYOUT ---
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.output)
        self.splitter.setSizes([350, 150])

        self.layout.addLayout(top_layout)
        self.layout.addLayout(radio_layout)
        self.layout.addLayout(format_layout)
        self.layout.addWidget(hint_label)
        self.layout.addWidget(self.splitter)
        self.setLayout(self.layout)

        self._last_update = time.time()

        self.log(
            "<br><b>Gotowy</b><br>Wklej link do filmu lub playlisty youtube, wybierz format wyjściowy, kliknij Pobierz. Pliki znajdziesz w katalogu użytkownika Pobrane, link pojawi się poniżej. Playlisty będą się zapisywać w podfolderach.")

    def open_batch_dialog(self):
        dialog = BatchDownloadDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            links = dialog.get_links()
            folder_name = dialog.get_folder_name()
            name_format = self.format_edit.text().strip()
            if links:
                self.process_batch(links, folder_name, name_format)

    def process_batch(self, urls: list[str], folder_name: str, name_format: str = ""):
        try:
            self.table.setRowCount(0)
            downloads = Path.home() / "Downloads"
            subfolder = folder_name if folder_name else "yt-downloader"
            batch_folder = downloads / subfolder
            batch_folder.mkdir(parents=True, exist_ok=True)
            self.log(f"<br><b>Pobieranie listy {len(urls)} linków</b>")
            self.log(f"Folder zapisu: <a href='file:///{batch_folder}'>{batch_folder}</a>")
            self.stop_flag = False

            imax = len(urls)
            for i, url in enumerate(urls, start=1):
                row = self.add_row(f"{i}/{imax}", url, batch_folder,
                                   name_format=name_format, nr=str(i), ilosc=str(imax))
                self.update_row(row)

            for row in range(self.table.rowCount()):
                if self.stop_flag:
                    break
                try:
                    self.download_with_retry(row)
                except Exception as e:
                    self.update_row(row, f"Błąd: {str(e)}")
        except Exception as e:
            self.log(f"Błąd pobierania listy: {e}")
            import traceback
            traceback.print_exc()

    def stop_download(self):
        self.stop_flag = True
        self.log("Wymuszenie STOPu")

    def handle_pobierz(self):
        url = self.input.text().strip()
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        playlist = params.get("list", [None])[0]

        if playlist:
            self.log(f"Playlista: {playlist}<br>")
            self.process_playlist(url)
        else:
            self.log(f"Brak playlisty sprawdzam video<br>")
            self.process_video(url)

    def add_row(self, row_id: str, url: str, folder: Path,
                name_format: str = "", nr: str = "", ilosc: str = ""):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0 - id
        self.table.setItem(row, 0, QTableWidgetItem(str(row_id)))
        # 1 - info
        self.table.setItem(row, 1, QTableWidgetItem(str(url)))
        # 3 - status
        self.table.setItem(row, 3, ProgressItem("", 0))
        # 2 - button
        btn: QPushButton = QPushButton('')
        btn.setFlat(True)

        btn.clicked.connect(lambda _, r=row: self.on_button_click(r))
        self.table.setCellWidget(row, 2, btn)
        self.set_data(row, ROLE_URL, url)
        self.set_data(row, ROLE_STATUS, STATUS_PENDING)
        self.set_data(row, ROLE_FOLDER, folder)
        self.set_data(row, ROLE_PRECENT, 0)
        self.set_data(row, ROLE_NAME_FORMAT, name_format)
        self.set_data(row, ROLE_NR, nr)
        self.set_data(row, ROLE_ILOSC, ilosc)
        QApplication.processEvents()
        return row

    def update_row(self, row: int, msg: str = None, column: int = 3):
        print(msg)
        item = self.table.item(row, column)
        if msg:
            if item:
                item.setText(msg)
            else:
                self.table.setItem(row, column, QTableWidgetItem(msg))

        btn = self.table.cellWidget(row, 2)
        status = self.get_data(row, ROLE_STATUS)
        if btn and item:
            btn.setText(status.icon)
        progress = self.table.item(row, 3)
        if progress:
            progress.set_percent(self.get_data(row, ROLE_PRECENT))
        self.table.scrollToItem(self.table.item(row, 0))
        QApplication.processEvents()

    def process_video(self, url: str):
        try:
            self.table.setRowCount(0)
            downloads = Path.home() / "Downloads"
            playlist_folder = downloads / "yt-downloader"
            playlist_folder.mkdir(parents=True, exist_ok=True)
            path = str(playlist_folder)
            self.log(f"Folder zapisu: <a href='file:///{playlist_folder}'>{playlist_folder}</a>")
            self.stop_flag = False
            name_format = self.format_edit.text().strip()
            row = self.add_row("1", url, playlist_folder, name_format=name_format, nr="1", ilosc="1")
            try:
                self.download_with_retry(row)
            except Exception as e:
                self.update_row(row, f"Błąd YT: {str(e)}")
                import traceback
                traceback.print_exc()

        except Exception as e:
            self.log(f"Błąd pobierania video: {e}")
            import traceback
            traceback.print_exc()

    def process_playlist(self, url: str):
        try:
            self.table.setRowCount(0)
            pl = Playlist(url=url)
            if not pl.video_urls:
                raise Exception("Nie udało się załadować playlisty (Brak playlisty / CAPTCHA / brak dostępu)")

            self.log(f"<br><b>Playlista</b>: {pl.title}")
            imax = len(pl.video_urls)

            id_start = self.start_input.value()
            self.log(f" Liczba filmów: {imax}, zaczynamy od {id_start}")

            downloads = Path.home() / "Downloads"
            playlist_folder = downloads / "yt-downloader" / safe_name(pl.title)
            playlist_folder.mkdir(parents=True, exist_ok=True)
            path = str(playlist_folder)
            self.log(f"Folder zapisu: <a href='file:///{playlist_folder}'>{playlist_folder}</a>")
            self.stop_flag = False
            name_format = self.format_edit.text().strip()
            urls = list(pl.video_urls)
            for i, url in enumerate(urls[0:], start=1):
                row = self.add_row(f"{i}/{imax}", url, playlist_folder,
                                   name_format=name_format, nr=str(i), ilosc=str(imax))
                if i < id_start:
                    self.set_data(row, ROLE_STATUS, STATUS_SKIPPED)
                self.update_row(row)

            for row in range(self.table.rowCount()):
                if self.get_data(row, ROLE_STATUS).code < 1:
                    continue
                if self.stop_flag:
                    break
                try:
                    self.download_with_retry(row)
                except Exception as e:
                    self.update_row(row, f"Błąd YT: {str(e)}")
        except Exception as e:
            self.log(f"Błąd Playlisty: {e}")
            import traceback
            traceback.print_exc()

    # zapisuje dane w wierszu
    def set_data(self, row: int, role, data):
        self.table.item(row, 0).setData(role, data)

    # odczytuje dane z wiersza
    def get_data(self, row: int, role):
        return self.table.item(row, 0).data(role)

    def download_with_retry(self, row: int, retries: int = 3):
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                if self.stop_flag:
                    raise UserBreakException("Zatrzymane przez użytkownika")

                ret = self.download_and_convert(row)
                self.set_data(row, ROLE_STATUS, STATUS_FINISHED)
                self.update_row(row, f"Zakończone: {Path(ret).name}")
                return ret

            except UserBreakException as e:
                self.set_data(row, ROLE_STATUS, STATUS_USER_BREAK)
                last_error = e
                self.update_row(row, str(e))

            except Exception as e:
                last_error = e
                self.set_data(row, ROLE_STATUS, STATUS_ERROR)
                self.update_row(row, f"Ponawiam {attempt}/{retries} ponieważ: {str(e)}")
        raise last_error

    def on_button_click(self, row: int):
        try:
            status_code = self.get_data(row, ROLE_STATUS)
            if status_code in (STATUS_ERROR, STATUS_USER_BREAK):
                self.stop_flag = False
                self.download_and_convert(row)
        except Exception as e:
            self.log(str(e))

    def download_and_convert(self, row: int):
        self.set_data(row, ROLE_STATUS, STATUS_DOWNLOADING)
        self.set_data(row, ROLE_PRECENT, 0)
        self.update_row(row, f"Pobieranie")
        self.current_row = row
        url = self.get_data(row, ROLE_URL)
        folder = self.get_data(row, ROLE_FOLDER)
        name_format = self.get_data(row, ROLE_NAME_FORMAT) or ""
        nr = self.get_data(row, ROLE_NR) or ""
        ilosc = self.get_data(row, ROLE_ILOSC) or ""

        yt = YouTube(url, on_progress_callback=self.on_progress)
        self.update_row(row, yt.title, 1)

        # Wyznacz docelową nazwę pliku
        folder_name = Path(folder).name
        final_name = apply_name_format(name_format, yt.title, nr, ilosc, folder_name)

        if self.mode_group.checkedId() == MODE_VIDEO:
            yt_file = yt.streams.filter(
                adaptive=True,
                only_video=False,
                file_extension="mp4"
            ).order_by("resolution").desc().first()
        else:
            yt_file = yt.streams.get_audio_only()

        if yt_file is None:
            raise Exception(f"Brak streamu {'video' if self.mode_group.checkedId() == 2 else 'audio'} z YT")

        filepath = yt_file.download(output_path=folder)
        self.set_data(row, ROLE_STATUS, STATUS_FINISHED)
        self.update_row(row, "Pobrano")

        if self.mode_group.checkedId() == MODE_MP3:
            self.set_data(row, ROLE_STATUS, STATUS_CONVERSION)
            self.set_data(row, ROLE_PRECENT, 0)
            self.update_row(row, "Konwersja do mp3...")

            output_file = os.path.join(folder, final_name + ".mp3")
            # Jeśli plik o takiej nazwie już istnieje, dopisz suffix
            if os.path.exists(output_file):
                output_file = os.path.join(folder, final_name + f"_{nr or 0}.mp3")

            result = self.convert_to_mp3(filepath, output_file, row)
            if result.returncode:
                raise Exception(result.stderr)

            self.set_data(row, ROLE_STATUS, STATUS_FINISHED)
            self.update_row(row, f"zapisane {os.path.basename(output_file)}")

            os.remove(filepath)
            return output_file
        else:
            # Tryb VIDEO / AUDIO — zmień nazwę pobranego pliku na final_name
            ext = os.path.splitext(filepath)[1]
            renamed = os.path.join(folder, final_name + ext)
            if filepath != renamed:
                if os.path.exists(renamed):
                    renamed = os.path.join(folder, final_name + f"_{nr or 0}" + ext)
                os.rename(filepath, renamed)
            self.set_data(row, ROLE_STATUS, STATUS_FINISHED)
            self.update_row(row, f"zapisane {os.path.basename(renamed)}")
            return renamed

    def on_progress(self, stream, chunk, bytes_remaining):
        total = stream.filesize
        percent = (1 - bytes_remaining / total) * 100
        self.set_data(self.current_row, ROLE_PRECENT, percent)
        self.update_row(
            self.current_row,
            f"Pobieranie {percent:.0f}%, {format_filesize(total - bytes_remaining)} / {format_filesize(total)}"
        )
        if self.stop_flag:
            raise Exception("Przerwane przez użytkownika")

    def log(self, msg: str):
        print(re.sub(r"<[^>]*>", "", msg))
        if hasattr(self, 'output') and self.output is not None:
            if self._msg:
                self._append_html(self._msg)
                self._msg = ""
            self._append_html(msg)
            now = time.time()
            if now - self._last_update > 0.05:
                QApplication.processEvents()
                self._last_update = now
        else:
            self._msg += msg

    def _append_html(self, html: str):
        """Dopisuje HTML na koniec bez przeładowania całego dokumentu."""
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertHtml(html)
        self.output.setTextCursor(cursor)
        self.output.verticalScrollBar().setValue(
            self.output.verticalScrollBar().maximum()
        )

    def convert_to_mp3(self, input_file, output_file, row: int, bitrate: int = 160):
        duration = get_duration(input_file)
        if duration == 0:
            raise Exception("Czas trwania pliku to 0s")

        process = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-i", input_file,
                "-vn",
                "-ab", f"{bitrate}k",
                output_file,
                "-progress", "pipe:1",
                "-nostats"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        for line in process.stdout:
            if self.stop_flag:
                process.kill()
                raise Exception("Zatrzymane przez użytkownika")

            if "out_time_ms=" in line:
                value = int(line.split("=")[1])
                current = value / 1_000_000  # ms → sec

                percent = (current / duration) * 100
                self.set_data(row, ROLE_PRECENT, percent)
                self.update_row(row, f"Konwersja do mp3: {percent:.0f}%")

        process.wait()

        return process

    def closeEvent(self, event):
        self.settings.setValue("name_format", self.format_edit.text())
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())