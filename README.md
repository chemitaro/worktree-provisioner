# worktree-provisioner

`worktree-provisioner` は、Git linked worktree を作成・観測・削除する
standalone CLI です。仕様書、タスク、Issue、Workbench を管理する製品ではありません。
初期版の正式な command scope は次の4つです。

- `create`: 管理 root 配下に linked worktree と local branch を作成する
- `list`: Git が保持する全 worktree record を inventory として表示する
- `show`: id、absolute path、または basename で1件を解決して表示する
- `remove`: 管理 namespace 内の1件だけを安全に削除する

対象プラットフォームは macOS と Linux、Python は 3.10 以上です。Windows は初期版の対応対象外です。Python の
runtime dependency はありません。

## Installation

### Wheel または source distribution

リリース作業を伴わず、リポジトリからビルドしてインストールできます。

```bash
uv build
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps dist/worktree_provisioner-*.whl
.venv/bin/worktree-provisioner --version
```

`uv build` が生成する wheel には実行パッケージと `LICENSE` が、source distribution にはプロジェクトの
ドキュメントと Codex skill source も含まれます。公開 repository の特定 revision から tool としてインストールする場合は、
revision を明示してください。

```bash
uv tool install \
  "git+https://github.com/chemitaro/worktree-provisioner.git@<commit-sha>"
```

package registry への公開はこの repository の責務に含めません。

### Codex skill

`skills/worktree-provisioner/` は CLI の Codex skill source です。wheel の Python runtime には含めず、skill として使う場合は
checkout した repository から Codex の skill directory へコピーします。

```bash
skill_root="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$skill_root"
cp -R skills/worktree-provisioner "$skill_root/"
```

skill は PATH 上の `worktree-provisioner` を薄い wrapper 経由で呼びます。skill は Git コマンド、root の注入、JSON の
text parsing、タスク lifecycle の変更を実装しません。skill を導入しなくても CLI は単独で使用できます。

## Root configuration

worktree の配置 root は必須です。指定方法は次の2つだけです。

1. `--root <absolute-path>`
2. `WORKTREE_PROVISIONER_ROOT=<absolute-path>`

`--root` が環境変数より優先されます。machine-specific な既定値はありません。SpecDock の旧環境変数
`SPEC_DOCK_WORKTREE_ROOT` は読み取らず、alias としても受理しません。

通常のローカル環境での設定例です。この path は運用例であり、製品に hard-code された default ではありません。

```bash
export WORKTREE_PROVISIONER_ROOT=/Volumes/990p2t/workspace/worktrees
worktree-provisioner list --repo /path/to/repository
```

`--repo` を省略すると現在の directory を使います。root、repository、namespace が存在しない場合は、製品の安全な
validation に従って作成またはエラーになります。root 自体が directory symlink であることは解決できますが、repository
namespace が symlink の場合は `create` と `remove` を拒否します。

## Layout and naming

main worktree の basename を repository namespace として使い、配置は次の形式です。

```text
<root>/<main-repository-basename>/<main-repository-basename>-<id>
```

label を省略すると id は `wt1`、`wt2`、… と採番されます。label を指定すると最初は label そのもの、衝突時は
`label2`、`label3`、… になります。label は lowercase letters、digits、hyphen のみを受理します。

新しい branch は create を呼び出した checkout の current branch を基点に `<current-branch>-<id>` となります。linked
worktree から呼び出した場合も、namespace と basename は main worktree、branch prefix は呼び出し元 checkout の branch
です。これにより linked checkout を重ねた chained namespace は作りません。

## Commands

### Create

```bash
worktree-provisioner create [label] \
  --repo /path/to/repository \
  --root /path/to/worktrees
```

Git worktree の作成に成功した後、対象 worktree に `Makefile` があり `init` target を検出できる場合は、既定で
`make init` を実行します。これは repository の Makefile に書かれた任意コードを実行するため、信頼できる repository
に対してのみ実行してください。Git checkout hook と filter は抑止されず、`--no-bootstrap` を指定しても実行され得ます。
`--no-bootstrap` は Make の検出・実行だけを無効にします。Makefile の検出時にも repository 管理下の挙動を評価・実行し得るため、
create は trusted repository に対してのみ実行してください。

`make init` の検出または実行が失敗しても、作成済み worktree を自動 rollback しません。path、branch、Git record を残したまま
`status=partial`、終了コード `1`、bootstrap の失敗内容を返します。作成された成果物は利用者が確認・後処理できます。

### List と show

```bash
worktree-provisioner list --repo /path/to/repository --root /path/to/worktrees
worktree-provisioner show <id-or-absolute-path-or-basename> \
  --repo /path/to/repository --root /path/to/worktrees
```

`list` と `show` は管理 namespace 外の worktree も Git inventory として表示しますが、external record は削除対象ではありません。
stable id が優先され、basename が複数候補に一致する場合は候補を含むエラーになります。branch 名を selector として使う
機能はありません。

managed namespace の nested descendant も `list` / `show` では managed record として表示されますが、初期版の remove
対象は namespace 直下の single path component に限られます。nested record は `removable=false`、
`remove_blockers=["nested_target_unsupported"]` となり、`--force` を指定しても削除されません。

### Remove

```bash
worktree-provisioner remove <id-or-absolute-path-or-basename> \
  --repo /path/to/repository --root /path/to/worktrees
```

`remove` は `WTP-THREAT-001` の脅威モデル内で configured managed namespace 直下の single path component worktree
だけを対象にします。main、現在使用中、bare、stale、external、nested、locked、unsafe namespace の record は削除できません。
locked worktree は `--force` でも unlock しません。先に利用者が
`git worktree unlock` を実行し、保護が解除されたことを確認してください。

既定の削除は non-force です。dirty または untracked の変更を Git が拒否した場合、filesystem cleanup は行いません。明示的な
強制削除が必要な場合だけ、同じ command に一度だけ `--force` を指定します。Git の削除成功後に残った target directory のみを
no-follow で処理し、local branch の削除、remote 操作、`prune`、`repair` は行いません。

### Safety threat model

managed namespace の destructive scope 保証は `WTP-THREAT-001` の脅威モデルの範囲です。preflight の
lexical / canonical containment、mutation 直前の inventory refresh、namespace symlink 拒否、descriptor-bound
Git / filesystem operation、no-follow cleanup、mutation 後の containment / identity recheck を組み合わせて、協調的な
tool operation と検出可能な path race を fail-closed または `partial` にします。

同一ユーザーの外部・非協調 process が、最後の check の後かつ Git CLI / kernel syscall の前に managed root / namespace
またはその ancestor inode を rename / replace することを、現在の Git CLI architecture のまま macOS / Linux 共通で
原子的に禁止することはできません。この final syscall window は out of scope であり、atomic prevention や、未検出の
race に対する destructive scope の絶対保証を主張しません。干渉を検出した場合は作成・削除を fail-closed または
`partial` として報告し、保持された成果物を自動 rollback しません。

## Human and agent interfaces

出力の既定は人間向け text です。agent、script、skill は実行ごとに明示的に `--json` を指定してください。TTY の有無で
出力形式は変わりません。

```bash
worktree-provisioner create --repo /path/to/repository \
  --root /path/to/worktrees --json
```

JSON は versioned schema v2 の1文書です。全応答に `schema_version`、`status`、`operation`、`result`、`error`、`warnings`
を含めます。成功は `status=ok`、bootstrap または cleanup の作成済み成果物を伴う失敗は `status=partial`、操作を開始できない
失敗は `status=error` です。正常終了は `0`、operational error/partial は `1`、CLI usage error は `2` です。JSON mode の
expected error は stdout にJSONを1文書だけ出し、stderr は空です。

field、型、nullability、各 command の result payload は [JSON schema v2 reference](docs/json-schema-v2.md) を正本とします。
agent は human text を解析せず、`status` と `error.code`、result 内の absolute path と artifact state を利用してください。
`warnings` は成功・partial・terminal error のすべてで必ず確認し、`collision_partial_artifact` があれば message を解析せず
`facts` の candidate、branch、path、各 existence state をそのまま報告してください。retry 後の後続エラーでも、保持された
partial artifact を success response から隠しません。

## Skill safety boundary

Codex skill は model-invoked skill です。利用者が create または remove の意図と対象を明示した場合にだけ操作します。create は
fact check 後に1回実行し、remove は `show` で対象と blocker を再確認してから1回実行します。曖昧な repository、target、remove
意図の場合は確認を返して実行しません。`--force` は利用者が明示した場合だけ渡します。

skill は create 後に id、branch、absolute path、bootstrap state を報告して終了し、Codex task、Issue、active pointer、
workbench directory を作成・移動しません。wrapper の実装は `command -v worktree-provisioner`、argv のそのままの転送、
stdout/stderr/exit status の転送だけです。

## Deliberate non-scope

次の機能はこの製品にはありません。

- Windows 対応
- SpecDock CLI、旧 JSON、旧環境変数との互換性、SpecDock node の移行・削除
- worktree の `status`、`prune`、`repair`、import、rename、migration
- branch deletion、remote/GitHub 操作、GitHub Issue、Workbench、Codex task lifecycle
- package registry publication、tag/release、protected branch settings

既存 worktree の移動や rename は行いません。4コマンドは Git worktree record と configured root の安全な観測・操作だけを担います。

## Development and verification

開発環境は `uv` を使います。

```bash
uv sync --all-groups --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -q
uv build
git diff --check
```

wheel の clean-install smoke は、生成物の内容と entry point を同時に検証します。

```bash
tmp_venv="$(mktemp -d)/venv"
python3 -m venv "$tmp_venv"
"$tmp_venv/bin/python" -m pip install --no-deps dist/worktree_provisioner-*.whl
"$tmp_venv/bin/worktree-provisioner" --help
"$tmp_venv/bin/worktree-provisioner" --version
```

GitHub Actions は Linux と macOS の Python matrix で Ruff、Mypy、Pytest、build、installed-wheel smoke を実行します。
