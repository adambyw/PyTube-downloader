from PyQt6.QtWidgets import QTableWidgetItem, QStyledItemDelegate, QStyle
from PyQt6.QtCore import Qt, QRect, QPointF
from PyQt6.QtGui import QColor, QLinearGradient

DEFAULT_PROGRESSBAR_COLOR = "#96e5b8"

class ProgressItem(QTableWidgetItem):
    """
    QTableWidgetItem z paskiem postępu w tle.
    Użycie:
        item = ProgressItem("Tekst", 50)  # 50% wypełnienia
        item.set_percent(75)  # zmiana na 75%
        item.set_progress_olor("#00ff00")  # zmiana koloru na zielony
        table.setItem(row, col, item)
    """

    def __init__(self, text="", percent:float=0, color=DEFAULT_PROGRESSBAR_COLOR):
        super().__init__(text)
        self._percent: float = max(0.0, min(100.0, float(percent)))
        self._color = QColor(color)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_percent(self, percent: float):
        """
        Ustawia procent wypełnienia (0-100)
        """
        self._percent = max(0.0, min(100.0, float(percent)))
        # Wymuś odświeżenie komórki
        if self.tableWidget():
            self.tableWidget().viewport().update()

    def get_percent(self) -> float:
        """
        Zwraca aktualny procent wypełnienia
        """
        return self._percent

    def set_progress_color(self, color):
        """
        Ustawia kolor paska postępu.
        Akceptuje: QString "#rrggbb", QColor lub nazwę koloru "red"
        """
        if isinstance(color, str):
            self._color = QColor(color)
        elif isinstance(color, QColor):
            self._color = color
        else:
            self._color = QColor(color)

        # Wymuś odświeżenie
        if self.tableWidget():
            self.tableWidget().viewport().update()

    def get_progress_color(self):
        """
        Zwraca kolor paska jako QColor
        """
        return self._color

    def clone(self):
        """
        Klonowanie obiektu (potrzebne przy kopiowaniu)
        """
        item = ProgressItem(self.text(), self._percent, self._color.name())
        return item

    # Przechowujemy dane dla delegate-a
    def data(self, role):
        if role == Qt.ItemDataRole.UserRole:
            return {
                'percent': self._percent,
                'color': self._color
            }
        return super().data(role)

    def setData(self, role, value):
        if role == Qt.ItemDataRole.UserRole and isinstance(value, dict):
            if 'percent' in value:
                self.set_percent(value['percent'])
            if 'color' in value:
                self.set_progress_color(value['color'])
        else:
            super().setData(role, value)


class ProgressDelegate(QStyledItemDelegate):
    """
    Delegate do rysowania paska postępu dla ProgressItem
    """

    def paint(self, painter, option, index):
        # Sprawdź czy to ProgressItem
        item = index.data(Qt.ItemDataRole.UserRole)

        if item and isinstance(item, dict) and 'percent' in item:
            percent = item['percent']
            color = item.get('color', QColor(DEFAULT_PROGRESSBAR_COLOR))

            painter.save()

            # Rysuj tło zaznaczenia jeśli jest zaznaczone
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())

            # Oblicz szerokość paska (width: x%)
            bar_width = int(option.rect.width() * (percent / 100.0))

            if bar_width > 0:
                bar_rect = QRect(option.rect.x(), option.rect.y(),
                                 bar_width, option.rect.height())

                # Gradient dla ładniejszego efektu
                gradient = QLinearGradient(
                    QPointF(bar_rect.topLeft()),
                    QPointF(bar_rect.topRight())
                )
                gradient.setColorAt(0, color.lighter(110))
                gradient.setColorAt(1, color.darker(110))

                painter.fillRect(bar_rect, gradient)

            # Rysuj tekst
            text = index.data(Qt.ItemDataRole.DisplayRole)
            if text:
                # Dla średnich wartości - sprawdź jasność tła
                brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
                painter.setPen(Qt.GlobalColor.white if brightness < 128 else Qt.GlobalColor.black)

                painter.drawText(option.rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

            painter.restore()
        else:
            # Dla zwykłych itemów - standardowe rysowanie
            super().paint(painter, option, index)