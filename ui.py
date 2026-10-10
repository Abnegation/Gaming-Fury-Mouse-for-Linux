"""UI для мыши Fury (PyQt6): режимы подсветки колёсика + цвет общей подсветки."""
"""                         Разработал @UnionMe                               """
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QGridLayout,
                             QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider,
                             QTabWidget, QVBoxLayout, QWidget)

from backend import MODES, FuryMouse

DEFAULT_CYCLE = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff", "#ff8000", "#ffffff"]


def make_picker(parent, color, slot):
    p = QColorDialog(color, parent)
    p.setWindowFlags(Qt.WindowType.Widget)
    p.setOptions(QColorDialog.ColorDialogOption.NoButtons | QColorDialog.ColorDialogOption.DontUseNativeDialog)
    p.currentColorChanged.connect(slot)
    return p


def make_slider(lo, hi, val, slot):
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    s.valueChanged.connect(slot)
    return s


class WheelTab(QWidget):
    """Колёсико: 12 режимов драйвера (команды 0x0C/0x0D) + цвета цикла."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.color = QColor(255, 0, 0)
        self.cycle_colors = [QColor(c) for c in DEFAULT_CYCLE]
        self.timer = QTimer(self, singleShot=True, interval=80)
        self.timer.timeout.connect(self.apply)
        self.cycle_dirty = False

        self.mode = QComboBox()
        for i, (name, _) in MODES.items():
            self.mode.addItem(f"{i}: {name}", i)
        self.mode.setCurrentIndex(8)
        self.mode.currentIndexChanged.connect(self.on_mode)

        self.bright = make_slider(0, 255, 255, self.changed)
        self.speed = make_slider(0, 4, 3, self.changed)      # 0 медленно ... 4 быстро
        self.length = make_slider(0, 4, 2, self.changed)     # 0 коротко ... 4 длинно
        self.rows = {}
        grid = QGridLayout()
        for r, (key, title, w) in enumerate([("brightness", "Яркость", self.bright),
                                             ("speed", "Скорость", self.speed),
                                             ("length", "Длина", self.length)]):
            lab = QLabel(title)
            grid.addWidget(lab, r, 0)
            grid.addWidget(w, r, 1)
            self.rows[key] = (lab, w)

        self.cycle = QCheckBox("Цикл цветов (вместо фиксированного)")
        self.cycle.toggled.connect(self.on_cycle)
        self.picker = make_picker(self, self.color, self.on_color)

        self.cyc_box = QWidget()
        cl = QHBoxLayout(self.cyc_box)
        cl.addWidget(QLabel("Цвета цикла:"))
        self.cyc_btns = []
        for i in range(8):
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.clicked.connect(lambda _=False, i=i: self.pick_cycle(i))
            self.cyc_btns.append(b)
            cl.addWidget(b)
        self.paint_cycle()

        lay = QVBoxLayout(self)
        lay.addWidget(self.mode)
        lay.addLayout(grid)
        lay.addWidget(self.cycle)
        lay.addWidget(self.cyc_box)
        lay.addWidget(self.picker)
        self.on_mode()

    def paint_cycle(self):
        for b, c in zip(self.cyc_btns, self.cycle_colors):
            b.setStyleSheet(f"background:{c.name()}; border:1px solid #666;")

    def pick_cycle(self, i):
        c = QColorDialog.getColor(self.cycle_colors[i], self, "Цвет цикла",
                                  QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if c.isValid():
            self.cycle_colors[i] = c
            self.cycle_dirty = True
            self.paint_cycle()
            self.changed()

    def params(self):
        return MODES[self.mode.currentData()][1]

    def on_mode(self, *_):
        p = self.params()
        for key, (lab, w) in self.rows.items():
            lab.setVisible(key in p)
            w.setVisible(key in p)
        has_color = "color" in p
        self.cycle.setVisible(has_color)
        self.on_cycle()
        self.changed()

    def on_cycle(self, *_):
        has_color = "color" in self.params()
        cyc = self.cycle.isChecked() and has_color
        self.picker.setVisible(has_color and not cyc)
        self.cyc_box.setVisible(cyc)

    def on_color(self, c):
        self.color = c
        self.changed()

    def changed(self, *_):
        self.timer.start()

    def apply(self, save=False):
        self.win.apply_wheel(self, save)


class BodyTab(QWidget):
    """Общая подсветка: цвет таблицы DPI (во все 8 слотов)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.color = QColor(255, 0, 0)
        self.timer = QTimer(self, singleShot=True, interval=80)
        self.timer.timeout.connect(self.apply)
        lay = QVBoxLayout(self)
        lay.addWidget(make_picker(self, self.color, self.on_color))

    def on_color(self, c):
        self.color = c
        self.timer.start()

    def apply(self, save=False):
        self.win.apply_body(self, save)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fury Mouse RGB")
        self.mouse = FuryMouse.from_config()
        self.tabs = QTabWidget()
        self.wheel = WheelTab(self)
        self.body = BodyTab(self)
        self.tabs.addTab(self.wheel, "Колёсико (режимы)")
        self.tabs.addTab(self.body, "Общая подсветка")
        self.save_cb = QCheckBox("Записывать в память мыши (команда сохранения)")
        self.save_cb.toggled.connect(lambda _: self.tabs.currentWidget().apply(self.save_cb.isChecked()))
        self.status = QLabel(f"Устройство: {self.mouse.node}")
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(self.save_cb)
        lay.addWidget(self.status)

    def _run(self, fn, text):
        try:
            if not self.mouse.is_open:
                self.mouse.open()
            fn()
            self.status.setText(text)
        except Exception as e:  # noqa: BLE001
            self.status.setText("Ошибка")
            QMessageBox.warning(self, "Ошибка", str(e))
            self.mouse.close()

    def apply_wheel(self, t: WheelTab, save: bool):
        save = save or self.save_cb.isChecked()
        mode = t.mode.currentData()
        cyc = t.cycle.isChecked() and "color" in t.params()

        def go():
            if cyc and t.cycle_dirty:
                self.mouse.set_cycle_colors([(c.red(), c.green(), c.blue()) for c in t.cycle_colors], save=False)
                t.cycle_dirty = False
            self.mouse.set_effect(mode, (t.color.red(), t.color.green(), t.color.blue()),
                                  t.bright.value(), t.speed.value(), t.length.value(), cyc, save=save)

        self._run(go, f"Колёсико: {MODES[mode][0]}" + (" (сохранено)" if save else ""))

    def apply_body(self, t: BodyTab, save: bool):
        save = save or self.save_cb.isChecked()
        c = t.color
        self._run(lambda: self.mouse.set_dpi_colors(c.red(), c.green(), c.blue(), save=save),
                  f"Общая: {c.name()}" + (" (сохранено)" if save else ""))

    def closeEvent(self, e):
        self.mouse.close()
        super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
