# worktree-provisioner

Git linked worktree を中央ディレクトリへ作成する小さな CLI です。SpecDock の
`worktree create` が持っていた作成契約を、仕様管理から独立したツールとして提供します。

## 動作

- main worktree の basename を namespace に使う
- label 未指定時は `wt1`, `wt2`, ... を採番する
- label 指定時は `label`, `label2`, ... と衝突を避ける
- branch は実行対象 checkout の current branch を基点に `<branch>-<id>` とする
- 作成後、利用可能なら worktree root で `make init` を実行する
- `make init` の未定義・検出失敗・実行失敗は worktree 作成を取り消さない

## 使用方法

```bash
uv run --project /Volumes/990p2t/workspace/tools/worktree-provisioner \
  worktree-provisioner create issue-123 \
  --repo /path/to/repository \
  --root /Volumes/990p2t/workspace/worktrees
```

label は lowercase letters、digits、hyphen のみ使用できます。省略すると `wtN` を採番します。

`--root` を省略した場合は、次の順に環境変数を参照します。

1. `WORKTREE_PROVISIONER_ROOT`
2. `SPEC_DOCK_WORKTREE_ROOT`（SpecDock からの移行互換）

エージェント向け JSON 出力:

```bash
worktree-provisioner create --repo /path/to/repository --root /path/to/worktrees --json
```

## 境界

このツールは worktree と新規 local branch の作成だけを扱います。worktree の削除、branch の削除、
SpecDock node、active pointer、GitHub Issue、Codex app 固有 metadata は変更しません。

作成元 checkout の未コミット変更は新しい worktree へコピーされません。新しい branch は現在の `HEAD`
から作成されます。

## 開発

```bash
uv run pytest
uv run ruff check .
```

