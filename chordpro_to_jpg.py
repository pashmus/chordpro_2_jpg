import os
import sys
import argparse
import logging
from pathlib import Path
from dataclasses import dataclass

import psycopg2
from playwright.sync_api import sync_playwright
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape
try:
    from .chordpro import ChordProParser
except ImportError:
    from chordpro import ChordProParser
from pychord.utils import transpose_note, note_to_val, val_to_note

# Конфигурация
INPUT_DIR = "input_cho_test"
OUTPUT_DIR = "output_jpg"
TEMPLATE_DIR = "templates"
LOG_FILE = "converter.log"

# Порог суммарной длины «хвостовых» аккордов (после текста),
# при превышении которого аккорды хвоста уменьшаются.
TRAILING_CHORDS_CHAR_LIMIT = 15


# Настройка доступа к конфигу и БД
_CURRENT_DIR = Path(__file__).resolve().parent
# .../chordpro_2_jpg -> .../Sbornik_samara_bot
_PROJECT_ROOT = _CURRENT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from config_data.config import load_config # type: ignore
except ImportError:
    load_config = None


def _create_logger():
    """
    Создаёт файловый логгер.
    Пишем только WARNING/ERROR в файл, без вывода INFO в консоль.
    """
    logger = logging.getLogger("chordpro_converter")
    logger.setLevel(logging.WARNING)
    logger.propagate = False

    if not logger.handlers:
        log_path = _CURRENT_DIR / LOG_FILE
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.WARNING)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


LOGGER = _create_logger()


class DatabaseManager:
    """
    Простой менеджер подключения к Postgres на основе config_data.config.load_config.
    """

    def __init__(self):
        self.db_config = {}
        if load_config is not None:
            try:
                config = load_config()
                self.db_config = {
                    "host": config.db.db_host,
                    "database": config.db.db_name,
                    "user": config.db.db_user,
                    "password": config.db.db_password,
                }
            except Exception as e:
                LOGGER.error(f"Ошибка загрузки конфигурации БД: {e}")
                self.db_config = {}
        else:
            LOGGER.warning(
                "Не удалось импортировать load_config из config_data.config. "
                "Работа с БД может быть недоступна."
            )
            self.db_config = {}

        self.conn = None

    def connect(self):
        """
        Устанавливает соединение с БД. Возвращает True при успехе, иначе False.
        """
        if not self.db_config:
            LOGGER.error("Конфигурация БД пуста, подключение невозможно.")
            return False

        try:
            self.conn = psycopg2.connect(**self.db_config)
            return True
        except Exception as e:
            LOGGER.error(f"Ошибка подключения к базе данных: {e}")
            return False

    def close(self):
        """
        Закрывает соединение с БД при наличии.
        """
        if self.conn:
            self.conn.close()
            self.conn = None


def get_special_style(text):
    """
    Возвращает 'chorus', 'pre_chorus', 'bridge' или None по префиксу текста.
    Шаблоны: "Пр." или "Припев" → chorus; "Пре-пр" → pre_chorus; "Bridge" или "Бридж" → bridge.
    """
    if not text:
        return None
    t = text.strip()
    # Проверка на "Пр.", "Припев"
    if t.lower().startswith('пр.') or t.lower().startswith('припев') or t.lower().startswith('chorus'):
        return 'chorus'
    # Check for "Пре-пр"
    if t.lower().startswith("пре-пр") or t.lower().startswith("пред-пр") or t.lower().startswith("препр") or t.lower().startswith("предпр"):
        return 'pre_chorus'
    # Проверка на "Bridge" или "Бридж"
    if t.lower().startswith('bridge') or t.lower().startswith('бридж') or t.lower().startswith('мост'):
        return 'bridge'
    return None


def parse_args():
    try:
        from .cli import parse_args as cli_parse_args
    except ImportError:
        from cli import parse_args as cli_parse_args
    return cli_parse_args()


try:
    from .api import RenderParams
except ImportError:
    from api import RenderParams


def _params_to_args(params: RenderParams):
    try:
        from .api import _params_to_args as api_params_to_args
    except ImportError:
        from api import _params_to_args as api_params_to_args
    return api_params_to_args(params)


def _resolve_local_dir(dir_path):
    """
    Нормализует путь к директории:
    - абсолютный путь оставляет как есть;
    - относительный трактует относительно папки chordpro_2_jpg.
    """
    raw = str(dir_path).strip()
    if os.path.isabs(raw):
        return raw
    return os.path.join(str(_CURRENT_DIR), raw)


def _load_template(template_dir):
    env = Environment(loader=FileSystemLoader(template_dir))
    env.filters["chord_backslash"] = format_chord_backslashes
    return env.get_template("song.html")


def find_input_files(input_dir):
    return [
        f
        for f in os.listdir(input_dir)
        if f.endswith(".chordpro") or f.endswith(".pro") or f.endswith(".cho")
    ]


def format_chord_backslashes(value):
    """
    Оборачивает каждый обратный слэш в отдельный span для стилизации.
    Нужен для уменьшения символа '\' в аккордах и grid-тексте.
    """
    if value is None:
        return ""

    escaped = escape(str(value))
    return Markup(
        str(escaped).replace("\\", '<span class="chord-backslash">\\</span>')
    )


def collect_parts(items):
    parts = []
    for item in items:
        if hasattr(item, "is_volta_group") and item.is_volta_group:
            parts.extend(collect_parts(item.parts))
        else:
            parts.append(item)
    return parts


def serialize_item(item, is_anchor=False, is_floating=False, floating_siblings=None):
    """
    Преобразует объект строки/вольты в словарь для шаблона.
    Переносит флаги вроде small_chord, is_anchor/is_floating и др.
    """
    small_chord = getattr(item, "small_chord", False)

    if hasattr(item, "is_volta_group") and item.is_volta_group:
        return {
            "is_volta_group": True,
            "number": item.number,
            "is_anchor": is_anchor,
            "is_floating": is_floating,
            "small_chord": small_chord,
            "floating_siblings": [
                serialize_item(s) for s in (floating_siblings or [])
            ],
            "parts": [serialize_item(p) for p in item.parts],
        }

    return {
        "chord": item.chord,
        "text": item.text,
        "volta": getattr(item, "volta", None),
        "is_volta_group": False,
        "small_chord": small_chord,
    }


def _split_chord_for_index(chord, enable_index=False):
    """
    Разбивает строку аккорда на основу и дополнение для режима подстрочных индексов.

    Правила:
    - Основа: буква тоники (A–G, H/B) + опциональный #/b.
    - Минорное m считается частью основы только если
      сразу после него идут цифры или ничего.
    - Всё остальное относится к дополнению (extra).
    """
    if not enable_index or not chord:
        return chord, ""

    s = str(chord).strip()
    if not s:
        return chord, ""

    # Аккорды с басом через слэш (E/G# и т.п.):
    # индекс применяем только к «голове» (до '/'),
    # бас целиком остаётся в основной части.
    if "/" in s:
        head, bass = s.split("/", 1)
        head = head.strip()
        bass = bass.strip()
        if not head or not bass:
            return chord, ""

        # Для головы применяем те же правила индекса и исключений,
        # затем добавляем бас целиком к основе.
        base_head, extra_head = _split_chord_for_index(head, enable_index=True)
        base = f"{base_head}/{bass}"
        extra = extra_head
        return base, extra

    i = 0
    n = len(s)

    # Тоника
    if i < n and s[i].upper() in "ABCDEFGH":
        i += 1
        # Диез/бемоль
        if i < n and s[i] in "#b":
            i += 1
        # Минорное m
        if i < n and s[i] == "m":
            j = i + 1
            # m относится к основе, только если далее цифры или конец строки
            if j == n or (j < n and s[j].isdigit()):
                i += 1

        base = s[:i]
        extra = s[i:]

        # Исключаем служебные символы из дополнения индекса:
        # вертикальная черта, слэш и обратный слэш (а также
        # возможный пробел перед ними) должны оставаться
        # обычным шрифтом, а не подстрочным индексом.
        while extra and extra[0] in " |/\\":
            base += extra[0]
            extra = extra[1:]

        return base, extra

    # Не удалось распознать стандартную структуру аккорда — не делим
    return chord, ""


def _apply_chord_split_to_part_dict(part_dict, enable_index=False):
    """
    Добавляет к словарю части поля chord_base / chord_extra с учётом режима индекса.
    Рекурсивно проходит по volta-группам.
    """
    if not isinstance(part_dict, dict):
        return

    if part_dict.get("is_volta_group"):
        for child in part_dict.get("parts", []):
            _apply_chord_split_to_part_dict(child, enable_index=enable_index)
        for sib in part_dict.get("floating_siblings", []):
            _apply_chord_split_to_part_dict(sib, enable_index=enable_index)
        return

    chord = part_dict.get("chord")
    base, extra = _split_chord_for_index(chord, enable_index=enable_index)
    part_dict["chord_base"] = base
    part_dict["chord_extra"] = extra


def _item_has_text(item):
    """
    Возвращает True, если в исходном объекте строки/вольты есть непустой текст.
    Для вольты проверяет дочерние части.
    """
    text = getattr(item, "text", None)
    if text and str(text).strip():
        return True

    if hasattr(item, "is_volta_group") and item.is_volta_group:
        for child in getattr(item, "parts", []):
            if _item_has_text(child):
                return True

    return False


def _item_has_chords(item):
    """
    Возвращает True, если в исходном объекте есть аккорды.
    Для вольты проверяет дочерние части.
    """
    chord = getattr(item, "chord", None)
    if chord:
        return True

    if hasattr(item, "is_volta_group") and item.is_volta_group:
        for child in getattr(item, "parts", []):
            if _item_has_chords(child):
                return True

    return False


def _section_has_any_chords(section):
    """
    Проверяет, есть ли в секции хотя бы один аккорд.
    Учитывает обычные строки/вольты и grid-ячейки.
    """
    for line in getattr(section, "lines", []):
        # Обычные строки (включая вложенные volta-группы)
        for part in collect_parts(getattr(line, "parts", [])):
            if getattr(part, "chord", None) and str(part.chord).strip():
                return True

        # Сетка (grid)
        for cell in getattr(line, "grid_cells", []):
            for cell_part in getattr(cell, "parts", []):
                if getattr(cell_part, "chord", None) and str(cell_part.chord).strip():
                    return True

    return False


def _item_chords_length(item):
    """
    Приблизительная суммарная длина строк аккордов в исходном объекте
    (учитываются вложенные части volta-группы).
    """
    total = 0

    chord = getattr(item, "chord", None)
    if chord:
        total += len(str(chord))

    if hasattr(item, "is_volta_group") and item.is_volta_group:
        for child in getattr(item, "parts", []):
            total += _item_chords_length(child)

    return total


def _mark_small_chord_on_item(item):
    """
    Помечает исходный объект (и вложенные элементы вольты) как требующие
    уменьшенного шрифта аккорда (small_chord = True).
    """
    setattr(item, "small_chord", True)

    if hasattr(item, "is_volta_group") and item.is_volta_group:
        for child in getattr(item, "parts", []):
            _mark_small_chord_on_item(child)


def _mark_small_trailing_chords_in_parts(parts, char_limit):
    """
    Ищет хвостовые аккорды в списке частей и уменьшает их, если суммарная длина
    превышает порог. Работает рекурсивно, включая хвосты внутри VoltaGroup.
    """
    if not parts:
        return

    # 1) Хвост на текущем уровне списка parts:
    # после последнего фрагмента с текстом идут только аккордовые части.
    last_text_idx = -1
    for idx, part in enumerate(parts):
        if _item_has_text(part):
            last_text_idx = idx

    if last_text_idx != -1:
        total_len = 0
        tail_indices = []
        for idx in range(len(parts) - 1, last_text_idx, -1):
            part = parts[idx]
            if not _item_has_chords(part):
                break

            tail_indices.append(idx)
            chord_len = _item_chords_length(part)
            if total_len > 0:
                total_len += 1  # условный пробел между частями
            total_len += chord_len

        if tail_indices and total_len > char_limit:
            for idx in tail_indices:
                _mark_small_chord_on_item(parts[idx])

    # 2) Хвосты внутри вольт на дочернем уровне
    for part in parts:
        if hasattr(part, "is_volta_group") and part.is_volta_group:
            _mark_small_trailing_chords_in_parts(
                getattr(part, "parts", []), char_limit
            )


def build_sections_data(song, index_chords=False):
    sections_data = []
    for sec in song.sections:
        if not sec.lines and not sec.label:
            continue
        lines_data = []

        # Проверка специального маркера (пустая секция с заданной меткой)
        special_style = None
        if not sec.lines and sec.label:
            special_style = get_special_style(sec.label)
            # Для пустых припевов: при отсутствии совпадений — стиль 'chorus' по умолчанию
            if special_style is None and sec.type == "chorus":
                special_style = "chorus"

        # Проверка выравнивания метки секции (для sidebar layout)
        offset_label = False
        if sec.lines:
            first_line = sec.lines[0]
            # Проверка: в первой строке есть и аккорды, и текст
            has_chords = False
            has_text = False

            flat_parts = collect_parts(first_line.parts)
            for part in flat_parts:
                if part.chord and part.chord.strip():
                    has_chords = True
                if part.text and part.text.strip():
                    has_text = True

            # Если есть и аккорды, и текст — сдвигаем метку для выравнивания с текстом
            if has_chords and has_text:
                offset_label = True

        for line in sec.lines:
            if getattr(line, "is_blank", False):
                lines_data.append(
                    {
                        "parts": [],
                        "is_comment": False,
                        "special_style": None,
                        "is_blank": True,
                    }
                )
                continue

            if sec.type == "grid" and hasattr(line, "grid_cells") and line.grid_cells:
                cells_data = []
                for cell in line.grid_cells:
                    # Ячейки сетки — простые Part; при появлении VoltaGroup потребуется доработка
                    cell_parts = []
                    for p in cell.parts:
                        base, extra = _split_chord_for_index(
                            p.chord, enable_index=index_chords
                        )
                        cell_parts.append(
                            {
                                "chord": p.chord,
                                "text": p.text,
                                "chord_base": base,
                                "chord_extra": extra,
                            }
                        )

                    # Проверка: такт по сути пустой
                    is_empty = not cell.is_bar and not any(
                        p.chord or (p.text and p.text.strip()) for p in cell.parts
                    )

                    # Тип черты для стилизации
                    bar_type = "standard"
                    if cell.is_bar:
                        text = cell.text
                        if ":" in text:
                            if text.startswith(":") or text.endswith(":") and len(text) > 1:
                                if text.startswith(":") and text.endswith(":"):
                                    # Редкий случай |:| и т.п.
                                    bar_type = "repeat-both"
                                elif text.startswith(":"):
                                    bar_type = "end-repeat"
                                else:
                                    bar_type = "start-repeat"
                        elif len(text) >= 2 and ("||" in text or "//" in text):
                            bar_type = "double-bar"

                    current_cell_data = {
                        "is_bar": cell.is_bar,
                        "text": cell.text,
                        "bar_type": bar_type,
                        "volta": cell.volta,
                        "is_empty": is_empty,
                        "is_shifted": False,  # Начальное состояние
                        "parts": cell_parts,
                    }

                    # Перенос volta на предыдущую черту при необходимости
                    # Текущая ячейка — такт (не черта) и есть volta
                    if not current_cell_data["is_bar"] and current_cell_data["volta"]:
                        # 1. Попытка объединить с предыдущим повтором при пустом такте/черте между
                        # Схема: [черта повтора] -> [пустой такт] -> [простая черта] -> [текущий такт с volta]
                        if len(cells_data) >= 3:
                            prev_bar = cells_data[-1]
                            prev_measure = cells_data[-2]
                            repeat_bar = cells_data[-3]
                            if (
                                prev_bar["is_bar"]
                                and prev_bar["text"] in ["|", "||"]
                                and prev_measure["is_empty"]
                                and repeat_bar["is_bar"]
                                and (":" in repeat_bar["text"])
                            ):
                                repeat_bar["volta"] = current_cell_data["volta"]
                                current_cell_data["volta"] = None
                                current_cell_data["is_shifted"] = True
                                # Удалить лишние промежуточные ячейки
                                cells_data.pop()  # Удалить простую черту
                                cells_data.pop()  # Удалить пустой такт
                                cells_data.append(current_cell_data)
                                continue

                        # 2. Обычный перенос на непосредственно предыдущую черту
                        if cells_data and cells_data[-1]["is_bar"]:
                            cells_data[-1]["volta"] = current_cell_data["volta"]
                            current_cell_data["volta"] = None
                            current_cell_data["is_shifted"] = True  # Сдвиг такта

                    cells_data.append(current_cell_data)

                lines_data.append(
                    {"grid_cells": cells_data, "is_comment": False, "is_blank": False}
                )
            else:
                # Определение хвостовых аккордов:
                # - на верхнем уровне строки
                # - внутри VoltaGroup (когда вольта начинается в тексте)
                if not getattr(line, "is_comment", False):
                    _mark_small_trailing_chords_in_parts(
                        line.parts, TRAILING_CHORDS_CHAR_LIMIT
                    )

                # Классификация voltas в строке:
                # - anchor только для локальной пары 1. -> 2.
                # - остальные volta-группы рендерятся самостоятельно на своей позиции
                volta_entries = [
                    (idx, p)
                    for idx, p in enumerate(line.parts)
                    if hasattr(p, "is_volta_group") and p.is_volta_group
                ]
                volta_roles = {}

                def _normalize_volta_number(num):
                    return str(num).strip().rstrip(".")

                i = 0
                while i < len(volta_entries):
                    curr_idx, curr_part = volta_entries[i]
                    curr_num = _normalize_volta_number(curr_part.number)

                    if i + 1 < len(volta_entries):
                        next_idx, next_part = volta_entries[i + 1]
                        next_num = _normalize_volta_number(next_part.number)
                        if curr_num == "1" and next_num == "2":
                            trailing_siblings = [next_part]
                            volta_roles[curr_idx] = {
                                "is_anchor": True,
                                "is_floating": False,
                                "floating_siblings": trailing_siblings,
                            }
                            volta_roles[next_idx] = {
                                "is_anchor": False,
                                "is_floating": True,
                                "floating_siblings": [],
                            }

                            # 3-я и все последующие (до следующей 1-й) идут тем же "паровозиком"
                            j = i + 2
                            while j < len(volta_entries):
                                sib_idx, sib_part = volta_entries[j]
                                sib_num = _normalize_volta_number(sib_part.number)
                                if sib_num == "1":
                                    break
                                trailing_siblings.append(sib_part)
                                volta_roles[sib_idx] = {
                                    "is_anchor": False,
                                    "is_floating": True,
                                    "floating_siblings": [],
                                }
                                j += 1

                            i = j
                            continue

                    volta_roles[curr_idx] = {
                        "is_anchor": False,
                        "is_floating": False,
                        "floating_siblings": [],
                    }
                    i += 1

                parts_data = []
                for part_idx, part in enumerate(line.parts):
                    if hasattr(part, "is_volta_group") and part.is_volta_group:
                        role = volta_roles.get(
                            part_idx,
                            {
                                "is_anchor": False,
                                "is_floating": False,
                                "floating_siblings": [],
                            },
                        )
                        part_dict = serialize_item(
                            part,
                            is_anchor=role["is_anchor"],
                            is_floating=role["is_floating"],
                            floating_siblings=role["floating_siblings"],
                        )

                        # Для standalone-вольты (не anchor и не floating):
                        # если в последней части вольты есть и аккорд, и текст,
                        # текст выносим в отдельный обычный Part после вольты.
                        # Это нужно, чтобы скобка standalone заканчивалась на
                        # закрывающем аккорде, а не тянулась до следующего аккорда.
                        trailing_text_part = None
                        if (
                            not role["is_anchor"]
                            and not role["is_floating"]
                            and part_dict.get("parts")
                        ):
                            last_child = part_dict["parts"][-1]
                            if (
                                isinstance(last_child, dict)
                                and last_child.get("chord")
                                and last_child.get("text")
                            ):
                                moved_text = last_child.get("text", "")
                                if moved_text:
                                    last_child["text"] = ""
                                    trailing_text_part = {
                                        "chord": None,
                                        "text": moved_text,
                                        "volta": None,
                                        "is_volta_group": False,
                                        "small_chord": False,
                                    }

                        _apply_chord_split_to_part_dict(
                            part_dict, enable_index=index_chords
                        )
                        parts_data.append(part_dict)
                        if trailing_text_part is not None:
                            _apply_chord_split_to_part_dict(
                                trailing_text_part, enable_index=index_chords
                            )
                            parts_data.append(trailing_text_part)
                    else:
                        part_dict = serialize_item(part)
                        _apply_chord_split_to_part_dict(
                            part_dict, enable_index=index_chords
                        )
                        parts_data.append(part_dict)

                line_special_style = None
                is_comment = getattr(line, "is_comment", False)
                if is_comment:
                    # Собрать текст из частей для проверки специального стиля
                    comment_text = "".join(p.text for p in line.parts if p.text)
                    line_special_style = get_special_style(comment_text)

                lines_data.append(
                    {
                        "parts": parts_data,
                        "is_comment": is_comment,
                        "special_style": line_special_style,
                        "is_blank": False,
                    }
                )

        no_chords = bool(sec.lines) and not _section_has_any_chords(sec)

        sections_data.append(
            {
                "type": sec.type,
                "label": sec.label,
                "lines": lines_data,
                "offset_label": offset_label,
                "special_style": special_style,
                "no_chords": no_chords,
            }
        )

    return sections_data


def build_context(song, layout, input_ger=False, output_ger=False, index_chords=False):
    def normalize_key_root_for_pychord(note, is_german_notation):
        if not note:
            return note
        s = note
        if is_german_notation:
            # German notation: H -> B, B -> Bb
            temp = "###TEMP###"
            s = s.replace("H", temp)
            s = s.replace("B", "Bb")
            s = s.replace(temp, "B")
        else:
            # Standard notation: если вдруг встретится H, привести к B
            s = s.replace("H", "B")
        return s

    def format_note_to_german(note):
        if not note:
            return note
        temp = "###TEMP###"
        s = note.replace("Bb", temp)
        s = s.replace("B", "H")
        s = s.replace(temp, "B")
        return s

    def format_key_for_header(base_key, source_key=None):
        if not base_key:
            return ""

        source_label = source_key if source_key else base_key

        try:
            capo_val = int(song.capo) if song.capo is not None else 0
        except Exception:
            capo_val = 0

        if capo_val == 0:
            return base_key

        key_root_raw, key_quality_raw = song.extract_root_note(base_key)
        if not key_root_raw:
            return base_key

        key_quality_raw = (key_quality_raw or "").strip()
        is_minor = (
            key_quality_raw
            and key_quality_raw.lower().startswith("m")
            and not key_quality_raw.lower().startswith("maj")
        )

        key_root_eng = normalize_key_root_for_pychord(
            key_root_raw, is_german_notation=output_ger
        )

        try:
            if is_minor:
                old_scale_root_eng = song._get_relative_major_root(key_root_eng)
            else:
                old_scale_root_eng = key_root_eng

            old_scale_val = note_to_val(old_scale_root_eng)
            new_scale_val = (old_scale_val + capo_val) % 12
            new_scale_root_eng = song._normalize_key_root_from_val(new_scale_val)

            key_root_val = note_to_val(key_root_eng)
            new_key_root_val = (key_root_val + capo_val) % 12
            sounding_root_eng = val_to_note(new_key_root_val, new_scale_root_eng)
            if output_ger:
                sounding_root = format_note_to_german(sounding_root_eng)
            else:
                sounding_root = sounding_root_eng

            sounding_key = sounding_root + ("m" if is_minor else "")
            return f"{sounding_key}({source_label})"
        except Exception:
            return base_key

    display_key = song.key
    modulation_keys = getattr(song, "modulation_keys", None) or []
    if modulation_keys:
        # Для модуляций правая часть в скобках должна совпадать с тем же ключом,
        # который используется как base_key для вычисления левой части (учет capo).
        # В modulation_keys этот ключ хранится в display_key.
        display_key = " -> ".join(
            format_key_for_header(
                item.get("display_key", ""),
                source_key=item.get("display_key", ""),
            )
            for item in modulation_keys
            if item.get("display_key")
        )
    else:
        display_key = format_key_for_header(song.key, source_key=song.key)

    return {
        "title": song.title,
        "artist": song.artist,
        "key": display_key,
        "capo": song.capo,
        "time": song.time,
        "tempo": song.tempo,
        "sections": build_sections_data(song, index_chords=index_chords),
        "layout": layout,
        "index_chords": index_chords,
    }


def render_song_to_html(
    song, template, layout, input_ger=False, output_ger=False, index_chords=False
):
    context = build_context(
        song,
        layout,
        input_ger=input_ger,
        output_ger=output_ger,
        index_chords=index_chords,
    )
    return template.render(context)


def apply_transforms(song, args):
    def _parse_capo_steps(raw_capo):
        """
        Нормализует capo к неотрицательному числу полутонов.
        Некорректное значение трактуется как 0.
        """
        if raw_capo is None:
            return 0
        try:
            return abs(int(str(raw_capo).strip()))
        except Exception:
            return 0

    def _resolve_capo_and_transpose(cli_transpose, cli_capo, source_capo):
        """
        Возвращает:
        - итоговый сдвиг для транспонирования аккордов/ключа
        - финальное значение capo для метаданных (строка) или None
        """
        total_transpose = cli_transpose
        capo_meta_value = None
        source_capo_steps = _parse_capo_steps(source_capo)

        if cli_capo is not None:
            # Для capo всегда компенсируем вниз по полутонам: 2, +2 и -2 -> -2.
            # Важно: сохраняем эталонное звучание, которое уже задано входными key+capo.
            # Поэтому учитываем исходный capo как базу и смещаем к новому target capo.
            capo_steps = _parse_capo_steps(cli_capo)
            total_transpose += source_capo_steps - capo_steps
            capo_meta_value = str(capo_steps)

        return total_transpose, capo_meta_value

    total_transpose, capo_meta_value = _resolve_capo_and_transpose(
        args.transpose, args.capo, song.capo
    )

    song.transpose(total_transpose, input_ger=args.in_ger, output_std=not args.out_ger)

    # Если capo передан через CLI, он имеет приоритет над входным {capo: ...}.
    if capo_meta_value is not None:
        song.capo = capo_meta_value

    # Опция: раскрыть ссылки на секции
    if args.expand_chorus:
        song.expand_section_references()


def render_song_to_files(
    filename,
    song,
    template,
    browser,
    layout,
    input_ger=False,
    output_ger=False,
    index_chords=False,
    output_dir=OUTPUT_DIR,
):
    try:
        html_content = render_song_to_html(
            song,
            template,
            layout,
            input_ger=input_ger,
            output_ger=output_ger,
            index_chords=index_chords,
        )
    except Exception as e:
        LOGGER.error(f"Ошибка рендера HTML для '{filename}': {e}")
        raise

    # Сохранить временный HTML
    os.makedirs(output_dir, exist_ok=True)
    temp_html_path = os.path.abspath(os.path.join(output_dir, f"{filename}.html"))
    try:
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as e:
        LOGGER.error(f"Ошибка сохранения HTML '{temp_html_path}': {e}")
        raise

    # Рендер в JPG
    # Ширина задана; высота null/0 — на всю страницу
    page = None
    try:
        page = browser.new_page(viewport={"width": 800, "height": 600})

        file_url = f"file://{temp_html_path}"
        page.goto(file_url)

    # Небольшая пауза для стабилизации вёрстки (для локального статического обычно мгновенно)
    # page.wait_for_timeout(100)

    # Компенсация горизонтального вылета абсолютных volta-stack через
    # невидимые spacers в конце строки (без изменения padding секций).
        page.evaluate(
            """
            () => {
                const lines = Array.from(
                    document.querySelectorAll('.song-container .line, .song-container .section-reference')
                );

                for (const line of lines) {
                    const lineRect = line.getBoundingClientRect();
                    if (!lineRect.width) continue;

                    // Правая граница фактического контента строки, а не всей
                    // растянутой flex-строки. Это важно для корректного overflow.
                    let contentRight = lineRect.left;
                    for (const child of line.children) {
                        if (
                            child.classList &&
                            child.classList.contains('volta-stack-spacer')
                        ) {
                            continue;
                        }
                        const childStyle = window.getComputedStyle(child);
                        if (childStyle.position === 'absolute') {
                            continue;
                        }
                        const childRect = child.getBoundingClientRect();
                        if (childRect.right > contentRight) {
                            contentRight = childRect.right;
                        }
                    }

                    const anchors = line.querySelectorAll('[data-volta-anchor]');
                    const spacers = line.querySelectorAll('[data-volta-spacer]');
                    if (!anchors.length || !spacers.length) continue;

                    // Сброс перед расчётом
                    for (const spacer of spacers) {
                        spacer.style.width = '0px';
                    }

                    const pairCount = Math.min(anchors.length, spacers.length);
                    for (let i = 0; i < pairCount; i++) {
                        const anchor = anchors[i];
                        const spacer = spacers[i];
                        const stack = anchor.querySelector('.volta-stack');
                        if (!stack) continue;

                        const stackRect = stack.getBoundingClientRect();
                        const overflow = Math.ceil(stackRect.right - contentRight);
                        if (overflow > 0) {
                            const applied = overflow + 2;
                            spacer.style.width = `${applied}px`;
                        }
                    }
                }
            }
            """
        )

        output_filename = os.path.splitext(filename)[0] + ".jpg"
        output_path = os.path.join(output_dir, output_filename)

        # Скриншот контейнера песни, чтобы убрать лишнее поле справа/снизу.
        locator = page.locator(".song-container")
        locator.screenshot(path=output_path, type="jpeg", quality=90)
    except Exception as e:
        output_filename = os.path.splitext(filename)[0] + ".jpg"
        output_path = os.path.join(output_dir, output_filename)
        LOGGER.error(f"Ошибка создания JPG '{output_path}': {e}")
        raise
    finally:
        if page is not None:
            page.close()
    return output_path


def _get_db_manager():
    """
    Возвращает экземпляр DatabaseManager или None при ошибке.
    """
    try:
        return DatabaseManager()
    except Exception as e:
        LOGGER.error(f"Ошибка создания менеджера базы данных: {e}")
        return None


def _fetch_song_chordpro_and_title(db_manager, song_number):
    """
    Возвращает кортеж (chordpro_text, title) или (None, None), если данные недоступны.
    """
    if not db_manager.conn:
        if not db_manager.connect():
            LOGGER.error("Не удалось подключиться к базе данных.")
            return None, None

    try:
        with db_manager.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    chordpro,
                    CASE WHEN alt_name IS NULL THEN name ELSE alt_name END AS title
                FROM songs
                WHERE num = %s
                """,
                (song_number,),
            )
            row = cursor.fetchone()
            if not row:
                LOGGER.warning(f"Песня №{song_number} не найдена в базе данных. Пропуск.")
                return None, None

            chordpro_text, title = row[0], row[1]
            if chordpro_text is None:
                LOGGER.warning(
                    f"Для песни №{song_number} поле chordpro в БД пустое. Пропуск."
                )
                return None, title

            return chordpro_text, title
    except Exception as e:
        LOGGER.error(f"Ошибка при чтении ChordPro для песни №{song_number}: {e}")
        return None, None


def _make_filename_for_song(song_number, title):
    """
    Формирует безопасное имя файла для песни из БД.
    """
    base = str(song_number)
    if title:
        unsafe_chars = '<>:"/\\|?*'
        safe_title = "".join("_" if c in unsafe_chars else c for c in str(title))
        safe_title = safe_title.strip()
        if safe_title:
            base = f"{song_number} {safe_title}"
    return f"{base}.cho"


def _parse_song_numbers(tokens):
    """
    Разбирает список строк с номерами и диапазонами песен в отсортированный список int.

    Поддерживаемые форматы:
      - '321'        -> [321]
      - '300-305'    -> [300, 301, 302, 303, 304, 305]
      - '400-390'    -> [390..400] (границы будут автоматически упорядочены)
    Некорректные токены пропускаются с сообщением.
    """
    if not tokens:
        return []

    result = set()
    for raw in tokens:
        token = str(raw).strip()
        if not token:
            continue

        if "-" in token:
            # Диапазон
            left, right = token.split("-", 1)
            left = left.strip()
            right = right.strip()
            if not left or not right:
                LOGGER.warning(
                    f"Некорректный диапазон номеров: '{token}'. Токен пропущен."
                )
                continue
            try:
                start = int(left)
                end = int(right)
            except ValueError:
                LOGGER.warning(
                    f"Некорректный диапазон номеров: '{token}'. Токен пропущен."
                )
                continue

            if start > end:
                start, end = end, start

            for n in range(start, end + 1):
                result.add(n)
        else:
            # Одиночный номер
            try:
                n = int(token)
                result.add(n)
            except ValueError:
                LOGGER.warning(f"Некорректный номер песни: '{token}'. Токен пропущен.")

    return sorted(result)


def _sanitize_filename_stem(stem):
    """
    Делает безопасную основу имени файла (без расширения).
    """
    unsafe_chars = '<>:"/\\|?*'
    safe_stem = "".join("_" if c in unsafe_chars else c for c in str(stem))
    safe_stem = safe_stem.strip().rstrip(".")
    return safe_stem or "song"


def render_chordpro_to_jpg(
    chordpro_text,
    filename_stem="song",
    *,
    transpose=0,
    capo=None,
    in_ger=False,
    out_ger=False,
    layout="sidebar",
    expand_chorus=False,
    small_extensions=False,
    output_dir=OUTPUT_DIR,
):
    try:
        from .api import render_chordpro_to_jpg as api_render_chordpro_to_jpg
    except ImportError:
        from api import render_chordpro_to_jpg as api_render_chordpro_to_jpg
    return api_render_chordpro_to_jpg(
        chordpro_text=chordpro_text,
        filename_stem=filename_stem,
        transpose=transpose,
        capo=capo,
        in_ger=in_ger,
        out_ger=out_ger,
        layout=layout,
        expand_chorus=expand_chorus,
        small_extensions=small_extensions,
        output_dir=output_dir,
    )


def render_songs_from_folder(args):
    try:
        from .cli import render_songs_from_folder as cli_render_songs_from_folder
    except ImportError:
        from cli import render_songs_from_folder as cli_render_songs_from_folder
    return cli_render_songs_from_folder(args)


def render_songs_from_db(args):
    try:
        from .cli import render_songs_from_db as cli_render_songs_from_db
    except ImportError:
        from cli import render_songs_from_db as cli_render_songs_from_db
    return cli_render_songs_from_db(args)


def main():
    try:
        from .cli import main as cli_main
    except ImportError:
        from cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    main()
