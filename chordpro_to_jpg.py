import os
import sys
import argparse
from playwright.sync_api import sync_playwright
from jinja2 import Environment, FileSystemLoader
from chordpro_parser import ChordProParser

# Configuration
INPUT_DIR = 'input_cho'
OUTPUT_DIR = 'output_jpg'
TEMPLATE_DIR = 'templates'
STYLES_DIR = 'styles'


def main():
    # Parse CLI arguments
    cli_parser = argparse.ArgumentParser(description="Convert ChordPro files to HTML/JPG images.")
    cli_parser.add_argument("--transpose", "-t", type=int, default=0, help="Transpose chords by N semitones")
    cli_parser.add_argument("--rbc", "-r", action="store_true",
                            help="Real B Chord: Input 'B' is B natural, 'Bb' is B flat. Default (without this flag) is German input: 'B' is B flat, 'H' is B natural.")
    cli_parser.add_argument("--layout", "-l", type=str, default="sidebar", choices=["standard", "sidebar"],
                            help="Layout type: 'sidebar' (default) or 'standard'")

    args = cli_parser.parse_args()

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Initialize Parser and Template
    parser = ChordProParser()
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template('song.html')

    # Find files
    files = [f for f in os.listdir(INPUT_DIR) if f.endswith('.chordpro') or f.endswith('.pro') or f.endswith('.cho')]

    if not files:
        print("No .chordpro files found in input directory.")
        return

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch()

        for filename in files:
            print(f"Processing {filename}...")
            filepath = os.path.join(INPUT_DIR, filename)

            # Read and Parse
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            song = parser.parse(content)

            # Always process to ensure correct German output notation (H=Si, B=Si b)
            # The rbc_mode flag controls how INPUT is interpreted.
            if args.transpose != 0:
                print(f"Transposing by {args.transpose} semitones...")

            song.transpose(args.transpose, rbc_mode=args.rbc)



            # Prepare context for template
            sections_data = []
            for sec in song.sections:
                if not sec.lines and not sec.label:
                    continue
                lines_data = []

                # Check alignment for the section label (for sidebar layout)
                # We look at the first line to decide if we need to offset the label.
                # Helper to flatten parts for checking content
                def collect_parts(items):
                    p = []
                    for item in items:
                        if hasattr(item, 'is_volta_group') and item.is_volta_group:
                            p.extend(collect_parts(item.parts))
                        else:
                            p.append(item)
                    return p

                # Helper to serialize parts for template
                def serialize_item(item, is_anchor=False, is_floating=False, floating_siblings=None):
                    if hasattr(item, 'is_volta_group') and item.is_volta_group:
                        return {
                            'is_volta_group': True,
                            'number': item.number,
                            'is_anchor': is_anchor,
                            'is_floating': is_floating,
                            'floating_siblings': [serialize_item(s) for s in (floating_siblings or [])],
                            'parts': [serialize_item(p) for p in item.parts]
                        }
                    else:
                        return {
                            'chord': item.chord,
                            'text': item.text,
                            'volta': item.volta,
                            'is_volta_group': False
                        }

                # Check alignment for the section label (for sidebar layout)
                # We look at the first line to decide if we need to offset the label.
                offset_label = False
                if sec.lines:
                    first_line = sec.lines[0]
                    # Check if first line has both chords and text
                    has_chords = False
                    has_text = False

                    flat_parts = collect_parts(first_line.parts)
                    for part in flat_parts:
                        if part.chord and part.chord.strip():
                            has_chords = True
                        if part.text and part.text.strip():
                            has_text = True

                    # If we have both chords and text, we need to offset the label to align with lyrics
                    if has_chords and has_text:
                        offset_label = True

                for line in sec.lines:
                    if sec.type == 'grid' and hasattr(line, 'grid_cells') and line.grid_cells:
                        cells_data = []
                        for i, cell in enumerate(line.grid_cells):
                            # Grid cells usually contain simple parts, but if they ever contain VoltaGroups (unlikely given grid parser), this would need update.
                            # Grid parser currently creates Parts directly.
                            cell_parts = [{'chord': p.chord, 'text': p.text} for p in cell.parts]

                            # Determine if measure is essentially empty
                            is_empty = not cell.is_bar and not any(p.chord or (p.text and p.text.strip()) for p in cell.parts)

                            # Determine bar type for styling
                            bar_type = 'standard'
                            if cell.is_bar:
                                text = cell.text
                                if ':' in text:
                                    if text.startswith(':') or text.endswith(':') and len(text) > 1:
                                        if text.startswith(':') and text.endswith(':'):
                                            # Rare case of |:| or similar
                                            bar_type = 'repeat-both'
                                        elif text.startswith(':'):
                                            bar_type = 'end-repeat'
                                        else:
                                            bar_type = 'start-repeat'
                                elif len(text) >= 2 and ('||' in text or '//' in text):
                                    bar_type = 'double-bar'

                            current_cell_data = {
                                'is_bar': cell.is_bar,
                                'text': cell.text,
                                'bar_type': bar_type,
                                'volta': cell.volta,
                                'is_empty': is_empty,
                                'is_shifted': False, # Initial state
                                'parts': cell_parts
                            }

                             # Move volta to previous bar if applicable
                            # Check if current is measure (not bar), has volta
                            if not current_cell_data['is_bar'] and current_cell_data['volta']:
                                # 1. Try to merge with previous repeat if separated by empty measure/bar
                                # Pattern: [Repeat Bar] -> [Empty Measure] -> [Simple Bar] -> [Current Measure with Volta]
                                if len(cells_data) >= 3:
                                    prev_bar = cells_data[-1]
                                    prev_measure = cells_data[-2]
                                    repeat_bar = cells_data[-3]
                                    if (prev_bar['is_bar'] and prev_bar['text'] in ['|', '||'] and
                                        prev_measure['is_empty'] and
                                        repeat_bar['is_bar'] and (':' in repeat_bar['text'])):

                                        repeat_bar['volta'] = current_cell_data['volta']
                                        current_cell_data['volta'] = None
                                        current_cell_data['is_shifted'] = True
                                        # Remove redundant intermediate cells
                                        cells_data.pop() # Remove simple bar
                                        cells_data.pop() # Remove empty measure
                                        cells_data.append(current_cell_data)
                                        continue

                                # 2. Standard move to immediately preceding bar
                                if cells_data and cells_data[-1]['is_bar']:
                                    cells_data[-1]['volta'] = current_cell_data['volta']
                                    current_cell_data['volta'] = None
                                    current_cell_data['is_shifted'] = True # Add shift to measure

                            cells_data.append(current_cell_data)

                        lines_data.append({
                            'grid_cells': cells_data,
                            'is_comment': False
                        })
                    else:
                        # Identify voltas in this line to handle stack logic
                        line_voltas = [p for p in line.parts if hasattr(p, 'is_volta_group') and p.is_volta_group]

                        parts_data = []
                        v_idx = 0
                        for part in line.parts:
                            if hasattr(part, 'is_volta_group') and part.is_volta_group:
                                is_anchor = (v_idx == 0)
                                is_floating = (v_idx > 0)
                                siblings = line_voltas[1:] if is_anchor else []
                                parts_data.append(serialize_item(part, is_anchor=is_anchor,
                                                                 is_floating=is_floating,
                                                                 floating_siblings=siblings))
                                v_idx += 1
                            else:
                                parts_data.append(serialize_item(part))

                        lines_data.append({
                            'parts': parts_data,
                            'is_comment': getattr(line, 'is_comment', False)
                        })

                sections_data.append({
                    'type': sec.type,
                    'label': sec.label,
                    'lines': lines_data,
                    'offset_label': offset_label
                })

            context = {
                'title': song.title,
                'artist': song.artist,
                'key': song.key,
                'capo': song.capo,
                'time': song.time,
                'tempo': song.tempo,
                'sections': sections_data,
                'layout': args.layout
            }

            # Render HTML
            html_content = template.render(context)

            # Save temporary HTML file
            temp_html_path = os.path.abspath(os.path.join(OUTPUT_DIR, f"{filename}.html"))
            with open(temp_html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            # Render to JPG
            # Set a specific width, height can be null/0 for full page
            page = browser.new_page(viewport={'width': 800, 'height': 600})

            file_url = f"file://{temp_html_path}"
            page.goto(file_url)

            # Wait a bit for layout to settle (though usually instant for local static)
            # page.wait_for_timeout(100)

            output_filename = os.path.splitext(filename)[0] + ".jpg"
            output_path = os.path.join(OUTPUT_DIR, output_filename)

            # Take screenshot of the song container to ensure we don't get infinite height or extra bg
            locator = page.locator('.song-container')
            locator.screenshot(path=output_path, type='jpeg', quality=90)

            print(f"Saved {output_path}")
            page.close()

        browser.close()
        print("Done!")


if __name__ == "__main__":
    main()
