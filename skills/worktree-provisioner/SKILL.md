---
name: worktree-provisioner
description: Git linked worktreeのcreate/list/show/removeをinstalled CLI経由で安全に操作するときに使う。Codexのtask/workflow管理やSpecDock lifecycle操作には使わない。
---

# worktree-provisioner

Git linked worktree を管理するための model-invoked skill です。実際の Git、root、target、bootstrap、cleanup、JSON schema の処理は installed `worktree-provisioner` CLI に委ね、skill は意図の認可、事実確認、結果の解釈だけを行います。

## 実行境界

- 自動実行は、この skill ディレクトリを基準にした `./scripts/worktree-provisioner` を使う。CLI は PATH 上の installed `worktree-provisioner` に委ねられる。
- 自動実行では、すべての呼び出しに `--json` を付ける。JSON envelope の `schema_version`、`status`、`operation`、`result`、`error.code` を確認し、text output の解析で判断しない。
- `status=ok` / exit `0` は完了、`status=partial` / exit `1` は成果物を保持した部分完了、`status=error` / exit `1` は失敗、usage error / exit `2` は入力修正が必要、と扱う。partial は成功として報告しない。
- wrapper が exit `127` を返した場合は、CLI が installed でないか PATH 上にない installation problem と報告する。
- Codex task の作成・移動、current task の変更、SpecDock lifecycle、GitHub/remote 操作はこの skill の仕事ではない。

## 事前確認

1. 依頼が create/list/show/remove のどれかを分類する。
2. create では create intent、対象 repository、managed root、実行による副作用が明確かを確認する。曖昧なら質問して停止し、wrapper を実行しない。label 省略だけは owner-approved auto-id のため質問しない。
3. remove では削除対象が明示された単一 target かを確認する。曖昧な basename、外部 target、protected target は停止する。
4. repository と root の事実は CLI の引数（`--repo`、`--root`）または合意済みの CLI default で明示する。wrapper に root を注入したり、CLI の root precedence を再実装したりしない。

完了条件: intent、repository、root、対象、side effect のうち必要な事実が確定し、未確定ならコマンドを一度も実行していない。

## Create

明示的な create request のみ処理します。

1. まず fact gate として次を一度実行する。

   ```text
   ./scripts/worktree-provisioner list --repo <repo> --root <root> --json
   ```

   `<root>` が CLI default または環境変数で確定している場合は、その `--root` を省略してよい。結果が exit `0` かつ `status=ok` でなければ create しない。
2. fact gate が通ったら、create を一度だけ実行する。label がある場合だけ `[label]` を渡し、bootstrap を抑止するのは利用者が明示した場合だけとする。

   ```text
   ./scripts/worktree-provisioner create [label] --repo <repo> --root <root> [--no-bootstrap] --json
   ```

3. `status` を解釈し、成功なら `result.id`、`result.branch`、`result.worktree_path` の絶対パス、`result.bootstrap.status` を報告する。
   成功・partial・error のいずれでも envelope の `warnings` を必ず確認する。`collision_partial_artifact` があれば warning の
   `message` は解析せず、`facts.candidate_id`、`facts.branch`、`facts.path`、各 `*_exists` を機械的に報告する。partial なら
   同じ成果物に加えて retained worktree/branch と bootstrap failure の詳細を明示し、完了扱いにしない。retry 後の後続 error でも
   warnings を確認し、保持された候補を隠さない。

完了条件: fact gate 成功後の create は1回だけで、報告に id、branch、absolute path、bootstrap state が含まれ、Codex task lifecycle は変更していない。

## List / Show

- 利用者が要求した read-only operation を wrapper 経由で JSON で一度実行する。
- `schema_version=1` と `status` を確認し、`status=ok` なら `result` を報告する。`error.code` と exit code がある場合はそのまま原因を報告する。
  `warnings` は常に確認し、`collision_partial_artifact` の stable code と typed `facts` を報告する。warning message の文言には依存しない。
- JSON envelope を人間向け text と混ぜず、CLI の schema や target resolution を skill 側で再実装しない。

完了条件: requested operation の JSON envelope を根拠に結果または修正可能な error を報告し、書き込み操作を追加していない。

## Remove

明示的な target removal のみ処理します。

1. まず次を一度実行して authorization/evidence gate とする。

   ```text
   ./scripts/worktree-provisioner show <target> --repo <repo> --root <root> --json
   ```

2. `status=ok` で、`result.worktree` が要求した target と完全一致し、`managed=true`、`removable=true`、`remove_blockers=[]` であることを確認する。いずれかが満たされなければ remove しない。external、ambiguous、protected、blocker 付きの target も停止する。
3. force の利用者明示がない限り `--force` を付けず、次を一度だけ実行する。利用者が force を明示した場合だけ `--force` をちょうど一度付ける。

   ```text
   ./scripts/worktree-provisioner remove <target> --repo <repo> --root <root> [--force] --json
   ```

4. `status=ok`、`partial`、`error` を区別して報告する。`warnings` があれば stable code と typed facts を確認・報告する。locked target は手動で `git worktree unlock` が必要だと報告するだけにし、skill 自身は unlock や再試行を実行しない。

完了条件: show の確認後に条件を満たす remove が最大1回だけ実行され、force intent が明示されていない呼び出しに `--force` がなく、task lifecycle や unlock を実行していない。

## CLI を正本とするもの

Git argv の組み立て、root precedence、target resolution、managed boundary と blockers、bootstrap execution、schema generation、worktree cleanup は CLI に委ねます。skill ではそれらの実装や text parsing を複製せず、CLI が返す versioned JSON の状態だけを解釈します。
