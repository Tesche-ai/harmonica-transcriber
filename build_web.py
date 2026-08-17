"""Inject out/systems.json into web/harp.html.

Idempotent: it replaces the <tbody> of table.score, so it can be re-run after
every transcription pass without duplicating rows.
"""
import html
import json
import re

import paths


def render_rows(systems):
    rows, current = [], None
    for s in systems:
        if s.get("section") and s["section"] != current:
            current = s["section"]
            rows.append(f'<tr class="secrow"><td colspan="2">{html.escape(current)}</td></tr>')
        cells = []
        for tok in s["tab"].split():
            if tok == "|":
                cells.append('<i class="bl">|</i>')
            elif tok == "-":
                cells.append('<i class="rst">&middot;</i>')
            elif tok == "X":
                cells.append('<i class="oor">X</i>')
            else:
                cls = "dr" if tok.startswith("-") else "bw"
                cells.append(f'<i class="{cls}">{html.escape(tok)}</i>')
        badge = ('<span class="bdg ok">checked</span>' if s["ok"]
                 else '<span class="bdg draft">draft</span>')
        rows.append(
            f'<tr><th class="brs">{s["lo"]}&ndash;{s["hi"]}<br>{badge}</th>'
            f'<td class="tabcell">{" ".join(cells)}</td></tr>')
    return "\n".join(rows)


def main():
    systems = json.load(open(paths.OUT / "systems.json", encoding="utf-8"))
    page = paths.WEB / "harp.html"
    src = page.read_text(encoding="utf-8")
    body = render_rows(systems)
    new, n = re.subn(r'(<table class="score">.*?<tbody>\n).*?(\n\s*</tbody>)',
                     lambda m: m.group(1) + body + m.group(2),
                     src, count=1, flags=re.S)
    if not n:
        raise SystemExit("could not find <table class=\"score\"> tbody in harp.html")
    page.write_text(new, encoding="utf-8")
    print(f"injected {len(systems)} systems into {page}")


if __name__ == "__main__":
    main()
