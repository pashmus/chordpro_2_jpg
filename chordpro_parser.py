import re
import copy
from pychord import Chord
from pychord.utils import transpose_note
from pychord.constants import qualities

# --- Patch pychord qualities ---
# Register missing qualities like Emaj7-5 (maj7-5 / maj7b5)
# Intervals: Root(0), Major 3rd(4), Diminished 5th(6), Major 7th(11) -> (0, 4, 6, 11)
if not any(q[0] == 'maj7-5' for q in qualities.DEFAULT_QUALITIES):
    qualities.DEFAULT_QUALITIES.append(('maj7-5', (0, 4, 6, 11)))

if not any(q[0] == 'maj7b5' for q in qualities.DEFAULT_QUALITIES):
    qualities.DEFAULT_QUALITIES.append(('maj7b5', (0, 4, 6, 11)))
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


    def expand_chorus_references(self):
        """
        Look for comments that refer to a chorus label (e.g. {comment: Chorus})
        and replace them with the actual chorus content.
        """
        # 1. Map labels to chorus sections
        chorus_map = {}
        for section in self.sections:
            if section.type == 'chorus' and section.label:
                # Normalize label: strip whitespace and trailing colon
                norm_label = section.label.strip().rstrip(':')

                # Prioritize sections with content
                if section.lines:
                    chorus_map[norm_label] = section
                elif norm_label not in chorus_map:
                    chorus_map[norm_label] = section

        if not chorus_map:
            return

        new_sections = []

        for section in self.sections:
            if not section.lines:
                new_sections.append(section)
                continue

            # Buffer for lines in the current section fragment
            current_lines = []

            for line in section.lines:
                # Check if this line is a comment reference
                match_found = False
                if line.is_comment:
                    # Construct text from parts
                    # Usually comments parsed by _parse_line have one part with text,
                    # but let's be safe and join all text components.
                    comment_text = "".join(p.text for p in line.parts if p.text).strip()
                    norm_comment = comment_text.rstrip(':')

                    if norm_comment in chorus_map:
                        match_found = True
                        referenced_chorus = chorus_map[norm_comment]

                        # 1. Flush current lines to a section (if any)
                        # We use the original section's metadata
                        # If we have lines accumulated, we create a section chunk
                        if current_lines:
                            # We create a new section of the same type/label
                            # Only if there were lines before this comment
                            sub_section = Section(type=section.type, label=section.label)
                            sub_section.lines = current_lines
                            new_sections.append(sub_section)
                            current_lines = []

                        # 2. Add the referenced chorus (Deep Copy)
                        # We don't want to modify the original chorus if we change this one later (though we re-generate)
                        chorus_copy = copy.deepcopy(referenced_chorus)
                        new_sections.append(chorus_copy)

                        # Note: We do NOT add the comment line itself. It is replaced.

                if not match_found:
                    current_lines.append(line)

            # Flush remaining lines
            if current_lines:
                # If we split the section, the subsequent parts generally inherit the label/type
                # Example: Verse 1 -> Chorus -> Verse 1 (cont)
                # However, usually {comment: Chorus} is at the end of a block.
                # If it was the only line, we might be creating an empty section if we didn't check current_lines?
                # But current_lines was initialized empty. If loop finished and we added nothing, we add nothing.
                # BUT if the original section had lines and we didn't split, we just rebuild it.
                # If the original section was JUST the comment, current_lines is empty.
                # We should be careful about empty sections?
                # If we replaced the only line with a chorus section, we don't want an empty "Verse" section before/after.
                # Logic:
                # If `current_lines` is populated, we add it.
                # If `current_lines` is empty, we don't add a section.
                # UNLESS the original section was empty? (Not possible as we iterate lines)

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
        match = re.match(r'^([A-GH])([#b]?)(.*)$', chord_str)
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
        if '/' not in chord_str:
            return None, chord_str, ""

        parts = chord_str.split('/', 1)
        before_slash = parts[0]
        after_slash = parts[1]

        # Извлекаем ноту из начала after_slash
        match = re.match(r'^([A-GH])([#b]?)(.*)$', after_slash)
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
                s = s.replace('H', temp)
                s = s.replace('B', 'Bb')
                s = s.replace(temp, 'B')
            else:
                # RBC Mode: H -> B (на всякий случай)
                s = s.replace('H', 'B')
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
            s = note.replace('Bb', temp)
            s = s.replace('B', 'H')
            s = s.replace(temp, 'B')
            return s

        # Форматируем транспонированные ноты
        final_root = format_note_to_german(transposed_root)
        final_bass = format_note_to_german(transposed_bass) if transposed_bass else None

        # Собираем результат
        result = final_root + root_rest
        if final_bass is not None:
            result += '/' + final_bass + after_bass

        return result

    def transpose(self, semitones, rbc_mode=False):
        """
        Transpose all chords in the song by the given number of semitones.

        Input Interpretation:
        - Default (rbc_mode=False): German Input. 'B' = Bb, 'H' = B.
        - rbc_mode=True: English Input (Real B Chord). 'B' = B, 'Bb' = Bb.

        Output Format:
        - Always German Output. Internal 'B' -> 'H', Internal 'Bb' -> 'B'.
        """
        # Note: We run this even if semitones == 0 to normalize the notation to German Output.

        # Helper functions for notation conversion
        def normalize_input(chord_str, is_rbc):
            """Convert input string to standard English Pychord notation."""
            s = chord_str
            # Always handle H -> B (Si natural) as a convenience/safety even in RBC mode,
            # though strictly RBC implies English B.
            # But primarily:
            if not is_rbc:
                # German Input Mode:
                # H -> B (Si natural)
                # B -> Bb (Si flat)
                temp = "###TEMP###"
                s = s.replace('H', temp)
                s = s.replace('B', 'Bb')
                s = s.replace(temp, 'B')
            else:
                # RBC Mode (English Input):
                # B -> B (No change)
                # Bb -> Bb (No change)
                # We still map H -> B just in case user mixed it up, as pychord doesn't know H.
                s = s.replace('H', 'B')
            return s

        def format_output(chord_str):
            """Convert standard English Pychord notation to German Output."""
            # B -> H (Si natural)
            # Bb -> B (Si flat)
            temp = "###TEMP###"
            s = chord_str.replace('Bb', temp)
            s = s.replace('B', 'H')
            s = s.replace(temp, 'B')
            return s

        # Determine current key
        # If no key is specified, default to 'C'
        current_key = self.key if self.key else "C"

        # Calculate new key
        # transpose_note(note, semitones, scale)
        # We usually transpose the key relative to C to find the new root
        new_key = transpose_note(current_key, semitones, "C")

        # Update the song key
        self.key = new_key

        def collect_parts(items):
            """Recursively collect Part objects from a list of items (Parts or VoltaGroups)."""
            parts = []
            for item in items:
                if hasattr(item, 'is_volta_group') and item.is_volta_group:
                    parts.extend(collect_parts(item.parts))
                else:
                    parts.append(item)
            return parts

        # Iterate through all sections and lines
        for section in self.sections:
            for line in section.lines:
                # 1. Handle standard parts (flatten structure for traversal)
                all_parts = collect_parts(line.parts)

                for part in all_parts:
                    if part.chord:
                        # Skip if not transposable
                        if not part.is_transposable:
                            continue

                        original_chord_str = part.chord.strip()

                        # Используем новую функцию для транспонирования root и басовых нот
                        # Работает даже при semitones == 0 для нормализации нотации (H/B конвертация)
                        final_chord_str = self.transpose_chord_parts(original_chord_str, semitones, new_key, rbc_mode)

                        # Update the part
                        part.chord = final_chord_str

                # 2. Handle grid cells
                if hasattr(line, 'grid_cells') and line.grid_cells:
                    for cell in line.grid_cells:
                        for part in cell.parts:
                            if part.chord:
                                original_chord_str = part.chord.strip()

                                # Используем новую функцию для транспонирования root и басовых нот
                                # Работает даже при semitones == 0 для нормализации нотации (H/B конвертация)
                                final_chord_str = self.transpose_chord_parts(original_chord_str, semitones, new_key, rbc_mode)

                                part.chord = final_chord_str






class Section:
    def __init__(self, type="verse", label=""):
        self.type = type # verse, chorus, bridge, grid (instrumental)
        self.label = label
        self.lines = []

class Line:
    def __init__(self, is_comment=False):
        self.parts = []
        self.grid_cells = [] # For grid sections
        self.is_comment = is_comment

class GridCell:
    def __init__(self, is_bar=False, text="", volta=None):
        self.is_bar = is_bar
        self.text = text
        self.volta = volta
        self.parts = []  # List of Part objects

class Part:
    def __init__(self, chord=None, text="", is_transposable=True, volta=None):
        self.chord = chord
        self.text = text
        self.is_transposable = is_transposable
        self.volta = volta

    def __repr__(self):
        return f"Part(chord={self.chord}, text={self.text}, transposable={self.is_transposable}, volta={self.volta})"

class VoltaGroup:
    def __init__(self, number, parts=None):
        self.number = number
        self.parts = parts if parts is not None else []
        self.is_volta_group = True

class ChordProParser:
    def parse(self, content):
        song = Song()
        lines = content.splitlines()

        # Start with an implied verse if lyrics appear before any directive,
        # but usually we wait for the first section or just append to a 'generic' section.
        # Let's start with None and create on demand.
        current_section = None

        def ensure_section(type="verse", label="Verse"):
            nonlocal current_section
            if current_section is None:
                current_section = Section(type=type, label=label)
                song.sections.append(current_section)
            return current_section

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Directives
            if line.startswith('{') and line.endswith('}'):
                inner = line[1:-1]
                parts = inner.split(':', 1)
                key = parts[0].strip().lower()
                value = parts[1].strip() if len(parts) > 1 else ""

                # Metadata
                if key in ['title', 't']:
                    song.title = value
                elif key in ['artist', 'a']:
                    song.artist = value
                elif key in ['key', 'k']:
                    song.key = value
                elif key in ['capo']:
                    song.capo = value
                elif key in ['tempo', 'bpm']:
                    song.tempo = value
                elif key in ['time']:
                    song.time = value
                # Section Start
                elif key in ['soc', 'start_of_chorus']:
                    label = value.strip()
                    # Check for Pre-chorus
                    if label.lower().startswith("пре-пр"):
                        current_section = Section(type="pre_chorus", label=label)
                    else:
                        current_section = Section(type="chorus", label=label if label else "Пр.:")

                    song.sections.append(current_section)
                elif key == 'chorus':
                    label = value if value else "Пр." # Item 2: Handle bare {chorus}
                    # Also check for Pre-chorus shorthand if it exists (unlikely but safe)
                    if label.lower().startswith("пре-пр"):
                        current_section = Section(type="pre_chorus", label=label)
                    else:
                        current_section = Section(type="chorus", label=label)
                    song.sections.append(current_section)
                    current_section = None
                elif key in ['sov', 'start_of_verse']:
                    label = value if value else "Куплет:"
                    current_section = Section(type="verse", label=label)
                    song.sections.append(current_section)
                elif key in ['sob', 'start_of_bridge']:
                    label = value if value else "Bridge:"
                    current_section = Section(type="bridge", label=label)
                    song.sections.append(current_section)
                elif key in ['sog', 'start_of_grid']:
                    label = value if value else "Instr.:"
                    current_section = Section(type="grid", label=label)
                    song.sections.append(current_section)

                elif key in ['c', 'comment']:
                     # Parse the comment content for chords
                    parsed_line = self._parse_line(value)
                    parsed_line.is_comment = True

                    if current_section is None:
                        current_section = Section(type="verse", label="")
                        song.sections.append(current_section)
                    current_section.lines.append(parsed_line)

                # Section End (we mainly just finish the current section,
                # effectively doing nothing as the next section start will handle creation,
                # but we can reset current_section to None to catch "orphan" lines if we wanted)
                elif key in ['eoc', 'end_of_chorus', 'eov', 'end_of_verse', 'eob', 'end_of_bridge', 'eog', 'end_of_grid']:
                    current_section = None
                    # Keeping current_section active allows trailing lines to attach to it,
                    # but typically ChordPro structure is strict.
                    pass

                else:
                    song.metadata[key] = value

                continue

            # Comments
            if line.startswith('#'):
                continue

            # Inline Comment Directive (e.g. {comment: Intro...})
            # Handled in directives loop if it was {comment:...},
            # BUT the directives loop above handles keys. We need to add 'comment' there.

            # Let's move comment handling into the directives block
            # Re-reading the code: I am outside the directives block here.
            # I need to modify the directives block to include 'comment' / 'c'
            pass

            # Parse content
            # If no section exists, create a default Verse
            if current_section is None:
                current_section = Section(type="verse", label="")
                song.sections.append(current_section)

            if current_section.type == 'grid':
                parsed_line = self._parse_grid_line(line)
            else:
                parsed_line = self._parse_line(line)

            current_section.lines.append(parsed_line)

        # Post-process: Expand chorus references
        song.expand_chorus_references()

        return song

    def _parse_grid_line(self, line_text):
        line_obj = Line()

        # Regex to split by bars.
        # Detect all bar types: |, ||, :|, |:, |1 (where 1 is volta, handled later), etc.
        # We look for standard bar delimiters.
        # Order matters for regex: longest first.
        tokens = re.split(r'(\|\||:\||\|:|\|)', line_text)

        for token in tokens:
            if not token:
                continue

            stripped = token.strip()

            # Check if it is a bar
            if stripped in ['|', '||', ':|', '|:']:
                # It is a bar
                cell = GridCell(is_bar=True, text=stripped)
                line_obj.grid_cells.append(cell)
            else:
                # It is content (measure)
                # Parse volta and content

                content_text = token.strip()
                if not content_text:
                    # Empty space between bars or at end?
                    # If it's just whitespace, we can ignore or add empty cell?
                    # Usually " | | " -> empty cell in between.
                    # But if split produced empty string, we already skipped it.
                    # If token was " ", stripped is "".
                    # Let's verify:
                    # "| |" -> split -> "", "|", " ", "|", ""
                    # " " -> skipped? No, token is " ". stripped is "".
                    # If we want empty measures to appear, we should handle empty stripped if token was not empty?
                    # But " " usually is insignificant. " | | " is one empty measure.
                    # If I skip it, I get Bar, Bar. That looks like "||".
                    # I need a cell between them if there was space.
                    # But `re.split` gives " " between "|" and "|".
                    pass

                # If stripped is empty but token was not, it's whitespace.
                # If we have `| |`, we want a cell.
                # But typically we want explicit content.
                # Let's proceed with parsing content if there is any text.

                if not content_text:
                    continue

                volta = None

                # Check for volta (starts with number)
                # Example: "1 Am" -> volta 1
                volta_match = re.match(r'^(\d[\d,\.]*)\s+(.*)', content_text)
                if volta_match:
                    volta = volta_match.group(1)
                    content_text = volta_match.group(2)
                else:
                    # Check if it's JUST a number (e.g. "1")
                    volta_match = re.match(r'^(\d[\d,\.]*)$', content_text)
                    if volta_match:
                        volta = volta_match.group(1)
                        content_text = ""

                cell = GridCell(is_bar=False, volta=volta)

                # Parse content parts
                # Split by space to identify chords / text
                sub_tokens = content_text.split()
                for sub in sub_tokens:
                    if sub == '.':
                         # Dot replacement -> spacing
                         cell.parts.append(Part(text="  "))
                    elif sub == '/':
                         cell.parts.append(Part(text="/"))
                    elif sub == '%':
                         cell.parts.append(Part(text="%"))
                    else:
                         # Check if token starts with a note (A-G, H) - treat as chord
                         # This allows non-standard chords like "Ebh4/Gb" to be recognized
                         note_match = re.match(r'^([A-GH])([#b]?)', sub)
                         if note_match:
                             # Starts with a note - treat as chord (even if pychord doesn't recognize it)
                             cell.parts.append(Part(chord=sub, text=""))
                         else:
                             # Not a chord, treat as text
                             cell.parts.append(Part(text=sub + " "))

                line_obj.grid_cells.append(cell)

        return line_obj

    def _parse_line(self, line_text):
        line_obj = Line()

        # Check for grid lines which might not have standard brackets if it's just | G | C |
        # But standard chordpro usually still brackets chords like [G] even in grids,
        # OR it uses the |...| syntax.
        # The example `| G/B | C2 | D | G | - 2x` suggests raw text with bars.
        # Since our parser looks for `[`, lines without brackets are treated as pure text.
        # To support chords without brackets in specific contexts requires a more complex parser (detecting chord names).
        # HOWEVER, in the user's example: `[G/B]` IS used in verses.
        # In the `{sog}` block: `| G/B | C2 | D | G | - 2x`. No brackets.
        # If we just treat this as text, it won't be highlighted as chords.
        # Strategy: regex for likely chords? Or just leave as text but style the "grid" section in monospace.
        # Monospace is the safest bet for grid sections without brackets.

        parts = line_text.split('[')

        # First part is text before any chord
        if parts[0]:
            line_obj.parts.append(Part(chord=None, text=parts[0]))

        for chunk in parts[1:]:
            if ']' in chunk:
                chord_part, text_part = chunk.split(']', 1)

                # Handle non-transposable chords (starting with *)
                is_transposable = True
                if chord_part.startswith('*'):
                    chord_part = chord_part[1:]
                    is_transposable = False

                line_obj.parts.append(Part(chord=chord_part, text=text_part, is_transposable=is_transposable))
            else:
                # Malformed
                line_obj.parts.append(Part(chord=None, text='[' + chunk))

        line_obj.parts = self._group_voltas(line_obj.parts)

        return line_obj

    def _group_voltas(self, parts):
        """
        Groups parts into VoltaGroup objects based on start/end markers.
        Start marker: Chord starts with '(', e.g., '(1.' or '(1.G'
        End marker: Chord ends with ')', e.g., 'E)' or ')'
        """
        new_items = []
        current_volta = None

        for part in parts:
            if not part.chord:
                if current_volta:
                    current_volta.parts.append(part)
                else:
                    new_items.append(part)
                continue

            chord_str = part.chord.strip()

            # Check for Start Marker: (1. or (1.2.
            # Regex: Starts with '(', digit, optional lines/dots, MUST end with dot.
            # We want to capture the number '1.' or '1.2.'
            start_match = re.match(r'^\((\d+[\d\.]*\.)', chord_str)
            # Check for End Marker: Ends with ')'
            end_match = chord_str.endswith(')')

            if start_match:
                # If we were already in a volta, close it (fallback behavior for missing end)
                if current_volta:
                    new_items.append(current_volta)
                    current_volta = None

                volta_num = start_match.group(1).rstrip('.')

                # Clean the chord string for display
                # Remove the leading '(1.' prefix
                clean_chord = chord_str[len(start_match.group(0)):]

                # If end marker is ALSO here (e.g. `[(1.G)]`)
                if end_match:
                     clean_chord = clean_chord[:-1] # Remove trailing ')'

                part.chord = clean_chord if clean_chord else None

                current_volta = VoltaGroup(number=volta_num)
                current_volta.parts.append(part)

                if end_match:
                    # Opens and closes in same part
                    self._optimize_and_append_volta(new_items, current_volta)
                    current_volta = None

            elif end_match and current_volta:
                # End of active volta: Remove trailing ')' and close group
                clean_chord = chord_str[:-1]
                part.chord = clean_chord if clean_chord else None

                current_volta.parts.append(part)
                self._optimize_and_append_volta(new_items, current_volta)
                current_volta = None

            else:
                # Normal part or closing bracket WITHOUT an active volta
                if current_volta:
                    current_volta.parts.append(part)
                else:
                    new_items.append(part)

        # Flush open volta
        if current_volta:
             self._optimize_and_append_volta(new_items, current_volta)

        return new_items

    def _optimize_and_append_volta(self, target_list, volta_group):
        """
        Optimizes the volta group by moving leading text-only parts OUT of the group.
        This ensures formatting (brackets, numbers) starts at the first actual chord,
        preventing lyrics breaks if the marker was placed before a syllable without a chord.
        """
        # If group is empty, just distinct
        if not volta_group.parts:
            target_list.append(volta_group)
            return

        # Check if there is AT LEAST ONE part with a chord in the group
        has_chords = any(p.chord for p in volta_group.parts)

        if not has_chords:
            # If no chords at all, we keep it as is (bracket over text)
            target_list.append(volta_group)
            return

        # Eject leading parts that have NO chord
        # We stop as soon as we hit a part with a chord
        while volta_group.parts and not volta_group.parts[0].chord:
            p = volta_group.parts.pop(0)
            target_list.append(p)

        # Determine if we still have parts (should allow yes if has_chords was true)
        if volta_group.parts:
            target_list.append(volta_group)
