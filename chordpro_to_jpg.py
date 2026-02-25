import os
import argparse
from playwright.sync_api import sync_playwright
from jinja2 import Environment, FileSystemLoader
from chordpro import ChordProParser
from pychord.utils import transpose_note, note_to_val, val_to_note

# Конфигурация
INPUT_DIR = 'input_cho'
OUTPUT_DIR = 'output_jpg'
TEMPLATE_DIR = 'templates'


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
    cli_parser = argparse.ArgumentParser(
        description="Convert ChordPro files to HTML/JPG images."
    )
    cli_parser.add_argument(
        "--transpose", "-t", type=int, default=0, help="Transpose chords by N semitones"
    )
    cli_parser.add_argument(
        "--ger",
        "-g",
        action="store_true",
        help=(
            "German notation on input: 'H' is B natural, 'B' is B flat. "
            "Default (without this flag) is standard input: 'B' is B natural, 'Bb' is B flat."
        ),
    )
    cli_parser.add_argument(
        "--std",
        "-s",
        action="store_true",
        help=(
            "Standard notation on output: 'B' is B natural, 'Bb' is B flat. "
            "Default (without this flag) is German output: 'H' is B natural, 'B' is B flat."
        ),
    )
    cli_parser.add_argument(
        "--layout",
        "-l",
        type=str,
        default="sidebar",
        choices=["standard", "sidebar"],
        help="Layout type: 'sidebar' (default) or 'standard'",
    )
    cli_parser.add_argument(
        "--expand-chorus",
        "-ex",
        action="store_true",
        help=(
            "Expand section references: replace comments that match section labels "
            "(chorus, pre-chorus, bridge) with the actual content."
        ),
    )
    return cli_parser.parse_args()


def find_input_files(input_dir):
    return [
        f
        for f in os.listdir(input_dir)
        if f.endswith(".chordpro") or f.endswith(".pro") or f.endswith(".cho")
    ]


def collect_parts(items):
    parts = []
    for item in items:
        if hasattr(item, "is_volta_group") and item.is_volta_group:
            parts.extend(collect_parts(item.parts))
        else:
            parts.append(item)
    return parts


def serialize_item(item, is_anchor=False, is_floating=False, floating_siblings=None):
    if hasattr(item, "is_volta_group") and item.is_volta_group:
        return {
            "is_volta_group": True,
            "number": item.number,
            "is_anchor": is_anchor,
            "is_floating": is_floating,
            "floating_siblings": [
                serialize_item(s) for s in (floating_siblings or [])
            ],
            "parts": [serialize_item(p) for p in item.parts],
        }
    return {
        "chord": item.chord,
        "text": item.text,
        "volta": item.volta,
        "is_volta_group": False,
    }


def build_sections_data(song):
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
            if sec.type == "grid" and hasattr(line, "grid_cells") and line.grid_cells:
                cells_data = []
                for cell in line.grid_cells:
                    # Ячейки сетки — простые Part; при появлении VoltaGroup потребуется доработка
                    cell_parts = [{"chord": p.chord, "text": p.text} for p in cell.parts]

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

                lines_data.append({"grid_cells": cells_data, "is_comment": False})
            else:
                # Найти voltas в строке для логики стека
                line_voltas = [
                    p
                    for p in line.parts
                    if hasattr(p, "is_volta_group") and p.is_volta_group
                ]

                parts_data = []
                v_idx = 0
                for part in line.parts:
                    if hasattr(part, "is_volta_group") and part.is_volta_group:
                        is_anchor = v_idx == 0
                        is_floating = v_idx > 0
                        siblings = line_voltas[1:] if is_anchor else []
                        parts_data.append(
                            serialize_item(
                                part,
                                is_anchor=is_anchor,
                                is_floating=is_floating,
                                floating_siblings=siblings,
                            )
                        )
                        v_idx += 1
                    else:
                        parts_data.append(serialize_item(part))

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
                    }
                )

        sections_data.append(
            {
                "type": sec.type,
                "label": sec.label,
                "lines": lines_data,
                "offset_label": offset_label,
                "special_style": special_style,
            }
        )

    return sections_data


def build_context(song, layout, input_ger=False):
    display_key = song.key
    if song.key and song.capo:
        try:
            capo_val = int(song.capo)
            if capo_val != 0:
                # Извлекаем корень и качество ключа (Dm -> ("D", "m"))
                key_root_raw, key_quality_raw = song.extract_root_note(song.key)
                if key_root_raw:
                    key_quality_raw = (key_quality_raw or "").strip()

                    # Минорный ли это ключ
                    is_minor = (
                        key_quality_raw
                        and key_quality_raw.lower().startswith("m")
                        and not key_quality_raw.lower().startswith("maj")
                    )

                    # Полная нормализация H/B (как в song.py)
                    def normalize_key_root_for_pychord(note, is_german_input):
                        if not note:
                            return note
                        s = note
                        if is_german_input:
                            # German Input: H -> B, B -> Bb
                            temp = "###TEMP###"
                            s = s.replace("H", temp)
                            s = s.replace("B", "Bb")
                            s = s.replace(temp, "B")
                        else:
                            # Standard Input: H -> B
                            s = s.replace("H", "B")
                        return s

                    # Используем переданный input_ger для нормализации
                    key_root_eng = normalize_key_root_for_pychord(key_root_raw, input_ger)

                    try:
                        # 1. Старый scale (relative major для минора, root для мажора)
                        if is_minor:
                            old_scale_root_eng = song._get_relative_major_root(key_root_eng)
                        else:
                            old_scale_root_eng = key_root_eng

                        # 2. Транспонируем scale численно и нормализуем
                        old_scale_val = note_to_val(old_scale_root_eng)
                        new_scale_val = (old_scale_val + capo_val) % 12
                        new_scale_root_eng = song._normalize_key_root_from_val(new_scale_val)

                        # 3. Транспонируем корень ключа в системе нового scale
                        key_root_val = note_to_val(key_root_eng)
                        new_key_root_val = (key_root_val + capo_val) % 12
                        sounding_root_eng = val_to_note(new_key_root_val, new_scale_root_eng)

                        # 4. Формируем отображаемый ключ
                        sounding_key = sounding_root_eng + ("m" if is_minor else "")
                        display_key = f"{sounding_key}({song.key})"
                    except Exception:
                        pass
        except Exception:
            # Игнорируем ошибки (некорректный capo, сложная тональность и т.д.)
            pass

    return {
        "title": song.title,
        "artist": song.artist,
        "key": display_key,
        "capo": song.capo,
        "time": song.time,
        "tempo": song.tempo,
        "sections": build_sections_data(song),
        "layout": layout,
    }


def render_song_to_html(song, template, layout, input_ger=False):
    context = build_context(song, layout, input_ger=input_ger)
    return template.render(context)


def apply_transforms(song, args):
    # Обрабатываем аккорды с учетом входной и выходной нотации
    if args.transpose != 0:
        print(f"Transposing by {args.transpose} semitones...")

    song.transpose(args.transpose, input_ger=args.ger, output_std=args.std)

    # Опция: раскрыть ссылки на секции
    if args.expand_chorus:
        print("Expanding section references...")
        song.expand_section_references()


def render_song_to_files(filename, song, template, browser, layout, input_ger=False):
    html_content = render_song_to_html(song, template, layout, input_ger=input_ger)

    # Сохранить временный HTML
    temp_html_path = os.path.abspath(os.path.join(OUTPUT_DIR, f"{filename}.html"))
    with open(temp_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Рендер в JPG
    # Ширина задана; высота null/0 — на всю страницу
    page = browser.new_page(viewport={"width": 800, "height": 600})

    file_url = f"file://{temp_html_path}"
    page.goto(file_url)

    # Небольшая пауза для стабилизации вёрстки (для локального статического обычно мгновенно)
    # page.wait_for_timeout(100)

    output_filename = os.path.splitext(filename)[0] + ".jpg"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    # Скриншот контейнера песни, чтобы избежать бесконечной высоты и лишнего фона
    locator = page.locator(".song-container")
    locator.screenshot(path=output_path, type="jpeg", quality=90)

    print(f"Saved {output_path}")
    page.close()


def main():
    args = parse_args()

    # Создать выходную директорию при отсутствии
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Инициализация парсера и шаблона
    parser = ChordProParser()
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("song.html")

    # Поиск файлов
    files = find_input_files(INPUT_DIR)

    if not files:
        print("No .chordpro files found in input directory.")
        return

    with sync_playwright() as p:
        # Запуск браузера
        browser = p.chromium.launch()

        for filename in files:
            print(f"Processing {filename}...")
            filepath = os.path.join(INPUT_DIR, filename)

            # Чтение и разбор
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            song = parser.parse(content)
            apply_transforms(song, args)
            render_song_to_files(filename, song, template, browser, args.layout, input_ger=args.ger)

        browser.close()
        print("Done!")


if __name__ == "__main__":
    main()
