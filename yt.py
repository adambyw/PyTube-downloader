import code
import sys
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QListWidget, QListWidgetItem, QCheckBox, QTextBrowser,
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

class App(QWidget):
    ROLE_URL = Qt.ItemDataRole.UserRole
    ROLE_STATUS = Qt.ItemDataRole.UserRole + 1
    ROLE_PLAYLIST_FOLDER = Qt.ItemDataRole.UserRole + 1

    STATUS_PENDING={'code':1, 'icon':"⏳"}
    STATUS_DOWLOADING = {'code': 2, 'icon': "🔽"}
    STATUS_CONVERSION = {'code': 3, 'icon': "🔄"}
    STATUS_FINISHED={'code':4, 'icon':"✅"}
    STATUS_ERROR={'code':-1, 'icon':"❌"}
    STATUS_USER_BREAK={'code':-2, 'icon':"🛑"}

    def __init__(self):
        super().__init__()

        self.stop_flag = False
        self.current_row = None
        self.setWindowTitle("YT Down")
        self.resize(1000, 500)

        self.layout = QVBoxLayout()

        # --- TOP: input + btn_download ---
        top_layout = QHBoxLayout()

        self.input = QLineEdit()
        self.input.setPlaceholderText("Wpisz link do playlisty YT...")

        self.btn_download = QPushButton("Pobierz")
        self.btn_download.clicked.connect(self.handle_submit)

        # enter jako submit
        self.input.returnPressed.connect(self.handle_submit)

        #radio dla konwersji
        self.radio_oryginal = QRadioButton("Format oryginalny (.m4a)")
        self.radio_mp3 = QRadioButton("Konwersja do .mp3")
        self.radio_mp3.setChecked(True)
        self.mode_group = QButtonGroup(self)

        self.mode_group.addButton(self.radio_mp3, 1)
        self.mode_group.addButton(self.radio_oryginal, 0)

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
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.clicked.connect(self.stop_download)

        top_layout.addWidget(self.input)
        top_layout.addWidget(self.start_input_label)
        top_layout.addWidget(self.start_input)
        top_layout.addWidget(self.btn_download)
        top_layout.addWidget(self.stop_btn)

        radio_layout = QHBoxLayout()
        radio_layout.addWidget(self.radio_mp3, alignment=Qt.AlignmentFlag.AlignLeft)
        radio_layout.addSpacing(20)
        radio_layout.addWidget(self.radio_oryginal, alignment=Qt.AlignmentFlag.AlignLeft)
        radio_layout.addStretch()


        # --- RESULT LIST ---
        self.output = QTextBrowser()
        self.output.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.output.setMaximumHeight(300)
        self.output.setOpenExternalLinks(False)
        self.output.anchorClicked.connect(self.open_folder)

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

    def handle_submit(self):
        url = self.input.text().strip()
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        list = params.get("list", [None])[0]

        html=f"list: {list}<br>"
        self.log(html)
        if list:
            self.get_url(url)

    def add_row(self, cols):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 0 - id
        self.table.setItem(row, 0, QTableWidgetItem(str(cols[0])))

        # 1 - info
        self.table.setItem(row, 1, QTableWidgetItem(str(cols[1])))

        # 3 - status
        self.table.setItem(row, 3, QTableWidgetItem(str(cols[2])))

        # 2 - button
        btn = QPushButton('')
        btn.setFlat(True)

        btn.clicked.connect(lambda _, r=row: self.on_button_click(r))
        self.table.setCellWidget(row, 2, btn)

        QApplication.processEvents()
        return row


    def update_row(self, row, msg, column=3):
        print(msg)
        item = self.table.item(row, column)
        if item:
            item.setText(msg)
        else:
            self.table.setItem(row, column, QTableWidgetItem(msg))

        btn = self.table.cellWidget(row, 2)
        status = self.get_data(row, self.ROLE_STATUS)
        if btn and item:
            btn.setText(status["icon"])

        self.table.scrollToBottom()
        QApplication.processEvents()

    def open_folder(self, url: QUrl):
        path = url.toLocalFile()
        import subprocess
        if os.name == "nt":  # Windows
            os.startfile(path)
        elif os.name == "posix":  # Linux/Mac
            subprocess.Popen(["xdg-open", path])

    def get_url(self, url):
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
                row = self.add_row([f"{i}/{imax}", url ,"--" ])
                self.set_data(row, self.ROLE_URL, url)
                self.set_data(row, self.ROLE_STATUS, self.STATUS_PENDING)
                self.set_data(row, self.ROLE_PLAYLIST_FOLDER, playlist_folder)

                try:
                    self.download_with_retry(url, playlist_folder, row)
                except Exception as e:
                    self.update_row(row, f"Błąd YT: {str(e)}")
        except Exception as e:
            self.log(f"Błąd pętli: {e}")
            import traceback
            traceback.print_exc()

    def set_data(self, row, role, data):
        self.table.item(row, 0).setData(role, data)

    def get_data(self, row, role):
        return self.table.item(row, 0).data(role)

    def download_with_retry(self, url, playlist_folder, row, retries=3):
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                if self.stop_flag:
                    raise UserBreakException("Zatrzymane przez użytkownika")

                ret = self.download_and_convert(url, playlist_folder, row)
                self.set_data(row, self.ROLE_STATUS, self.STATUS_FINISHED)
                self.update_row(row,f"Zakończone {ret}")
                return ret

            except UserBreakException as e:
                self.set_data(row, self.ROLE_STATUS, self.STATUS_USER_BREAK)
                last_error = e
                self.update_row(row, str(e))

            except Exception as e:
                last_error = e
                self.set_data(row, self.ROLE_STATUS, self.STATUS_ERROR)
                self.update_row(row, f"Ponawiam {attempt}/{retries} ponieważ: {str(e)}")
        raise last_error

    def on_button_click(self, row):
        try:
            status_code = self.get_data(row, self.ROLE_STATUS)
            if status_code == self.STATUS_ERROR:
                self.download_and_convert(
                    self.get_data(row, self.ROLE_URL),
                    self.get_data(row, self.ROLE_PLAYLIST_FOLDER),
                    row)
        except Exception as e:
            self.log(str(e))

    def download_and_convert(self, url, playlist_folder, row):
        self.set_data(row, self.ROLE_STATUS, self.STATUS_DOWLOADING)
        self.update_row(row, f"Pobieranie")
        self.current_row = row

        yt = YouTube(url, on_progress_callback=self.on_progress)
        self.update_row(row, yt.title,1)
        yt_file = yt.streams.get_audio_only()
        if yt_file is None:
            raise Exception("Brak audio streamu")

        filepath = yt_file.download(output_path=playlist_folder)
        self.set_data(row, self.ROLE_STATUS, self.STATUS_FINISHED)
        self.update_row(row, "Pobrano")

        if self.mode_group.checkedId():
            self.set_data(row, self.ROLE_STATUS, self.STATUS_CONVERSION)
            self.update_row(row, "Konwersja do mp3...")
            base = os.path.splitext(os.path.basename(filepath))[0]
            output_file = os.path.join(playlist_folder, base + ".mp3")

            result=self.convert_to_mp3(filepath, output_file)
            if result.returncode:
                raise Exception(result.stderr)

            self.set_data(row, self.ROLE_STATUS, self.STATUS_FINISHED)
            self.update_row(row, f"zapisane {os.path.basename(output_file)}")

            os.remove(filepath)
            return output_file
        else:
            return filepath


    def on_progress(self, stream, chunk, bytes_remaining):
        total = stream.filesize
        percent = int((1 - bytes_remaining / total) * 100)

        self.update_row(self.current_row, f"Pobieranie {percent}%")

    def log(self, msg):
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

    def convert_to_mp3(self, input_file, output_file):
        return subprocess.run([
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-i", input_file,
            "-vn",
            "-ab", "160k",
            output_file
        ], check=True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())