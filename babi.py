import _curses
import argparse
import collections
import contextlib
import curses
import io
import os
import signal
from typing import Dict
from typing import Generator
from typing import IO
from typing import List
from typing import NamedTuple
from typing import Tuple

VERSION_STR = 'babi v0'


class Margin(NamedTuple):
    header: bool
    footer: bool

    @property
    def body_lines(self) -> int:
        return curses.LINES - self.header - self.footer

    @classmethod
    def from_screen(cls, screen: '_curses._CursesWindow') -> 'Margin':
        if curses.LINES == 1:
            return cls(header=False, footer=False)
        elif curses.LINES == 2:
            return cls(header=False, footer=True)
        else:
            return cls(header=True, footer=True)


class Position:
    def __init__(self) -> None:
        self.file_line = self.cursor_line = self.x = self.x_hint = 0

    def __repr__(self) -> str:
        attrs = ', '.join(f'{k}={v}' for k, v in self.__dict__.items())
        return f'{type(self).__name__}({attrs})'

    def _scroll_amount(self) -> int:
        return int(curses.LINES / 2 + .5)

    def _set_x_after_vertical_movement(self, lines: List[str]) -> None:
        self.x = min(len(lines[self.cursor_line]), self.x_hint)

    def maybe_scroll_down(self, margin: Margin) -> None:
        if self.cursor_line >= self.file_line + margin.body_lines:
            self.file_line += self._scroll_amount()

    def down(self, margin: Margin, lines: List[str]) -> None:
        if self.cursor_line < len(lines) - 1:
            self.cursor_line += 1
            self.maybe_scroll_down(margin)
            self._set_x_after_vertical_movement(lines)

    def maybe_scroll_up(self, margin: Margin) -> None:
        if self.cursor_line < self.file_line:
            self.file_line -= self._scroll_amount()

    def up(self, margin: Margin, lines: List[str]) -> None:
        if self.cursor_line > 0:
            self.cursor_line -= 1
            self.maybe_scroll_up(margin)
            self._set_x_after_vertical_movement(lines)

    def right(self, margin: Margin, lines: List[str]) -> None:
        if self.x >= len(lines[self.cursor_line]):
            if self.cursor_line < len(lines) - 1:
                self.x = 0
                self.cursor_line += 1
                self.maybe_scroll_down(margin)
        else:
            self.x += 1
        self.x_hint = self.x

    def left(self, margin: Margin, lines: List[str]) -> None:
        if self.x == 0:
            if self.cursor_line > 0:
                self.cursor_line -= 1
                self.x = len(lines[self.cursor_line])
                self.maybe_scroll_up(margin)
        else:
            self.x -= 1
        self.x_hint = self.x

    def home(self, margin: Margin, lines: List[str]) -> None:
        self.x = self.x_hint = 0

    def end(self, margin: Margin, lines: List[str]) -> None:
        self.x = self.x_hint = len(lines[self.cursor_line])

    DISPATCH = {
        curses.KEY_DOWN: down,
        curses.KEY_UP: up,
        curses.KEY_LEFT: left,
        curses.KEY_RIGHT: right,
        curses.KEY_HOME: home,
        curses.KEY_END: end,
    }

    def dispatch(self, key: int, margin: Margin, lines: List[str]) -> None:
        return self.DISPATCH[key](self, margin, lines)

    def cursor_y(self, margin: Margin) -> int:
        return self.cursor_line - self.file_line + margin.header

    def line_x(self) -> int:
        margin = min(curses.COLS - 3, 6)
        if self.x + 1 < curses.COLS:
            return 0
        elif curses.COLS == 1:
            return self.x
        else:
            return (
                curses.COLS - margin - 2 +
                (self.x + 1 - curses.COLS) //
                (curses.COLS - margin - 2) *
                (curses.COLS - margin - 2)
            )

    def cursor_x(self) -> int:
        return self.x - self.line_x()


def _get_color_pair_mapping() -> Dict[Tuple[int, int], int]:
    ret = {}
    i = 0
    for bg in range(-1, 16):
        for fg in range(bg, 16):
            ret[(fg, bg)] = i
            i += 1
    return ret


COLORS = _get_color_pair_mapping()
del _get_color_pair_mapping


def _has_colors() -> bool:
    # https://github.com/python/typeshed/pull/3115
    return curses.has_colors and curses.COLORS >= 16  # type: ignore


def _color(fg: int, bg: int) -> int:
    if _has_colors():
        if bg > fg:
            return curses.A_REVERSE | curses.color_pair(COLORS[(bg, fg)])
        else:
            return curses.color_pair(COLORS[(fg, bg)])
    else:
        if bg > fg:
            return curses.A_REVERSE | curses.color_pair(0)
        else:
            return curses.color_pair(0)


def _init_colors(stdscr: '_curses._CursesWindow') -> None:
    curses.use_default_colors()
    if not _has_colors():
        return
    for (fg, bg), pair in COLORS.items():
        if pair == 0:  # cannot reset pair 0
            continue
        curses.init_pair(pair, fg, bg)


def _color_test(stdscr: '_curses._CursesWindow') -> None:
    _write_header(stdscr, '<<color test>>', modified=False)

    maxy, maxx = stdscr.getmaxyx()
    if maxy < 19 or maxx < 68:  # pragma: no cover (will be deleted)
        raise SystemExit('--color-test needs a window of at least 68 x 19')

    y = 1
    for fg in range(-1, 16):
        x = 0
        for bg in range(-1, 16):
            if bg > fg:
                s = f'*{COLORS[bg, fg]:3}'
            else:
                s = f' {COLORS[fg, bg]:3}'
            stdscr.addstr(y, x, s, _color(fg, bg))
            x += 4
        y += 1
    stdscr.get_wch()


def _write_header(
        stdscr: '_curses._CursesWindow',
        filename: str,
        *,
        modified: bool,
) -> None:
    filename = filename or '<<new file>>'
    if modified:
        filename += ' *'
    centered_filename = filename.center(curses.COLS)[len(VERSION_STR) + 2:]
    s = f' {VERSION_STR} {centered_filename}'
    stdscr.insstr(0, 0, s, curses.A_REVERSE)


def _write_lines(
        stdscr: '_curses._CursesWindow',
        pos: Position,
        margin: Margin,
        lines: List[str],
) -> None:
    lines_to_display = min(len(lines) - pos.file_line, margin.body_lines)
    for i in range(lines_to_display):
        line_idx = pos.file_line + i
        line = lines[line_idx]
        line_x = pos.line_x()
        if line_idx == pos.cursor_line and line_x:
            line = f'«{line[line_x + 1:]}'
            if len(line) > curses.COLS:
                line = f'{line[:curses.COLS - 1]}»'
            else:
                line = line.ljust(curses.COLS)
        elif len(line) > curses.COLS:
            line = f'{line[:curses.COLS - 1]}»'
        else:
            line = line.ljust(curses.COLS)
        stdscr.insstr(i + margin.header, 0, line)
    blankline = ' ' * curses.COLS
    for i in range(lines_to_display, margin.body_lines):
        stdscr.insstr(i + margin.header, 0, blankline)


def _write_status(
        stdscr: '_curses._CursesWindow',
        margin: Margin,
        status: str,
) -> None:
    if margin.footer or status:
        stdscr.insstr(curses.LINES - 1, 0, ' ' * curses.COLS)
        if status:
            status = f' {status} '
            offset = (curses.COLS - len(status)) // 2
            if offset < 0:
                offset = 0
                status = status.strip()
            stdscr.insstr(curses.LINES - 1, offset, status, curses.A_REVERSE)


def _move_cursor(
        stdscr: '_curses._CursesWindow',
        pos: Position,
        margin: Margin,
) -> None:
    stdscr.move(pos.cursor_y(margin), pos.cursor_x())


def _restore_lines_eof_invariant(lines: List[str]) -> None:
    """The file lines will always contain a blank empty string at the end to
    simplify rendering.  This should be called whenever the end of the file
    might change.
    """
    if not lines or lines[-1] != '':
        lines.append('')


def _get_lines(sio: IO[str]) -> Tuple[List[str], str, bool]:
    lines = []
    newlines = collections.Counter({'\n': 0})  # default to `\n`
    for line in sio:
        for ending in ('\r\n', '\n'):
            if line.endswith(ending):
                lines.append(line[:-1 * len(ending)])
                newlines[ending] += 1
                break
        else:
            lines.append(line)
    _restore_lines_eof_invariant(lines)
    (nl, _), = newlines.most_common(1)
    mixed = len({k for k, v in newlines.items() if v}) > 1
    return lines, nl, mixed


def c_main(stdscr: '_curses._CursesWindow', args: argparse.Namespace) -> None:
    if args.color_test:
        return _color_test(stdscr)

    modified = False
    filename = args.filename
    status = ''
    status_action_counter = -1
    pos = Position()
    margin = Margin.from_screen(stdscr)

    def _set_status(s: str) -> None:
        nonlocal status, status_action_counter
        status = s
        # if the window is only 1-tall, clear status quicker
        if not margin.footer:
            status_action_counter = 1
        else:
            status_action_counter = 25

    if args.filename is not None:
        with open(args.filename, newline='') as f:
            lines, nl, mixed = _get_lines(f)
    else:
        lines, nl, mixed = _get_lines(io.StringIO(''))
    if mixed:
        _set_status(f'mixed newlines will be converted to {nl!r}')
        modified = True

    while True:
        if status_action_counter == 0:
            status = ''
        status_action_counter -= 1

        if margin.header:
            _write_header(stdscr, filename, modified=modified)
        _write_lines(stdscr, pos, margin, lines)
        _write_status(stdscr, margin, status)
        _move_cursor(stdscr, pos, margin)

        wch = stdscr.get_wch()
        key = wch if isinstance(wch, int) else ord(wch)
        keyname = curses.keyname(key)

        if key == curses.KEY_RESIZE:
            curses.update_lines_cols()
            margin = Margin.from_screen(stdscr)
            pos.maybe_scroll_down(margin)
        elif key in Position.DISPATCH:
            pos.dispatch(key, margin, lines)
        elif keyname == b'^A':
            pos.home(margin, lines)
        elif keyname == b'^E':
            pos.end(margin, lines)
        elif keyname == b'^X':
            return
        elif keyname == b'^Z':
            curses.endwin()
            os.kill(os.getpid(), signal.SIGSTOP)
            stdscr = _init_screen()
        elif key == curses.KEY_BACKSPACE:
            # backspace at the beginning of the file does nothing
            if pos.cursor_line == 0 and pos.x == 0:
                pass
            # at the beginning of the line, we join the current line and
            # the previous line
            elif pos.x == 0:
                victim = lines.pop(pos.cursor_line)
                new_x = len(lines[pos.cursor_line - 1])
                lines[pos.cursor_line - 1] += victim
                pos.up(margin, lines)
                pos.x = pos.x_hint = new_x
                # deleting the fake end-of-file doesn't cause modification
                modified |= pos.cursor_line < len(lines) - 1
                _restore_lines_eof_invariant(lines)
            else:
                s = lines[pos.cursor_line]
                lines[pos.cursor_line] = s[:pos.x - 1] + s[pos.x:]
                pos.left(margin, lines)
                modified = True
        elif key == curses.KEY_DC:
            # noop at end of the file
            if pos.cursor_line == len(lines) - 1:
                pass
            # if we're at the end of the line, collapse the line afterwards
            elif pos.x == len(lines[pos.cursor_line]):
                lines[pos.cursor_line] += lines[pos.cursor_line + 1]
                lines.pop(pos.cursor_line + 1)
                modified = True
            else:
                s = lines[pos.cursor_line]
                lines[pos.cursor_line] = s[:pos.x] + s[pos.x + 1:]
                modified = True
        elif wch == '\r':
            s = lines[pos.cursor_line]
            lines[pos.cursor_line] = s[:pos.x]
            lines.insert(pos.cursor_line + 1, s[pos.x:])
            pos.down(margin, lines)
            pos.x = pos.x_hint = 0
            modified = True
        elif isinstance(wch, str) and wch.isprintable():
            s = lines[pos.cursor_line]
            lines[pos.cursor_line] = s[:pos.x] + wch + s[pos.x:]
            pos.right(margin, lines)
            modified = True
            _restore_lines_eof_invariant(lines)
        else:
            _set_status(f'unknown key: {keyname} ({key})')


def _init_screen() -> '_curses._CursesWindow':
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    # <enter> is not transformed into '\n' so it can be differentiated from ^J
    curses.nonl()
    # ^S / ^Q / ^Z / ^\ are passed through
    curses.raw()
    stdscr.keypad(True)
    with contextlib.suppress(curses.error):
        curses.start_color()
    _init_colors(stdscr)
    return stdscr


@contextlib.contextmanager
def make_stdscr() -> Generator['_curses._CursesWindow', None, None]:
    """essentially `curses.wrapper` but split out to implement ^Z"""
    stdscr = _init_screen()
    try:
        yield stdscr
    finally:
        curses.endwin()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--color-test', action='store_true')
    parser.add_argument('filename', nargs='?')
    args = parser.parse_args()
    with make_stdscr() as stdscr:
        c_main(stdscr, args)
    return 0


if __name__ == '__main__':
    exit(main())
