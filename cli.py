"""
Точка входа CLI и связанные функции запуска.
"""

import argparse


def parse_args():
    cli_parser = argparse.ArgumentParser(
        description="Convert ChordPro files to HTML/JPG images."
    )
    cli_parser.add_argument(
        "--transpose", "-t", type=int, default=0, help="Transpose chords by N semitones"
    )
    cli_parser.add_argument(
        "--capo",
        "-capo",
        type=int,
        default=None,
        help=(
            "Set capo position for output metadata and compensate chords/key by capo semitones. "
            "Values 2, +2 and -2 are treated identically."
        ),
    )
    cli_parser.add_argument(
        "-in-ger",
        "--in-ger",
        dest="in_ger",
        action="store_true",
        help=(
            "German notation on input: 'H' is B natural, 'B' is B flat. "
            "Default (without this flag) is standard input: 'B' is B natural, 'Bb' is B flat."
        ),
    )
    cli_parser.add_argument(
        "-out-ger",
        "--out-ger",
        dest="out_ger",
        action="store_true",
        help=(
            "German notation on output: 'H' is B natural, 'B' is B flat. "
            "Default (without this flag) is standard output: 'B' is B natural, 'Bb' is B flat."
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
    cli_parser.add_argument(
        "-db",
        "--from-db",
        nargs="+",
        metavar="SONG_NUM_OR_RANGE",
        help=(
            "Брать песни из БД по указанным номерам или диапазонам "
            "(формат N или N-M) вместо чтения файлов из папки. "
            "Примеры: -db 321 322 323  или  -db 300-350 400"
        ),
    )
    cli_parser.add_argument(
        "-small-ext",
        "--small-extensions",
        dest="small_extensions",
        action="store_true",
        help=(
            "Включить режим уменьшенных дополнений аккордов "
            "(dim7, maj7, sus4 и т.п.), кроме знаков диез/бемоль и минорного m "
            "сразу после тоники."
        ),
    )
    return cli_parser.parse_args()


def render_songs_from_folder(args):
    """
    Рендерит песни из файлов в папке INPUT_DIR (текущий стандартный режим).
    """
    try:
        from . import chordpro_to_jpg as core
    except ImportError:
        import chordpro_to_jpg as core

    input_dir = core._resolve_local_dir(core.INPUT_DIR)
    output_dir = core._resolve_local_dir(core.OUTPUT_DIR)
    template_dir = core._resolve_local_dir(core.TEMPLATE_DIR)

    # Создать выходную директорию при отсутствии
    core.os.makedirs(output_dir, exist_ok=True)

    # Инициализация парсера и шаблона
    parser = core.ChordProParser()
    template = core._load_template(template_dir)

    # Поиск файлов
    files = core.find_input_files(input_dir)

    if not files:
        core.LOGGER.warning(
            f"Во входной директории '{input_dir}' не найдено файлов "
            ".chordpro/.pro/.cho."
        )
        return

    with core.sync_playwright() as p:
        # Запуск браузера
        browser = p.chromium.launch()
        try:
            for filename in files:
                filepath = core.os.path.join(input_dir, filename)

                # Чтение и разбор
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    core.LOGGER.error(f"Ошибка чтения входного файла '{filepath}': {e}")
                    continue

                try:
                    song = parser.parse(content)
                except Exception as e:
                    core.LOGGER.error(f"Ошибка разбора файла '{filename}': {e}")
                    continue

                try:
                    core.apply_transforms(song, args)
                except Exception as e:
                    core.LOGGER.error(
                        f"Ошибка применения преобразований для '{filename}': {e}"
                    )
                    continue

                try:
                    core.render_song_to_files(
                        filename,
                        song,
                        template,
                        browser,
                        args.layout,
                        input_ger=args.in_ger,
                        output_ger=args.out_ger,
                        index_chords=args.small_extensions,
                        output_dir=output_dir,
                    )
                except Exception:
                    continue
        finally:
            browser.close()


def render_songs_from_db(args):
    """
    Рендерит песни, взятые из поля songs.chordpro по номерам/диапазонам,
    указанным во флаге -db/--from-db.
    """
    try:
        from . import chordpro_to_jpg as core
    except ImportError:
        import chordpro_to_jpg as core

    raw_tokens = args.from_db or []
    song_numbers = core._parse_song_numbers(raw_tokens)
    if not song_numbers:
        core.LOGGER.error("Не удалось разобрать номера песен для режима --from-db.")
        return

    output_dir = core._resolve_local_dir(core.OUTPUT_DIR)
    template_dir = core._resolve_local_dir(core.TEMPLATE_DIR)

    db_manager = core._get_db_manager()
    if db_manager is None:
        return

    # Создать выходную директорию при отсутствии
    core.os.makedirs(output_dir, exist_ok=True)

    # Инициализация парсера и шаблона
    parser = core.ChordProParser()
    template = core._load_template(template_dir)

    with core.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for song_number in song_numbers:
                chordpro_text, title = core._fetch_song_chordpro_and_title(
                    db_manager, song_number
                )
                if not chordpro_text:
                    # Уже выведено предупреждение, просто пропускаем
                    continue

                filename = core._make_filename_for_song(song_number, title)

                try:
                    song = parser.parse(chordpro_text)
                except Exception as e:
                    core.LOGGER.error(f"Ошибка разбора файла '{filename}': {e}")
                    continue

                try:
                    core.apply_transforms(song, args)
                except Exception as e:
                    core.LOGGER.error(
                        f"Ошибка применения преобразований для '{filename}': {e}"
                    )
                    continue

                try:
                    core.render_song_to_files(
                        filename,
                        song,
                        template,
                        browser,
                        args.layout,
                        input_ger=args.in_ger,
                        output_ger=args.out_ger,
                        index_chords=args.small_extensions,
                        output_dir=output_dir,
                    )
                except Exception:
                    continue
        finally:
            browser.close()
            db_manager.close()


def main():
    args = parse_args()

    # Если указан режим работы с БД, берём песни по номерам из songs.chordpro
    if getattr(args, "from_db", None):
        render_songs_from_db(args)
    else:
        # Стандартный режим: брать .cho/.pro/.chordpro файлы из папки INPUT_DIR
        render_songs_from_folder(args)


__all__ = ["parse_args", "render_songs_from_folder", "render_songs_from_db", "main"]

