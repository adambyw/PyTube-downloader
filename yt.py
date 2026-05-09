import sys
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextBrowser,
    QTableWidget, QTableWidgetItem, QSpinBox, QLabel, QSizePolicy, QSplitter,
    QRadioButton, QButtonGroup
)
from PyQt6.QtCore import Qt, QUrl
from urllib.parse import urlparse, parse_qs
import time
import os
import subprocess
from pytubefix import YouTube, Playlist
from pathlib import Path

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

STATUS_PENDING =     Status( 1, "⏳")
STATUS_DOWNLOADING = Status( 2, "🔽")
STATUS_CONVERSION =  Status( 3, "🔄")
STATUS_FINISHED =    Status( 4, "✅")
STATUS_ERROR =       Status(-1, "❌")
STATUS_USER_BREAK =  Status(-2, "🛑")


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


class App(QWidget):
    
    def __init__(self):
        super().__init__()

        self.stop_flag = False
        self.current_row: int|None = None
        self.setWindowTitle("YT Down")
        self.resize(1000, 500)

        self.layout = QVBoxLayout()

        # --- TOP: input + btn_download ---
        top_layout = QHBoxLayout()

        self.input:QLineEdit = QLineEdit()
        self.input.setPlaceholderText("Wpisz link do playlisty YT...")

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
        else:
            self.radio_mp3.setEnabled(False)
            self.radio_m4a.setChecked(True)
            self.log("Brak ffmpeg do konwersja .mp3")

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_mp3, MODE_MP3)
        self.mode_group.addButton(self.radio_m4a, MODE_AUDIO)
        self.mode_group.addButton(self.radio_video, MODE_VIDEO)

        #tabela
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["id", "Info","St", "Status"])
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 500)
        self.table.setColumnWidth(2, 20)
        self.table.setColumnWidth(3, 400)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
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

    def stop_download(self):
        self.stop_flag = True
        self.log("Wymuszenie STOPu")

    def handle_pobierz(self):
        url = self.input.text().strip()
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        playlist = params.get("list", [None])[0]

        if list:
            self.log(f"Playlista: {playlist}<br>")
            print(f"Playlista: {playlist}")
            self.process_playlist(url)
        else:
            self.log (f"Brak playlisty sprawdzam video")
            self.log("Brak playlisty sprawdzam video")
            self.process_video(url)

    def add_row(self, row_id: str, url: str, folder: Path):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0 - id
        self.table.setItem(row, 0, QTableWidgetItem(str(row_id)))
        # 1 - info
        self.table.setItem(row, 1, QTableWidgetItem(str(url)))
        # 3 - status
        self.table.setItem(row, 3, QTableWidgetItem(''))
        # 2 - button
        btn:QPushButton = QPushButton('')
        btn.setFlat(True)

        btn.clicked.connect(lambda _, r=row: self.on_button_click(r))
        self.table.setCellWidget(row, 2, btn)
        self.set_data(row, ROLE_URL, url)
        self.set_data(row, ROLE_STATUS, STATUS_PENDING)
        self.set_data(row, ROLE_FOLDER, folder)
        QApplication.processEvents()
        return row


    def update_row(self, row:int, msg: str, column: int=3):
        print(msg)
        item = self.table.item(row, column)
        if item:
            item.setText(msg)
        else:
            self.table.setItem(row, column, QTableWidgetItem(msg))

        btn = self.table.cellWidget(row, 2)
        status = self.get_data(row, ROLE_STATUS)
        if btn and item:
            btn.setText(status.icon)

        self.table.scrollToBottom()
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
        except Exception as e:
            self.log(f"Błąd pobierania video: {e}")
            import traceback
            traceback.print_exc()

    def process_playlist(self, url:str):
        try:
            self.table.setRowCount(0)
            pl = Playlist(url=url)
            self.log(f"<br><b>Playlista</b>: {pl.title}")

            imax=len(pl.video_urls)
            if imax < 1:
                raise Exception("Link do playlisty nie zawiera filmów. Sprawdź captcha")
            id_start = self.start_input.value()
            self.log(f" Liczba filmów: {imax}, zaczynamy od {id_start}")

            downloads = Path.home() / "Downloads"
            playlist_folder = downloads / "yt-downloader" / safe_name(pl.title)
            playlist_folder.mkdir(parents=True, exist_ok=True)
            path = str(playlist_folder)
            self.log(f"Folder zapisu: <a href='file:///{playlist_folder}'>{playlist_folder}</a>")
            self.stop_flag = False
            urls = list(pl.video_urls)
            for i, url in enumerate(urls[id_start - 1:], start=id_start):
                if self.stop_flag:
                    break
                row = self.add_row(f"{i}/{imax}", url , playlist_folder)
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
                self.update_row(row,f"Zakończone {ret}")
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
            if status_code == STATUS_ERROR:
                self.download_and_convert(row)
        except Exception as e:
            self.log(str(e))

    def download_and_convert(self, row: int):
        self.set_data(row, ROLE_STATUS, STATUS_DOWNLOADING)
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
        percent = int((1 - bytes_remaining / total) * 100)
        self.update_row(self.current_row, f"Pobieranie {percent}%")
        if self.stop_flag:
            raise Exception("Przerwane przez użytkownika")

    def log(self, msg: str):
        #self.output.append(msg)
        print(msg)
        self.output.setHtml(self.output.toHtml() + msg)
        self.output.verticalScrollBar().setValue(
            self.output.verticalScrollBar().maximum()
        )
        now = time.time()
        if now - self._last_update > 0.05:  # 50ms
            QApplication.processEvents()
            self._last_update = now

    def convert_to_mp3(self, input_file, output_file, row:int) :
        duration = get_duration(input_file)
        if duration == 0:
            raise Exception("Czas trwania pliku to 0s") 

        process = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-i", input_file,
                "-vn",
                "-ab", "160k",
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
                self.update_row(row, f"Konwersja do mp3: {percent}%")

        process.wait()

        return output_file

    # Sprawdza czy jest ffmpeg i zwraca true jak jest


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())