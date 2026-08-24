# JSON schema v1

`worktree-provisioner --json` は、実行環境にかかわらず次の envelope を stdout に1文書だけ出力します。

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "create",
  "result": {},
  "error": null,
  "warnings": []
}
```

## Common fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | 常に `1`。将来の envelope 変更は別 version とする |
| `status` | `ok` / `partial` / `error` | 操作結果の状態 |
| `operation` | `create` / `list` / `show` / `remove` / `null` | 実行した command。parse 前の usage error は `null` の場合がある |
| `result` | object / `null` | 成功または partial の operation payload |
| `error` | object / `null` | `code`、`message`、`details`。成功時は `null` |
| `warnings` | array | `{code, message}` の warning。通常は空配列 |

expected JSON error は `status=error` または `status=partial` であり、`error.code` を機械判定に使用します。message wording は
stable contract ではありません。usage error の終了コードは `2`、operational error と partial は `1` です。

## Operation payloads

- `create.result`: `id`、`main_worktree_path`、`container_path`、`worktree_path`、`branch`、`bootstrap`、`artifacts`
- `list.result`: `worktrees` 配列
- `show.result`: `target`、`worktree`
- `remove.result`: `target`、`resolved_target`、`force_requested`、`removed_record`、`removed_directory`、
  `branch_deleted`（branch は削除しないため常に `false`）

`bootstrap` は `requested`、`status`（`disabled`、`skipped`、`succeeded`、`failed`、`detection_failed`）、`command`、
`exit_code`、`detail` を持ちます。`artifacts` は `container_exists`、`worktree_path_exists`、`branch_exists`、
`worktree_record_exists` を持ち、観測できない値は `null` です。

`worktree` は `id`、`path`、`basename`、`branch`、`head`、`detached`、`bare`、`locked`、`lock_reason`、`main`、
`current`、`path_exists`、`record_exists`、`managed`、`classification_available`、`classification_reason`、`origin`、
`removable`、`remove_blockers` を持ちます。path は常に absolute string、unknown な nullable value は `null`、boolean は
boolean、blocker の一覧は配列です。

## Example: bootstrap partial

```json
{
  "schema_version": 1,
  "status": "partial",
  "operation": "create",
  "result": {
    "id": "wt1",
    "main_worktree_path": "/repo",
    "container_path": "/worktrees/repo",
    "worktree_path": "/worktrees/repo/repo-wt1",
    "branch": "main-wt1",
    "bootstrap": {
      "requested": true,
      "status": "failed",
      "command": ["make", "init"],
      "exit_code": 1,
      "detail": "bootstrap failed"
    },
    "artifacts": {
      "container_exists": true,
      "worktree_path_exists": true,
      "branch_exists": true,
      "worktree_record_exists": true
    }
  },
  "error": {
    "code": "bootstrap_failed",
    "message": "bootstrap failed",
    "details": {}
  },
  "warnings": []
}
```
