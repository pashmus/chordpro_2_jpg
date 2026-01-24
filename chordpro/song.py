import copy
import re
from pychord.utils import transpose_note
from pychord.constants import qualities

from .models import Section

# --- Патч квалификаторов (свойств) pychord ---
# Регистрация отсутствующих квалификаторов (свойств), напр. Emaj7-5 (maj7-5 / maj7b5)
# Интервалы: тоника(0), большая терция(4), уменьшённая квинта(6), большая септима(11) -> (0, 4, 6, 11)
if not any(q[0] == "maj7-5" for q in qualities.DEFAULT_QUALITIES):
    qualities.DEFAULT_QUALITIES.append(("maj7-5", (0, 4, 6, 11)))

if not any(q[0] == "maj7b5" for q in qualities.DEFAULT_QUALITIES):
    qualities.DEFAULT_QUALITIES.append(("maj7b5", (0, 4, 6, 11)))
# -------------------------------


class Song:
    def __init__(self):
        self.title = "Untitled"
        self.artist = ""
        self.key = ""
        self.tempo = ""
        self.tempo = ""
        self.time = ""
        self.capo = ""
        self.metadata = {}
        self.sections = []

    def expand_section_references(self):
        """
        Ищет комментарии-ссылки на секцию (напр. {comment: Припев}) и подставляет содержимое.
        Поддержка: припев, пре-припев, бридж.
        """
        # 1. Сопоставить метки и секции
        section_map = {}
        for section in self.sections:
            if section.type in ["chorus", "pre_chorus", "bridge"] and section.label:
                # Нормализация метки: обрезка пробелов и завершающего двоеточия
                norm_label = section.label.strip().rstrip(":")

                # Приоритет у секций с содержимым
                if section.lines:
                    section_map[norm_label] = section
                elif norm_label not in section_map:
                    section_map[norm_label] = section

        if not section_map:
            return

        new_sections = []

        for section in self.sections:
            if not section.lines:
                new_sections.append(section)
                continue

            # Буфер строк текущего фрагмента секции
            current_lines = []

            for line in section.lines:
                # Проверка: строка — ссылка на секцию (комментарий)
                match_found = False
                if line.is_comment:
                    # Собрать текст из Part
                    comment_text = "".join(p.text for p in line.parts if p.text).strip()
                    norm_comment = comment_text.rstrip(":")

                    if norm_comment in section_map:
                        match_found = True
                        referenced_section = section_map[norm_comment]

                        # 1. Сбросить накопленные строки в секцию (если есть)
                        if current_lines:
                            sub_section = Section(type=section.type, label=section.label)
                            sub_section.lines = current_lines
                            new_sections.append(sub_section)
                            current_lines = []

                        # 2. Добавить секцию-ссылку (глубокая копия)
                        section_copy = copy.deepcopy(referenced_section)
                        new_sections.append(section_copy)

                        # Строку-комментарий не добавляем — она заменяется.

                if not match_found:
                    current_lines.append(line)

            # Сбросить оставшиеся строки
            if current_lines:
                sub_section = Section(type=section.type, label=section.label)
                sub_section.lines = current_lines
                new_sections.append(sub_section)

        self.sections = new_sections

    def extract_root_note(self, chord_str):
        """
        Извлекает root ноту из начала строки аккорда.
        Root нота: заглавная буква (A-G, H) + опционально # или b.
        Возвращает (note, rest) где note - нота или None, rest - остаток строки.
        """
        if not chord_str:
            return None, chord_str

        # Паттерн: заглавная буква A-G или H, затем опционально # или b
        match = re.match(r"^([A-GH])([#b]?)(.*)$", chord_str)
        if match:
            note = match.group(1) + match.group(2)  # Буква + опциональный знак
            rest = match.group(3)  # Остаток строки
            return note, rest
        return None, chord_str

    def extract_bass_note(self, chord_str):
        """
        Извлекает басовую ноту после слэша.
        Басовая нота: / + заглавная буква (A-G, H) + опционально # или b.
        Возвращает (note, before_slash, after_note) где:
        - note - нота или None
        - before_slash - часть до слэша
        - after_note - часть после ноты
        """
        if "/" not in chord_str:
            return None, chord_str, ""

        parts = chord_str.split("/", 1)
        before_slash = parts[0]
        after_slash = parts[1]

        # Извлекаем ноту из начала after_slash
        match = re.match(r"^([A-GH])([#b]?)(.*)$", after_slash)
        if match:
            note = match.group(1) + match.group(2)
            after_note = match.group(3)
            return note, before_slash, after_note

        return None, before_slash, after_slash

    def transpose_chord_parts(self, chord_str, semitones, new_key, rbc_mode):
        """
        Транспонирует root и басовую ноты в аккорде, сохраняя остальные символы.

        Args:
            chord_str: строка аккорда (без квадратных скобок)
            semitones: количество полутонов для транспонирования
            new_key: новая тональность
            rbc_mode: режим интерпретации входа (True = English B, False = German B)

        Returns:
            транспонированная строка аккорда
        """
        if not chord_str:
            return chord_str

        # Извлекаем root ноту
        root_note, root_rest = self.extract_root_note(chord_str)

        # Если root нота не найдена, возвращаем как есть
        if root_note is None:
            return chord_str

        # Извлекаем басовую ноту (если есть)
        bass_note, before_slash, after_bass = self.extract_bass_note(root_rest)

        # Если басовая нота найдена, root_rest = before_slash
        # Если нет, root_rest остается как есть
        if bass_note is not None:
            root_rest = before_slash

        # Нормализуем ноты для pychord (конвертация H/B)
        def normalize_note(note, is_rbc):
            """Конвертирует ноту в формат pychord (English notation)."""
            if not note:
                return note
            s = note
            if not is_rbc:
                # German Input: H -> B, B -> Bb
                temp = "###TEMP###"
                s = s.replace("H", temp)
                s = s.replace("B", "Bb")
                s = s.replace(temp, "B")
            else:
                # RBC Mode: H -> B (на всякий случай)
                s = s.replace("H", "B")
            return s

        # Транспонируем root ноту
        normalized_root = normalize_note(root_note, rbc_mode)
        try:
            transposed_root = transpose_note(normalized_root, semitones, new_key)
        except Exception:
            # Если транспонирование не удалось, оставляем как есть
            transposed_root = normalized_root

        # Транспонируем басовую ноту (если есть)
        transposed_bass = None
        if bass_note is not None:
            normalized_bass = normalize_note(bass_note, rbc_mode)
            try:
                transposed_bass = transpose_note(normalized_bass, semitones, new_key)
            except Exception:
                transposed_bass = normalized_bass

        # Форматируем обратно в немецкую нотацию
        def format_note_to_german(note):
            """Конвертирует ноту из English в German notation."""
            if not note:
                return note
            # B -> H, Bb -> B
            temp = "###TEMP###"
            s = note.replace("Bb", temp)
            s = s.replace("B", "H")
            s = s.replace(temp, "B")
            return s

        # Форматируем транспонированные ноты
        final_root = format_note_to_german(transposed_root)
        final_bass = format_note_to_german(transposed_bass) if transposed_bass else None

        # Собираем результат
        result = final_root + root_rest
        if final_bass is not None:
            result += "/" + final_bass + after_bass

        return result

    def transpose(self, semitones, rbc_mode=False):
        """
        Транспонировать все аккорды на заданное число полутонов.
        Интерпретация входа: по умолчанию (rbc_mode=False) — немецкий ввод (B=Bb, H=B);
        rbc_mode=True — английский (B=B, Bb=Bb). Выход всегда в немецкой нотации: B->H, Bb->B.
        """
        # Выполняем и при semitones == 0 для нормализации в немецкую нотацию.

        # Определить текущую тональность
        # Если тональность не указана — по умолчанию C
        current_key = self.key if self.key else "C"

        # Вычислить новую тональность
        # transpose_note(нота, полутоны, строй); тональность обычно транспонируем от C для нового тонического звука
        new_key = transpose_note(current_key, semitones, "C")

        # Обновить тональность в песне
        self.key = new_key

        def collect_parts(items):
            """Рекурсивно собрать Part из списка (Part или VoltaGroup)."""
            parts = []
            for item in items:
                if hasattr(item, "is_volta_group") and item.is_volta_group:
                    parts.extend(collect_parts(item.parts))
                else:
                    parts.append(item)
            return parts

        # Перебор всех секций и строк
        for section in self.sections:
            for line in section.lines:
                # 1. Обычные Part (развернуть структуру для обхода)
                all_parts = collect_parts(line.parts)

                for part in all_parts:
                    if part.chord:
                        # Пропуск нетранспонируемых
                        if not part.is_transposable:
                            continue

                        original_chord_str = part.chord.strip()

                        # Используем новую функцию для транспонирования root и басовых нот
                        # Работает даже при semitones == 0 для нормализации нотации (H/B конвертация)
                        final_chord_str = self.transpose_chord_parts(
                            original_chord_str, semitones, new_key, rbc_mode
                        )

                        # Обновить Part
                        part.chord = final_chord_str

                # 2. Ячейки сетки
                if hasattr(line, "grid_cells") and line.grid_cells:
                    for cell in line.grid_cells:
                        for part in cell.parts:
                            if part.chord:
                                original_chord_str = part.chord.strip()

                                # Используем новую функцию для транспонирования root и басовых нот
                                # Работает даже при semitones == 0 для нормализации нотации (H/B конвертация)
                                final_chord_str = self.transpose_chord_parts(
                                    original_chord_str, semitones, new_key, rbc_mode
                                )

                                part.chord = final_chord_str
