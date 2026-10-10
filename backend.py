"""
Backend для мыши Holtek 04D9:A09F (драйвер Fury_3327, сенсор 3327).

Протокол (восстановлен из MouseDriver.exe / MSDriver.dll):
  * транспорт: HID Feature Report, report ID = 0, 8 байт данных
    (HidD_SetFeature с буфером 9 байт = [0x00] + 8 байт);
  * пакет: [cmd, d1..d6, checksum], checksum = (0xFF - sum(первых 7 байт)) & 0xFF;
  * если в Data.ini Password из 6 символов (у вас FB1255) — пакет шифруется
    функцией scramble() (перестановка + XOR с ключом + сдвиг на 3 бита + константы);
  * 0x0C [R, G, B]              — фиксированный цвет;
  * 0x0D [mode, brightness, cm] — режим подсветки (8 = "Standard"), cm: 0 = фикс. цвет;
  * 08 AA CC EE                 — фиксация/запись (используется драйвером в конце профиля).

Интерфейс 2 (HID, Usage Page 0xFF00) не имеет драйвера ядра -> hidraw-узла нет,
поэтому шлём SET_REPORT (Feature, ID 0, 8 байт) напрямую через usbfs (/dev/bus/usb).
Никаких зависимостей: ни pyusb, ни libusb.

Использование:
  python backend.py list            - проверить, видна ли мышь по USB
  python backend.py probe           - автоподбор рабочих параметров (спрашивает "изменился ли цвет?")
  python backend.py color ff8000    - выставить цвет (hex)  [--brightness 0..255] [--save]
  python backend.py effect 4 --speed 3   - режим 0..11 (см. MODES) [--color --brightness --speed --length --cycle --save]
  python backend.py dpi ff0000      - цвет DPI-таблицы (общая подсветка) [--save]
  python backend.py info            - сырой ответ устройства (для отладки)
"""


"""                         Разработал @UnionMe                               """


from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, asdict

VID = 0x04D9
PID = 0xA09F
PASSWORD = b"FB1255"          # Password из Data.ini
CONFIG_PATH = os.path.expanduser("~/.config/fury_mouse.json")

MODE_STANDARD = 8             # "Standard" (постоянный цвет)

# 12 режимов драйвера (lan.xml, light_mode_N_text -> индекс N-1) и какие параметры в них используются
# (разобрано по MouseDriver.exe:0x418d77..0x418e0e)
MODES = {
    0: ("Off", ()),
    1: ("Ripple breathing", ("brightness", "color")),
    2: ("Lightning breath", ("speed", "color")),
    3: ("Neon", ("speed", "brightness")),
    4: ("Wave", ("speed", "brightness")),
    5: ("Key reaction", ("brightness", "color")),
    6: ("Trailing", ("length", "color")),
    7: ("Drag back and forth", ("length", "color")),
    8: ("Standard", ("brightness", "color")),
    9: ("YO-YO", ("brightness", "speed", "color")),
    10: ("Marbles", ("brightness", "speed")),
    11: ("Flying star", ("brightness", "color")),
}
SEND_DELAY = 0.02             # драйвер делает Sleep(20) после каждого пакета

IFACE = 2                     # HID-интерфейс с Usage Page/Usage 0xFF00 (его открывает драйвер)
UDEV_RULE = ('SUBSYSTEM=="usb", ATTR{idVendor}=="04d9", ATTR{idProduct}=="a09f", '
             'MODE="0660", TAG+="uaccess"')


# ---------------------------------------------------------------- packets
def make_packet(cmd: int, *data: int) -> bytes:
    body = [cmd, *data]
    if len(body) > 7:
        raise ValueError("слишком длинный пакет")
    body += [0] * (7 - len(body))
    body = [b & 0xFF for b in body]
    chk = (0xFF - sum(body)) & 0xFF
    return bytes(body + [chk])


def scramble(p: bytes, key: bytes = PASSWORD) -> bytes:
    """Точный порт MouseDriver.exe:0x414660."""
    k = key
    q = [p[5], p[4] ^ k[0], p[7] ^ k[1], p[6] ^ k[2],
         p[1] ^ k[3], p[0] ^ k[4], p[3] ^ k[5], p[2]]
    add = [0x25, -0x0A, -0x1C, 0x76, 0x47, 0x54, -0x1A, 0x76]
    out = []
    for i in range(8):
        v = ((q[i] << 3) & 0xFF) | (q[(i + 1) % 8] >> 5)
        out.append((v + add[i]) & 0xFF)
    return bytes(out)


# ---------------------------------------------------------------- usbfs
def _ioc(d: int, nr: int, size: int) -> int:
    return (d << 30) | (size << 16) | (ord("U") << 8) | nr


_CTRL_FMT = "@BBHHHIP"                                   # struct usbdevfs_ctrltransfer
USBDEVFS_CONTROL = _ioc(3, 0, struct.calcsize(_CTRL_FMT))
_BULK_FMT = "@IIIP"                                      # struct usbdevfs_bulktransfer
USBDEVFS_BULK = _ioc(3, 2, struct.calcsize(_BULK_FMT))
EP_OUT = 0x03                                            # ep_03 интерфейса 2 (interrupt OUT)
USBDEVFS_CLAIMINTERFACE = _ioc(2, 15, 4)
USBDEVFS_RELEASEINTERFACE = _ioc(2, 16, 4)


def find_device() -> str | None:
    """Путь /dev/bus/usb/BBB/DDD устройства 04d9:a09f (через sysfs)."""
    base = "/sys/bus/usb/devices"
    for name in sorted(os.listdir(base)):
        try:
            rd = lambda f: open(f"{base}/{name}/{f}").read().strip()
            if int(rd("idVendor"), 16) == VID and int(rd("idProduct"), 16) == PID:
                return f"/dev/bus/usb/{int(rd('busnum')):03d}/{int(rd('devnum')):03d}"
        except (OSError, ValueError):
            continue
    return None


@dataclass
class Config:
    encrypt: bool = True
    commit: bool = False

    def save(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @staticmethod
    def load() -> "Config":
        try:
            with open(CONFIG_PATH) as f:
                d = json.load(f)
            return Config(bool(d.get("encrypt", True)), bool(d.get("commit", False)))
        except (OSError, ValueError, TypeError):
            return Config()


class FuryMouse:
    node = f"USB {VID:04x}:{PID:04x}, interface {IFACE}"

    def __init__(self, encrypt: bool = True, commit: bool = False):
        self.encrypt = encrypt
        self.commit = commit
        self.dev = None

    @classmethod
    def from_config(cls) -> "FuryMouse":
        c = Config.load()
        return cls(c.encrypt, c.commit)

    @property
    def is_open(self) -> bool:
        return self.dev is not None

    def open(self):
        path = find_device()
        if path is None:
            raise RuntimeError("Мышь 04d9:a09f не найдена по USB. Подключена ли она?")
        try:
            fd = os.open(path, os.O_RDWR)
        except PermissionError:
            raise PermissionError(
                f"Нет доступа к {path}. Создайте udev-правило:\n"
                f"  echo '{UDEV_RULE}' | sudo tee /etc/udev/rules.d/60-fury-usb.rules\n"
                "  sudo udevadm control --reload && sudo udevadm trigger\n"
                "и переподключите мышь.")
        try:
            fcntl.ioctl(fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", IFACE))
        except OSError:
            os.close(fd)
            raise
        self.dev = fd
        return self

    def close(self):
        if self.dev is not None:
            try:
                fcntl.ioctl(self.dev, USBDEVFS_RELEASEINTERFACE, struct.pack("I", IFACE))
            except OSError:
                pass
            os.close(self.dev)
            self.dev = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # --- low level
    def _wire(self, packet: bytes) -> bytes:
        return scramble(packet) if self.encrypt else packet

    def _ctrl(self, req_type: int, request: int, data: bytes | int) -> bytes:
        out = isinstance(data, (bytes, bytearray))
        length = len(data) if out else data
        buf = ctypes.create_string_buffer(bytes(data) if out else length, length)
        arg = bytearray(struct.pack(_CTRL_FMT, req_type, request, (3 << 8) | 0x00, IFACE,
                                    length, 1000, ctypes.addressof(buf)))
        n = fcntl.ioctl(self.dev, USBDEVFS_CONTROL, arg, True)   # mutable arg -> возвращает int
        return buf.raw[:n]

    def send(self, packet: bytes):
        """HidD_SetFeature(report ID 0) = SET_REPORT (0x09), тип Feature (3), ID 0, 8 байт."""
        if self.dev is None:
            self.open()
        self._ctrl(0x21, 0x09, self._wire(packet))
        time.sleep(SEND_DELAY)

    def get(self) -> bytes:
        """HidD_GetFeature = GET_REPORT (0x01), тип Feature, ID 0."""
        return self._ctrl(0xA1, 0x01, 8)

    # --- high level
    def set_effect(self, mode: int, color=(255, 0, 0), brightness: int = 255, speed: int = 3,
                   length: int = 2, cycle: bool = False, save: bool | None = None):
        """Режим подсветки (0..11, см. MODES).
        color      - фиксированный RGB (команда 0x0C), используется если cycle=False;
        brightness - 0..255;  speed, length - 0..4 (как слайдеры драйвера);
        cycle      - цикл по таблице цветов вместо фиксированного цвета."""
        clamp = lambda v, hi=255: max(0, min(hi, int(v)))
        brightness, speed, length = clamp(brightness), clamp(speed, 4), clamp(length, 4)
        cm = (1 if mode == 8 else 2) if cycle else 0      # "color mode" из драйвера
        if mode == 0:
            p1 = p2 = 0
        elif mode in (1, 5, 11):
            p1, p2 = brightness, cm
        elif mode == 2:
            p1, p2 = speed, cm
        elif mode in (3, 4):
            p1, p2 = speed, brightness
        elif mode in (6, 7):
            p1, p2 = 4 - length, cm
        elif mode == 8:
            p1, p2 = brightness, cm
        elif mode == 9:
            p1, p2 = brightness, cm | ((4 - speed) << 4)
        elif mode == 10:
            p1, p2 = brightness, 4 - speed
        else:
            raise ValueError("режим 0..11")
        if not cycle and "color" in MODES[mode][1]:
            self.send(make_packet(0x0C, *(clamp(v) for v in color)))
        self.send(make_packet(0x0D, mode, p1, p2))
        if self.commit if save is None else save:
            self.save()

    def set_color(self, r: int, g: int, b: int, brightness: int = 255, save: bool | None = None):
        """Постоянный (Standard) цвет любого RGB."""
        self.set_effect(MODE_STANDARD, (r, g, b), brightness, save=save)

    def set_cycle_colors(self, colors, save: bool | None = None):
        """8 цветов для циклических режимов (таблица page 0, как в драйвере)."""
        if self.dev is None:
            self.open()
        cols = (list(colors) + [(0, 0, 0)] * 8)[:8]
        block = b"".join(bytes(max(0, min(255, int(v))) for v in c) for c in cols).ljust(32, b"\x00")
        self.send(make_packet(0x10, 0x00, 0x18))
        self._write_ep(block)
        if self.commit if save is None else save:
            self.save()

    def _write_ep(self, data: bytes):
        """WriteFile драйвера -> interrupt OUT на ep_03 (32 байта данных)."""
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        arg = bytearray(struct.pack(_BULK_FMT, EP_OUT, len(data), 1000, ctypes.addressof(buf)))
        fcntl.ioctl(self.dev, USBDEVFS_BULK, arg, True)
        time.sleep(SEND_DELAY)

    def set_dpi_colors(self, r: int, g: int, b: int, save: bool | None = None):
        """Цвет DPI-индикатора (колёсико): таблица из 8 слотов, пишем один цвет во все.
        Драйвер: feature [0x10, page=1, len=0x18] + 32 байта (8 x RGB, остаток 0) в ep_03."""
        if self.dev is None:
            self.open()
        rgb = bytes(max(0, min(255, int(v))) for v in (r, g, b))
        block = (rgb * 8).ljust(32, b"\x00")
        self.send(make_packet(0x10, 0x01, 0x18))
        self._write_ep(block)
        if self.commit if save is None else save:
            self.save()

    def save(self):
        self.send(make_packet(0x08, 0xAA, 0xCC, 0xEE))

    def off(self):
        self.send(make_packet(0x0D, 0, 0, 0))

    def info(self) -> bytes:
        """Запрос 0x8B (как в драйвере), возвращает сырой ответ."""
        self.send(make_packet(0x83))
        self.send(make_packet(0x8B))
        return self.get()


# ---------------------------------------------------------------- CLI
def cmd_list():
    d = find_device()
    print("Найдена:", d or "нет (мышь 04d9:a09f по USB не найдена)")


def _ask(q: str) -> str:
    while True:
        a = input(q + " [y/n/q]: ").strip().lower()
        if a in ("y", "n", "q"):
            return a


def cmd_probe():
    if find_device() is None:
        print("Мышь 04d9:a09f по USB не найдена")
        return
    print("Смотрите на подсветку мыши и отвечайте y (цвет изменился на указанный) / n / q (выход).\n")
    for encrypt in (True, False):
        for commit in (False, True):
            m = FuryMouse(encrypt, commit)
            try:
                m.open()
                m.set_color(255, 0, 0, 255)
            except PermissionError as e:
                print(e)
                return
            except OSError as e:
                print(f"encrypt={encrypt} commit={commit}: USB-ошибка: {e}")
                m.close()
                continue
            print(f"encrypt={encrypt} commit={commit}: отправлен КРАСНЫЙ")
            a = _ask("Мышь горит красным?")
            if a == "q":
                m.close()
                return
            if a == "y":
                m.set_color(0, 0, 255, 255)
                b = _ask("Теперь СИНИЙ?")
                if b == "y":
                    m.set_color(255, 128, 0, 255)
                    c = _ask("И ОРАНЖЕВЫЙ (произвольный RGB)?")
                    m.close()
                    Config(encrypt, commit).save()
                    print(f"\nГотово. Сохранено в {CONFIG_PATH}: encrypt={encrypt} commit={commit}")
                    if c != "y":
                        print("Внимание: оранжевый не подтверждён - сообщите, какой цвет получился.")
                    return
            m.close()
    print("\nНи одна комбинация не сработала. Запустите `python backend.py info` и пришлите вывод.")


def cmd_info():
    m = FuryMouse.from_config()
    with m:
        for enc in (True, False):
            m.encrypt = enc
            try:
                print(f"encrypt={enc}: ответ =", m.info().hex(" "))
            except OSError as e:
                print(f"encrypt={enc}: ошибка {e}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("probe")
    sub.add_parser("info")
    e = sub.add_parser("effect", help="режим 0..11, напр.: effect 4 --speed 3 --brightness 200")
    e.add_argument("mode", type=int)
    e.add_argument("--color", default="ff0000")
    e.add_argument("--brightness", type=int, default=255)
    e.add_argument("--speed", type=int, default=3)
    e.add_argument("--length", type=int, default=2)
    e.add_argument("--cycle", action="store_true")
    e.add_argument("--save", action="store_true")
    d = sub.add_parser("dpi")
    d.add_argument("hex")
    d.add_argument("--save", action="store_true")
    c = sub.add_parser("color")
    c.add_argument("hex", help="например ff8000")
    c.add_argument("--brightness", type=int, default=255)
    c.add_argument("--save", action="store_true")
    a = ap.parse_args()

    if a.cmd == "list":
        cmd_list()
    elif a.cmd == "probe":
        cmd_probe()
    elif a.cmd == "info":
        cmd_info()
    elif a.cmd == "effect":
        h = a.color.lstrip("#")
        col = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        with FuryMouse.from_config() as m:
            m.set_effect(a.mode, col, a.brightness, a.speed, a.length, a.cycle, save=a.save or None)
        print("OK", MODES[a.mode][0])
    elif a.cmd == "dpi":
        h = a.hex.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        with FuryMouse.from_config() as m:
            m.set_dpi_colors(r, g, b, save=a.save or None)
        print("OK")
    elif a.cmd == "color":
        h = a.hex.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        with FuryMouse.from_config() as m:
            m.set_color(r, g, b, a.brightness, save=a.save or None)
        print("OK")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, PermissionError, OSError) as e:
        sys.exit(str(e))
