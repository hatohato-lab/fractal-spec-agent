#!/usr/bin/env python3
"""fractal-spec-agent のオラクル（文書構造の採点係）。

フラクタル設計書（枠→要素→また枠、の入れ子Markdown群）が構造規則を守っているかを
機械判定する。規則は samples/reference/要素4_構造規則.md に人間向けの記述があり、
ここはその機械実装。

検査する規則:
  R1 1枚1画面   … 各ファイルは MAX_LINES 行以内
  R2 枠は2〜9行 … 枠の表のデータ行数が 2〜9
  R3 同形式     … ルート以外の全ノードに「上へ戻る」リンクがある。全ノードに表がある
  R4 リンク完全 … リンク先の .md が実在する
  R5 孤児なし   … ルートから辿れない .md が無い（用語.md はルートから参照済み）
  R6 用語定義   … 監視語（watchlist）が本文に出たら、用語.md の表に定義が要る
  R7 図の規則   … ルート(00_枠)にPlantUML図（@startuml）が1つ以上。
                  横向き指定（left to right direction／mermaidのLR/RL）は禁止
  R8 配置       … 00_入力の不足.md がある／設計の本体は 01_設計/ ／用語.md は 02_学習/
  R9 推定の隔離 … AIの推定は 03_推定/ の中だけ。【推定】タグが本文に出たら違反。
                  03_推定/ の表の全行に状態（未確認／採用済）が要る

使い方（リポジトリのルートで実行）:
  python eval/oracle.py                 # お手本(samples/reference)を採点 → PASS
  python eval/oracle.py --selftest      # オラクル自身を検証（壊した見本を検出できるか）
  python eval/oracle.py --check <dir>   # 任意の設計書フォルダを採点
  python eval/oracle.py --gaps <file>   # 入力そのものを点検し、何が書かれていないかを出す
  python eval/oracle.py --html <dir>    # 設計書ツリーを1枚のHTMLにまとめて <dir>/index.html に出力

依存: Python 3 標準ライブラリのみ。
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Windowsコンソールの文字化け対策（日本語の違反メッセージを読める形で出す）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
REFERENCE = REPO / "samples" / "reference"
SELFTEST = ROOT / "selftest"
WATCHLIST = ROOT / "corpus" / "watchlist.json"

# 既定の閾値（編集上の既定値。認知科学は上限の目安であり厳密な導出ではない）
# eval/corpus/rules.json があれば {"max_lines":.., "frame_min":.., "frame_max":..} で上書きできる
MAX_LINES = 45
FRAME_MIN, FRAME_MAX = 2, 9
_rules_file = Path(__file__).resolve().parent / "corpus" / "rules.json"
if _rules_file.exists():
    import json as _json
    _r = _json.loads(_rules_file.read_text(encoding="utf-8"))
    MAX_LINES = _r.get("max_lines", MAX_LINES)
    FRAME_MIN = _r.get("frame_min", FRAME_MIN)
    FRAME_MAX = _r.get("frame_max", FRAME_MAX)
# 出力の配置（役割がフォルダ名で分かる形。R8で機械検査する）
#   00_枠.md          … 入口
#   00_入力の不足.md   … 入力そのものの点検結果
#   01_設計/          … 設計の本体（要素と、その下の階層）
#   02_学習/用語.md    … 学習の層
#   03_推定/          … （任意）AIが推定した補完候補の隔離場所（R9で機械検査）
ROOT_NAME = "00_枠.md"
GAPS_NAME = "00_入力の不足.md"
DESIGN_DIR = "01_設計"
LEARN_DIR = "02_学習"
GLOSSARY = "用語.md"
ESTIMATE_DIR = "03_推定"
ESTIMATE_TAG = "【推定】"
CHECKLIST = ROOT / "corpus" / "input_checklist.json"

LINK_RE = re.compile(r"\]\(([^)#]+\.md)\)")


def find_root(doc_dir: Path) -> Path | None:
    p = doc_dir / ROOT_NAME
    return p if p.exists() else None


def table_data_rows(text: str) -> list[str]:
    """最初のMarkdown表のデータ行（ヘッダ・罫線を除く）を返す。"""
    rows, in_table, seen_sep = [], False, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            in_table = True
            if re.match(r"^\|[\s:|-]+\|$", s):
                seen_sep = True
                continue
            if seen_sep:
                rows.append(s)
        elif in_table:
            break
    return rows


def check_tree(doc_dir: Path) -> list[str]:
    """設計書フォルダを検査し、違反のリストを返す（空＝合格）。"""
    violations = []
    doc_dir = doc_dir.resolve()
    all_md = sorted(p for p in doc_dir.rglob("*.md"))
    if not all_md:
        return ["ファイルが1枚もない"]

    root = find_root(doc_dir)
    if root is None:
        return [f"ルート {ROOT_NAME} が無い"]

    # R1, R2, R3, R4
    link_targets = {root.resolve()}
    for p in all_md:
        rel = p.relative_to(doc_dir).as_posix()
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()

        if len(lines) > MAX_LINES:
            violations.append(f"R1違反[{rel}]: {len(lines)}行（上限{MAX_LINES}）")

        rows = table_data_rows(text)
        is_estimate = rel.split("/")[0] == ESTIMATE_DIR
        if p.name != GLOSSARY:
            if not rows:
                violations.append(f"R3違反[{rel}]: 表（枠）が無い")
            elif not is_estimate and not (FRAME_MIN <= len(rows) <= FRAME_MAX):
                # 推定の表は件数が入力次第で変わるため、枠の行数制限（R2）の対象外
                violations.append(f"R2違反[{rel}]: 枠の行数 {len(rows)}（許容{FRAME_MIN}〜{FRAME_MAX}）")

        if p.resolve() != root.resolve() and p.name != GLOSSARY and "上へ戻る" not in text:
            violations.append(f"R3違反[{rel}]: 「上へ戻る」リンクが無い")

        for target in LINK_RE.findall(text):
            t = (p.parent / target).resolve()
            if not t.exists():
                violations.append(f"R4違反[{rel}]: リンク切れ → {target}")
            else:
                link_targets.add(t)

    # R5 孤児
    for p in all_md:
        if p.resolve() not in link_targets:
            violations.append(f"R5違反[{p.relative_to(doc_dir).as_posix()}]: どこからもリンクされていない孤児")

    # R6 用語カバレッジ
    watch = []
    if WATCHLIST.exists():
        watch = json.loads(WATCHLIST.read_text(encoding="utf-8")).get("terms", [])
    glossary_file = doc_dir / LEARN_DIR / GLOSSARY
    defined = set()
    if glossary_file.exists():
        for row in table_data_rows(glossary_file.read_text(encoding="utf-8")):
            cells = [c.strip() for c in row.strip("|").split("|")]
            if cells:
                defined.add(re.sub(r"[（(].*", "", cells[0]).strip())
    body = "\n".join(p.read_text(encoding="utf-8") for p in all_md if p.name != GLOSSARY)
    for term in watch:
        if term in body and not any(term in d or d in term for d in defined):
            violations.append(f"R6違反: 監視語「{term}」が本文に出るが 用語.md に定義が無い")

    # R7 図の規則（PlantUML）
    root_text = root.read_text(encoding="utf-8")
    if "@startuml" not in root_text:
        violations.append(f"R7違反[{ROOT_NAME}]: 全体構成図（PlantUML @startuml）が無い")
    fence_re = re.compile(r"```.*?```", re.S)
    for p2 in all_md:
        # 横向き指定は「図の中」だけを検査する（規則の説明文が語を含んでも誤検出しない）
        code = "\n".join(fence_re.findall(p2.read_text(encoding="utf-8")))
        if "left to right direction" in code or re.search(r"(flowchart|graph)\s+(LR|RL)", code):
            violations.append(f"R7違反[{p2.relative_to(doc_dir).as_posix()}]: 横向きの図（left to right direction／LR/RL）は禁止")

    # R8 配置規則（フォルダ名で役割が分かること）
    if not (doc_dir / GAPS_NAME).exists():
        violations.append(f"R8違反: {GAPS_NAME} が無い（入力の点検結果が付いていない）")
    for p3 in all_md:
        rel3 = p3.relative_to(doc_dir).as_posix()
        if rel3 in (ROOT_NAME, GAPS_NAME):
            continue
        top = rel3.split("/")[0]
        if p3.name == GLOSSARY:
            if top != LEARN_DIR:
                violations.append(f"R8違反[{rel3}]: {GLOSSARY} は {LEARN_DIR}/ に置く")
        elif top not in (DESIGN_DIR, ESTIMATE_DIR):
            violations.append(f"R8違反[{rel3}]: 設計の本体は {DESIGN_DIR}/ に置く")

    # R9 推定の隔離（AIが推定した未確認情報を、確定した本文に混ぜない）
    for p4 in all_md:
        rel4 = p4.relative_to(doc_dir).as_posix()
        text4 = p4.read_text(encoding="utf-8")
        if rel4.split("/")[0] == ESTIMATE_DIR:
            for row in table_data_rows(text4):
                if "未確認" not in row and "採用済" not in row:
                    violations.append(f"R9違反[{rel4}]: 状態（未確認／採用済）の無い推定行がある")
        elif ESTIMATE_TAG in text4:
            violations.append(
                f"R9違反[{rel4}]: {ESTIMATE_TAG}タグが本文に混入している（推定は {ESTIMATE_DIR}/ に隔離する）")

    return violations


# ---------------------------------------------------------------- HTML出力

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _anchor(rel: str) -> str:
    """相対パス → アンカーID（.md を落とすだけ。日本語はそのまま使える）。"""
    return rel[:-3] if rel.endswith(".md") else rel


# --- PlantUML → インラインSVG -----------------------------------------------
# 外部サーバもJavaも使わず、ブラウザだけで図が見えるようにする。
# 対応するのは、この設計書で使う範囲の構文に限る（下の PUML_SUPPORTED）。
# 解釈できない構文が1つでもあれば、図にせずソースのまま出す（黙って壊さない）。

PUML_SUPPORTED = """コンポーネント図: [A] --> [B] / [A] ..> [B] : ラベル
クラス図(構成): class X / A *-- B / A <.. B : ラベル"""

_PUML_NODE = re.compile(r"\[([^\]]+)\]")
_PUML_EDGE = re.compile(
    r"^\s*(?:\[([^\]]+)\]|(\w+))\s*(\*--|<\.\.|\.\.>|-->|--)\s*"
    r"(?:\[([^\]]+)\]|(\w+))\s*(?::\s*(.+?))?\s*$"
)


def _puml_parse(src: str):
    """PlantUMLソースを (ノード順, 辺) に還元する。未対応構文があれば None。

    別名（`class "表示名" as F`）は表示名に解決してから辺をつなぐ。
    """
    nodes, edges, alias = [], [], {}

    def add(n):
        if n not in nodes:
            nodes.append(n)

    def name(x):
        return alias.get(x, x)

    for raw in src.splitlines():
        line = raw.strip()
        if not line or line.startswith(("@startuml", "@enduml", "'", "skinparam", "hide", "title")):
            continue
        if line == "}":
            continue
        # class "表示名" as 別名  ／  class 名前  ／  class 名前 { ... }
        m = re.match(r'^class\s+"([^"]+)"\s+as\s+(\w+)\s*\{?[^}]*\}?\s*$', line)
        if m:
            alias[m.group(2)] = m.group(1)
            add(m.group(1))
            continue
        m = re.match(r"^class\s+(\w+)\s*\{?[^}]*\}?\s*$", line)
        if m:
            add(m.group(1))
            continue
        m = _PUML_EDGE.match(line)
        if m:
            a = name(m.group(1) or m.group(2))
            arrow, label = m.group(3), m.group(6) or ""
            b = name(m.group(4) or m.group(5))
            add(a)
            add(b)
            edges.append((a, b, arrow, label))
            continue
        return None  # 未対応の行があった
    return (nodes, edges) if nodes else None


def _puml_to_svg(src: str) -> str | None:
    """縦方向の層に並べたSVGを返す。描けなければ None。"""
    parsed = _puml_parse(src)
    if not parsed:
        return None
    nodes, edges = parsed

    # 層を決める（親→子の辺で1段下げる。循環しても止まる）
    layer = {n: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for a, b, arrow, _lbl in edges:
            want = layer[a] + 1
            if arrow in ("<..",):      # b が a を使う向き（矢印は逆）
                want = layer[b] + 1
                if layer[a] < want:
                    layer[a], changed = want, True
                continue
            if layer[b] < want:
                layer[b], changed = want, True
        if not changed:
            break

    rows: dict[int, list[str]] = {}
    for n in nodes:
        rows.setdefault(layer[n], []).append(n)

    BW, BH, GX, GY, PAD = 190, 46, 34, 74, 20
    width = max(len(v) for v in rows.values()) * (BW + GX) - GX + PAD * 2
    height = (max(rows) + 1) * (BH + GY) - GY + PAD * 2
    pos = {}
    for ly, names in rows.items():
        total = len(names) * (BW + GX) - GX
        x0 = (width - total) / 2
        for i, n in enumerate(names):
            pos[n] = (x0 + i * (BW + GX), PAD + ly * (BH + GY))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(width)} {int(height)}" '
        f'width="100%" style="max-width:{int(width)}px;height:auto" role="img">',
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#000"/></marker>'
        '<marker id="dm" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="9" '
        'markerHeight="9" orient="auto"><path d="M0,6 L6,1 L12,6 L6,11 z" fill="#000"/>'
        '</marker></defs>',
    ]
    for a, b, arrow, label in edges:
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        x1, y1 = ax + BW / 2, ay + BH
        x2, y2 = bx + BW / 2, by
        if ay > by:  # 下から上へ向かう辺
            y1, y2 = ay, by + BH
        dash = ' stroke-dasharray="6,4"' if arrow in ("..>", "<..") else ""
        head = "dm" if arrow == "*--" else "ah"
        out.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                   f'stroke="#000" stroke-width="1.5"{dash} marker-end="url(#{head})"/>')
        if label:
            out.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2:.0f}" '
                       f'font-size="13" fill="#000" text-anchor="middle" '
                       f'dy="-4">{_esc(label)}</text>')
    for n, (x, y) in pos.items():
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{BH}" '
                   f'fill="#fff" stroke="#000" stroke-width="1.5"/>')
        out.append(f'<text x="{x + BW / 2:.0f}" y="{y + BH / 2 + 5:.0f}" font-size="15" '
                   f'fill="#000" text-anchor="middle">{_esc(n)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _md_to_html(text: str, cur_dir: Path, doc_dir: Path) -> str:
    """最小のMarkdown→HTML変換（この設計書ツリーで使う記法のみ対応）。

    対応: 見出し / 表 / フェンスコード（plantumlはソースのまま<pre>埋め込み）/
          リンク（.mdはページ内アンカー化）/ 太字 / インラインコード / 箇条書き / 段落
    """
    def inline(s: str) -> str:
        s = _esc(s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)

        def link(m):
            label, url = m.group(1), m.group(2)
            if url.endswith(".md"):
                t = (cur_dir / url).resolve()
                try:
                    return f'<a href="#{_anchor(t.relative_to(doc_dir).as_posix())}">{label}</a>'
                except ValueError:
                    return label
            return f'<a href="{url}">{label}</a>'
        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s)

    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("```"):
            lang = s[3:].strip()
            block, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            src = chr(10).join(block)
            if lang in ("plantuml", "puml"):
                svg = _puml_to_svg(src)
                if svg:
                    out.append(f'<figure class="uml">{svg}</figure>')
                else:
                    # 解釈できない構文。黙って壊さず、ソースを見せる
                    out.append(f'<pre class="uml">{_esc(src)}</pre>')
            else:
                out.append(f"<pre>{_esc(src)}</pre>")
        elif s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                r = lines[i].strip()
                if not re.match(r"^\|[\s:|-]+\|$", r):
                    rows.append([inline(c.strip()) for c in r.strip("|").split("|")])
                i += 1
            i -= 1
            if rows:
                head = "".join(f"<th>{c}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows[1:])
                out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        elif s.startswith("#"):
            level = min(len(s) - len(s.lstrip("#")) + 1, 4)  # 階層を1段下げる（h1はページ題）
            out.append(f"<h{level}>{inline(s.lstrip('#').strip())}</h{level}>")
        elif s.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{inline(lines[i].strip()[2:])}</li>")
                i += 1
            i -= 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif s:
            out.append(f"<p>{inline(s)}</p>")
        i += 1
    return "\n".join(out)


def build_html(doc_dir: Path) -> str:
    """設計書ツリーを、リンク順（ルートから深さ優先）で1枚のHTMLにまとめる。

    PlantUMLはインラインSVGに変換して埋め込む（外部サーバもJavaも使わない）。
    未対応の構文が混じっていた図だけ、ソースのまま <pre class="uml"> で出す。
    """
    doc_dir = doc_dir.resolve()
    root = find_root(doc_dir)
    if root is None:
        raise FileNotFoundError(f"{ROOT_NAME} が無い: {doc_dir}")

    order, seen = [], set()

    def visit(p: Path, depth: int):
        rp = p.resolve()
        if rp in seen or not rp.exists():
            return
        seen.add(rp)
        order.append((rp, depth))
        for target in LINK_RE.findall(rp.read_text(encoding="utf-8")):
            visit(rp.parent / target, depth + 1)

    visit(root, 0)
    for p in sorted(doc_dir.rglob("*.md")):  # 孤児も末尾に含める（取りこぼさない）
        visit(p, 1)

    title = "設計書"
    m = re.match(r"#\s+(.+)", root.read_text(encoding="utf-8"))
    if m:
        title = re.sub(r"[—-].*", "", m.group(1)).strip()

    sections = []
    for p, depth in order:
        rel = p.relative_to(doc_dir).as_posix()
        body = _md_to_html(p.read_text(encoding="utf-8"), p.parent, doc_dir)
        sections.append(
            f'<section id="{_anchor(rel)}" style="margin-left:{depth * 1.5}em">\n'
            f'<div class="path">{_esc(rel)}</div>\n{body}\n</section>'
        )

    css = (
        "body{background:#fff;color:#000;font-family:Meiryo,'Hiragino Kaku Gothic ProN',"
        "'Yu Gothic',sans-serif;font-size:17px;line-height:1.8;max-width:60em;margin:0 auto;"
        "padding:2em 1em}h1,h2,h3,h4{border-bottom:2px solid #000;padding-bottom:.2em}"
        "table{border-collapse:collapse;margin:1em 0}th,td{border:1px solid #999;"
        "padding:.4em .7em;font-size:16px}th{background:#ebebeb}"
        "pre{background:#f5f5f5;border:1px solid #999;padding:1em;overflow-x:auto;font-size:14px}"
        "pre.uml::before{content:'PlantUML（未対応構文のためソース表示）';display:block;color:#333;"
        "font-size:13px;margin-bottom:.5em}"
        "figure.uml{margin:1em 0;padding:1em;border:1px solid #999;background:#fff;"
        "overflow-x:auto;text-align:center}"
        "section{border-left:3px solid #ccc;padding-left:1em;margin:1.5em 0}"
        ".path{color:#333;font-size:13px}a{color:#0645ad}"
    )
    return (
        "<!DOCTYPE html>\n<html lang=\"ja\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{_esc(title)}</title>\n<style>{css}</style>\n</head>\n<body>\n"
        + "\n".join(sections)
        + "\n</body>\n</html>\n"
    )


def run_html(doc_dir: Path) -> int:
    if not doc_dir.is_dir():
        print(f"エラー: フォルダが見つかりません → {doc_dir}")
        return 2
    try:
        html = build_html(doc_dir)
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        return 2
    out = doc_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"出力: {out}")
    n_svg = html.count('<figure class="uml">')
    n_src = html.count('<pre class="uml">')
    print(f"※ PlantUML: {n_svg}枚をSVGで描画" + (f"／{n_src}枚は未対応構文のためソース表示" if n_src else ""))
    return 0


# ---------------------------------------------------------------- 入力の点検

def analyze_gaps(text: str) -> list[dict]:
    """入力テキストを観点表と突き合わせ、観点ごとの有無を返す。

    語で探すため、別の言い回しは取りこぼす。だから「無い」ではなく「見当たらない」
    と報告する。ここは判定ではなく分析であり、設計書の生成は一切しない。
    """
    items = json.loads(CHECKLIST.read_text(encoding="utf-8"))["観点"]
    out = []
    for it in items:
        hit = next((w for w in it["手がかり"] if w in text), None)
        out.append({
            "観点": it["観点"],
            "根拠": hit,
            "問い": it["問い"],
            "重大": bool(it.get("重大")),
        })
    return out


def run_gaps(path: Path) -> int:
    if not path.exists():
        print(f"エラー: 入力ファイルが見つかりません → {path}")
        return 2
    rows = analyze_gaps(path.read_text(encoding="utf-8", errors="replace"))
    miss = [r for r in rows if r["根拠"] is None]
    print(f"入力の点検: {path}")
    print(f"\n## 見当たらない観点（{len(rows)}観点中 {len(miss)}件）\n")
    if miss:
        for r in miss:
            mark = "★" if r["重大"] else "  "
            print(f"{mark} {r['観点']}\t{r['問い']}")
    else:
        print("   なし")
    print("\n## 書かれている観点\n")
    for r in rows:
        if r["根拠"] is not None:
            print(f"   {r['観点']}\t（根拠の語: {r['根拠']}）")
    heavy = [r for r in miss if r["重大"]]
    print("")
    if heavy:
        print(f"★は、欠けると設計書全体が崩れる観点です（{len(heavy)}件）。先に埋めてください。")
    print("※ 語で探しています。別の言い回しで書かれている場合は取りこぼします。")
    return 0


def run_reference() -> int:
    v = check_tree(REFERENCE)
    print(f"お手本の採点: {REFERENCE.name}")
    for x in v:
        print("  ", x)
    print(f"\n## 採点: {'PASS' if not v else 'FAIL（' + str(len(v)) + '件）'}")
    return 0 if not v else 1


def selftest() -> int:
    checks = []

    def t(name, cond):
        checks.append((name, bool(cond)))

    # 1) お手本は合格すること（誤検出ゼロ）
    t("お手本(reference)が合格する", not check_tree(REFERENCE))

    # 2) きれいな最小ツリーも合格
    t("きれいな最小見本が合格する", not check_tree(SELFTEST / "clean_minimal"))

    # 3) 壊した見本を、それぞれ正しい規則で検出できること
    expects = {
        "broken_overlong": "R1違反",
        "broken_bigframe": "R2違反",
        "broken_noreturn": "R3違反",
        "broken_deadlink": "R4違反",
        "broken_orphan": "R5違反",
        "broken_undefined_term": "R6違反",
        "broken_nodiagram": "R7違反",
        "broken_sideways": "R7違反",
        "broken_flatlayout": "R8違反",
        "broken_nogaps": "R8違反",
        "broken_estimate_in_body": "R9違反",
        "broken_estimate_unmarked": "R9違反",
    }
    for name, code in expects.items():
        v = check_tree(SELFTEST / name)
        t(f"{name} を {code} として検出", any(code in x for x in v))
        others = [x for x in v if code not in x]
        t(f"{name} で他の規則を誤検出しない", not others)

    # 4) 表パーサの単体検証
    rows = table_data_rows("|a|b|\n|---|---|\n|1|2|\n|3|4|\n")
    t("表パーサ: データ行を正しく数える", len(rows) == 2)

    # 4.5) HTML出力（--html）。1枚に全ノードが入り、.mdリンクがアンカー化されること
    html = build_html(REFERENCE)
    t("HTML出力: ルートの題を含む", "フラクタル設計書ジェネレータ" in html)
    t("HTML出力: .mdへのリンクが残らずアンカー化される",
      ".md\"" not in html and 'href="#01_設計/要素1_目的"' in html)

    # 4.6) PlantUMLが図（SVG）になること。ソースのまま残っていないこと
    t("HTML出力: PlantUMLがSVGとして描画される",
      '<figure class="uml">' in html and "<svg" in html and "@startuml" not in html)
    t("HTML出力: 未対応構文はSVGにせずソースのまま出す",
      _puml_to_svg("@startuml\nrobot foo #bar<>\n@enduml") is None)

    # 5) 入力の点検（--gaps）。生成はせず、何が書かれていないかだけを返すこと
    full = analyze_gaps((ROOT / "corpus" / "input_full.md").read_text(encoding="utf-8"))
    thin = analyze_gaps((ROOT / "corpus" / "input_thin.md").read_text(encoding="utf-8"))
    t("観点表は8観点", len(full) == 8)
    t("十分な入力では、見当たらない観点が0", not [r for r in full if r["根拠"] is None])
    thin_missing = {r["観点"] for r in thin if r["根拠"] is None}
    t("薄い入力で「前提」の欠落を検出する", "前提" in thin_missing)
    t("薄い入力で「代替案」「失敗時」の欠落も検出する",
      {"代替案", "失敗時"} <= thin_missing)
    t("薄い入力でも書かれている観点は欠落と言わない",
      "目的" not in thin_missing and "完了条件" not in thin_missing)
    t("欠けた「前提」は重大（★）として印が付く",
      any(r["観点"] == "前提" and r["重大"] for r in thin))

    ok = all(c for _, c in checks)
    for name, cond in checks:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    print(f"\n({sum(1 for _, c in checks if c)}/{len(checks)} PASS)")
    print(f"\n## オラクル判定: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", metavar="DIR")
    ap.add_argument("--gaps", metavar="FILE",
                    help="入力ファイルを点検し、何が書かれていないかを出す（生成はしない）")
    ap.add_argument("--html", metavar="DIR",
                    help="設計書ツリーを1枚のHTMLにまとめて DIR/index.html に出力する")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.gaps:
        return run_gaps(Path(args.gaps))
    if args.html:
        return run_html(Path(args.html))
    if args.check:
        v = check_tree(Path(args.check))
        for x in v:
            print(" ", x)
        print(f"\n## 採点: {'PASS' if not v else 'FAIL（' + str(len(v)) + '件）'}")
        return 0 if not v else 1
    return run_reference()


if __name__ == "__main__":
    sys.exit(main())