"""
Публичный библиотечный API для интеграции с внешними проектами.
"""

import argparse
from dataclasses import dataclass


@dataclass
class RenderParams:
    """
    Параметры рендера для библиотечного вызова.
    Значения по умолчанию повторяют поведение CLI.
    """
    transpose: int = 0
    capo: int = None
    in_ger: bool = False
    out_ger: bool = False
    layout: str = "sidebar"
    expand_chorus: bool = False
    small_extensions: bool = False


def _params_to_args(params: RenderParams):
    """
    Преобразует RenderParams в объект с CLI-совместимыми полями.
    Это позволяет переиспользовать apply_transforms() без дублирования логики.
    """
    return argparse.Namespace(
        transpose=params.transpose,
        capo=params.capo,
        in_ger=params.in_ger,
        out_ger=params.out_ger,
        layout=params.layout,
        expand_chorus=params.expand_chorus,
        small_extensions=params.small_extensions,
        from_db=None,
    )


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
    output_dir=None,
):
    """
    Публичный библиотечный API:
    рендерит один ChordPro-текст в JPG и возвращает путь к JPG.
    """
    if not chordpro_text or not str(chordpro_text).strip():
        raise ValueError("chordpro_text пустой: рендер невозможен.")

    if layout not in ("standard", "sidebar"):
        raise ValueError("layout должен быть 'standard' или 'sidebar'.")

    try:
        from . import chordpro_to_jpg as core
    except ImportError:
        import chordpro_to_jpg as core

    parser = core.ChordProParser()
    template_dir = core._resolve_local_dir(core.TEMPLATE_DIR)
    resolved_output_dir = core._resolve_local_dir(output_dir or core.OUTPUT_DIR)
    template = core._load_template(template_dir)

    try:
        song = parser.parse(chordpro_text)
    except Exception as e:
        core.LOGGER.error(f"Ошибка разбора ChordPro в библиотечном режиме: {e}")
        raise

    params = RenderParams(
        transpose=transpose,
        capo=capo,
        in_ger=in_ger,
        out_ger=out_ger,
        layout=layout,
        expand_chorus=expand_chorus,
        small_extensions=small_extensions,
    )
    args = _params_to_args(params)

    try:
        core.apply_transforms(song, args)
    except Exception as e:
        core.LOGGER.error(f"Ошибка применения трансформаций в библиотечном режиме: {e}")
        raise

    safe_stem = core._sanitize_filename_stem(filename_stem)
    with core.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            return core.render_song_to_files(
                safe_stem,
                song,
                template,
                browser,
                layout,
                input_ger=in_ger,
                output_ger=out_ger,
                index_chords=small_extensions,
                output_dir=resolved_output_dir,
            )
        finally:
            browser.close()


__all__ = ["RenderParams", "render_chordpro_to_jpg"]

