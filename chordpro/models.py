class Section:
    def __init__(self, type="verse", label=""):
        self.type = type  # verse, chorus, bridge, grid (instrumental), comment
        self.label = label
        self.lines = []


class Line:
    def __init__(self, is_comment=False):
        self.parts = []
        self.grid_cells = []  # For grid sections
        self.is_comment = is_comment


class GridCell:
    def __init__(self, is_bar=False, text="", volta=None):
        self.is_bar = is_bar
        self.text = text
        self.volta = volta
        self.parts = []  # Список объектов Part


class Part:
    def __init__(self, chord=None, text="", is_transposable=True, volta=None):
        self.chord = chord
        self.text = text
        self.is_transposable = is_transposable
        self.volta = volta

    def __repr__(self):
        return (
            f"Part(chord={self.chord}, text={self.text}, "
            f"transposable={self.is_transposable}, volta={self.volta})"
        )


class VoltaGroup:
    def __init__(self, number, parts=None):
        self.number = number
        self.parts = parts if parts is not None else []
        self.is_volta_group = True
