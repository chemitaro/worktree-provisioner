# P10 実装・品質検証レポート

## 固定点と候補

- 要件・設計・計画の基準 SHA: `41129b3a3a7f0b913f6181aa4b7fbc7bd3b7f7be`
- P10 開始時の実装親 SHA: `2224f9dd125e9edd731888c0a84a42d7c4b95ebd`
- P10 の最終 candidate SHA: P10 の変更を primary が commit した後に更新する（このレポート作成時点では未commit）。
- リポジトリ: `chemitaro/worktree-provisioner`
- ブランチ: `codex/implement-worktree-provisioner`

P10 は remote、GitHub、commit、push を実行していない。最終 candidate SHA、upstream SHA、ChatGPT Final Quality Gate Strict の結果は primary の commit/push 後に確定する。

## フェーズの実装結果

P0〜P9 は次の順で実装済みである。

| Phase | commit | 内容 |
| --- | --- | --- |
| P1 | `6c6b0fb` | executable contract tests |
| P2 | `3ba5fe3` | package/module scaffold |
| P3a | `df9c761` | application contracts/ports |
| P3b | `e716dab` | Git adapter and porcelain parser |
| P3c | `237f48e` | environment, make, filesystem adapters |
| P4 | `407e436` | create, naming, collision, bootstrap partial |
| P5 | `e3bf3a0` | list/show inventory and target resolver |
| P6 | `189ba66` | managed-only remove and cleanup |
| P7 | `d935e03` | CLI dispatch, text, JSON schema v1 |
| P8 | `f0fdff0` | Codex skill and thin wrapper |
| P9 | `2224f9d` | README, packaging, installation, CI |

P10 では全体 Ruff format の機械的整形、テスト fixture の Protocol 型適合、JSON fixture callback の型注釈、Make runner の型注釈を行った。仕様の意味、CLI の挙動、JSON schema、安全方針は変更していない。`docs/specification/design.md` の差分も Ruff がコードブロック内へ空行を挿入した機械的整形だけである。

P10 の変更ファイルは次のとおりである。

```text
docs/specification/design.md
src/worktree_provisioner/__init__.py
src/worktree_provisioner/__main__.py
src/worktree_provisioner/application/ports.py
src/worktree_provisioner/infra/git_cli.py
src/worktree_provisioner/infra/make_cli.py
tests/conftest.py
tests/integration/test_cli_contracts.py
tests/integration/test_cli_create.py
tests/unit/test_create.py
tests/unit/test_git_porcelain.py
tests/unit/test_inventory.py
tests/unit/test_make_cli.py
tests/unit/test_p1_contracts.py
tests/unit/test_root_and_naming.py
docs/implementation-report.md
```

## Full quality commands

実行環境は macOS arm64（`Darwin Mac-mini-261.local ... arm64`）、Python `3.14.6`、uv `0.11.24`、Git `2.54.0` である。

| Command | Result |
| --- | --- |
| `uv sync --all-groups --locked` | PASS (`Resolved 16 packages`, `Checked 13 packages`) |
| `uv run ruff format .` | PASS; 既知の format debt 12 files を機械的に整形 |
| `uv run ruff format --check .` | PASS; `47 files already formatted` |
| `uv run ruff check .` | PASS; `All checks passed!` |
| `uv run mypy src tests` | PASS; `Success: no issues found in 39 source files` |
| `uv run pytest -q` | PASS; `205 passed` |
| `uv build` | PASS; wheel と sdist を生成 |
| `git diff --check` | PASS |

mypy は P10 修正前に test fixture / JSON callback / Make runner の型エラー36件を報告した。production の挙動を変えず、Protocol cast、typed fake adapter、`Callable[[str], dict[str, Any]]`、typed runner に限定して修正し、最終的に0件となった。

## 配布物と clean install

生成物は次のとおりである。

| Artifact | SHA-256 |
| --- | --- |
| `dist/worktree_provisioner-0.1.0-py3-none-any.whl` | `d92eb1b94756ce649e10cab0aeb49d03a18ad848a95df2387249c52c4f951888` |
| `dist/worktree_provisioner-0.1.0.tar.gz` | `73c94fb1bfda73a1ef0da4d023c36cad7772fe752c56c1030b405c2fbd758bb9` |

wheel の検査結果:

- 22 files。`worktree_provisioner` package、entry point metadata、`dist-info/licenses/LICENSE` を含む。
- `METADATA` に `Requires-Dist` は存在しない。runtime Python dependency はない。
- wheel には Python runtime を、sdist には README、仕様書、`skills/worktree-provisioner/SKILL.md`、thin wrapper source を含む。
- source distribution は `LICENSE`、README、仕様書、JSON schema、skill source を含むことを `tar -tzf` で確認した。P10 handoff report 自体は配布物の自己参照を避けるため sdist から除外している。

Hatch の sdist exclude 設定追加後、report 更新前のbuildと、reportのhash/format件数更新後のbuildを比較した。両buildで wheel は `d92eb1b94756ce649e10cab0aeb49d03a18ad848a95df2387249c52c4f951888`、sdist は `73c94fb1bfda73a1ef0da4d023c36cad7772fe752c56c1030b405c2fbd758bb9` となり、report更新による自己参照hash変動は発生しなかった。

管理された一時 session 内の clean virtual environment へ、次のコマンドで `--no-deps` install した。

```text
pip install --force-reinstall --no-deps dist/worktree_provisioner-0.1.0-py3-none-any.whl
```

インストール後の smoke は次の6 commandすべて exit `0` だった。

```text
worktree-provisioner --help
worktree-provisioner --version
worktree-provisioner create --help
worktree-provisioner list --help
worktree-provisioner show --help
worktree-provisioner remove --help
```

出力には top-level の `create`, `list`, `show`, `remove` と version `0.1.0` が含まれた。検証後、一時 virtual environment は codex-tmp の管理コマンドで削除した。

## JSON schema と実行サンプル

JSON の正本は [`docs/json-schema-v1.md`](json-schema-v1.md) である。テストは `tests/integration/test_cli_schema.py`、`tests/unit/test_json_v1.py`、各 command integration suite にある。

macOS 上の clean-install CLI で `list --json` を実行し、stdout に exactly one JSON document、stderr 空、exit `0` を確認した。代表的な envelope は次の形である。

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "list",
  "result": {"worktrees": [{"id": "main", "managed": false, "removable": false}]},
  "error": null,
  "warnings": []
}
```

target を省略した `show --json` は exit `2` で、stdout に次の schema v1 error を1文書だけ返した。

```json
{
  "schema_version": 1,
  "status": "error",
  "operation": "show",
  "result": null,
  "error": {"code": "usage_error", "message": "invalid command-line usage", "details": {"diagnostic": "the following arguments are required: target"}},
  "warnings": []
}
```

## Skill / wrapper

- `uv run pytest tests/integration/test_skill_wrapper.py -q`: PASS、`11 passed`。
- `sh -n skills/worktree-provisioner/scripts/worktree-provisioner`: PASS。
- `quick_validate.py skills/worktree-provisioner`: PASS、`Skill is valid!`。
- ShellCheck はこの macOS 環境にインストールされていなかったため未実行（代替として `sh -n` は実行済み）。
- wrapper は PATH 上の installed CLI の argv/stdout/stderr/exit status を伝播するだけで、Git、root、target、bootstrap、cleanup、task lifecycle のロジックを持たない。

## Static scope / prototype audit

次の production source checks はすべて PASS だった。

```text
no spec_dock_runtime in src/worktree_provisioner
no SPEC_DOCK_WORKTREE_ROOT in src/worktree_provisioner
no /Volumes/990p2t/workspace/worktrees in src/worktree_provisioner
no worktree status/prune/repair, branch delete, github issue, or workbench operation in src/worktree_provisioner
```

`src/worktree_provisioner/core.py` と `tests/test_cli.py` は存在せず、prototype removal check は PASS だった。`rg --files | rg '[A-Z]'` の結果は `README.md`、`LICENSE`、`skills/worktree-provisioner/SKILL.md` の必要な慣例だけであり、追加の不用意な uppercase path はなかった。README の `SPEC_DOCK_WORKTREE_ROOT` 言及は非対応であることを説明する文書上の記述であり、production lookup ではない。

## Safety checklist

- [x] legacy env lookupなし（production source）
- [x] machine root defaultなし
- [x] namespace symlink の create/remove 拒否
- [x] external remove 拒否
- [x] locked worktree は force でも拒否
- [x] default remove は non-force
- [x] force は明示された一回だけ
- [x] remove final refresh あり
- [x] Git failure 後の filesystem cleanup なし
- [x] target-only no-follow cleanup
- [x] branch deletion なし
- [x] bootstrap failure は partial/non-zero/no rollback
- [x] JSON は one-document contract
- [x] skill wrapper は thin
- [x] skill の force authorization は explicit
- [x] Codex task lifecycle mutation なし
- [x] SpecDock repository の変更なし（本P10 laneの変更範囲は本repositoryのみ）

安全シナリオの実行証拠は `tests/scenario_provenance.json` と、`tests/integration/test_cli_contracts.py`、`tests/integration/test_cli_remove.py`、`tests/unit/test_remove.py` に対応する。全体テストは205件 passした。

## Platform evidence と残る制限

- macOS: 上記 full quality、clean wheel install、CLI smoke、JSON samples、skill validation をこの macOS arm64 環境で実行済み。
- Linux: `.github/workflows/ci.yml` に `ubuntu-latest` と Python `3.10`〜`3.13` の matrix、Ruff、Mypy、Pytest、build、installed-wheel smoke を設定済み。ただし、P10 laneでは GitHub Actions を起動・確認していない。Linux が実行済みであるとは扱わない。
- Windows: 初期版の対応対象外。
- package registry publication、tag/release、protected branch 設定変更は未実施であり、計画スコープ外。
- ChatGPT Final Quality Gate Strict は P10 の commit/push 後に primary が実施する。最終 SHA と gate result はこのレポートの後続更新で確定する。

P10 のローカル実装・品質検証ステータスは PASS であるが、repository publication と Final Quality Gate Strict の完了をもって最終提出とする。
