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
  R8 配置       … 00_入力の不足.md がある／設計の本体は 01_設計/ ／用語.md は 02_学習/

使い方（リポジトリのルートで実行）:
  python eval/oracle.py                 # お手本(samples/reference)を採点 → PASS
  python eval/oracle.py --selftest      # オラクル自身を検証（壊した見本を検出できるか）
  python eval/oracle.py --check <dir>   # 任意の設計書フォルダを採点
  python eval/oracle.py --gaps <file>   # 入力そのものを点検し、何が書かれていないかを出す

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
ROOT_NAME = "00_枠.md"
GAPS_NAME = "00_入力の不足.md"
DESIGN_DIR = "01_設計"
LEARN_DIR = "02_学習"
GLOSSARY = "用語.md"
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

    # R7 図の規則
    root_text = root.read_text(encoding="utf-8")
    if "```mermaid" not in root_text:
        violations.append(f"R7違反[{ROOT_NAME}]: 全体構成図（mermaid）が無い")
    import re as _re
    for p2 in all_md:
        txt2 = p2.read_text(encoding="utf-8")
        if _re.search(r"(flowchart|graph)\s+(LR|RL)", txt2):
            violations.append(f"R7違反[{p2.relative_to(doc_dir).as_posix()}]: 横向きの図（LR/RL）は禁止")

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
        elif top != DESIGN_DIR:
            violations.append(f"R8違反[{rel3}]: 設計の本体は {DESIGN_DIR}/ に置く")

    return violations


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
    }
    for name, code in expects.items():
        v = check_tree(SELFTEST / name)
        t(f"{name} を {code} として検出", any(code in x for x in v))
        others = [x for x in v if code not in x]
        t(f"{name} で他の規則を誤検出しない", not others)

    # 4) 表パーサの単体検証
    rows = table_data_rows("|a|b|\n|---|---|\n|1|2|\n|3|4|\n")
    t("表パーサ: データ行を正しく数える", len(rows) == 2)

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
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.gaps:
        return run_gaps(Path(args.gaps))
    if args.check:
        v = check_tree(Path(args.check))
        for x in v:
            print(" ", x)
        print(f"\n## 採点: {'PASS' if not v else 'FAIL（' + str(len(v)) + '件）'}")
        return 0 if not v else 1
    return run_reference()


if __name__ == "__main__":
    sys.exit(main())