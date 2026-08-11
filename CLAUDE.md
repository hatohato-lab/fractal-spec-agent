# CLAUDE.md — fractal-spec-agent

要件・Issue・問題意識から「フラクタル構造の設計書＋学習の層」を生成するエージェントと、
その構造を機械検査するオラクル。

## 確認のしかた

- `python eval/oracle.py --selftest` … オラクル自身の検証（29項目。壊した見本10種の検出＋誤検出なし＋入力点検6項目）
- `python eval/oracle.py` … お手本（samples/reference）を採点 → PASS
- `python eval/oracle.py --check <dir>` … 任意の設計書フォルダを採点
- `python eval/oracle.py --gaps <file>` … 入力そのものを点検し、何が書かれていないかを出す（生成はしない）

## 出力の配置（R8で機械検査）

```
出力先/
├── 00_枠.md            入口
├── 00_入力の不足.md     入力の点検結果
├── 01_設計/            設計の本体
└── 02_学習/用語.md      学習の層
```

## いじるときの約束

- 構造規則を変えるときは、①samples/reference/01_設計/要素4_構造規則.md（人間向け）②oracle.py（機械実装）③selftestの見本、の3点を同時に更新する
- 入力の観点表（`eval/corpus/input_checklist.json`）を変えたら、`input_full.md`／`input_thin.md` の見本も合わせて更新する（full＝全観点あり、thin＝前提・代替案・失敗時が欠落）
- selftestの壊した見本は「1見本＝1違反」を保つ（複合違反にすると誤検出テストが濁る）
- Python標準ライブラリのみ。個人情報・実在の固有名詞をサンプルに入れない
- `.gitignore` に `00_設計書/`・`_notes/`（個人メモは公開しない）
