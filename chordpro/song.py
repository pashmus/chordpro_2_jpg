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

    def _get_section_type(self, text):
        """
        Determines section type based on text prefix.
        Patterns match chordpro_to_jpg.py logic.
        """
        if not text:
            return None
        t = text.strip().lower()
        # Chorus
        if t.startswith('пр.') or t.startswith('припев') or t.startswith('chorus'):
            return 'chorus'
        # Pre-chorus
        if t.startswith("пре-пр") or t.startswith("пред-пр") or t.startswith("препр") or t.startswith("предпр"):
            return 'pre_chorus'
        # Bridge
        if t.startswith('bridge') or t.startswith('бридж') or t.startswith('мост'):
            return 'bridge'
        return None

    def _get_section_id(self, text):
        """
        Extracts the first number found in text as the section ID.
        Returns int or None.
        """
        if not text:
            return None
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else None

    def expand_section_references(self):
        """
        Ищет комментарии-ссылки на секцию (напр. {comment: Припев}) и подставляет содержимое.
        Поддержка: припев, пре-припев, бридж.
        """
        # 1. Catalog all definitions (registry)
        # definitions[type][id] = section
        # definitions[type][None] = section (for unnumbered sections)
        definitions = {"chorus": {}, "pre_chorus": {}, "bridge": {}}

        for section in self.sections:
            if section.lines and section.type in ["chorus", "pre_chorus", "bridge"]:
                # Use parser's type, but we can also extract ID from label
                s_type = section.type
                s_id = self._get_section_id(section.label)

                # Store in registry
                definitions[s_type][s_id] = section
                # If ID is not None, also store as None if None not set?
                # No, strict mapping is better for validation.
                # But if we have "Chorus 1" and we ask for "Chorus", we might not find it if we look for None.
                # However, logic says: if ID not found/valid, use last_seen.

        # 2. Build new sections list with expansions
        new_sections = []

        # Last seen "full" section of each type
        last_seen = {"chorus": None, "pre_chorus": None, "bridge": None}

        for section in self.sections:
            # Update last_seen if this is a full section
            if section.lines and section.type in ["chorus", "pre_chorus", "bridge"]:
                last_seen[section.type] = section
                new_sections.append(section)
                continue

            # Check if this is an empty reference section (like {chorus})
            # Parser creates empty section with type='chorus'
            is_empty_ref = (
                not section.lines
                and section.type in ["chorus", "pre_chorus", "bridge"]
            )

            if is_empty_ref:
                # 1. Determine Type
                # Check label first (e.g. {chorus: Bridge})
                detected_type = self._get_section_type(section.label)
                ref_type = detected_type if detected_type else section.type

                # 2. Determine ID
                # Try to extract ID from label (e.g. {chorus: Chorus 2})
                ref_id = self._get_section_id(section.label)

                target = None

                # 3. Resolve Target
                # Validation: Check if this ID exists in definitions
                if ref_id is not None and ref_id in definitions[ref_type]:
                     target = definitions[ref_type][ref_id]
                else:
                    # If ID not valid or not present, use last_seen
                    target = last_seen[ref_type]

                if target:
                    # Create copy
                    new_sec = copy.deepcopy(target)

                    # 4. Determine Label
                    # If label is "generic" (just type name), use target label
                    # "Generic" means it consists ONLY of the type alias (and maybe whitespace/punctuation).
                    # If it has extra text (like "with fade"), it is NOT generic.

                    is_generic = False
                    lbl = section.label.strip() if section.label else ""

                    # Check for digits first (if digits exist, it's specific, e.g. "Chorus 2")
                    has_digits = any(c.isdigit() for c in lbl)

                    if not has_digits:
                        # Normalize for comparison: lowercase, remove trailing punctuation (colon, dot)
                        # We want to check if the WHOLE string is just a type alias.

                        # Known aliases (should match _get_section_type logic but strict)
                        # Chorus aliases
                        chorus_aliases = ["пр", "пр.", "припев", "chorus"]
                        # Pre-chorus aliases
                        pre_aliases = ["пре-пр", "пред-пр", "препр", "предпр", "pre-chorus", "prechorus"]
                        # Bridge aliases
                        bridge_aliases = ["bridge", "бридж", "мост"]

                        all_aliases = chorus_aliases + pre_aliases + bridge_aliases

                        # Clean label: remove trailing punctuation for comparison
                        clean_lbl = lbl.lower().rstrip(".: ")

                        if clean_lbl in all_aliases:
                            is_generic = True

                    if is_generic:
                        new_sec.label = target.label
                    else:
                         new_sec.label = section.label

                    new_sections.append(new_sec)
                else:
                    # Unresolved, keep as is
                    new_sections.append(section)

                continue

            # Check for comments inside section lines (e.g. Verse with {comment: Chorus})
            # Only relevant for non-empty sections that are NOT the ones we already handled above
            if section.lines:
                current_lines = []
                modified = False

                for line in section.lines:
                    match_found = False
                    if line.is_comment:
                        comment_text = "".join(p.text for p in line.parts if p.text).strip()

                        # Identify type
                        ref_type = self._get_section_type(comment_text)

                        if ref_type:
                            ref_id = self._get_section_id(comment_text)

                            # Resolve
                            target = None
                            if ref_id is not None and ref_id in definitions[ref_type]:
                                target = definitions[ref_type][ref_id]
                            else:
                                target = last_seen[ref_type]

                            if target:
                                match_found = True
                                modified = True

                                # Flush current lines
                                if current_lines:
                                    sub_section = Section(type=section.type, label=section.label)
                                    sub_section.lines = current_lines
                                    new_sections.append(sub_section)
                                    current_lines = []

                                # Insert expanded section
                                new_sec = copy.deepcopy(target)
                                new_sec.label = comment_text # Use comment text (e.g. "Chorus 2x")
                                new_sections.append(new_sec)

                    if not match_found:
                        current_lines.append(line)

                if current_lines:
                    if modified:
                        sub_section = Section(type=section.type, label=section.label)
                        sub_section.lines = current_lines
                        new_sections.append(sub_section)
                    else:
                        new_sections.append(section)
            else:
                new_sections.append(section)

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

    def transpose_chord_parts(self, chord_str, semitones, new_key, input_ger, output_std):
        """
        Транспонирует root и басовую ноты в аккорде, сохраняя остальные символы.

        Args:
            chord_str: строка аккорда (без квадратных скобок)
            semitones: количество полутонов для транспонирования
            new_key: новая тональность
            input_ger: режим интерпретации входа (True = German H/B, False = Standard B/Bb)
            output_std: режим выходной нотации (True = Standard B/Bb, False = German H/B)

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
        def normalize_note(note, is_german_input):
            """Конвертирует ноту в формат pychord (English notation)."""
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
                # Standard Input: H -> B (на всякий случай, если встретится H)
                s = s.replace("H", "B")
            return s

        # Транспонируем root ноту
        normalized_root = normalize_note(root_note, input_ger)
        try:
            transposed_root = transpose_note(normalized_root, semitones, new_key)
        except Exception:
            # Если транспонирование не удалось, оставляем как есть
            transposed_root = normalized_root

        # Транспонируем басовую ноту (если есть)
        transposed_bass = None
        if bass_note is not None:
            normalized_bass = normalize_note(bass_note, input_ger)
            try:
                transposed_bass = transpose_note(normalized_bass, semitones, new_key)
            except Exception:
                transposed_bass = normalized_bass

        # Форматируем обратно в нужную нотацию
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

        # Форматируем транспонированные ноты в зависимости от режима выхода
        if output_std:
            # Стандартная нотация на выходе: оставляем как есть (B, Bb)
            final_root = transposed_root
            final_bass = transposed_bass if transposed_bass else None
        else:
            # Германская нотация на выходе: конвертируем (H, B)
            final_root = format_note_to_german(transposed_root)
            final_bass = format_note_to_german(transposed_bass) if transposed_bass else None

        # Собираем результат
        result = final_root + root_rest
        if final_bass is not None:
            result += "/" + final_bass + after_bass

        return result

    def transpose(self, semitones, input_ger=False, output_std=False):
        """
        Транспонировать все аккорды на заданное число полутонов.
        
        Args:
            semitones: количество полутонов для транспонирования
            input_ger: режим входной нотации (True = German H/B, False = Standard B/Bb)
            output_std: режим выходной нотации (True = Standard B/Bb, False = German H/B)
        
        Интерпретация входа: по умолчанию (input_ger=False) — стандартный ввод (B=B, Bb=Bb);
        input_ger=True — немецкий ввод (B=Bb, H=B).
        Выход: по умолчанию (output_std=False) — немецкая нотация (B->H, Bb->B);
        output_std=True — стандартная нотация (B, Bb остаются как есть).
        """
        # Выполняем и при semitones == 0 для нормализации нотации.

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
                            original_chord_str, semitones, new_key, input_ger, output_std
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
                                    original_chord_str, semitones, new_key, input_ger, output_std
                                )

                                part.chord = final_chord_str
