---
document: design
product: worktree-provisioner
status: proposed
baseline_repository: chemitaro/spec-dock
baseline_branch: main
baseline_sha: ff09fd05d9862c399d4e22e760170dcb8c46ec6a
verified_at: 2026-08-24
language: ja
---

# worktree-provisioner 設計

## 1. 設計決定サマリー

### DD-001: command family 全体を独立させる

`create` / `list` / `show` / `remove` を一つの bounded capability として移植する。`status` / `prune` / `repair` / branch deletion / Workbench は含めない。

### DD-002: source architecture は縮小して維持する

SpecDock の layered runtime を丸ごと複製せず、worktree-specific contracts / ports / application / adapters / presentation だけを小さな standalone package に保持する。

### DD-003: whole-file copy ではなく bounded copy + symbol extraction

- small cohesive files は import rename 程度で copy する。
- broad shared files は必要 symbol だけを新しい slim file に抽出する。
- CLI registry / dispatch / broad bootstrap は standalone `argparse` wiring に書き直す。

### DD-004: prototype monolith は production baseline にしない

prototype の `core.py` を拡張せず、source parity を保つ module boundary に置換する。packaging scaffold だけを再評価して残す。

### DD-005: compatibility は behavior-first / JSON-versioned

current SpecDock の behavior を baseline とするが、product identity、JSON schema、legacy env、remove force policy は明示的な migration / intentional delta として扱う。

## 2. Architecture overview

```text
argparse CLI
  |
  +--> request construction / repo resolution
  |
  +--> application.worktree
          |
          +--> GitGateway ----------> infra.git_cli ----------> git CLI
          +--> BootstrapGateway ----> infra.make_cli ---------> make CLI (optional)
          +--> EnvironmentGateway --> infra.environment ------> process env
          +--> FilesystemGateway ---> infra.fs_cli -----------> local filesystem
          |
          +--> application.worktree_target
  |
  +--> presentation.cli_text / JSON
```

設計上の責務は次のとおりである。

- CLI:
  - argument parsing
  - `--repo` resolution
  - request construction
  - exit code / stdout / stderr boundary
- application:
  - naming
  - root selection policy
  - collision retry
  - inventory / classification
  - target resolution
  - remove blocker / containment / refresh policy
  - result / error aggregation
- infra:
  - subprocess argv execution
  - Git porcelain parsing
  - `make init` detection / execution
  - filesystem target inspection / removal
  - environment lookup
- presentation:
  - human text
  - schema version `1` JSON
  - product-neutral output values

## 3. Destination directory layout

```text
worktree-provisioner/
├── LICENSE
├── README.md
├── pyproject.toml
├── docs/
│   ├── compatibility.md
│   └── safety.md
├── src/
│   └── worktree_provisioner/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── application/
│       │   ├── __init__.py
│       │   ├── contracts.py
│       │   ├── ports.py
│       │   ├── worktree.py
│       │   └── worktree_target.py
│       ├── infra/
│       │   ├── __init__.py
│       │   ├── environment.py
│       │   ├── fs_cli.py
│       │   ├── git_cli.py
│       │   └── make_cli.py
│       └── presentation/
│           ├── __init__.py
│           └── cli_text.py
└── tests/
    ├── conftest.py
    ├── contract/
    │   ├── test_create.py
    │   ├── test_inventory.py
    │   ├── test_target.py
    │   ├── test_remove.py
    │   └── test_json_schema.py
    ├── integration/
    │   ├── test_cli_create.py
    │   ├── test_cli_inventory.py
    │   ├── test_cli_remove.py
    │   └── test_installed_package.py
    └── parity/
        └── test_spec_dock_baseline.py
```

この layout は source と同じ責務境界を保つが、SpecDock の generic command framework、domain、provider asset mirror を持たない。

## 4. Module and dependency boundaries

### 4.1 `application/contracts.py`

worktree capability に必要な型だけを定義する。

```python
BootstrapStatus = Literal[
    "disabled",
    "skipped",
    "succeeded",
    "failed",
    "detection_failed",
]

WorktreeClassificationReason = Literal[
    "root_valid",
    "root_missing",
    "root_blank",
    "root_invalid",
    "namespace_symlink",
]

WorktreeOrigin = Literal[
    "managed_namespace",
    "external",
    "classification_unavailable",
]
```

主な dataclass:

- `GitWorktreeRecord`
  - `path: Path`
  - `head: str | None`
  - `branch: str | None`
  - `detached: bool`
  - `bare: bool`
  - `locked: bool`
- `BootstrapResult`
  - `status`
  - `command: str | None`
  - `exit_code: int | None`
  - `warnings: list[str]`
- `WorktreeCreateRequest`
  - `label: str | None`
  - `root: str | None`
  - `bootstrap_enabled: bool`
- `WorktreeCreateResult`
  - `id`
  - `main_worktree_path`
  - `container_path`
  - `worktree_path`
  - `branch_name`
  - `bootstrap: BootstrapResult`
  - `warnings`
- `WorktreeListRequest`
  - `root: str | None`
  - `root_explicit: bool`
- `WorktreeShowRequest`
  - `target`
  - root fields
- `WorktreeRemoveRequest`
  - `target`
  - `force`
  - root fields
- `WorktreeRecordView`
- `WorktreeListResult`
- `WorktreeShowResult`
- `WorktreeRemoveResult`
- `ArtifactState`
  - `container_exists: bool | None`
  - `path_exists: bool | None`
  - `branch_exists: bool | None`
  - `record_exists: bool | None`
- `WorktreeCommandError`

`WorktreeCommandError` は expected operation failure を表し、少なくとも次を保持する。

```python
class WorktreeCommandError(RuntimeError):
    code: str
    message: str
    operation: str
    target: str | None
    candidates: list[WorktreeRecordView]
    worktree: WorktreeRecordView | None
    remove_blockers: list[str]
    git_error: str | None
    artifact_state: ArtifactState | None
    attempted_id: str | None
    attempted_path: Path | None
    attempted_branch: str | None
    removed_record: bool | None
    removed_directory: bool | None
    warnings: list[str]
```

expected failure を generic `RuntimeError` text だけにしない。expected operational error は `WorktreeCommandError` として処理する。unexpected exception は expected error に偽装せず、installed CLI では detail を露出しない `internal_error` JSON/text と exit `1` を返す。test では underlying exception を直接検証できる adapter / use-case boundary を維持する。

### 4.2 `application/ports.py`

SpecDock の broad `Ports` を copy せず、次だけを定義する。

```python
class GitGateway(Protocol):
    def current_branch_or_none(self, repo_root: Path) -> str | None: ...
    def local_branch_exists(self, repo_root: Path, branch: str) -> bool: ...
    def check_ref_format_branch(self, repo_root: Path, branch: str) -> bool: ...
    def worktree_list(self, repo_root: Path) -> list[GitWorktreeRecord]: ...
    def add_worktree_with_new_branch(self, repo_root: Path, *, path: Path, branch: str) -> None: ...
    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None: ...

class BootstrapGateway(Protocol):
    def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult: ...

class EnvironmentGateway(Protocol):
    def getenv(self, name: str) -> str | None: ...

class FilesystemGateway(Protocol):
    def path_exists(self, path: Path) -> bool: ...
    def remove_target(self, path: Path) -> None: ...

@dataclass(frozen=True)
class Ports:
    repo_root: Path
    git_gateway: GitGateway
    bootstrap_gateway: BootstrapGateway
    environment_gateway: EnvironmentGateway
    filesystem_gateway: FilesystemGateway
```

- `node_reader` dummy dependencyを持ち込まない。
- optional `None` gateway を多数持つ SpecDock pattern を持ち込まず、standalone runtime で必須 adapter を構築してから use case を呼ぶ。
- test は fake gateway を明示的に注入する。

### 4.3 `application/worktree.py`

次の public use case を持つ。

```python
worktree_create(req: WorktreeCreateRequest, ports: Ports) -> WorktreeCreateResult
worktree_list(req: WorktreeListRequest, ports: Ports) -> WorktreeListResult
worktree_show(req: WorktreeShowRequest, ports: Ports) -> WorktreeShowResult
worktree_remove(req: WorktreeRemoveRequest, ports: Ports) -> WorktreeRemoveResult
```

private helper は source の分割を基本的に維持する。

- label / candidate:
  - `_normalize_label`
  - `_candidate_id`
  - `_preflight_collision`
  - `_is_retryable_worktree_add_error`
- root:
  - `_resolve_worktree_root`
  - `_validate_worktree_root`
  - `_worktree_classification_context`
- inventory:
  - `_build_inventory`
  - `_build_inventory_from_records`
  - `_raw_worktree_id`
  - `_is_managed_path`
  - `_worktree_origin`
- remove:
  - `_remove_blockers`
  - `_non_bypassable_remove_blockers`
  - `_guard_remove_containment`
  - `_protected_cleanup_paths`
- diagnostics:
  - `_artifact_state`
  - `_canonical_path`

root resolver は explicit value と env source を区別する。`list` / `show` / `remove` で explicit invalid `--root` は fatal、env invalid は classification diagnostic とするため、単純な `getenv` 一つだけではなく次の内部 value を用いる。

`create` は resolved namespace (`<root>/<repo-basename>`) が symlink の場合に `invalid_root` で拒否する。current source の inventory は `namespace_symlink` を unavailable とするが create に dedicated reject guard がないため、この追加は `SEC-DEC-003` の intentional safety delta である。

```python
@dataclass(frozen=True)
class RootSelection:
    raw_value: str | None
    source: Literal[
        "explicit",
        "worktree_provisioner_env",
        "spec_dock_legacy_env",
        "missing",
    ]
    explicit: bool
```

### 4.4 `application/worktree_target.py`

source の `resolve_worktree_target` を import rename だけで維持する。

resolution order:

1. exact stable id
2. absolute path canonical match
3. basename
4. branch-only diagnostic
5. not found

exact id を basename ambiguity より優先する behavior を変更しない。

### 4.5 `infra/git_cli.py`

stdlib `subprocess` の薄い adapter とする。runtime dependency を追加しない。

必要 function:

- `_ensure_git_available`
- `resolve_repo_root(path: Path) -> Path`
- `current_branch_or_none`
- `local_branch_exists`
- `check_ref_format_branch`
- `worktree_list`
- `add_worktree_with_new_branch`
- `remove_worktree`
- `_parse_worktree_porcelain`

非採用 function:

- `require_clean_working_tree`
- `current_head_or_none`
- `status_short_or_none`
- `checkout_branch`
- `create_and_checkout_branch`
- GitHub remote URL / publication functions

`resolve_repo_root`:

- `--repo` を `expanduser` する。
- directory であることを確認する。
- `git rev-parse --show-toplevel` を実行し、absolute invocation checkout root を返す。
- bare repository は初期版の invocation target としない。
- `subprocess` の `FileNotFoundError` / `OSError` を stable `repository_unavailable` / `git_unavailable` error へ変換する。

`_parse_worktree_porcelain` は `detached` / `bare` / `locked` を保持する。prototype parser のようにこれらを捨てない。

`remove_worktree` の provisional implementation:

```python
cmd = ["git", "worktree", "remove"]
if force:
    cmd.append("--force")
cmd.append(str(path))
```

current SpecDock の `--force --force` は copy しない。`SEC-DEC-001` が strict parity を選択した場合だけ adapter と contract test を切り替える。

### 4.6 `infra/make_cli.py`

source file は import path rename 以外ほぼそのまま利用できる。

- `make` unavailable -> `detection_failed`
- `make -n init` target missing -> `skipped`
- other detection error -> `detection_failed`
- `make init` zero -> `succeeded`
- non-zero -> `failed`

`--no-bootstrap` は application layer で `BootstrapResult(status="disabled", ...)` を作り、adapter を呼ばない。

Security note:

- `make -n` は recipe execution を抑制するが、Makefile parse / expansion 自体を trust-free にする保証ではない。
- automatic bootstrap は checked-out repository code を実行する operation と説明する。
- arbitrary `cwd` を受け取る public bootstrap command にしない。

### 4.7 `infra/fs_cli.py`

source file 全体は Workbench-specific hardened copy implementation を大量に含むため copy しない。次だけを抽出する。

- `path_exists`
- `remove_tree`
- `remove_target`

`remove_target` contract:

- `lstat` で type を判定する。
- symlink / regular file -> `unlink`
- directory -> `shutil.rmtree`
- FIFO / device / socket 等 -> unsupported error
- symlink target を follow しない。
- missing / race / permission は content-bearing path context を含む stable operation error にする。

### 4.8 `infra/environment.py`

```python
@dataclass(frozen=True)
class OsEnvironmentGateway:
    def getenv(self, name: str) -> str | None:
        return os.environ.get(name)
```

root precedence 自体は application に置く。infra は policy を持たない。

### 4.9 `presentation/cli_text.py`

SpecDock `CliText` type を copy せず、standalone の小さな rendering contract を用いる。

```python
@dataclass(frozen=True)
class RenderedOutput:
    stdout: str
    stderr: str
```

必要 renderer:

- `render_worktree_create_text`
- `render_worktree_list_text`
- `render_worktree_show_text`
- `render_worktree_remove_text`
- `render_worktree_error_text`
- `render_worktree_create_json`
- `render_worktree_list_json`
- `render_worktree_show_json`
- `render_worktree_remove_json`
- `render_worktree_error_json`
- `_worktree_payload`

JSON は `json.dumps(..., ensure_ascii=False, indent=2)` を用い、Path は explicit string conversion する。`dataclasses.asdict` に任せた後で一部 Path だけ変換する prototype pattern は、schema drift を見落とすため採用しない。

### 4.10 `cli.py`

SpecDock `CommandSpec` / registry / UseCases / dispatch を持ち込まない。

- parser construction
- shared options の各 subcommand への binding
- repo resolution
- concrete Ports wiring
- use case invocation
- renderer selection
- output / exit code

だけを持つ。

create / list / show / remove の branch を巨大な generic framework にしない。CLI function は次の形で十分である。

```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo_root = resolve_repo_root(args.repo)
        ports = build_ports(repo_root)
        result = dispatch_known_command(args, ports)
    except WorktreeCommandError as exc:
        return emit_expected_error(exc, as_json=args.json)
    return emit_success(result, as_json=args.json)
```

`argparse` usage error は標準 exit `2` を維持する。

## 5. Canonical CLI contract

### 5.1 Commands

```bash
worktree-provisioner create [LABEL] [--repo PATH] [--root PATH] [--no-bootstrap] [--json]
worktree-provisioner list [--repo PATH] [--root PATH] [--json]
worktree-provisioner show <target> [--repo PATH] [--root PATH] [--json]
worktree-provisioner remove <target> [--repo PATH] [--root PATH] [--force] [--json]
```

### 5.2 Option semantics

| option | commands | default | semantics |
| --- | --- | --- | --- |
| `LABEL` | create | `None` | `^[a-z0-9-]+$` |
| `target` | show/remove | required | stable id / absolute path / basename |
| `--repo PATH` | all | `.` | checkout root または checkout 内 path |
| `--root PATH` | all | env resolution | create placement / inventory classification |
| `--no-bootstrap` | create | false | `make init` adapter を呼ばず `disabled` |
| `--force` | remove | false | provisional single Git force |
| `--json` | all | false | schema version 1 JSON |

### 5.3 Environment contract

```text
WORKTREE_PROVISIONER_ROOT
SPEC_DOCK_WORKTREE_ROOT  # deprecated migration compatibility
```

selection algorithm:

```text
if --root was provided:
    select it, even if blank -> validate/fail
else if WORKTREE_PROVISIONER_ROOT exists:
    select it, even if blank -> required/blank diagnostic
else if SPEC_DOCK_WORKTREE_ROOT exists:
    select it and add legacy warning
else:
    missing
```

### 5.4 Exit / stream contract

| mode | success | expected error | warning |
| --- | --- | --- | --- |
| text | stdout, exit 0 | stderr, exit 1 | stderr; success remains 0 |
| JSON | one JSON on stdout, exit 0 | one JSON on stdout, exit 1 | `warnings[]` in JSON; no human duplicate |
| argparse | help stdout / usage stderr | exit 2 | N/A |

## 6. Directory and naming design

### 6.1 Main / current distinction

- `repo_root`: invocation checkout root resolved from `--repo`
- `main_worktree_path`: first/main Git worktree record
- `repo_basename`: `main_worktree_path.name`
- `current branch`: branch of invocation checkout

linked worktree invocation example:

```text
main worktree: /repos/example
invocation checkout: /worktrees/example/example-outer
current branch: main-outer
root: /worktrees

namespace: /worktrees/example
new path: /worktrees/example/example-inner
new branch: main-outer-inner
```

### 6.2 Candidate generation

```python
id = f"wt{index}"                    # no label
id = label if index == 1 else f"{label}{index}"
path = root / repo_basename / f"{repo_basename}-{id}"
branch = f"{current_branch}-{id}"
```

### 6.3 Collision order

1. record path collision
2. filesystem path collision
3. local branch collision
4. generated ref validation
5. mkdir namespace
6. `git worktree add`

recognized Git collision fragments は baseline と同じ最小 set を初期値とする。

```text
already exists
is already checked out
a branch named
```

message matching は Git version / locale に依存するため、classifier を一箇所に閉じ込め、unknown message は retry しない。

## 7. Bootstrap design

### 7.1 State model

| status | command | exit_code | fatal |
| --- | --- | --- | --- |
| `disabled` | `None` | `None` | no |
| `skipped` | `None` | `None` | no |
| `succeeded` | `make init` | `0` | no |
| `failed` | `make init` | non-zero | no |
| `detection_failed` | `make -n init` | non-zero / `None` | no |

### 7.2 Flow

```text
create succeeds
  |
  +-- --no-bootstrap --> disabled
  |
  +-- make unavailable --> detection_failed warning
  |
  +-- make -n init
        |
        +-- missing target --> skipped
        +-- other failure --> detection_failed warning
        +-- success --> make init
                         |
                         +-- success --> succeeded
                         +-- failure --> failed warning
```

bootstrap failure は create transaction の rollback trigger ではない。

## 8. Inventory and target model

### 8.1 Record parsing

`git worktree list --porcelain` の block ごとに次を読む。

- `worktree <path>`
- `HEAD <sha>`
- `branch <ref>`
- `detached`
- `bare`
- `locked [reason]`

branch `refs/heads/` prefix は除去する。

### 8.2 Classification

`managed` 判定:

```text
classification available
AND namespace is not a symlink
AND canonical(record.path) is a strict descendant of canonical(namespace)
```

namespace 自体は managed worktree としない。

`origin`:

```text
classification unavailable -> classification_unavailable
managed=true             -> managed_namespace
otherwise                -> external
```

### 8.3 Stable id

- main -> `main`
- managed basename `<repo>-<suffix>` -> `<suffix>`
- external -> basename
- duplicate -> canonical path sort に基づく `~N`

inventory output order は Git record order を維持し、id disambiguation だけ canonical sort を使う。

### 8.4 Target resolver

id match が一つなら即採用する。これにより、同じ文字列が別 record の basename でも stable id が優先される。basename ambiguity は candidate payload を返す。branch name は便利でも destructive ambiguity を生むため target にしない。

## 9. Remove design

### 9.1 Eligibility versus Git removability

`removable=true` は hard application blocker がないことを示す。dirty / locked 等による実際の Git success を保証しない。

hard blockers:

```text
main_worktree
current_worktree
bare_worktree
path_missing
record_missing
protected_cleanup_path
```

`managed=false` / `origin=external` は blocker ではない。

### 9.2 Sequence

```text
1. inventory
2. resolve target
3. hard blocker check
4. Git records refresh
5. re-resolve same target
6. canonical path identity check
7. hard blocker re-check
8. containment guard
9. git worktree remove [--force] <path>
10. containment guard re-check
11. if lstat target exists: remove target only
12. return removed_record=true, removed_directory=true
```

Git step 9 が失敗したら step 10 以降へ進まない。

### 9.3 Containment invariants

cleanup target が次のいずれかなら拒否する。

- repo root
- main worktree
- central root
- namespace
- central root / namespace を包含する ancestor
- protected symlink namespace 配下の lexical target

cleanup は target path の type を `lstat` で再確認し、symlink target を follow しない。

### 9.4 Force policy delta

current SpecDock:

```text
application always force=True
adapter emits --force --force
CLI --force is compatibility no-op
```

proposed standalone:

```text
no flag -> no Git force
--force -> one Git --force
locked -> Git refusal / manual git worktree unlock
```

この差は実装 typo ではなく security decision として test / docs / migration matrix に固定する。

## 10. JSON interface

### 10.1 Common envelope

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "create",
  "result": {},
  "warnings": []
}
```

error:

```json
{
  "schema_version": 1,
  "status": "error",
  "operation": "remove",
  "error": {
    "code": "remove_blocked",
    "message": "worktree remove blocked",
    "target": "main",
    "remove_blockers": ["main_worktree"]
  },
  "warnings": []
}
```

rules:

- `schema_version` は integer。
- success は `result` のみ、error は `error` のみ。
- missing optional value は `null`。field 自体を case ごとに不規則に消さない。
- path は absolute string。
- warnings は strings の array とし、初期 extraction で unnecessary warning object model を追加しない。
- error message は human detail、consumer は `code` を使う。

### 10.2 Create success

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "create",
  "result": {
    "id": "wt1",
    "main_worktree_path": "/repos/example",
    "container_path": "/worktrees/example",
    "worktree_path": "/worktrees/example/example-wt1",
    "branch_name": "main-wt1",
    "bootstrap": {
      "status": "skipped",
      "command": null,
      "exit_code": null
    }
  },
  "warnings": []
}
```

### 10.3 Worktree payload

```json
{
  "id": "feature",
  "path": "/worktrees/example/example-feature",
  "basename": "example-feature",
  "branch": "main-feature",
  "head": "0123456789abcdef",
  "detached": false,
  "bare": false,
  "locked": false,
  "managed": true,
  "managed_classification_available": true,
  "classification_reason": "root_valid",
  "origin": "managed_namespace",
  "main": false,
  "current": false,
  "path_exists": true,
  "record_exists": true,
  "removable": true,
  "remove_blockers": []
}
```

### 10.4 List success

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "list",
  "result": {
    "worktrees": []
  },
  "warnings": []
}
```

### 10.5 Show success

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "show",
  "result": {
    "target": "feature",
    "worktree": {}
  },
  "warnings": []
}
```

### 10.6 Remove success

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "remove",
  "result": {
    "target": "feature",
    "resolved_target": {},
    "removed_record": true,
    "removed_directory": true,
    "branch_deleted": false
  },
  "warnings": []
}
```

### 10.7 Create partial failure

```json
{
  "schema_version": 1,
  "status": "error",
  "operation": "create",
  "error": {
    "code": "git_worktree_add_failed",
    "message": "git worktree add failed",
    "attempted_id": "wt1",
    "attempted_path": "/worktrees/example/example-wt1",
    "attempted_branch": "main-wt1",
    "artifact_state": {
      "container_exists": true,
      "path_exists": false,
      "branch_exists": true,
      "record_exists": false
    },
    "git_error": "..."
  },
  "warnings": []
}
```

### 10.8 JSON compatibility rules

- new optional field addition: schema `1` のまま可能
- existing field removal / rename / type change: schema major change
- error code meaning change: schema major change
- unexpected exception は `code=internal_error`、generic message、operation context のみを返し、raw traceback / environment / credential-bearing Git output を JSON に入れない
- enum value change: schema major change
- text output wording change: JSON compatibility versionとは独立

## 11. Error and warning design

### 11.1 Stable error codes

| code | operation | mutation boundary |
| --- | --- | --- |
| `invalid_label` | create | before mutation |
| `root_required` | create | before mutation |
| `invalid_root` | all | before worktree mutation |
| `repository_unavailable` | all | before mutation |
| `git_unavailable` | all | before mutation |
| `detached_head` | create | before mutation |
| `git_worktree_list_failed` | all | before operation mutation |
| `candidate_exhausted` | create | no successful add |
| `container_create_failed` | create | container may be partial |
| `git_worktree_add_failed` | create | partial branch/path/record possible |
| `target_not_found` | show/remove | before remove |
| `ambiguous_target` | show/remove | before remove |
| `unsupported_branch_target` | show/remove | before remove |
| `remove_blocked` | remove | before Git remove |
| `git_worktree_remove_failed` | remove | no filesystem cleanup |
| `post_remove_cleanup_failed` | remove | record removed, path may remain |
| `internal_error` | all | unexpected failure; mutation state is not inferred |

### 11.2 Warning strings

initial extraction では existing simple `list[str]` を維持する。minimum warning categories:

- `legacy environment variable used: SPEC_DOCK_WORKTREE_ROOT`
- `make init detection failed: ...`
- `make init failed: ...`

将来 warning code object を導入する場合は JSON schema change policy を適用する。

## 12. Exact source-to-destination mapping

### 12.1 Source implementation mapping

| source path / symbol | destination | action | rationale |
| --- | --- | --- | --- |
| `.../application/worktree.py` `worktree_create` | `application/worktree.py` | lightly copy | naming / collision / partial state の proven logic |
| same `worktree_list`, `_build_inventory*` | same | lightly copy | Git record source-of-truth / classification |
| same `worktree_show` | same | lightly copy | target resolver integration |
| same `worktree_remove` | same | copy then policy edit | refresh / guard / Git-first cleanup。force semantics は intentional delta |
| same root helpers | same | copy then generalize | env name / explicit root precedence を standalone 化 |
| same `_artifact_state` | same | copy then structure | string だけでなく `ArtifactState` を返す |
| `.../application/worktree_target.py` `resolve_worktree_target` | `application/worktree_target.py` | near whole-file copy | standalone で閉じた pure resolver |
| `.../application/contracts.py` `GitWorktreeRecord` | `application/contracts.py` | symbol extraction | broad SpecDock contracts を除外 |
| same `BootstrapResult`, `BootstrapStatus` | same | symbol extraction + `disabled` | bootstrap contract |
| same `Worktree*Request/Result/View` | same | symbol extraction / normalize | family contract |
| same `WorktreeCommandError` | same | symbol extraction + create fields | stable JSON error |
| `.../application/ports.py` worktree methods in `GitGateway` | `application/ports.py` | symbol extraction | broad gateway methods を除外 |
| same `BootstrapGateway` | same | copy | exact responsibility |
| same `EnvironmentGateway` | same | copy | exact responsibility |
| same worktree methods in `FilesystemGateway` | same | symbol extraction | Workbench methods を除外 |
| same `Ports` | same | rewrite slim | dummy `node_reader` / optional broad fields を除外 |
| `.../infra/git_cli.py` worktree functions | `infra/git_cli.py` | symbol extraction | remote / checkout helpers を除外 |
| same `_parse_worktree_porcelain` | same | near copy | detached/bare/locked を保持 |
| `.../infra/make_cli.py` | `infra/make_cli.py` | near whole-file copy | worktree-specific small adapter |
| `.../infra/fs_cli.py` `path_exists`, `remove_tree`, `remove_target` | `infra/fs_cli.py` | symbol extraction | Workbench hardened copy code は非対象 |
| `.../commands/worktree.py` args / renderer selection | `cli.py` | contract reference only | `CommandSpec` / `UseCases` dependencyを除外 |
| `.../presentation/cli_text.py` worktree renderer functions | `presentation/cli_text.py` | symbol extraction + rewrite | prefix / JSON schema / no `CliText` dependency |
| `.../cli/parser.py` worktree parser branch | `cli.py` | rewrite | broad parser 非再利用 |
| `.../cli/registry.py` | none | do not copy | standalone family に registry 不要 |
| `.../cli/bootstrap.py` `_GitGateway` etc. | `cli.py` / infra adapters | rewrite minimal | broad runtime wiring 非再利用 |
| `.../cli/dispatch.py` | `cli.py` | rewrite minimal | broad warning / command dispatch 非再利用 |
| `.../docs/reference_worktree.md` worktree sections | `README.md`, `docs/*` | extract / rewrite | Workbench section と SpecDock prefix を除外 |
| `tests/cli_runtime/test_worktree.py` scenarios | destination tests | port scenario-by-scenario | SpecDock harness / asset assertions を除外 |
| `application/workbench.py` dependency on worktree inventory | no destination copy | evidence only | later SpecDock removal sequencing に必要 |

`...` は `src/spec_dock/assets/spec_dock/scripts/spec_dock_runtime` を表す。

### 12.2 Non-reusable source symbols

次は destination に持ち込まない。

- `UseCases`
- `CommandSpec`, `CommandArgs`, `CommandOutcome`
- `CliText`
- Node / active / deps / artifact / GitHub gateway
- provider asset / dogfooding mirror update mechanism
- `workbench_copy` と Workbench filesystem protocol
- GitHub remote publication helpers
- SpecDock validation / sync command

### 12.3 Prototype mapping

| prototype | disposition | details |
| --- | --- | --- |
| `README.md` | rewrite | family、JSON、safety、migration を反映 |
| `pyproject.toml` | retain with edits | name/version/Python/Hatchling/entrypoint は維持。Mypy、strict pytest markers、license file を追加 |
| `src/worktree_provisioner/core.py` | delete after replacement | monolith / create-only / incomplete parser |
| `src/worktree_provisioner/cli.py` | rewrite | family / stable JSON / errors / shared options |
| `tests/test_cli.py` | split and replace | scenario reuse は可、coverage contract は不足 |
| unlisted prototype files | inspect before action | attachmentにないため存在 / content を推測しない |

## 13. Safety invariants

### INV-001

main checkout 内に standard worktree container を作らない。

### INV-002

namespace / repo basename は main worktree record を基準にし、branch prefix は current checkout を基準にする。

### INV-003

invalid label / root / repo / detached HEAD は Git mutation 前に停止する。

### INV-004

retry は recognized collision に限定し、unknown Git failure を握りつぶさない。

### INV-005

bootstrap failure は create success を rollback しないが、status / warning を隠さない。

### INV-006

inventory は Git records を正本とし、configured root は classification context に限定する。

### INV-007

unmanaged / external classification は remove blocker にしない。

### INV-008

main/current/bare/stale/record-missing/protected cleanup path は force でも削除しない。

### INV-009

remove は final refresh / re-resolution 後にのみ Git mutation を行う。

### INV-010

Git remove failure 後に filesystem cleanup を行わない。

### INV-011

post-remove cleanup は target-only、`lstat`、no symlink follow とする。

### INV-012

branch deletion、prune、repair、orphan cleanup を副作用にしない。

### INV-013

subprocess は argv list、`shell=False` とする。

### INV-014

secret / env file を tool 独自に copy しない。

### INV-015

JSON expected response は一つの document とし、human output を混在させない。

## 14. Alternatives and rejected options

### ALT-001: create-only tool

**Rejected.**

- lifecycle ownership が分割される。
- agent safety surface が SpecDock に残る。
- later extraction で同じ contracts / parser / tests を再度移動する。

### ALT-002: SpecDock runtime package を dependency にする

**Rejected.**

- independent repository にならない。
- SpecDock simplification と逆方向。
- broad domain / asset / command dependencies を引き込む。

### ALT-003: source runtime directory を丸ごと copy

**Rejected.**

- contracts / ports / CLI / presentation が broad SpecDock concerns と混在する。
- provider / dogfooding duplicate distribution まで持ち込む。
- maintenance cost が不必要に増える。

### ALT-004: prototype `core.py` を拡張する

**Rejected.**

- source family の inventory / target / remove safety が欠落する。
- porcelain parser が safety flags を捨てる。
- source parity test より先に rewrite した create logic を正本にしてしまう。

### ALT-005: shell script へ単純化する

**Rejected.**

- structured result、JSON、partial state、fake gateway test、remove guard の testability を失う。
- current source の proven layer boundary より危険。

### ALT-006: SpecDock が external CLI を即時 call-through する

**Rejected for tool delivery.**

- tool の独立 delivery と SpecDock migration が結合する。
- installation availability / version negotiation が必要になる。
- later separate migration task で必要性を判断する。

### ALT-007: Workbench も同時に移す

**Rejected.**

Workbench は SpecDock scope / node layout / non-canonical artifact policy に依存する。worktree provisioning の独立 boundary ではない。

## 15. Contradictions and stale documentation handling

### 15.1 Remove semantics

historical design は normal / force を区別するが、current code / shipped docs は default force-equivalent である。source parity baseline は current behavior として認識し、standalone では `SEC-DEC-001` により intentional delta を明示する。namespace symlink 経由 create の拒否も `SEC-DEC-003` として parity matrix に明示する。

### 15.2 Epic report lifecycle

`report.md` の `in_progress` / pending PR は current main の implementation presence と一致しない。implementation source / test / shipped docs を current authority とし、report を status authority にしない。

### 15.3 Test name drift

report の sibling-container test 名は current central-root test 名と一致しない。destination test mapping は実在する source test symbol を使う。

### 15.4 Workbench co-location

`reference_worktree.md` の Workbench section は worktree tool scope ではない。さらに `application/workbench.py` は worktree inventory / resolver を import しているため、later SpecDock removal では dependency cut を先に行う。

## 16. Later removal from SpecDock

独立 tool の delivery 後、別 task で次を行う。

1. worktree-provisioner の versioned release / install path / migration docs を確定する。
2. SpecDock consumer / agent instruction を new CLI へ移行する。
3. `application/workbench.py` の fate を決める。
   - Workbench を残す場合:
     - read-only Git worktree inventory / resolver を neutral SpecDock internal module に分離する。
     - create / remove capability と CLI wiring だけを削除可能にする。
   - Workbench を削除する場合:
     - worktree inventory dependency と同じ migration で除去する。
4. SpecDock の `worktree` parser / registry / commands / use case wiring / renderer / docs / tests を separate PR で削除する。
5. shared `contracts.py` / `ports.py` / `git_cli.py` / `fs_cli.py` は remaining consumer を確認して symbol 単位で縮小する。
6. historical Epic docs は history として保持し、current reference / README だけを更新する。
7. migration verification 後にのみ `SPEC_DOCK_WORKTREE_ROOT` compatibility の終了を検討する。

worktree-provisioner repository の initial delivery は上記 1-7 の完了を待たない。
