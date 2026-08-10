# CLAUDE.md — fractal-spec-agent

要件・Issue・問題意識から「フラクタル構造の設計書＋学習の層」を生成するエージェントと、
その構造を機械検査するオラクル。

## 確認のしかた

- `python eval/oracle.py --selftest` … オラクル自身の検証（15項目。壊した見本6種の検出＋誤検出なし）
- `python eval/oracle.py` … お手本（samples/reference）を採点 → PASS
- `python eval/oracle.py --check <dir>` … 任意の設計書フォルダを採点

## いじるときの約束

- 構造規則を変えるときは、①samples/reference/要素4_構造規則.md（人間向け）②oracle.py（機械実装）③selftestの見本、の3点を同時に更新する
- selftestの壊した見本は「1見本＝1違反」を保つ（複合違反にすると誤検出テストが濁る）
- Python標準ライブラリのみ。個人情報・実在の固有名詞をサンプルに入れない
- `.gitignore` に `00_設計書/`・`_notes/`（個人メモは公開しない）
