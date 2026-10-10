# Fury Mouse RGB
"""                         Разработал @UnionMe                               """

<img width="1690" height="1381" alt="image" src="https://github.com/user-attachments/assets/8dd590ab-aeee-410c-ad02-bf73ae5882ce" />

<img width="1280" height="758" alt="image" src="https://github.com/user-attachments/assets/ec495d7f-fc44-43f7-a9f4-527963c9d1e4" />





Управление подсветкой мыши **E-Signal USB Gaming Mouse** (Holtek `04d9:a09f`, сенсор 3327) на Linux.
Протокол восстановлен из официального драйвера `Fury_3327.exe`.

## Возможности
- Колёсико: 12 режимов драйвера (Off, Ripple breathing, Lightning breath, Neon, Wave, Key reaction,
  Trailing, Drag back and forth, Standard, YO-YO, Marbles, Flying star), яркость, скорость, длина,
  любой RGB, цикл по 8 цветам.
- Общая подсветка: любой RGB.
- Необязательная запись в память мыши.
- Стеклянный интерфейс на PyQt6.

## Файлы
| Файл | Назначение |
|---|---|
| `backend.py` | работа с мышью (USB, шифрование пакетов) + консольные команды |
| `ui.py` | графический интерфейс |
| `icon.png` | иконка |

### Доступ к устройству (один раз)

```
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="04d9", ATTR{idProduct}=="a09f", MODE="0660", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/60-fury-usb.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Затем переподключите мышь.

## Первый запуск

```
python backend.py probe
```

Скрипт подберёт рабочие параметры и спросит, изменился ли цвет мыши (`y/n`). Результат сохраняется
в `~/.config/fury_mouse.json`. Нужно сделать один раз.

```
python ui.py
```

## Консольные команды

```
python backend.py list                         # видна ли мышь
python backend.py color ff8000 --brightness 200
python backend.py effect 4 --speed 3           # режим 0..11
python backend.py effect 9 --color 00ff00 --brightness 150
python backend.py effect 6 --cycle             # цикл цветов
python backend.py dpi 00ff00                   # общая подсветка
python backend.py info                         # сырой ответ устройства (для отладки)
```

Ко всем командам, меняющим цвет, можно добавить `--save`, чтобы записать настройку в память мыши.

## Сборка

Положите `build.sh`, `ui.py`, `backend.py` и `icon.png` в одну папку:

```
chmod +x build.sh
./build.sh            # результат: dist/FuryMouse
./build.sh install    # плюс ярлык в меню приложений с иконкой
```

Если рядом есть `.venv`, скрипт использует его.

## Как это работает
- Мышь управляется через HID Feature Report (ID 0, 8 байт) на USB-интерфейсе 2. У этого интерфейса нет
  драйвера ядра, поэтому `/dev/hidraw*` для него не появляется, и запросы отправляются напрямую через
  `/dev/bus/usb` (usbfs).
- Пакеты шифруются (в `Data.ini` драйвера задан `Password=FB1255`), алгоритм перенесён из `MouseDriver.exe`.
- Колёсико: команды `0x0C` (цвет) и `0x0D` (режим). Общая подсветка: таблица цветов DPI (page 1) в `ep_03`.
  Цвета цикла: таблица page 0.

## Ограничения
- Направление ползунков «Скорость» и «Длина» выведено из кода драйвера; если в каком-то режиме оно
  окажется обратным, это нужно поправить в `backend.py` (`set_effect`).
- Не реализованы: макросы, DPI, частота опроса, назначение кнопок и флаги включения цветов цикла.
- Проверено только на одной модели мыши.

## Устранение проблем
- «Нет доступа к USB-устройству»: проверьте правило udev и переподключите мышь.
- «Мышь не найдена»: `python backend.py list`; проверьте, что мышь подключена напрямую или через
  работающий хаб.
- Цвет не меняется: заново выполните `python backend.py probe` и при неудаче пришлите вывод
  `python backend.py info`.
