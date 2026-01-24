import re

from .models import GridCell, Line, Part, Section, VoltaGroup
from .song import Song


class ChordProParser:
    def parse(self, content):
        song = Song()
        lines = content.splitlines()

        # Начинаем без явной секции; если текст до первой директивы — создаём секцию по требованию.
        current_section = None


        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Директивы
            if line.startswith("{") and line.endswith("}"):
                inner = line[1:-1]
                parts = inner.split(":", 1)
                key = parts[0].strip().lower()
                value = parts[1].strip() if len(parts) > 1 else ""

                # Метаданные
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
                # Начало секции
                elif key in ["soc", "start_of_chorus"]:
                    label = value.strip()
                    # Проверка на пре-припев
                    if label.lower().startswith("пре-пр") or label.lower().startswith("пред-пр") or label.lower().startswith("препр") or label.lower().startswith("предпр"):
                        current_section = Section(type="pre_chorus", label=label)
                    else:
                        current_section = Section(
                            type="chorus", label=label if label else "Пр.:"
                        )

                    song.sections.append(current_section)
                elif key == "chorus":
                    label = value if value else "Пр."  # Обработка голого {chorus}
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
                    # Разбор текста комментария на аккорды
                    parsed_line = self._parse_line(value)
                    parsed_line.is_comment = True

                    # Create a NEW section for each comment
                    # Метка оставляется пустой, чтобы не дублировать текст комментария
                    comment_section = Section(type="comment", label="")
                    comment_section.lines.append(parsed_line)
                    song.sections.append(comment_section)

                    # Сбрасываем current_section, чтобы следующий комментарий создал новую секцию
                    current_section = None

                # Конец секции: по сути сбрасываем current_section; следующее {start_of_*} создаст новую
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
                    # Оставить current_section активным — хвост строк попадёт в секцию; в ChordPro чаще жёсткая структура
                    pass

                else:
                    song.metadata[key] = value

                continue

            # Комментарии (строки, начинающиеся с #)
            if line.startswith("#"):
                continue

            # Директива {comment: ...} обрабатывается в цикле директив выше (ключи 'comment' / 'c').
            pass

            # Разбор содержимого
            # Если секции нет — создать куплет по умолчанию
            if current_section is None:
                current_section = Section(type="verse", label="")
                song.sections.append(current_section)

            if current_section.type == "grid":
                parsed_line = self._parse_grid_line(line)
            else:
                parsed_line = self._parse_line(line)

            current_section.lines.append(parsed_line)

        # Пост-обработка: раскрытие ссылок на припев (вынесено на уровень вызывающего кода)
        # song.expand_chorus_references()

        return song

    def _parse_grid_line(self, line_text):
        line_obj = Line()

        # Регулярка: разбиение по тактовым чертам с сохранением разделителей
        # Чётные индексы (0, 2, 4...) — такты; нечётные (1, 3, 5...) — разделители (черты)
        # Поддерживаемые черты: ||:, :||, |:, :|, ||, |, //:, ://
        tokens = re.split(r"(\|\|:|:\|\||\|:|:\||\|\||\||//:|://)", line_text)

        for i, token in enumerate(tokens):
            # token может быть пустой строкой при разбиении; re.split с группой сохраняет разделители
            if token is None:
                continue

            if i % 2 == 1:
                # Нечётный индекс — черта (BAR)
                stripped = token.strip()
                cell = GridCell(is_bar=True, text=stripped)
                line_obj.grid_cells.append(cell)
            else:
                # Чётный индекс — такт (содержимое)
                # Пустой такт тоже добавляем для сохранения индексации
                content_text = token.strip()

                volta = None
                # Проверка volta: число в начале или только число
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

                # Разбор частей такта: аккорды, текст, символы
                sub_tokens = content_text.split()
                for sub in sub_tokens:
                    if sub == ".":
                        cell.parts.append(Part(text="  "))
                    elif sub == "/":
                        cell.parts.append(Part(text="/"))
                    elif sub == "%":
                        cell.parts.append(Part(text="%"))
                    else:
                        # Определение аккорда: начало на A–G, H
                        note_match = re.match(r"^([A-GH])([#b]?)", sub)
                        if note_match:
                            cell.parts.append(Part(chord=sub, text=""))
                        else:
                            cell.parts.append(Part(text=sub + " "))

                line_obj.grid_cells.append(cell)

        return line_obj

    def _parse_line(self, line_text):
        line_obj = Line()

        # В сетках без скобок (напр. | G | C |) парсер ищет `[`; строки без скобок — чистый текст.
        # В куплетах используется [G/B]; в {sog}: | G/B | C2 | D | G | без скобок — без подсветки аккордов.
        # Стратегия: regex на вероятные аккорды или моноширинный вывод секции grid; моноширина надёжнее.

        parts = line_text.split("[")

        # Первый фрагмент — текст до первого аккорда
        if parts[0]:
            line_obj.parts.append(Part(chord=None, text=parts[0]))

        for chunk in parts[1:]:
            if "]" in chunk:
                chord_part, text_part = chunk.split("]", 1)

                # Обработка нетранспонируемых аккордов (начинаются с *)
                is_transposable = True
                if chord_part.startswith("*"):
                    chord_part = chord_part[1:]
                    is_transposable = False

                line_obj.parts.append(
                    Part(chord=chord_part, text=text_part, is_transposable=is_transposable)
                )
            else:
                # Некорректная скобка
                line_obj.parts.append(Part(chord=None, text="[" + chunk))

        line_obj.parts = self._group_voltas(line_obj.parts)

        return line_obj

    def _group_voltas(self, parts):
        """
        Группирует Part в VoltaGroup по маркерам начала/конца.
        Начало: аккорд с '(', напр. '(1.' или '(1.G'. Конец: аккорд с ')' в конце, напр. 'E)' или ')'.
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

            # Маркер начала: (1. или (1.2.
            # Regex: '(', цифра, опционально цифры/точки, обязательно точка в конце. Нужно захватить '1.' или '1.2.'
            start_match = re.match(r"^\((\d+[\d\.]*\.)", chord_str)
            # Маркер конца: заканчивается на ')'
            end_match = chord_str.endswith(")")

            if start_match:
                # Если уже внутри volta — закрываем её (на случай пропущенного маркера конца)
                if current_volta:
                    new_items.append(current_volta)
                    current_volta = None

                volta_num = start_match.group(1).rstrip(".")

                # Очистка аккорда для вывода: убрать префикс '(1.'
                clean_chord = chord_str[len(start_match.group(0)) :]

                # Если маркер конца здесь же, напр. [(1.G)]
                if end_match:
                    clean_chord = clean_chord[:-1]  # Удалить завершающую ')'

                part.chord = clean_chord if clean_chord else None

                current_volta = VoltaGroup(number=volta_num)
                current_volta.parts.append(part)

                if end_match:
                    # Начало и конец в одном Part
                    self._optimize_and_append_volta(new_items, current_volta)
                    current_volta = None

            elif end_match and current_volta:
                # Конец активной volta: убрать ')' в конце и закрыть группу
                clean_chord = chord_str[:-1]
                part.chord = clean_chord if clean_chord else None

                current_volta.parts.append(part)
                self._optimize_and_append_volta(new_items, current_volta)
                current_volta = None

            else:
                # Обычный Part или закрывающая скобка при отсутствии активной volta
                if current_volta:
                    current_volta.parts.append(part)
                else:
                    new_items.append(part)

        # Закрыть оставшуюся открытой volta
        if current_volta:
            self._optimize_and_append_volta(new_items, current_volta)

        return new_items

    def _optimize_and_append_volta(self, target_list, volta_group):
        """
        Оптимизирует VoltaGroup: выносит ведущие Part без аккорда из группы, чтобы скобки
        и номера начинались с первого реального аккорда и не ломали слоги.
        """
        # Если группа пуста — просто добавляем
        if not volta_group.parts:
            target_list.append(volta_group)
            return

        # Проверка: есть ли в группе хотя бы один Part с аккордом
        has_chords = any(p.chord for p in volta_group.parts)

        if not has_chords:
            # Если аккордов нет — оставляем группу как есть (скобка над текстом)
            target_list.append(volta_group)
            return

        # Вынести начальные Part без аккорда; остановка при первом Part с аккордом
        while volta_group.parts and not volta_group.parts[0].chord:
            p = volta_group.parts.pop(0)
            target_list.append(p)

        # Проверить, остались ли Part в группе (должно быть да, если has_chords)
        if volta_group.parts:
            target_list.append(volta_group)
