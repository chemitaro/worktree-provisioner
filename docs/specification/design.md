---
document: design
product: worktree-provisioner
status: proposed
verified_repository: chemitaro/worktree-provisioner
verified_branch: main
verified_sha: 18c80a1f222a31df0617df5c8193388b3c301e0e
owner_decision_source: docs/interview.md
owner_decision_status: complete
verified_at: 2026-08-24
language: ja
---

# worktree-provisioner 設計

## 1. 設計サマリー

`worktree-provisioner` は、4つの Git worktree operation を一つの小さな Python CLI package として実装する。SpecDock の broad runtime framework は複製せず、worktree-specific contracts、application service、Git/make/filesystem adapters、text/JSON presentation に限定する。

設計上の中心は次の5点である。

1. **単一 command family**: `create`, `list`, `show`, `remove` を同じ inventory / target / namespace model で扱う。
2. **管理境界**: inventory は同一 repository の外部 worktree も見せるが、`WTP-THREAT-001` の脅威モデル内で remove は configured managed namespace 内だけを変更する。
3. **partial first-class**: bootstrap failure と post-remove cleanup failure は complete success にせず、成果物を残した `status=partial` と non-zero で返す。
4. **agent contract**: default text と versioned JSON を分離し、agent は明示的な `--json` を使う。
5. **skill thinness**: skill は authorization / fact check / result interpretation だけを持ち、PATH 上の installed CLI を thin wrapper で呼ぶ。

## 2. Authority boundary

- planning baseline: `chemitaro/worktree-provisioner@18c80a1f222a31df0617df5c8193388b3c301e0e`
- owner decisions: `docs/interview.md` (`status: complete`)
- current code: create-only prototype
- SpecDock source/tests: provenance と regression scenario の参照元
- implementation status: 未実装

既存 prototype の module structure と behavior は design authority ではない。特に legacy env lookup、bootstrap failure exit `0`、create-only CLI、generic JSON error、worktree flags を失う parser は置換する。

## 3. Fixed decisions versus local implementation choices

### 3.1 Fixed external decisions

次は implementation が変更できない。

- 4 command scope
- default text / explicit versioned JSON
- root source は `--root` / `WORKTREE_PROVISIONER_ROOT` のみ
- label / id / branch naming
- automatic `make init` と `--no-bootstrap`
- bootstrap failure = retained worktree + exit `1` + JSON `partial`
- default non-force / explicit single force
- locked worktree は force でも不可
- external inventory observable / external remove forbidden
- remove eligibility is limited to a single path component directly under the configured managed namespace; nested descendants remain observable and managed-classified but are blocked with `nested_target_unsupported`
- namespace symlink は create/remove forbidden
- macOS/Linux only
- skill authorization rules、thin wrapper、no task lifecycle mutation
- no SpecDock migration phase

### 3.2 Local implementation choices

次は contract を維持する限り変更可能である。

- file-level module split
- internal class/function names
- retryable Git message classifier の実装
- path race guard の descriptor 使用有無
- exact test helper implementation
- skill source packaging path

本設計は implementation-ready な reference layout を示すが、上記 local choice の合理的な簡素化は許容する。

### 3.3 `WTP-THREAT-001` Namespace ancestor race boundary

managed namespace の destructive scope は、管理 root / namespace を critical window に rename・replace しない
協調的な tool operation を前提にする。実装は preflight の lexical / canonical containment、mutation 直前の Git
inventory refresh、namespace symlink / no-follow guard、利用可能な場合の descriptor-bound Git / filesystem operation、
mutation 後の containment / identity recheck を組み合わせて race を縮小する。

これは atomic authorization ではない。現在の Git CLI argv architecture のままでは、同一ユーザーの外部・非協調 process が
最後の bound check の後かつ Git CLI / kernel syscall の前に managed root / namespace またはその ancestor inode を
rename・replace することを、macOS / Linux 共通の非特権 primitive で禁止できない。`WTP-THREAT-001` はこの final
syscall window を out of scope とし、atomic prevention を主張しない。干渉を mutation 前に検出した場合は fail-closed、
Git 後の recheck で検出した場合は partial とし、非協調 process の未検出 race について destructive scope の絶対保証を
与えない。

## 4. Reference architecture

```text
CLI / argparse
  |
  +-- repository + root resolution
  +-- request construction
  +-- output mode / exit boundary
  |
  v
application.worktree_service
  |
  +-- create naming / collision / bootstrap aggregation
  +-- list inventory / classification / stable ids
  +-- show target resolution
  +-- remove blockers / refresh / containment / cleanup orchestration
  |
  +--> GitGateway ---------> infra.git_cli ---------> git CLI
  +--> BootstrapGateway ---> infra.make_cli --------> make CLI
  +--> FilesystemGateway --> infra.filesystem ------> local filesystem
  +--> EnvironmentGateway -> infra.environment -----> process env
  |
  v
presentation
  +-- text renderer
  +-- JSON schema v1 renderer

Codex skill
  +-- SKILL.md: intent, authorization, fact checks, reporting
  +-- thin wrapper: exec worktree-provisioner "$@"
```

Dependency direction:

- application は infra concrete implementation を import しない。
- infra は application contracts / protocols を実装する。
- presentation は result/error contracts だけを読む。
- CLI は concrete adapters を wire する composition root である。
- skill は Python modules を import せず installed executable だけを呼ぶ。

## 5. Reference repository layout

```text
worktree-provisioner/
├── LICENSE
├── README.md
├── pyproject.toml
├── src/
│   └── worktree_provisioner/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── application/
│       │   ├── __init__.py
│       │   ├── contracts.py
│       │   ├── ports.py
│       │   ├── worktree_service.py
│       │   └── target_resolver.py
│       ├── infra/
│       │   ├── __init__.py
│       │   ├── environment.py
│       │   ├── filesystem.py
│       │   ├── git_cli.py
│       │   └── make_cli.py
│       └── presentation/
│           ├── __init__.py
│           ├── json_v1.py
│           └── text.py
├── skills/
│   └── worktree-provisioner/
│       ├── SKILL.md
│       └── scripts/
│           └── worktree-provisioner
├── tests/
│   ├── unit/
│   │   ├── test_contracts.py
│   │   ├── test_git_porcelain.py
│   │   ├── test_root_and_naming.py
│   │   ├── test_create.py
│   │   ├── test_inventory.py
│   │   ├── test_target_resolver.py
│   │   ├── test_remove.py
│   │   ├── test_make_cli.py
│   │   ├── test_filesystem.py
│   │   └── test_json_v1.py
│   ├── integration/
│   │   ├── test_cli_create.py
│   │   ├── test_cli_list_show.py
│   │   ├── test_cli_remove.py
│   │   ├── test_installed_package.py
│   │   └── test_skill_wrapper.py
│   └── fixtures/
│       └── fake-worktree-provisioner
└── .github/
    └── workflows/
        └── ci.yml
```

この layout は推奨であり、例えば `worktree_service.py` を `create.py`, `inventory.py`, `remove.py` に分割してもよい。ただし broad generic framework や SpecDock package を導入してはならない。

## 6. Core contracts

### 6.1 Value types

```python
BootstrapStatus = Literal[
    "disabled",
    "skipped",
    "succeeded",
    "failed",
    "detection_failed",
]

ResponseStatus = Literal["ok", "partial", "error"]

ClassificationReason = Literal[
    "root_valid",
    "namespace_symlink",
]

WorktreeOrigin = Literal[
    "managed_namespace",
    "external",
    "classification_unavailable",
]
```

root missing / blank / invalid は command-level error であり、record classification reason にはしない。namespace symlink は list/show が inventory を維持するための unavailable reason として残す。

### 6.2 Git record

```python
@dataclass(frozen=True)
class GitWorktreeRecord:
    path: Path
    head: str | None
    branch: str | None
    detached: bool = False
    bare: bool = False
    locked: bool = False
    lock_reason: str | None = None
```

`git worktree list --porcelain` parser は final blank line の有無に依存せず、`detached`, `bare`, `locked [reason]` を保持する。

### 6.3 Bootstrap result

```python
@dataclass(frozen=True)
class BootstrapResult:
    requested: bool
    status: BootstrapStatus
    command: tuple[str, ...] | None
    exit_code: int | None
    detail: str | None
```

- `disabled`: `requested=false`, command/exit null
- `skipped`: `requested=true`, command/exit null
- `succeeded`: command `("make", "init")`, exit `0`
- `detection_failed`: command `("make", "-n", "init")` または equivalent detection argv
- `failed`: command `("make", "init")`, non-zero exit

`detail` は human diagnostic であり、consumer logic は status / error code を使う。

### 6.4 Artifact state

```python
@dataclass(frozen=True)
class ArtifactState:
    container_exists: bool | None
    worktree_path_exists: bool | None
    branch_exists: bool | None
    worktree_record_exists: bool | None
```

`None` は inspection failure / unknown を表す。error handling 中の追加 inspection failure により original error を上書きしない。

### 6.5 Record view

```python
@dataclass(frozen=True)
class WorktreeRecordView:
    id: str
    path: Path
    basename: str
    branch: str | None
    head: str | None
    detached: bool
    bare: bool
    locked: bool
    lock_reason: str | None
    main: bool
    current: bool
    path_exists: bool
    record_exists: bool
    managed: bool
    classification_available: bool
    classification_reason: ClassificationReason
    origin: WorktreeOrigin
    removable: bool
    remove_blockers: tuple[str, ...]
```

`removable` は application blockers がないことを示すだけであり、dirty state に対する Git success を保証しない。

### 6.6 Requests/results

```python
@dataclass(frozen=True)
class CreateRequest:
    repo_root: Path
    root: Path
    label: str | None
    bootstrap_enabled: bool


@dataclass(frozen=True)
class CreateResult:
    id: str
    main_worktree_path: Path
    container_path: Path
    worktree_path: Path
    branch: str
    bootstrap: BootstrapResult
    artifacts: ArtifactState


@dataclass(frozen=True)
class ListRequest:
    repo_root: Path
    root: Path


@dataclass(frozen=True)
class ShowRequest:
    repo_root: Path
    root: Path
    target: str


@dataclass(frozen=True)
class RemoveRequest:
    repo_root: Path
    root: Path
    target: str
    force: bool
```

CLI が repo/root を解決して request に渡す設計を推奨する。application が environment を直接読む設計でも外部 contract は実現できるが、testability と policy separation のため composition root での解決を優先する。

### 6.7 Expected error

```python
class WorktreeProvisionerError(RuntimeError):
    code: str
    operation: str | None
    message: str
    details: Mapping[str, object]
    result: object | None
    status: Literal["partial", "error"]
```

- expected error は code / status / details を構造化する。
- bootstrap failure は `status="partial"`, result=`CreateResult`。
- post-remove cleanup failure は `status="partial"`, result=`RemoveResult`。
- unexpected exception は CLI boundary で `internal_error` に変換し、traceback を user output に含めない。

## 7. Ports and adapters

### 7.1 GitGateway

```python
class GitGateway(Protocol):
    def resolve_checkout_root(self, path: Path) -> Path: ...
    def current_branch_or_none(self, repo_root: Path) -> str | None: ...
    def local_branch_exists(self, repo_root: Path, branch: str) -> bool: ...
    def check_branch_ref(self, repo_root: Path, branch: str) -> bool: ...
    def worktree_list(self, repo_root: Path) -> list[GitWorktreeRecord]: ...
    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None: ...
    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None: ...
```

Exact argv:

```text
resolve: git rev-parse --show-toplevel
branch:  git rev-parse --abbrev-ref HEAD
list:    git worktree list --porcelain
add:     git worktree add -b <branch> <path>
remove:  git worktree remove <path>
force:   git worktree remove --force <path>
```

`--force --force` は使用しない。`git worktree unlock` を自動実行しない。

### 7.2 BootstrapGateway

```python
class BootstrapGateway(Protocol):
    def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult: ...
```

reference implementation は最初に標準 makefile 名（`GNUmakefile`, `makefile`, `Makefile`）の存在を確認し、存在しなければ `skipped` とする。makefile がある場合は `make -n init` で target existence / parseability を確認し、成功した場合だけ `make init` を実行する。この方式は implementation choice であり、同じ observable state model を満たすより堅牢な検出へ交換可能である。

`make -n` でも Makefile parse / expansion が完全に trust-free ではない。automatic bootstrap は repository-controlled code/data を評価・実行する operation であり、help / README / skill に trust boundary を明記する。

### 7.3 FilesystemGateway

```python
class FilesystemGateway(Protocol):
    def lstat_kind(self, path: Path) -> str: ...
    def path_exists_no_follow(self, path: Path) -> bool: ...
    def ensure_directory(self, path: Path) -> None: ...
    def remove_target_no_follow(self, path: Path) -> None: ...
```

- symlink / broken symlink / regular file: `unlink`
- directory: `shutil.rmtree`
- special file: unsupported error
- missing / permission / race: structured cleanup failure

### 7.4 EnvironmentGateway

```python
class EnvironmentGateway(Protocol):
    def getenv(self, name: str) -> str | None: ...
```

policy は CLI/application に置き、adapter は `os.environ.get` だけを行う。`SPEC_DOCK_WORKTREE_ROOT` を query しない。

## 8. CLI design

### 8.1 Parser

```bash
worktree-provisioner create [LABEL] [--repo PATH] [--root PATH] [--no-bootstrap] [--json]
worktree-provisioner list [--repo PATH] [--root PATH] [--json]
worktree-provisioner show <TARGET> [--repo PATH] [--root PATH] [--json]
worktree-provisioner remove <TARGET> [--repo PATH] [--root PATH] [--force] [--json]
```

Shared behavior:

- `--repo`: `Path.cwd()` default
- `--root`: explicit string/path; absent時だけ `WORKTREE_PROVISIONER_ROOT`
- `--json`: explicit machine mode
- blank explicit root は error。env fallbackしない。
- `--version`: package version

### 8.2 Composition root

```python
def main(argv: list[str] | None = None) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser(raw_argv)
    args = parser.parse_args(raw_argv)
    json_mode = bool(args.json)
    try:
        repo_root = git_gateway.resolve_checkout_root(args.repo)
        root = resolve_root(args.root, environment_gateway)
        result = dispatch(args, repo_root, root, ports)
    except WorktreeProvisionerError as exc:
        return emit_expected(exc, json_mode=json_mode)
    except Exception as exc:
        return emit_internal(exc, operation=infer_operation(args), json_mode=json_mode)
    return emit_ok(result, json_mode=json_mode)
```

usage error の JSON 対応には custom `ArgumentParser.error()` または parse wrapper を使用する。`--json` が raw argv に存在する場合は schema v1 error を stdout に一つ出し exit `2`、それ以外は通常 argparse stderr/exit `2` とする。

## 9. Root and namespace design

### 9.1 Selection

```text
if --root is present:
    use it exactly; blank/invalid => error
else:
    read WORKTREE_PROVISIONER_ROOT; missing/blank/invalid => error
```

`SPEC_DOCK_WORKTREE_ROOT` を読む branch は存在しない。

### 9.2 Validation

1. `expanduser`
2. absolute check
3. `lstat` / existence classification
4. existing root:
   - directory: valid
   - directory symlink: valid root source; canonical rootを保持
   - file/broken symlink/other: invalid
5. missing root:
   - create: parent permissionsを含め後で作成可能
   - list/show/remove: root value自体は有効。inventory classification は canonical prospective namespaceを使用
6. namespace = `<root>/<main-basename>`
7. namespace `lstat`:
   - symlink: create/remove reject; list/show unavailable classification
   - directory: valid
   - missing: create may create; list/show classify all existing records external
   - other: 全commandで `unsafe_namespace` error。Git inventoryを安全なmanaged classificationとして公開しない。

### 9.3 Managed containment

`managed=true` の条件:

- classification available
- record path は namespace 自体ではない
- lexical path が namespace の strict descendant
- canonical path も canonical namespace の strict descendant
- namespace path component が symlink ではない

これにより namespace 内の symlink path が外部 directory を指すケースを managed と誤認しない。

containment は inventory snapshot と再確認による race reduction であり、非協調 ancestor rename に対する原子的な
排他ではない。descriptor は開いた directory inode に Git / cleanup の相対操作を束縛するが、namespace の path identity
が critical window に変化すること自体を防止しない。

## 10. Create flow

```text
1. resolve checkout root
2. resolve/validate root
3. validate label
4. read current branch; detached => error
5. read Git worktree inventory
6. identify main record and repo basename
7. derive namespace; reject namespace symlink/unsafe type
8. generate candidate id/path/branch
9. preflight record/path/branch/ref collisions
10. create/recheck root and namespace
11. git worktree add -b branch path
12. on recognized collision: refresh records and next candidate
13. on unknown failure: inspect artifacts, return error, no rollback
14. if --no-bootstrap: disabled, return ok
15. detect init target
16. no target: skipped, return ok
17. detection failure: return partial + artifacts, exit 1
18. run make init
19. success: return ok
20. execution failure: return partial + artifacts, exit 1
```

descriptor-bound Git operation が利用できる場合は、開いた namespace inode に相対 target を束縛し、成功後に
path identity を再確認する。この再確認は干渉の検出であり、最後の bound check と Git syscall の間に発生する
非協調 ancestor rename を原子的に禁止するものではない。

### 10.1 Candidate generation

```python
id = f"wt{index}" if label is None else label if index == 1 else f"{label}{index}"
path = namespace / f"{repo_basename}-{id}"
branch = f"{current_branch}-{id}"
```

candidate ceiling は `10_000` を reference constant とする。これは owner decision ではなく bounded-loop implementation choice であり、変更する場合も finite / tested でなければならない。

### 10.2 Retry classifier

SpecDock reference にある次の fragments を initial evidence とする。

```text
already exists
is already checked out
a branch named
```

Git version / locale 依存を一箇所へ閉じ込める。classifier が確信できない message は retryしない。将来 machine-readable Git interface が利用可能になった場合は内部実装を交換できる。

### 10.3 Partial artifact inspection

Git add failure後の inspection は best-effort である。

- path: no-follow existence
- branch: `show-ref --verify --quiet refs/heads/<branch>`
- record: refreshed worktree list
- container: no-follow existence

inspection failure は `null`。自動削除は行わない。

## 11. Bootstrap design

### 11.1 State transition

```text
--no-bootstrap --------------------> disabled / ok / exit 0
no Makefile or no init target -----> skipped / ok / exit 0
make init success -----------------> succeeded / ok / exit 0
make unavailable/detection error --> detection_failed / partial / exit 1
make init non-zero ----------------> failed / partial / exit 1
```

### 11.2 Partial result

bootstrap failure は worktree creation 自体が確認済みなので、JSON に result と error の双方を入れる。

```json
{
  "schema_version": 1,
  "status": "partial",
  "operation": "create",
  "result": {
    "id": "setup",
    "main_worktree_path": "/repos/example",
    "container_path": "/worktrees/example",
    "worktree_path": "/worktrees/example/example-setup",
    "branch": "main-setup",
    "bootstrap": {
      "requested": true,
      "status": "failed",
      "command": ["make", "init"],
      "exit_code": 7
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
    "message": "make init failed",
    "details": {}
  },
  "warnings": []
}
```

raw Makefile output は size limit / secret redaction policyを適用し、machine control fieldにしない。

## 12. Inventory and target resolution

### 12.1 Main/current

- main: first/main Git worktree record
- current: canonical record path == invocation checkout root
- detached status は record fieldとして保持

### 12.2 Stable id

- main record: `main`
- safely managed recordで basename `<repo>-<suffix>`: `<suffix>`
- external/unavailable record: basename
- duplicate raw id: canonical path sortにより first unsuffixed, subsequent `~2`, `~3`, ...
- result list order: Git record orderを維持してよい。id disambiguationだけ canonical sortを使う。

### 12.3 Blocker calculation

record view の blockers:

- main -> `main_worktree`
- current -> `current_worktree`
- bare -> `bare_worktree`
- locked -> `locked_worktree`
- missing path -> `path_missing`
- managed false + classification available -> `outside_managed_namespace`
- classification unavailable -> `classification_unavailable`
- managed path with more than one lexical component below namespace -> `nested_target_unsupported`

`removable = blockers is empty`。

### 12.4 Resolver order

1. exact stable id
2. exact absolute canonical path
3. basename
4. branch-only diagnostic
5. not found

exact id は basename ambiguity より優先する。ambiguous candidatesは full record payloadで返す。

## 13. Remove design

### 13.1 Sequence

```text
1. resolve repo/root
2. reject namespace symlink/unsafe namespace
3. read inventory
4. resolve target
5. calculate blockers; any blocker => stop
6. refresh Git worktree records
7. rebuild inventory with same root/namespace
8. re-resolve original selector
9. verify canonical path identity unchanged
10. recalculate blockers; any blocker => stop
11. recheck protected paths / containment
12. verify target is a single path component directly below namespace; nested descendant blocker => stop
13. git worktree remove [--force] <path>
14. on Git failure: no filesystem cleanup; best-effort read-only inventory refresh and target `lstat` observation may classify partial state
15. recheck containment
16. if target remains: target-only no-follow cleanup
17. cleanup success: ok
18. cleanup failure: partial, removed_record=true, removed_directory=false
```

final refresh / bound check は mutation 前の race reduction である。Git remove 後の namespace / target recheck が
干渉を検出した場合は cleanup を拡大せず `status=partial` とする。最後の check と Git syscall の間に非協調 process
が root / namespace またはその ancestor inode を rename・replace する race は `WTP-THREAT-001` により out of scope
であり、atomic prevention を主張しない。

### 13.2 Hard blockers

`--force` でも解除しない。

- `main_worktree`
- `current_worktree`
- `bare_worktree`
- `locked_worktree`
- `path_missing`
- `record_missing_after_refresh`
- `target_changed_after_refresh`
- `outside_managed_namespace`
- `classification_unavailable`
- `nested_target_unsupported`
- `protected_cleanup_path`
- `unsafe_namespace`

### 13.3 Force

```python
cmd = ["git", "worktree", "remove"]
if force:
    cmd.append("--force")
cmd.append(str(path))
```

- default dirty/untracked behavior は Git に委ねる。
- locked record は adapter 前に拒否する。
- nested descendant は adapter 前に拒否し、`--force` でも解除しない。
- force intent は request booleanに明示する。
- skill は userの別途明示なしに booleanをtrueにしない。

### 13.4 Protected paths

次は cleanup targetにならない。

- central root
- repository namespace
- main repository root
- invocation checkout root
- 上記を包含する ancestor
- canonical containmentを外れる path
- symlink namespace配下の lexical path

### 13.5 Cleanup partial

Git record removal後に cleanupが失敗した場合:

```json
{
  "schema_version": 1,
  "status": "partial",
  "operation": "remove",
  "result": {
    "target": "done",
    "resolved_target": {},
    "force_requested": false,
    "removed_record": true,
    "removed_directory": false,
    "branch_deleted": false
  },
  "error": {
    "code": "post_remove_cleanup_failed",
    "message": "Git worktree record was removed but target cleanup failed",
    "details": {}
  },
  "warnings": []
}
```

## 14. JSON schema version 1

### 14.1 Common envelope

全expected responseで全fieldを出す。

```json
{
  "schema_version": 1,
  "status": "ok",
  "operation": "list",
  "result": {},
  "error": null,
  "warnings": []
}
```

```json
{
  "schema_version": 1,
  "status": "error",
  "operation": "show",
  "result": null,
  "error": {
    "code": "target_not_found",
    "message": "worktree target was not found",
    "details": {}
  },
  "warnings": []
}
```

Warning object:

```json
{
  "code": "diagnostic_code",
  "message": "human-readable detail"
}
```

### 14.2 Create result

```json
{
  "id": "wt1",
  "main_worktree_path": "/repos/example",
  "container_path": "/worktrees/example",
  "worktree_path": "/worktrees/example/example-wt1",
  "branch": "main-wt1",
  "bootstrap": {
    "requested": true,
    "status": "skipped",
    "command": null,
    "exit_code": null
  },
  "artifacts": {
    "container_exists": true,
    "worktree_path_exists": true,
    "branch_exists": true,
    "worktree_record_exists": true
  }
}
```

### 14.3 Worktree payload

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
  "lock_reason": null,
  "main": false,
  "current": false,
  "path_exists": true,
  "record_exists": true,
  "managed": true,
  "classification_available": true,
  "classification_reason": "root_valid",
  "origin": "managed_namespace",
  "removable": true,
  "remove_blockers": []
}
```

### 14.4 List/show/remove result

List:

```json
{"worktrees": []}
```

Show:

```json
{"target": "feature", "worktree": {}}
```

Remove:

```json
{
  "target": "feature",
  "resolved_target": {},
  "force_requested": false,
  "removed_record": true,
  "removed_directory": true,
  "branch_deleted": false
}
```

### 14.5 Error details

- ambiguous: `target`, `candidates`
- remove blocked: `target`, `worktree`, `remove_blockers`
- create add failure: `attempted_id`, `attempted_path`, `attempted_branch`, `artifacts`
- bootstrap partial: result側に create facts、error detailsに bounded diagnostic
- cleanup partial: result側に mutation facts

### 14.6 Version policy

- field removal / rename / type change: schema version update
- enum semantic change: schema version update
- error code semantic change: schema version update
- additive field: v1内で可。consumerはunknown fieldを無視する。
- message text: versioned contractではない

## 15. Text and stream design

### 15.1 Complete create

```text
worktree-provisioner: ok (create) id=wt1 branch=main-wt1 path=/abs/worktrees/example/example-wt1
worktree-provisioner: bootstrap status=skipped command=-
```

### 15.2 Partial create

stdout:

```text
worktree-provisioner: partial (create) id=setup branch=main-setup path=/abs/worktrees/example/example-setup
worktree-provisioner: bootstrap status=failed command="make init" exit_code=7
```

stderr:

```text
worktree-provisioner: error: make init failed; worktree was retained
```

exit `1`。

### 15.3 JSON streams

- expected ok/partial/error: stdoutのみ、exactly one JSON
- stderr: empty
- `--json` usage error: stdout JSON、exit `2`
- unexpected traceback: user streamへ出さない

## 16. Skill and wrapper design

### 16.1 Skill source

推奨source path:

```text
skills/worktree-provisioner/SKILL.md
skills/worktree-provisioner/scripts/worktree-provisioner
```

host固有のskill installation pathはdistribution concernであり、business logicを変えない。

### 16.2 Thin wrapper

Reference behavior:

```sh
#!/bin/sh
set -eu

if ! command -v worktree-provisioner >/dev/null 2>&1; then
  echo "worktree-provisioner is not installed or not available on PATH" >&2
  exit 127
fi

exec worktree-provisioner "$@"
```

wrapperはこれ以上のlogicを持たない。

### 16.3 Skill create flow

```text
1. classify request intent
2. if create intent/repository is ambiguous: ask clarification; do not execute
3. resolve explicit/default repository context and root fact
4. call wrapper: list --repo ... --root ... --json (fact check)
5. call wrapper: create [label] --repo ... --root ... [--no-bootstrap] --json
6. parse schema_version/status
7. report id, branch, absolute path, bootstrap status
8. do not create/move Codex task
```

`list` fact checkが失敗した場合は createしない。label absentはauto-id decisionなのでclarification要因にしない。

### 16.4 Skill remove flow

```text
1. require explicit removal target
2. call show <target> --json
3. verify exact record, managed=true, removable=true, blockers=[]
4. require separate explicit force intent before adding --force
5. call remove <target> [--force] --json
6. report complete/partial/error
```

application側はfinal refreshを行うため、skillのshowはauthorization/evidence check、tool refreshはTOCTOU safety checkである。

### 16.5 Skill non-responsibilities

- Git command construction
- root precedence
- target resolver
- blocker calculation
- JSON schema generation
- bootstrap execution
- worktree cleanup
- Codex task lifecycle

## 17. Packaging and installation design

### 17.1 Python package

- Python `>=3.10`
- Hatchling
- no Python runtime dependencies
- dev: pytest, ruff, mypy, build tooling
- entry point: `worktree-provisioner = worktree_provisioner.cli:main`

### 17.2 Installation verification

Implementation taskでは少なくとも次を検証する。

```bash
uv sync --all-groups
uv run worktree-provisioner --help
uv build
python3 -m venv "$tmp_venv"
"$tmp_venv/bin/pip" install dist/worktree_provisioner-*.whl
PATH="$tmp_venv/bin:$PATH" worktree-provisioner --version
```

public repositoryからのexact tag/SHA install smokeを追加してよい。PyPI publicationはcompletion requirementではない。

### 17.3 Platform

CI matrixはmacOS/Linuxを含める。Windows jobをrequiredにせず、READMEでunsupportedを明示する。

## 18. Source-to-destination provenance mapping

この表は provenance / extraction review のためのものであり、互換性義務ではない。

`...` は SpecDock の `src/spec_dock/assets/spec_dock/scripts/spec_dock_runtime` を表す。

| Reference source | Destination candidate | Reuse disposition | Notes |
| --- | --- | --- | --- |
| `.../application/worktree.py` create naming/collision helpers | `application/worktree_service.py` | lightly extract | label/id/path/branch logic、linked normalization、bounded retryのevidence |
| same inventory/classification helpers | same | extract then change policy | external visibilityは維持、remove eligibilityはmanaged-onlyへ変更 |
| same remove refresh/containment flow | same | extract then harden | default force-equivalentを禁止、locked/externalをhard blocker化 |
| `.../application/worktree_target.py` | `application/target_resolver.py` | near whole-file extraction | id/path/basename/branch rejectionのpure logic |
| `.../application/contracts.py` worktree symbols | `application/contracts.py` | symbol extraction | broad SpecDock contractsを持ち込まない |
| `.../application/ports.py` worktree protocols | `application/ports.py` | symbol extraction / slim rewrite | optional broad Ports containerを持ち込まない |
| `.../infra/git_cli.py` worktree functions/parser | `infra/git_cli.py` | symbol extraction | detached/bare/lockedを保持、removeはsingle forceへ変更 |
| `.../infra/make_cli.py` | `infra/make_cli.py` | near whole-file extraction | failure exit semanticsはapplication/CLIでpartialへ変更 |
| `.../infra/fs_cli.py` path/remove helpers | `infra/filesystem.py` | symbol extraction | Workbench copy codeは非対象 |
| `.../presentation/cli_text.py` worktree renderer | `presentation/{text,json_v1}.py` | contract reference / rewrite | product prefixとschemaを新規設計 |
| SpecDock `commands/worktree.py` | `cli.py` | argument contract reference | CommandSpec/UseCases frameworkは非採用 |
| SpecDock `cli/parser.py`, `registry.py`, `bootstrap.py` | none / `cli.py` composition root | rewrite | broad runtimeをcopyしない |
| SpecDock `tests/cli_runtime/test_worktree.py` scenarios | destination tests | scenario-by-scenario port | parityではなくfunctional/safety regression evidence |
| current prototype `pyproject.toml` | destination `pyproject.toml` | retain with edits | identity、Python、Hatchling、entrypoint、no deps |
| current prototype `core.py` | replacement modules | discard after replacement | create-only、legacy env、incomplete parser |
| current prototype `cli.py` | destination `cli.py` | rewrite | four commands、partial、schema v1 |
| current prototype `tests/test_cli.py` | split test suites | rewrite | legacy env / bootstrap exit 0 assertionsを反転 |
| current prototype `README.md` | destination `README.md` | rewrite | create-only / legacy env wordingを除去 |

## 19. Security invariants

| ID | Invariant |
| --- | --- |
| `INV-001` | main checkout内へdefault worktree containerを作らない。 |
| `INV-002` | namespace/basenameはmain record、branch prefixはinvocation checkoutを使う。 |
| `INV-003` | rootはnew product configのみ。legacy envを読まない。 |
| `INV-004` | namespace symlinkではcreate/removeしない。 |
| `INV-005` | lexical + canonical containmentを満たさないrecordをmanagedとしない。判定は snapshot / recheck による race reduction であり、atomic preventionではない。 |
| `INV-006` | unknown Git failureをcollision retryしない。 |
| `INV-007` | bootstrap failureをrollbackせず、partial/non-zeroで公開する。 |
| `INV-008` | external/main/current/bare/locked/stale/unsafe targetをforceでも削除しない。 |
| `INV-009` | removeはfinal refresh後だけmutationする。 |
| `INV-010` | Git remove failure後にfilesystem cleanupしない。 |
| `INV-011` | cleanupはtarget-only、no-followとする。 |
| `INV-012` | branch deletion、unlock、prune、repairを副作用にしない。 |
| `INV-013` | subprocessはargv list、shell falseとする。 |
| `INV-014` | expected JSONは一文書のみでhuman outputを混ぜない。 |
| `INV-015` | skill/wrapperにbusiness logicを複製しない。 |
| `INV-016` | skillはexplicit force intentなしに`--force`を使わない。 |
| `INV-017` | tool/skillはCodex task lifecycleを変更しない。 |
| `INV-018` | tool taskはSpecDock repositoryを変更しない。 |
| `INV-019` | `WTP-THREAT-001` の final syscall window における非協調 ancestor rename は out of scope とし、検出時は fail-closed または partial で公開する。 |

## 20. Rejected alternatives

### `ALT-001` create-only initial product

Rejected。inventory/target/remove safetyが別所有になり、agent contractが不完全になる。

### `ALT-002` SpecDock compatibility layer

Rejected。旧CLI、legacy env、旧JSON、default force behaviorを独立productへ持ち込む。

### `ALT-003` SpecDock runtime package dependency

Rejected。独立配布とSpecDock simplificationの目的に反する。

### `ALT-004` Prototype `core.py` extension

Rejected。create-only monolith、legacy env、incomplete Git record modelを正本化する。

### `ALT-005` External worktree removal

Rejected。observabilityは維持するがdestructive ownershipをconfigured namespaceに限定するowner decisionに反する。

### `ALT-006` Force-equivalent default / double force

Rejected。default non-force、single explicit force、manual unlockというowner decisionに反する。

### `ALT-007` Bootstrap failure exit 0

Rejected。agentがuninitialized worktreeをcomplete successと誤認する。partial/non-zeroがowner decisionである。

### `ALT-008` Skill内implementation copy

Rejected。toolとskillのbehavior drift、安全確認の二重実装を生む。

### `ALT-009` SpecDock migration phase in this plan

Rejected。product taskの完全なscope外である。

## 21. Corrected contradictions from prior proposal/prototype

| Prior statement/behavior | Canonical correction |
| --- | --- |
| behavior parity as compatibility requirement | functional coverage / safety regression evidenceのみ |
| `SPEC_DOCK_WORKTREE_ROOT` warning付きsupport | 完全に非対応。lookupしない |
| bootstrap detection/execution failure exit `0` | worktree retained、status `partial`、exit `1` |
| external worktree remove allowed | list/showのみ。removeはmanaged namespace内だけ |
| default force-equivalent / `--force` no-op | default non-force、explicit single force |
| locked remove delegated to Git/double force | application hard blocker。manual unlock required |
| namespace symlink create未拒否 | create/removeをpreflight reject |
| create-only scope | 4 command family |
| generic JSON error | schema v1 typed ok/partial/error |
| skill acceptanceが暗黙 | SKILL.md + thin wrapper + authorization testsを必須化 |
| later SpecDock removal phase | 文書・計画から完全に削除 |
