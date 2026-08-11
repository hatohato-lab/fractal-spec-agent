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
  R7 図の規則   … ルート(00_枠)にmermaid図が1つ以上。横向き図（flowchart LR/graph LR/RL）は禁止

使い方（リポジトリのルートで実行）:
  python eval/oracle.py                 # お手本(samples/reference)を採点 → PASS
  python eval/oracle.py --selftest      # オラクル自身を検証（壊した見本を検出できるか）
  python eval/oracle.py --check <dir>   # 任意の設計書フォルダを採点

依存: Python 3 標準ライブラリのみ。
"""

import argparse
import json
import re
import sys
from pathlib import Path

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
ROOT_NAME = "00_枠.md"
GLOSSARY = "用語.md"

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
        if p.name != GLOSSARY:
            if not rows:
                violations.append(f"R3違反[{rel}]: 表（枠）が無い")
            elif not (FRAME_MIN <= len(rows) <= FRAME_MAX):
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
    glossary_file = doc_dir / GLOSSARY
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

    # R7 図の規則
    root_text = root.read_text(encoding="utf-8")
    if "```mermaid" not in root_text:
        violations.append(f"R7違反[{ROOT_NAME}]: 全体構成図（mermaid）が無い")
    import re as _re
    for p2 in all_md:
        txt2 = p2.read_text(encoding="utf-8")
        if _re.search(r"(flowchart|graph)\s+(LR|RL)", txt2):
            violations.append(f"R7違反[{p2.relative_to(doc_dir).as_posix()}]: 横向きの図（LR/RL）は禁止")

    return violations


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
    }
    for name, code in expects.items():
        v = check_tree(SELFTEST / name)
        t(f"{name} を {code} として検出", any(code in x for x in v))
        others = [x for x in v if code not in x]
        t(f"{name} で他の規則を誤検出しない", not others)

    # 4) 表パーサの単体検証
    rows = table_data_rows("|a|b|\n|---|---|\n|1|2|\n|3|4|\n")
    t("表パーサ: データ行を正しく数える", len(rows) == 2)

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
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        v = check_tree(Path(args.check))
        for x in v:
            print(" ", x)
        print(f"\n## 採点: {'PASS' if not v else 'FAIL（' + str(len(v)) + '件）'}")
        return 0 if not v else 1
    return run_reference()


if __name__ == "__main__":
    sys.exit(main())