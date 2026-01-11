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
        self.time = ""
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
        
        # Iterate through all sections and lines
        for section in self.sections:
            for line in section.lines:
                # 1. Handle standard parts
                for part in line.parts:
                    if part.chord:
                        # Skip if not transposable
                        if not part.is_transposable:
                            continue

                        original_chord_str = part.chord.strip()
                        
                        # Step 1: Normalize Input to English
                        english_chord_str = normalize_input(original_chord_str, rbc_mode)
                        
                        try:
                            # Create chord object
                            c = Chord(english_chord_str)
                            # Transpose
                            if semitones != 0:
                                c.transpose(semitones, scale=new_key)
                            
                            # Get string representation (Internal English)
                            new_chord_str = str(c)
                            
                            # Step 2: Format Output to German (Always)
                            final_chord_str = format_output(new_chord_str)
                                
                            # Update the part
                            part.chord = final_chord_str
                        except Exception as e:
                            # If pychord cannot parse the chord, leave it as is
                            # This handles cases like N.C. or custom formatting
                            print(f"Warning: Could not transpose chord '{part.chord}': {e}")
                
                # 2. Handle grid cells
                if hasattr(line, 'grid_cells') and line.grid_cells:
                    for cell in line.grid_cells:
                        for part in cell.parts:
                            if part.chord:
                                original_chord_str = part.chord.strip()
                                english_chord_str = normalize_input(original_chord_str, rbc_mode)
                                try:
                                    c = Chord(english_chord_str)
                                    if semitones != 0:
                                        c.transpose(semitones, scale=new_key)
                                    new_chord_str = str(c)
                                    final_chord_str = format_output(new_chord_str)
                                    part.chord = final_chord_str
                                except Exception as e:
                                    print(f"Warning: Could not transpose grid chord '{part.chord}': {e}")


    def align_chords(self):
        """
        Visually centers chords over text by shifting characters from the previous part
        to the current part (effectively moving the chord left), or adding spaces
        if at the start of the line.
        """
        for section in self.sections:
            for line in section.lines:
                for i, part in enumerate(line.parts):
                    if not part.chord:
                        continue
                        
                    # Skip if chord is not attached to text (e.g. [A] [B])
                    # User: "Аккорды, которые не привязаны к тексту таким образом не надо выравнивать"
                    if not part.text or not part.text.strip():
                        continue
                        
                    chord_len = len(part.chord)
                    shift_needed = (chord_len - 1) // 2
                    
                    if shift_needed <= 0:
                        continue
                        
                    shifts_done = 0
                    while shifts_done < shift_needed:
                        # Case 1: Start of line -> Prepend space
                        if i == 0:
                            part.text = " " + part.text
                            shifts_done += 1
                            continue
                            
                        prev_part = line.parts[i-1]
                        
                        # Case 2: Previous part has text -> Move char
                        if prev_part.text:
                            char_to_move = prev_part.text[-1]
                            prev_part.text = prev_part.text[:-1]
                            part.text = char_to_move + part.text
                            shifts_done += 1
                        
                        # Case 3: Previous part has NO text
                        else:
                            # If previous part has a chord, we are blocked.
                            if prev_part.chord:
                                break # Stop shifting
                            
                            # If previous part has NO chord (and no text), it's empty text part (e.g. initially empty or exhausted start)
                            # Treat as start of line (add space)
                            if not prev_part.chord:
                                part.text = " " + part.text
                                shifts_done += 1
                            else:
                                break




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
                elif key in ['tempo', 'bpm']:
                    song.tempo = value
                elif key in ['time']:
                    song.time = value
                # Section Start
                elif key in ['soc', 'start_of_chorus']:
                    label = value if value else "Припев:"
                    current_section = Section(type="chorus", label=label)
                    song.sections.append(current_section)
                elif key == 'chorus':
                    label = value if value else "Припев"
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
                         # Try to parse as chord
                         try:
                             check_token = sub.replace('H', 'B')
                             # Basic validation
                             Chord(check_token)
                             cell.parts.append(Part(chord=sub, text=""))
                         except:
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

        # Post-process: Merge volta markers [1.] with following chords [E]
        # Look for Part(chord="(1.)", text="") followed immediately by Part(chord="...")
        merged_parts = []
        i = 0
        while i < len(line_obj.parts):
            p = line_obj.parts[i]
            
            # Check if this part is a volta marker
            is_volta = False
            volta_num = None
            
            if p.chord and not p.text.strip():
                # Regex for (1.) or (1.2.) etc.
                match = re.match(r'^\((\d+(?:\.\d+)?)\.\)$', p.chord.strip())
                if match:
                    is_volta = True
                    volta_num = match.group(1)
            
            # If it is a volta and next part exists and has a chord
            if is_volta and i + 1 < len(line_obj.parts):
                next_p = line_obj.parts[i+1]
                if next_p.chord:
                    # Merge volta into next part
                    next_p.volta = volta_num
                    merged_parts.append(next_p)
                    i += 2 # Skip both current and next (since we used next)
                    continue
            
            merged_parts.append(p)
            i += 1
            
        line_obj.parts = merged_parts
                
        return line_obj
