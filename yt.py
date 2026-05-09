import sys, time, os, re, subprocess

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextBrowser,
    QTableWidget, QTableWidgetItem, QSpinBox, QLabel, QSizePolicy, QSplitter,
    QRadioButton, QButtonGroup, QHeaderView
)
from PyQt6.QtCore import Qt, QUrl
from urllib.parse import urlparse, parse_qs
from pytubefix import YouTube, Playlist
from pathlib import Path
from progressbar import ProgressItem, ProgressDelegate

class UserBreakException(Exception):
    pass

def safe_name(name):
    return "".join(c for c in name if c not in r'\/:*?"<>|')

from dataclasses import dataclass
@dataclass
class Status:
    code: int
    icon: str

MODE_AUDIO =0
MODE_MP3 = 1
MODE_VIDEO = 2

ROLE_URL = Qt.ItemDataRole.UserRole
ROLE_STATUS = Qt.ItemDataRole.UserRole + 1
ROLE_FOLDER = Qt.ItemDataRole.UserRole + 2
ROLE_PRECENT = Qt.ItemDataRole.UserRole + 3

STATUS_FINISHED =    Status( 0, "✅")
STATUS_PENDING =     Status( 1, "⏳")
STATUS_DOWNLOADING = Status( 2, "🔽")
STATUS_CONVERSION =  Status( 3, "🔄")
STATUS_ERROR =       Status(-1, "❌")
STATUS_USER_BREAK =  Status(-2, "🛑")
STATUS_SKIPPED =     Status(-3, "➖")

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
    path = url.toLocalFile()
    import subprocess
    if os.name == "nt":  # Windows
        os.startfile(path)
    elif os.name == "posix":  # Linux/Mac
        subprocess.Popen(["xdg-open", path])


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


class App(QWidget):
    
    def __init__(self):
        super().__init__()
        self._msg:str = ''
        self.stop_flag = False
        self.current_row: int|None = None
        self.setWindowTitle("YT Downloader")
        self.setWindowIcon(QIcon('icons/app.png'))
        self.resize(1000, 500)

        self.layout = QVBoxLayout()

        # --- TOP: input + btn_download ---
        top_layout = QHBoxLayout()

        self.input:QLineEdit = QLineEdit()
        self.input.setPlaceholderText("Wpisz link do playlisty lub filu YT...")

        self.btn_download: QPushButton= QPushButton("Pobierz")
        self.btn_download.clicked.connect(self.handle_pobierz)

        # enter jako submit
        self.input.returnPressed.connect(self.handle_pobierz)

        #radio dla konwersji
        self.radio_mp3 = QRadioButton("Konwersja do .mp3")
        self.radio_m4a = QRadioButton("Format oryginalny (.m4a)")
        self.radio_video = QRadioButton("Wideo")

        #sprawdza czy jest ffmpeg i udostępnia konwersję
        if check_ffmpeg():
            self.radio_mp3.setChecked(True)
            self.log("ffpmeg obecne<br>")
        else:
            self.radio_mp3.setEnabled(False)
            self.radio_m4a.setChecked(True)
            self.log("Brak ffmpeg do konwersji .mp3<br>")
            self.log("Pobierz i zainstaluj bibliotekę ręcznie z <a href='https://ffmpeg.org/download.html'>https://ffmpeg.org/download.html</a>")

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_mp3, MODE_MP3)
        self.mode_group.addButton(self.radio_m4a, MODE_AUDIO)
        self.mode_group.addButton(self.radio_video, MODE_VIDEO)

        #tabela
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["id", "Info","St", "Status"])
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

        #numer startowy
        self.start_input_label = QLabel("Start od")
        self.start_input = QSpinBox()
        self.start_input.setRange(1, 9999)  # 4 cyfry
        self.start_input.setValue(1)
        self.start_input.setFixedWidth(100)
        self.start_input.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)

        #btn_download stopu
        self.stop_btn: QPushButton = QPushButton("STOP")
        self.stop_btn.clicked.connect(self.stop_download)

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


        # --- RESULT window ---
        self.output:QTextBrowser = QTextBrowser()
        self.output.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.output.setMaximumHeight(300)
        self.output.setOpenExternalLinks(False)
        self.output.anchorClicked.connect(open_folder)

        # --- LAYOUT ---
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.output)
        self.splitter.setSizes([350, 150])

        self.layout.addLayout(top_layout)
        self.layout.addLayout(radio_layout)
        self.layout.addWidget(self.splitter)
        self.setLayout(self.layout)

        self._last_update = time.time()

        self.log("<br><b>Gotowy</b><br>Wklej link do filmu lub playlisty youtube, wybierz format wyjściowy, kliknij Pobierz. Pliki znajdziesz w katalogu użytkownika Pobrane, link pojawi się poniżej. Playlisty będą się zapisywać w podfolderach.")

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

    def add_row(self, row_id: str, url: str, folder: Path):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0 - id
        self.table.setItem(row, 0, QTableWidgetItem(str(row_id)))
        # 1 - info
        self.table.setItem(row, 1, QTableWidgetItem(str(url)))
        # 3 - status
        self.table.setItem(row, 3, ProgressItem("", 0))
        # 2 - button
        btn:QPushButton = QPushButton('')
        btn.setFlat(True)

        btn.clicked.connect(lambda _, r=row: self.on_button_click(r))
        self.table.setCellWidget(row, 2, btn)
        self.set_data(row, ROLE_URL, url)
        self.set_data(row, ROLE_STATUS, STATUS_PENDING)
        self.set_data(row, ROLE_FOLDER, folder)
        self.set_data(row, ROLE_PRECENT, 0)
        QApplication.processEvents()
        return row


    def update_row(self, row:int, msg: str = None, column: int=3):
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
        progress=self.table.item(row, 3)
        if progress:
            progress.set_percent(self.get_data(row, ROLE_PRECENT))
        self.table.scrollToItem(self.table.item(row, 0))
        QApplication.processEvents()

    def process_video(self, url:str):
        try:
            self.table.setRowCount(0)
            downloads = Path.home() / "Downloads"
            playlist_folder = downloads / "yt-downloader"
            playlist_folder.mkdir(parents=True, exist_ok=True)
            path = str(playlist_folder)
            self.log(f"Folder zapisu: <a href='file:///{playlist_folder}'>{playlist_folder}</a>")
            self.stop_flag = False
            row = self.add_row("1", url , playlist_folder )
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

    def process_playlist(self, url:str):
        try:
            self.table.setRowCount(0)
            pl = Playlist(url=url)
            if not pl.video_urls:
                raise Exception("Nie udało się załadować playlisty (Brak playlisty / CAPTCHA / brak dostępu)")

            self.log(f"<br><b>Playlista</b>: {pl.title}")
            imax=len(pl.video_urls)

            id_start = self.start_input.value()
            self.log(f" Liczba filmów: {imax}, zaczynamy od {id_start}")

            downloads = Path.home() / "Downloads"
            playlist_folder = downloads / "yt-downloader" / safe_name(pl.title)
            playlist_folder.mkdir(parents=True, exist_ok=True)
            path = str(playlist_folder)
            self.log(f"Folder zapisu: <a href='file:///{playlist_folder}'>{playlist_folder}</a>")
            self.stop_flag = False
            urls = list(pl.video_urls)
            for i, url in enumerate(urls[0:], start=1):
                row = self.add_row(f"{i}/{imax}", url, playlist_folder)
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

    #zapisuje dane w wierszu
    def set_data(self, row: int, role, data):
        self.table.item(row, 0).setData(role, data)

    # odczytuje dane z wiersza
    def get_data(self, row: int, role):
        return self.table.item(row, 0).data(role)

    def download_with_retry(self, row: int, retries: int=3):
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                if self.stop_flag:
                    raise UserBreakException("Zatrzymane przez użytkownika")

                ret = self.download_and_convert(row)
                self.set_data(row, ROLE_STATUS, STATUS_FINISHED)
                self.update_row(row,f"Zakończone: {Path(ret).name}")
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
        url=self.get_data(row, ROLE_URL)
        folder=self.get_data(row, ROLE_FOLDER)

        yt = YouTube(url, on_progress_callback=self.on_progress)
        self.update_row(row, yt.title,1)
        if self.mode_group.checkedId() == MODE_VIDEO:
            yt_file=yt.streams.filter(
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


            base = os.path.splitext(os.path.basename(filepath))[0]
            output_file = os.path.join(folder, base + ".mp3")

            result=self.convert_to_mp3(filepath, output_file, row)
            if result.returncode:
                raise Exception(result.stderr)

            self.set_data(row, ROLE_STATUS, STATUS_FINISHED)
            self.update_row(row, f"zapisane {os.path.basename(output_file)}")

            os.remove(filepath)
            return output_file
        else:
            return filepath


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
        #self.output.append(msg)
        print(re.sub(r"<[^>]*>", "", msg))
        if hasattr(self, 'output') and self.output is not None:
            # Jeśli bufor nie jest pusty, wyświetl go najpierw
            if self._msg:
                self.output.setHtml(self.output.toHtml() + self._msg)
                self._msg = ""  # Opróżnij bufor

            # Wyświetl bieżącą wiadomość
            self.output.setHtml(self.output.toHtml() + msg)
            self.output.verticalScrollBar().setValue(
                self.output.verticalScrollBar().maximum()
            )

            now = time.time()
            if now - self._last_update > 0.05:
                QApplication.processEvents()
                self._last_update = now
        else:
            # Output nie istnieje - dopisz do bufora
            self._msg += msg

    def convert_to_mp3(self, input_file, output_file, row:int, bitrate:int=160) :
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())