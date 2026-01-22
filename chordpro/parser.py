import re

from .models import GridCell, Line, Part, Section, VoltaGroup
from .song import Song


class ChordProParser:
    def parse(self, content):
        song = Song()
        lines = content.splitlines()

        # Start with an implied verse if lyrics appear before any directive,
        # but usually we wait for the first section or just append to a 'generic' section.
        # Let's start with None and create on demand.
        current_section = None

        # Helper function to ensure a section exists (not used)
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
            if line.startswith("{") and line.endswith("}"):
                inner = line[1:-1]
                parts = inner.split(":", 1)
                key = parts[0].strip().lower()
                value = parts[1].strip() if len(parts) > 1 else ""

                # Metadata
                if key in ["title", "t"]:
                    song.title = value
                elif key in ["artist", "a"]:
                    song.artist = value
                elif key in ["key", "k"]:
                    song.key = value
                elif key in ["capo"]:
                    song.capo = value
                elif key in ["tempo", "bpm"]:
                    song.tempo = value
                elif key in ["time"]:
                    song.time = value
                # Section Start
                elif key in ["soc", "start_of_chorus"]:
                    label = value.strip()
                    # Check for Pre-chorus
                    if label.lower().startswith("пре-пр"):
                        current_section = Section(type="pre_chorus", label=label)
                    else:
                        current_section = Section(
                            type="chorus", label=label if label else "Пр.:"
                        )

                    song.sections.append(current_section)
                elif key == "chorus":
                    label = value if value else "Пр."  # Item 2: Handle bare {chorus}
                    # Also check for Pre-chorus shorthand if it exists (unlikely but safe)
                    if label.lower().startswith("пре-пр"):
                        current_section = Section(type="pre_chorus", label=label)
                    else:
                        current_section = Section(type="chorus", label=label)
                    song.sections.append(current_section)
                    current_section = None
                elif key in ["sov", "start_of_verse"]:
                    label = value if value else "Куплет:"
                    current_section = Section(type="verse", label=label)
                    song.sections.append(current_section)
                elif key in ["sob", "start_of_bridge"]:
                    label = value if value else "Bridge:"
                    current_section = Section(type="bridge", label=label)
                    song.sections.append(current_section)
                elif key in ["sog", "start_of_grid"]:
                    label = value if value else "Instr.:"
                    current_section = Section(type="grid", label=label)
                    song.sections.append(current_section)

                elif key in ["c", "comment"]:
                    # Parse the comment content for chords
                    parsed_line = self._parse_line(value)
                    parsed_line.is_comment = True

                    # Create a NEW section for each comment
                    # Метка оставляется пустой, чтобы не дублировать текст комментария
                    comment_section = Section(type="comment", label="")
                    comment_section.lines.append(parsed_line)
                    song.sections.append(comment_section)

                    # Reset current_section to ensure next comment creates a new section
                    current_section = None

                # Section End (we mainly just finish the current section,
                # effectively doing nothing as the next section start will handle creation,
                # but we can reset current_section to None to catch "orphan" lines if we wanted)
                elif key in [
                    "eoc",
                    "end_of_chorus",
                    "eov",
                    "end_of_verse",
                    "eob",
                    "end_of_bridge",
                    "eog",
                    "end_of_grid",
                ]:
                    current_section = None
                    # Keeping current_section active allows trailing lines to attach to it,
                    # but typically ChordPro structure is strict.
                    pass

                else:
                    song.metadata[key] = value

                continue

            # Comments
            if line.startswith("#"):
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

            if current_section.type == "grid":
                parsed_line = self._parse_grid_line(line)
            else:
                parsed_line = self._parse_line(line)

            current_section.lines.append(parsed_line)

        # Post-process: Expand chorus references (Moved to caller control)
        # song.expand_chorus_references()

        return song

    def _parse_grid_line(self, line_text):
        line_obj = Line()

        # Regex to split by bars, preserving delimiters
        # Even indices (0, 2, 4...) are content (measures)
        # Odd indices (1, 3, 5...) are separators (bars)
        # Supported bars: ||:, :||, |:, :|, ||, |, //:, ://
        tokens = re.split(r"(\|\|:|:\|\||\|:|:\||\|\||\||//:|://)", line_text)

        for i, token in enumerate(tokens):
            # token can be None if split at the very end with certain regex,
            # but re.split with capturing group usually returns empty strings.
            if token is None:
                continue

            if i % 2 == 1:
                # ODD index -> it's a BAR
                stripped = token.strip()
                cell = GridCell(is_bar=True, text=stripped)
                line_obj.grid_cells.append(cell)
            else:
                # EVEN index -> it's a MEASURE (content)
                # Even if it's empty, we add a cell to maintain column indexing
                content_text = token.strip()

                volta = None
                # Check for volta (starts with number) or is just a number
                volta_match = re.match(r"^(\d[\d,\.]*)\s+(.*)", content_text)
                if volta_match:
                    volta = volta_match.group(1)
                    if not volta.endswith("."):
                        volta += "."
                    content_text = volta_match.group(2)
                else:
                    volta_match = re.match(r"^(\d[\d,\.]*)$", content_text)
                    if volta_match:
                        volta = volta_match.group(1)
                        if not volta.endswith("."):
                            volta += "."
                        content_text = ""

                cell = GridCell(is_bar=False, volta=volta)

                # Parse content parts (chords/text/symbols)
                sub_tokens = content_text.split()
                for sub in sub_tokens:
                    if sub == ".":
                        cell.parts.append(Part(text="  "))
                    elif sub == "/":
                        cell.parts.append(Part(text="/"))
                    elif sub == "%":
                        cell.parts.append(Part(text="%"))
                    else:
                        # Chord detection (starts with A-G, H)
                        note_match = re.match(r"^([A-GH])([#b]?)", sub)
                        if note_match:
                            cell.parts.append(Part(chord=sub, text=""))
                        else:
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

        parts = line_text.split("[")

        # First part is text before any chord
        if parts[0]:
            line_obj.parts.append(Part(chord=None, text=parts[0]))

        for chunk in parts[1:]:
            if "]" in chunk:
                chord_part, text_part = chunk.split("]", 1)

                # Handle non-transposable chords (starting with *)
                is_transposable = True
                if chord_part.startswith("*"):
                    chord_part = chord_part[1:]
                    is_transposable = False

                line_obj.parts.append(
                    Part(chord=chord_part, text=text_part, is_transposable=is_transposable)
                )
            else:
                # Malformed
                line_obj.parts.append(Part(chord=None, text="[" + chunk))

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
            start_match = re.match(r"^\((\d+[\d\.]*\.)", chord_str)
            # Check for End Marker: Ends with ')'
            end_match = chord_str.endswith(")")

            if start_match:
                # If we were already in a volta, close it (fallback behavior for missing end)
                if current_volta:
                    new_items.append(current_volta)
                    current_volta = None

                volta_num = start_match.group(1).rstrip(".")

                # Clean the chord string for display
                # Remove the leading '(1.' prefix
                clean_chord = chord_str[len(start_match.group(0)) :]

                # If end marker is ALSO here (e.g. `[(1.G)]`)
                if end_match:
                    clean_chord = clean_chord[:-1]  # Remove trailing ')'

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
