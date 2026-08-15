"""Search the user's actual symbol and footprint libraries.

The point of this module is to stop an agent inventing library identifiers.
`Device:R` and `Resistor_SMD:R_0805_2012Metric` look plausible whether or not
they exist on this machine, and a netlist full of plausible-but-wrong ids fails
only later, in KiCad, confusingly. Look them up instead.
"""

from __future__ import annotations

import functools
import glob
import os
import re
from typing import Iterable, Optional

# KiCad's built-in defaults, used when kicad_common.json sets no environment
# vars (the common case). Covers the current and previous major versions.
_DEFAULT_ENV = {}
for _v in (10, 9, 8, 7):
    _DEFAULT_ENV[f"KICAD{_v}_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
    _DEFAULT_ENV[f"KICAD{_v}_FOOTPRINT_DIR"] = "/usr/share/kicad/footprints"
    _DEFAULT_ENV[f"KICAD{_v}_3DMODEL_DIR"] = "/usr/share/kicad/3dmodels"

_LIB_RE = re.compile(
    r'\(lib\s+\(name\s+"([^"]+)"\)\s*\(type\s+"([^"]+)"\)\s*\(uri\s+"([^"]+)"\)'
)
_VAR_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
# Top-level symbol definitions sit at exactly one tab of indentation; nested
# unit symbols ("R_0_1") are deeper, so anchoring on the indent skips them.
_SYM_RE = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)


def _config_dirs() -> list[str]:
    base = os.path.expanduser("~/.config/kicad")
    if not os.path.isdir(base):
        return []
    versions = sorted(
        (d for d in os.listdir(base) if re.fullmatch(r"\d+\.\d+", d)),
        key=lambda s: [int(p) for p in s.split(".")],
        reverse=True,
    )
    return [os.path.join(base, v) for v in versions]


def _expand(uri: str, env: dict) -> str:
    def sub(m):
        key = m.group(1)
        return os.environ.get(key) or env.get(key) or m.group(0)

    return os.path.expanduser(_VAR_RE.sub(sub, uri))


def _read_table(path: str, env: dict, seen: set) -> dict[str, str]:
    """Parse a *-lib-table, following nested tables. Returns {nickname: path}."""
    path = os.path.realpath(path)
    if path in seen or not os.path.isfile(path):
        return {}
    seen.add(path)

    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}

    out: dict[str, str] = {}
    for nickname, kind, uri in _LIB_RE.findall(text):
        resolved = _expand(uri, env)
        if kind.lower() == "table":
            # Nested tables contribute their libraries to one flat namespace --
            # they are addressed as "Device:R", never "KiCad:Device:R".
            out.update(_read_table(resolved, env, seen))
        else:
            out.setdefault(nickname, resolved)
    return out


class LibraryIndex:
    """Resolved view of the symbol and footprint libraries visible to KiCad."""

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = os.path.abspath(project_dir) if project_dir else None
        self.env = dict(_DEFAULT_ENV)

    def _tables(self, kind: str) -> list[str]:
        """Project tables take precedence over global ones, as in KiCad."""
        name = "sym-lib-table" if kind == "sym" else "fp-lib-table"
        paths = []
        if self.project_dir:
            paths.append(os.path.join(self.project_dir, name))
        paths += [os.path.join(d, name) for d in _config_dirs()]
        return paths

    @functools.cached_property
    def symbol_libs(self) -> dict[str, str]:
        seen: set = set()
        out: dict[str, str] = {}
        for t in self._tables("sym"):
            for k, v in _read_table(t, self.env, seen).items():
                out.setdefault(k, v)
        return out

    @functools.cached_property
    def footprint_libs(self) -> dict[str, str]:
        seen: set = set()
        out: dict[str, str] = {}
        for t in self._tables("fp"):
            for k, v in _read_table(t, self.env, seen).items():
                out.setdefault(k, v)
        return out

    # -- symbols ----------------------------------------------------------

    @functools.cached_property
    def _symbol_names(self) -> list[tuple[str, str]]:
        pairs = []
        for nick, path in self.symbol_libs.items():
            if not os.path.isfile(path):
                continue
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for name in _SYM_RE.findall(text):
                pairs.append((nick, name))
        return pairs

    def search_symbols(self, query: str, limit: int = 40) -> list[dict]:
        return _rank(self._symbol_names, query, limit)

    def _lib_text(self, ident: str) -> tuple[str, str, str]:
        """(library path, symbol name, library file text) for one 'Lib:Name'."""
        nick, _, name = ident.partition(":")
        path = self.symbol_libs.get(nick)
        if not path:
            raise KeyError(f"no such symbol library: {nick}")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"library file missing: {path}")
        return path, name, open(path, encoding="utf-8", errors="replace").read()

    def symbol_sexpr(self, ident: str) -> dict:
        """The raw symbol definition, ready to drop into a schematic.

        Authoring a `.kicad_sch` by hand means writing its `lib_symbols` block,
        which is a verbatim copy of the library definition with the outer name
        changed from `R` to `Device:R`. Reproducing that by re-serialising a
        parsed tree loses formatting and invites quoting bugs, so hand back the
        original text with just the name rewritten.
        """
        path, name, text = self._lib_text(ident)
        block = _raw_block(text, name)
        if block is None:
            raise KeyError(f"no such symbol: {ident}")

        detail_props: dict = {}
        node = _find_symbol(_parse(text), name)
        if node is not None:
            _collect(node, detail_props, [])
        extends = detail_props.get("__extends__")
        if extends:
            parent = _raw_block(text, extends)
            if parent is None:
                raise KeyError(f"{ident} extends {extends!r}, which is not in {path}")
            block = _merge_extends(parent, block)

        # Only the outer name is namespaced; nested unit symbols ("R_0_1")
        # keep their bare names inside the block, exactly as KiCad writes them.
        block = _rename_block(block, ident)
        return {
            "id": ident,
            "library_path": path,
            "extends": extends,
            "sexpr": block,
        }

    def symbol_detail(self, ident: str) -> dict:
        """Pins and properties for one 'Lib:Name'. This is what a netlist needs."""
        path, name, _text = self._lib_text(ident)

        node = _find_symbol(_parse(open(path, encoding="utf-8", errors="replace").read()), name)
        if node is None:
            raise KeyError(f"no such symbol: {ident}")

        props, pins = {}, []
        _collect(node, props, pins)

        # A derived symbol inherits its parent's pins.
        extends = props.pop("__extends__", None)
        if extends and not pins:
            parent = _find_symbol(
                _parse(open(path, encoding="utf-8", errors="replace").read()), extends
            )
            if parent is not None:
                _collect(parent, {}, pins)

        pins.sort(key=lambda p: (len(p["number"]), p["number"]))
        return {
            "id": ident,
            "library_path": path,
            "extends": extends,
            "reference": props.get("Reference"),
            "value": props.get("Value"),
            "default_footprint": props.get("Footprint") or None,
            "datasheet": props.get("Datasheet") or None,
            "description": props.get("ki_description") or props.get("Description"),
            "keywords": props.get("ki_keywords"),
            "footprint_filters": props.get("ki_fp_filters"),
            "pin_count": len(pins),
            "pins": pins,
        }

    # -- footprints -------------------------------------------------------

    @functools.cached_property
    def _footprint_names(self) -> list[tuple[str, str]]:
        pairs = []
        for nick, path in self.footprint_libs.items():
            if not os.path.isdir(path):
                continue
            for f in glob.glob(os.path.join(path, "*.kicad_mod")):
                pairs.append((nick, os.path.splitext(os.path.basename(f))[0]))
        return pairs

    def search_footprints(self, query: str, limit: int = 40) -> list[dict]:
        return _rank(self._footprint_names, query, limit)

    # -- validation -------------------------------------------------------

    def validate(self, symbols: Iterable[str] = (), footprints: Iterable[str] = ()) -> dict:
        """Check identifiers before they reach a netlist. Suggests near misses."""
        sym_set = {f"{a}:{b}" for a, b in self._symbol_names}
        fp_set = {f"{a}:{b}" for a, b in self._footprint_names}

        def check(items, universe, pairs):
            ok, bad = [], []
            for i in items:
                if i in universe:
                    ok.append(i)
                else:
                    bad.append({"id": i, "suggestions": _suggest(pairs, i)})
            return ok, bad

        s_ok, s_bad = check(list(symbols), sym_set, self._symbol_names)
        f_ok, f_bad = check(list(footprints), fp_set, self._footprint_names)
        return {
            "ok": not (s_bad or f_bad),
            "symbols": {"valid": s_ok, "invalid": s_bad},
            "footprints": {"valid": f_ok, "invalid": f_bad},
        }

    def summary(self) -> dict:
        return {
            "symbol_libraries": len(self.symbol_libs),
            "footprint_libraries": len(self.footprint_libs),
            "symbols": len(self._symbol_names),
            "footprints": len(self._footprint_names),
            "project_dir": self.project_dir,
        }


# --------------------------------------------------------------------------
# suggestions


def _suggest(pairs: list[tuple[str, str]], ident: str, n: int = 5) -> list[str]:
    """Best guesses for an identifier that does not exist.

    A wrong id is usually wrong in a specific way -- an invented suffix
    ("LED_Generic" for "LED"), or the right part in the wrong library. So try
    the substring ranker, then fall back to fuzzy matching, preferring the
    library the caller already named.
    """
    import difflib

    lib, _, name = ident.partition(":")
    name = name or ident

    hits = [h["id"] for h in _rank(pairs, name, n)]
    if hits:
        return hits

    # Prefer candidates from the library that was asked for.
    same_lib = [nm for nk, nm in pairs if nk == lib]
    out = [f"{lib}:{m}" for m in difflib.get_close_matches(name, same_lib, n, 0.5)]

    # Then the leading token, which is usually the part that was right.
    if len(out) < n:
        stem = re.split(r"[_\-]", name)[0].lower()
        if stem:
            for h in _rank(pairs, stem, n * 2):
                if h["id"] not in out:
                    out.append(h["id"])

    if not out:
        allnames = {nm: f"{nk}:{nm}" for nk, nm in pairs}
        out = [allnames[m] for m in difflib.get_close_matches(name, list(allnames), n, 0.6)]
    return out[:n]


# --------------------------------------------------------------------------
# ranking


def _rank(pairs: list[tuple[str, str]], query: str, limit: int) -> list[dict]:
    """Exact match first, then prefix, then substring, then all query words."""
    q = query.lower().strip()
    words = q.split()
    scored = []
    for nick, name in pairs:
        ident = f"{nick}:{name}"
        low = name.lower()
        if low == q:
            score = 0
        elif low.startswith(q):
            score = 1
        elif q and q in low:
            score = 2
        elif q and q in ident.lower():
            score = 3
        elif words and all(w in ident.lower() for w in words):
            score = 4
        else:
            continue
        scored.append((score, len(name), ident, nick, name))
    scored.sort()
    return [
        {"id": i, "library": nk, "name": nm}
        for _, _, i, nk, nm in scored[:limit]
    ]


# --------------------------------------------------------------------------
# raw text extraction, for callers that need to *write* KiCad files


def _num(tok):
    try:
        return float(tok)
    except (TypeError, ValueError):
        return tok


def _span(text: str, start: int) -> int:
    """End index (exclusive) of the s-expression whose '(' is at `start`."""
    depth, i, in_str = 0, start, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced s-expression")


def _raw_block(text: str, name: str) -> Optional[str]:
    """Verbatim text of a top-level `(symbol "name" ...)` in a .kicad_sym."""
    m = re.search(r'^\t\(symbol "%s"' % re.escape(name), text, re.MULTILINE)
    if not m:
        return None
    start = text.index("(", m.start())
    return text[start:_span(text, start)]


def _rename_block(block: str, new_name: str) -> str:
    """Rewrite the outer symbol name, leaving nested unit names alone."""
    return re.sub(r'^\(symbol "[^"]*"', '(symbol "%s"' % new_name, block, count=1)


def _children(block: str):
    """(head, start, end) for each direct child s-expression of `block`."""
    i = block.index("(") + 1
    # skip past the head token and the quoted name
    while i < len(block) and block[i] not in "(":
        if block[i] == '"':
            i = _span_string(block, i)
        else:
            i += 1
    while i < len(block):
        if block[i] == "(":
            end = _span(block, i)
            head = re.match(r"\(\s*([^\s()]+)", block[i:end])
            yield (head.group(1) if head else ""), i, end
            i = end
        else:
            i += 1


def _span_string(text: str, start: int) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return i


def _merge_extends(parent: str, child: str) -> str:
    """Flatten a derived symbol: parent graphics and pins, child properties.

    KiCad resolves `(extends ...)` when it caches a symbol into a schematic's
    `lib_symbols`, so a file that keeps the reference will not load standalone.
    """
    overrides = {}
    for head, s, e in _children(child):
        if head == "property":
            m = re.match(r'\(property\s+"([^"]*)"', child[s:e])
            if m:
                overrides[m.group(1)] = child[s:e]

    out, consumed = [], 0
    for head, s, e in _children(parent):
        if head == "extends":
            out.append(parent[consumed:s])
            consumed = e
            continue
        if head != "property":
            continue
        m = re.match(r'\(property\s+"([^"]*)"', parent[s:e])
        if m and m.group(1) in overrides:
            out.append(parent[consumed:s])
            out.append(overrides.pop(m.group(1)))
            consumed = e
    out.append(parent[consumed:])
    merged = "".join(out)

    if overrides:  # properties the child adds that the parent never had
        cut = merged.rindex(")")
        merged = merged[:cut] + "".join("\t" + v + "\n" for v in overrides.values()) + merged[cut:]
    return merged


# --------------------------------------------------------------------------
# minimal s-expression parser (symbol detail here, netlists in board.py)


def _parse(text: str):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            if not stack:
                break
            parent = stack.pop()
            parent.append(cur)
            cur = parent
        elif t.startswith('"'):
            cur.append(t[1:-1].replace('\\"', '"').replace("\\n", "\n"))
        else:
            cur.append(t)
    return cur


def _find_symbol(tree, name: str):
    for node in tree:
        if isinstance(node, list) and node and node[0] == "kicad_symbol_lib":
            for child in node[1:]:
                if isinstance(child, list) and len(child) > 1 \
                        and child[0] == "symbol" and child[1] == name:
                    return child
    return None


def _collect(node, props: dict, pins: list):
    """Walk a symbol node, gathering properties and pins from all units."""
    for child in node[2:] if len(node) > 2 else []:
        if not isinstance(child, list) or not child:
            continue
        head = child[0]
        if head == "property" and len(child) > 2:
            props[child[1]] = child[2]
        elif head == "extends" and len(child) > 1:
            props["__extends__"] = child[1]
        elif head == "pin":
            etype = child[1] if len(child) > 1 else None
            number = name = None
            at = length = None
            for sub in child:
                if isinstance(sub, list) and sub:
                    if sub[0] == "number" and len(sub) > 1:
                        number = sub[1]
                    elif sub[0] == "name" and len(sub) > 1:
                        name = sub[1]
                    elif sub[0] == "at" and len(sub) > 2:
                        at = [_num(v) for v in sub[1:4]]
                    elif sub[0] == "length" and len(sub) > 1:
                        length = _num(sub[1])
            if number is not None:
                pin = {"number": number, "name": name, "type": etype}
                if at is not None:
                    # The connection point, in symbol coordinates (y up). A
                    # schematic wire has to land on exactly this, so it is the
                    # one number that matters when authoring .kicad_sch text.
                    pin["x"], pin["y"] = at[0], at[1]
                    pin["rotation"] = at[2] if len(at) > 2 else 0
                if length is not None:
                    pin["length"] = length
                pins.append(pin)
        elif head == "symbol":
            _collect(child, props, pins)   # a unit
