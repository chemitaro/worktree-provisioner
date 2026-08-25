---
document: plan
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

# worktree-provisioner 実装計画

## 1. 計画の目的と境界

本計画は `worktree-provisioner` standalone tool、versioned JSON interface、Codex skill、thin wrapper を後続実装タスクで完成させるための ordered plan である。現時点では code implementation、commit、push、release を完了したとは扱わない。

計画 baseline:

- repository: `chemitaro/worktree-provisioner`
- branch: `main`
- verified SHA: `18c80a1f222a31df0617df5c8193388b3c301e0e`
- owner decisions: `docs/interview.md` complete
- current implementation: create-only prototype

本計画は SpecDock repository の変更を一切含まない。SpecDock CLI の shim、deprecation、削除、migration、docs/tests更新を行う phase は設けない。

## 2. Non-negotiable implementation constraints

実装者は次を再決定しない。

- `create` / `list` / `show` / `remove` を初期版で同時に完成する。
- `SPEC_DOCK_WORKTREE_ROOT` を読まない。
- bootstrap failure は retained worktree + exit `1` + JSON `partial`。
- remove default non-force、`--force`はsingle force。
- locked targetはforceでも拒否。
- external targetは表示のみ、remove不可。
- namespace symlinkではcreate/remove不可。
- skillはinstalled CLIのthin wrapperだけを使う。
- force intentは別途明示が必要。
- skillはCodex taskを作成・移動しない。
- status/prune/repair/branch deletion/remote/GitHub/Workbenchを追加しない。
- SpecDock-side workを追加しない。
- managed namespace の destructive scope は `WTP-THREAT-001` の脅威モデル内で保証する。descriptor / refresh /
  no-follow / post-check は race を縮小・検出するが、Git CLI / kernel syscall 前の非協調 ancestor rename に対する
  atomic prevention は主張しない。

## 3. Recommended delivery sequence

```text
P0  authority and baseline guard
  -> P1  executable contract tests
  -> P2  package/module scaffold normalization
  -> P3  contracts and adapters
  -> P4  create and bootstrap partial semantics
  -> P5  list/show inventory and target resolver
  -> P6  managed-only remove and cleanup
  -> P7  CLI, text, JSON schema v1
  -> P8  Codex skill and thin wrapper
  -> P9  docs, packaging, installation, CI
  -> P10 full verification and handoff
```

各phaseは前phaseのgateを満たしてから進む。`application/worktree_service.py` のようなshared fileはsequential ownership handoffとし、parallel writerを置かない。

## 4. P0 — Authority and baseline guard

### 4.1 目的

implementation task開始時のrepository revisionとprototype stateを確認し、stale specや別branchをsilent authorityにしない。

### 4.2 作業

```bash
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=all
git remote -v
find . -maxdepth 5 -type f -print | sort
```

判定:

- current HEADがverified SHAそのもの、またはこの3文書を追加したreviewed descendantであることを確認する。
- unrelated/unreviewed commitsがある場合はstopし、diffを再評価する。
- root `AGENTS.md` が追加されている場合は最初に読む。baseline treeには存在しない。
- secret、credential、generated build artifactをinventoryする。
- prototype filesをaccepted architectureとして扱わない。

### 4.3 Evidence

- exact base SHA
- current HEAD / branch
- before `git status`
- current tree inventory
- owner decision source path

### 4.4 Stop conditions

- repository/branchが違う。
- verified SHAとのancestryを確認できない。
- owner interviewがmissing/modifiedで、decision sourceを検証できない。
- credentialsまたはunreviewed binary artifactがある。
- SpecDock repositoryへのeditがimplementation scopeへ混入している。

### 4.5 Gate `G0`

- authority chainが一意。
- implementation scopeがstandalone repoだけに限定される。
- prototype replacement対象が明示される。

## 5. P1 — Executable contract tests first

### 5.1 目的

owner-approved external contractをproduction rewriteより先にfailing testsとして固定する。SpecDockとの差分比較ではなく、functional coverageとsafety regressionを証明する。

### 5.2 Test foundation

`tests/conftest.py`またはfixture modulesで次を提供する。

- temp Git repository
- initial tracked commit
- local user.name / user.email
- `gc.auto=0`
- `maintenance.auto=false`
- exact environment runner
- temp central root
- symlink capability check
- fake Git/Bootstrap/Filesystem gateways
- subprocess CLI runner
- no host `WORKTREE_PROVISIONER_ROOT` leakage
- no host `SPEC_DOCK_WORKTREE_ROOT` influence

live repositoryまたは標準実worktree rootをtest targetにしない。

### 5.3 First failing tests

1. top-level helpに4 commands
2. `SPEC_DOCK_WORKTREE_ROOT` only => `root_required`
3. basic create / naming
4. linked normalization
5. bootstrap failure => retained + exit `1` + JSON `partial`
6. list managed/external
7. show id/path/basename/ambiguity
8. external remove blocked
9. dirty default remove refusal / force success
10. locked force blocked
11. namespace symlink create/remove blocked
12. JSON common envelope and stream cleanliness
13. skill wrapper argv/exit propagation
14. skill force authorization

### 5.4 Scenario provenance manifest

SpecDock test namesをcompatibility gateにせず、scenario provenanceとしてrecordする。

```python
@dataclass(frozen=True)
class ScenarioProvenance:
    requirement_ids: tuple[str, ...]
    reference_source: str
    reference_symbols: tuple[str, ...]
    intentional_changes: tuple[str, ...]
```

minimum reference groups:

- create naming/collision/linked normalization
- root validation
- make init statuses
- partial Git failure
- worktree porcelain flags
- stable target resolution
- final refresh
- Git-first cleanup
- no-follow target cleanup

### 5.5 Verification

```bash
uv run pytest tests/unit tests/integration -q
```

このphaseではfailureがexpectedだが、test reviewで次を確認する。

- owner decisionを反映している。
- legacy compatibility assertionがない。
- SpecDock executableを必要としない。
- real workspaceを変更しない。

### 5.6 Stop conditions

- behavior parityをpass criterionにする。
- legacy env testをsuccessとして残す。
- bootstrap failure exit `0` assertionを残す。
- external remove success assertionを残す。
- force no-op/double-force assertionを残す。

### 5.7 Gate `G1`

全`WTP-AC-*`にtest placeholderまたはtest mappingがある。

## 6. P2 — Package and module scaffold normalization

### 6.1 目的

packaging scaffoldの有用部分を残し、create-only monolithをbounded architectureへ移す。

### 6.2 Files

- retain/review:
  - `pyproject.toml`
  - `LICENSE`
  - `src/worktree_provisioner/__init__.py`
  - `src/worktree_provisioner/__main__.py`
- create:
  - `application/`
  - `infra/`
  - `presentation/`
  - `skills/worktree-provisioner/`
  - split tests
- freeze until replacement passes:
  - prototype `core.py`
  - prototype `cli.py`
  - prototype `tests/test_cli.py`

### 6.3 `pyproject.toml` changes

- keep name/version/Python/Hatchling/entry point
- add mypy
- define Ruff formatting/lint
- strict pytest markers if needed
- include license files
- no runtime Python dependencies
- update description from create-only to full worktree lifecycle family

### 6.4 Checkpoint

```bash
uv sync --all-groups
uv run python -c 'import worktree_provisioner'
uv run worktree-provisioner --version
```

### 6.5 Stop conditions

- package importがSpecDock pathに依存する。
- generic frameworkを先に作る。
- prototypeをtestsなしで削除する。
- machine-specific rootをdefault化する。

### 6.6 Gate `G2`

- package skeleton imports。
- no runtime dependencies。
- replacement pathが明示される。

## 7. P3 — Contracts and adapters

### 7.1 Ordered files

1. `application/contracts.py`
2. `application/ports.py`
3. `infra/environment.py`
4. `infra/git_cli.py`
5. `infra/make_cli.py`
6. `infra/filesystem.py`

### 7.2 Contract tests

- enum values
- response status
- artifact nullability
- record origin/blocker derivation
- error object serialization
- no mutable default fields

### 7.3 Git parser tests

- main + linked blocks
- branch ref prefix removal
- detached
- bare
- locked without reason
- locked with reason
- final block without blank line
- malformed/no record response

### 7.4 Git adapter tests

Exact argv:

- `rev-parse --show-toplevel`
- `rev-parse --abbrev-ref HEAD`
- `show-ref --verify --quiet`
- `check-ref-format --branch`
- `worktree list --porcelain`
- `worktree add -b`
- remove default
- remove single force

Failures:

- Git missing
- repo path missing/file/outside/bare
- stderr+stdout aggregation
- subprocess OSError

### 7.5 Make adapter tests

- no Makefile/no target -> skipped
- make unavailable -> detection_failed
- parse/include error -> detection_failed
- success -> succeeded
- command failure -> failed
- exact created-worktree cwd
- `--no-bootstrap` application branch does not call adapter

### 7.6 Filesystem tests

- no-follow existence for normal/broken symlink
- directory creation
- existing namespace directory
- namespace symlink detection
- directory/symlink/broken symlink/file cleanup
- special file rejection
- lstat/unlink/rmtree/permission/race errors

### 7.7 Commands

```bash
uv run pytest tests/unit/test_contracts.py tests/unit/test_git_porcelain.py \
  tests/unit/test_make_cli.py tests/unit/test_filesystem.py -q
uv run ruff check src/worktree_provisioner/application src/worktree_provisioner/infra
uv run mypy src/worktree_provisioner/application src/worktree_provisioner/infra
```

### 7.8 Stop conditions

- parserがdetached/bare/lockedを捨てる。
- Git adapterがdouble forceまたはunlockを実行する。
- environment adapterがlegacy envを読む。
- filesystem adapterにWorkbench copy logicを持ち込む。

### 7.9 Gate `G3`

contracts/adaptersがstandaloneでpassし、production packageにSpecDock importがない。

## 8. P4 — Create and bootstrap partial semantics

### 8.1 Ordered implementation

1. root selection
2. label validation
3. repo/current branch validation
4. main record normalization
5. namespace validation
6. candidate generation
7. preflight collision
8. bounded retry
9. Git add error classification
10. artifact observation
11. bootstrap disabled/skipped/succeeded
12. bootstrap partial errors

### 8.2 Test checkpoint `C1` — no mutation validation

- invalid label
- missing/blank root
- legacy env only
- relative/file/broken root
- namespace file/symlink
- detached HEAD
- repo outside

Assert:

- no branch
- no record
- no target path
- no bootstrap marker

### 8.3 Test checkpoint `C2` — naming/collision

- `wt1`, `wt2`
- label, label2
- current branch containing `/`
- directory-only/branch-only/record-only collision
- retryable add collision
- invalid generated ref
- candidate ceiling
- unknown failure no retry

### 8.4 Test checkpoint `C3` — linked normalization

- main worktree basename drives namespace
- invocation linked branch drives new branch
- no chained name

### 8.5 Test checkpoint `C4` — bootstrap

| case | expected status | response | exit |
| --- | --- | --- | ---: |
| `--no-bootstrap` | disabled | ok | 0 |
| no target | skipped | ok | 0 |
| success | succeeded | ok | 0 |
| make missing | detection_failed | partial | 1 |
| parse failure | detection_failed | partial | 1 |
| command non-zero | failed | partial | 1 |

全partial caseでworktree/branch/recordを保持し、rollback call count `0`。

### 8.6 Test checkpoint `C5` — Git partial state

fake cases:

- branch only
- path only
- record only
- branch + path
- record refresh unavailable
- branch inspection unavailable

Error fields:

- attempted id/path/branch
- artifacts with true/false/null
- no cleanup

### 8.7 Verification

```bash
uv run pytest tests/unit/test_root_and_naming.py tests/unit/test_create.py \
  tests/integration/test_cli_create.py -q
```

### 8.8 Stop conditions

- missing rootでsibling placementへfallbackする。
- legacy envを使う。
- bootstrap failureをexit `0`にする。
- partial resultからabsolute pathを落とす。
- unknown Git errorをretryする。

### 8.9 Gate `G4`

`WTP-AC-002`〜`WTP-AC-007`がpass。

## 9. P5 — List/show inventory and target resolver

### 9.1 Ordered implementation

1. Git list error conversion
2. main/current identification
3. namespace classification context
4. lexical+canonical managed containment
5. blocker calculation
6. raw/stable id generation
7. list result
8. target resolver
9. show result

### 9.2 Inventory matrix

- main
- managed
- external
- detached
- bare
- locked + reason
- stale path
- duplicate raw id
- missing namespace
- namespace symlink classification unavailable

### 9.3 Expected blockers

- main/current/bare/locked/path_missing
- outside_managed_namespace
- classification_unavailable

external recordはlist/show successでobservable、`removable=false`。

### 9.4 Target tests

- exact id
- absolute path
- basename
- id priority over basename ambiguity
- deterministic `~2`
- ambiguous candidates
- branch rejection
- not found

### 9.5 Verification

```bash
uv run pytest tests/unit/test_inventory.py tests/unit/test_target_resolver.py \
  tests/integration/test_cli_list_show.py -q
```

### 9.6 Stop conditions

- externalをinventoryから除外する。
- externalをremovableにする。
- branch selectorを許可する。
- duplicate basenameをfirst matchで選ぶ。
- managedをcreation provenanceと説明する。

### 9.7 Gate `G5`

`WTP-AC-008`, `WTP-AC-009`がpass。

## 10. P6 — Managed-only remove and cleanup

### 10.1 Ordered implementation

1. namespace safety preflight
2. initial inventory/resolve
3. hard blocker evaluation
4. final Git inventory refresh
5. target re-resolution
6. canonical identity check
7. blocker re-evaluation
8. protected path / containment check
9. Git remove default/single force
10. post-Git containment recheck
11. target-only no-follow cleanup
12. partial cleanup result

### 10.2 Test checkpoint `R1` — hard blockers × force

各caseを `force=False/True` で検証する。

- main
- current
- bare
- locked
- path missing
- external
- classification unavailable
- namespace symlink
- root/namespace/protected ancestor
- record missing after refresh
- target changed after refresh

Git remove call countは`0`。

### 10.3 Test checkpoint `R2` — force semantics

- clean managed default success
- dirty/untracked managed default Git refusal
- 同じtargetのexplicit force success
- argvにforceなし / single force
- double forceなし
- unlock commandなし
- branch remains

### 10.4 Test checkpoint `R3` — final refresh races

- target disappears
- stable id now points elsewhere
- new duplicate ambiguity
- target becomes bare/locked/current
- namespace changes to symlink

wrong pathへのremove callは`0`。
この checkpoint は検出可能な干渉に対する fail-closed を検証するものであり、非協調 process による final syscall
window の rename を原子的に防止する証明ではない。脅威境界は `WTP-THREAT-001` と R6 の documentation contract で固定する。

### 10.5 Test checkpoint `R4` — Git-first

- Git remove failure -> filesystem cleanup gateway call `0`; read-only inventory refresh and target `lstat` observation are allowed
- no cleanup before Git success
- surfaced Git error is bounded/redacted

### 10.6 Test checkpoint `R5` — cleanup

- leftover directory
- symlink
- broken symlink
- regular file
- special file
- lstat race
- unlink/rmtree permission
- target disappears after Git success

parent/root/namespace/main sentinelsが残る。

### 10.7 Test checkpoint `R6` — documented threat boundary

`WTP-THREAT-001` が requirement / design / plan、README、SKILL.md のすべてに存在し、次を同じ意味で記述している
ことを contract test で固定する。

- preflight、final refresh、namespace symlink / no-follow、descriptor-bound operation、post-check が supported guard である。
- 検出した interference は fail-closed または partial で返し、既存の partial artifact を自動 rollback しない。
- 同一ユーザーの外部・非協調 process による final syscall window の root / namespace ancestor rename は out of scope であり、
  atomic prevention または未検出 race の絶対保証を主張しない。

### 10.8 Partial cleanup assertion

- exit `1`
- status `partial`
- code `post_remove_cleanup_failed`
- `removed_record=true`
- `removed_directory=false`
- `branch_deleted=false`

### 10.9 Verification

```bash
uv run pytest tests/unit/test_remove.py tests/unit/test_filesystem.py \
  tests/integration/test_cli_remove.py -q
```

### 10.10 Stop conditions

- external targetを削除する。
- `req.force`を無視する。
- lockedをGitへ渡す。
- Git failure後にcleanupする。
- branch deletion/prune/repairを呼ぶ。
- parent/namespace/rootをcleanupする。

### 10.11 Gate `G6`

`WTP-AC-010`〜`WTP-AC-012`がpass。

## 11. P7 — CLI, text, and JSON schema v1

### 11.1 Parser/dispatch

- 4 subcommands
- shared `--repo`, `--root`, `--json`
- create `--no-bootstrap`
- remove `--force`
- no `delete`
- `--version`
- custom JSON usage error path

### 11.2 JSON builders

explicit field mappingを使用し、`dataclasses.asdict`任せのPath変換にしない。

Tests:

- common fields always present
- ok/partial/error nullability
- schema version integer 1
- operation enum
- path absolute string
- booleans typed
- null unknown artifacts
- warning objects
- one document only
- expected JSON stderr empty

### 11.3 Error code coverage

`WTP-RQ-017` の各error codeに最低1つのcontract testを置く。message wordingはsnapshot固定せず、code/details typesを固定する。

### 11.4 Text tests

- default text
- product prefix
- absolute path
- complete success stdout
- partial result stdout + error stderr
- exit code mapping

### 11.5 Prototype replacement point

P4-P7 test pass後にのみ次を行う。

- prototype `core.py`を削除
- prototype `cli.py`をfinal composition rootへ置換
- prototype `tests/test_cli.py`をsplit testsへ置換
- unique scenarioが新suiteに存在することを確認
- `legacy/` copyを作らない

### 11.6 Verification

```bash
uv run pytest tests/unit/test_json_v1.py tests/integration -q
uv run worktree-provisioner --help
uv run worktree-provisioner create --help
uv run worktree-provisioner list --help
uv run worktree-provisioner show --help
uv run worktree-provisioner remove --help
```

### 11.7 Stop conditions

- JSON expected errorをstderr textだけで返す。
- JSON stdoutにhuman linesを追加する。
- bootstrap partialをstatus error/okに誤分類する。
- create-only helpが残る。

### 11.8 Gate `G7`

`WTP-AC-013`, `WTP-AC-014`, `WTP-AC-019`がpass。

## 12. P8 — Codex skill and thin wrapper

### 12.1 Deliverables

```text
skills/worktree-provisioner/SKILL.md
skills/worktree-provisioner/scripts/worktree-provisioner
```

### 12.2 Wrapper implementation

- `command -v worktree-provisioner`
- missing -> stderr + exit 127
- otherwise `exec worktree-provisioner "$@"`
- no business logic

### 12.3 Wrapper tests

fake executableで:

- argv exactness
- stdout propagation
- stderr propagation
- exit 0/1/2 propagation
- arguments with spaces/metacharacters remain one argv element
- no root injection
- no JSON modification

Static check:

- `git`, `make`, `WORKTREE_PROVISIONER_ROOT`, target resolver codeがwrapperにない。

### 12.4 Skill behavior tests/review cases

#### Create

- explicit “このrepoでworktreeを作成” -> fact check + create
- explicit create without label -> auto-id; no unnecessary clarification
- ambiguous “worktreeを用意して” with repo unclear -> clarification; no execution
- create partial -> report retained path/branch/bootstrap failure
- create success -> id/branch/path/bootstrap only; no task lifecycle call

#### Remove

- explicit target remove -> show then remove
- ambiguous basename -> stop
- blocker/external -> stop
- force not mentioned -> no `--force`
- explicit force intent -> `--force` exactly once
- locked -> report manual `git worktree unlock` requirement; do not execute remove

### 12.5 Verification

```bash
uv run pytest tests/integration/test_skill_wrapper.py -q
shellcheck skills/worktree-provisioner/scripts/worktree-provisioner  # available時
```

SKILL.mdはmanual reviewer checklistも通す。

### 12.6 Stop conditions

- skill/wrapperにGit commandsを実装する。
- wrapperがhard-coded rootを注入する。
- skillがtext parsingする。
- force intentを推測する。
- create後にCodex taskを作成/移動する。

### 12.7 Gate `G8`

`WTP-AC-015`〜`WTP-AC-017`がpass。

## 13. P9 — Documentation, packaging, installation, CI

### 13.1 README rewrite

最低限含める。

- purpose and 4-command scope
- human/agent interfaces
- root config and no legacy env
- standard local env example（hard-coded defaultではない）
- layout/naming/linked normalization
- automatic bootstrap trust warning / `--no-bootstrap`
- partial + exit `1`
- managed-only remove
- `WTP-THREAT-001` の race threat model、supported guards、非協調 ancestor rename の out-of-scope 境界
- force/locked policy
- JSON schema link/summary
- skill install/use boundary
- unsupported scope/platform
- dev verification

### 13.2 Packaging

- descriptionを4-command productへ更新
- mypy/dev tools
- license file inclusion
- wheel/sdist
- no Python runtime deps
- skill sourceのdistribution方法をREADMEに記載

### 13.3 CI

GitHub Actions matrix:

- Linux supported Python versions
- macOS supported Python versions
- Ruff format/check
- Mypy
- Pytest
- build
- installed wheel smoke

Windows jobはrequiredにしない。

### 13.4 Installation smoke

```bash
uv build

tmp_venv="$(mktemp -d)/venv"
python3 -m venv "$tmp_venv"
"$tmp_venv/bin/pip" install dist/worktree_provisioner-*.whl
PATH="$tmp_venv/bin:$PATH" worktree-provisioner --help
PATH="$tmp_venv/bin:$PATH" worktree-provisioner --version
```

public repositoryのtag/exact SHAからの`uv tool install` smokeを別test/scriptで検証してよい。package index publicationは要求しない。

### 13.5 Static scope checks

```bash
! rg -n 'spec_dock_runtime' src/worktree_provisioner
! rg -n 'SPEC_DOCK_WORKTREE_ROOT' src/worktree_provisioner
! rg -n '/Volumes/990p2t/workspace/worktrees' src/worktree_provisioner
! rg -n 'worktree (status|prune|repair)|branch delete|github issue|workbench' src/worktree_provisioner
```

README内のstandard local path exampleは許容する。

### 13.6 Gate `G9`

`WTP-AC-001`, `WTP-AC-018`, `WTP-AC-020`がpass。

## 14. P10 — Full verification and handoff

### 14.1 Full commands

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -q
uv build
git diff --check
```

Installed artifact smokeとmacOS/Linux evidenceを添付する。

### 14.2 Safety audit checklist

- [ ] legacy env lookupなし
- [ ] machine root defaultなし
- [ ] namespace symlink create/remove拒否
- [ ] external remove拒否
- [ ] locked force拒否
- [ ] default remove non-force
- [ ] single forceのみ
- [ ] final refreshあり
- [ ] Git failure後cleanupなし
- [ ] target-only no-follow cleanup
- [ ] `WTP-THREAT-001` を docs / README / SKILL に明記し、atomic preventionを主張しない
- [ ] branch deletionなし
- [ ] bootstrap failure partial/non-zero/no rollback
- [ ] JSON one-document contract
- [ ] skill wrapper thin
- [ ] force authorization explicit
- [ ] no task lifecycle mutation
- [ ] no SpecDock edits

### 14.3 Handoff evidence

- base SHA / implementation HEAD
- changed file list
- test/lint/type/build commands and results
- macOS/Linux evidence
- wheel/sdist filenames and SHA-256
- installed CLI smoke
- skill wrapper tests
- JSON schema samples
- prototype removal evidence
- final `git status`
- known implementation-level limitations

### 14.4 Commit/publication boundary

本計画はcommit/push/release authorityを自動的に付与しない。implementation taskの具体的なauthorizationに従う。

Repositoryは既にpublicであるが、次は別の明示authorityを必要とする。

- package registry publication
- tag/release creation
- protected branch setting変更
- external service deployment

### 14.5 Completion criteria

全`WTP-AC-001`〜`WTP-AC-020`と`G0`〜`G9`がpassし、P10 handoff evidenceが揃った時点でstandalone product implementationをcomplete候補とする。

## 15. File ownership matrix

| Path | Primary phase/owner | Later writers | Constraint |
| --- | --- | --- | --- |
| `pyproject.toml` | P2 foundation | P9 packaging | no runtime deps / no legacy config |
| `LICENSE` | P2 foundation | none without policy review | include in artifacts |
| `src/worktree_provisioner/cli.py` | P7 CLI | P9 help/version | composition root only |
| `application/contracts.py` | P3 contracts | P4-P7 via owner review | JSON-facing types stable |
| `application/ports.py` | P3 contracts | adapter change review | worktree-only protocols |
| `application/worktree_service.py` | P4 create | P5 then P6 sequential handoff | no parallel writers |
| `application/target_resolver.py` | P5 inventory | P6 bug fixes | pure resolver |
| `infra/environment.py` | P3 | P4 root fixes | no legacy env |
| `infra/git_cli.py` | P3 | P6 remove argv review | no remote/GitHub functions |
| `infra/make_cli.py` | P3 | P4 bootstrap fixes | no rollback policy |
| `infra/filesystem.py` | P3 | P6 cleanup fixes | no Workbench logic |
| `presentation/json_v1.py` | P7 | schema review only | explicit mapping |
| `presentation/text.py` | P7 | P9 help/docs consistency | wording not machine contract |
| `skills/.../SKILL.md` | P8 skill | P9 docs consistency | authorization boundary |
| `skills/.../scripts/worktree-provisioner` | P8 wrapper | none except portability | thin exec only |
| `tests/unit/test_create.py` | P4 | P10 hardening | partial semantics |
| `tests/unit/test_inventory.py` | P5 | P10 | external observable |
| `tests/unit/test_remove.py` | P6 | P10 | destructive safety |
| `tests/unit/test_json_v1.py` | P7 | schema change review | field/type lock |
| `tests/integration/test_skill_wrapper.py` | P8 | P10 | argv/auth flows |
| `README.md` | P9 | P10 corrections | no stale prototype text |
| `.github/workflows/ci.yml` | P9 | P10 | macOS/Linux |

## 16. Quality gates

| Gate | Required evidence |
| --- | --- |
| `G0 Authority` | exact repo/branch/SHA ancestry、owner interview、tree inventory |
| `G1 Contract` | all AC mapped to tests; no legacy/parity assumptions |
| `G2 Scaffold` | package imports、tool config、no hard-coded root |
| `G3 Adapters` | Git/make/fs/env tests、single force、no SpecDock import |
| `G4 Create` | naming/root/linked/collision/bootstrap partial/artifacts |
| `G5 Inventory` | managed/external/flags/stable ids/target resolver |
| `G6 Remove` | managed-only、locked、force、refresh、Git-first、cleanup partial |
| `G7 Interface` | text/JSON/exit/schema/error-code coverage |
| `G8 Skill` | explicit intent、show-before-remove、force intent、thin wrapper |
| `G9 Distribution` | lint/type/tests/build/install/macOS/Linux/docs/scope checks |

## 17. Traceability tables

### 17.1 Owner decisions

| Owner decision | Requirement / design expression | Implementation phase | Evidence |
| --- | --- | --- | --- |
| `OD-001` | `WTP-RQ-002`; Design §4, §8 | P2, P7 | four-command help and integration suite |
| `OD-002`, `OD-003` | `WTP-RQ-014`, `WTP-RQ-015`; Design §14-15 | P7 | default text and explicit JSON tests |
| `OD-004` | `WTP-RQ-021`; Design §18, §21 | P1, P9 | no compatibility/parity gate; provenance-only review |
| `OD-005` | `WTP-RQ-004`; Design §9.1 | P3, P4 | legacy env only => `root_required`; production-source grep |
| `OD-006` | `WTP-RQ-004`; Design §9, §17 | P9 | no machine path in source; README operational example review |
| `OD-007` | `WTP-RQ-006`, `WTP-RQ-007`; Design §10.1-10.2 | P4 | auto/label/collision/ref tests |
| `OD-008` | `WTP-RQ-009`; Design §11 | P3, P4 | disabled/skipped/succeeded bootstrap tests |
| `OD-009` | `WTP-RQ-009`, `WTP-RQ-015`, `WTP-RQ-016`; Design §11.2, §14 | P4, P7 | partial JSON, exit 1, retained artifacts, no rollback |
| `OD-010` | `WTP-RQ-020`; Design §17.3 | P9, P10 | macOS/Linux CI and install evidence |
| `OD-011` | `WTP-RQ-013`; Design §13.3 | P6 | dirty default refusal; argv without force |
| `OD-012` | `WTP-RQ-012`, `WTP-RQ-013`; Design §12.3, §13.2 | P5, P6 | locked blocker × force; no Git mutation |
| `OD-013` | `WTP-RQ-010`, `WTP-RQ-012`; Design §12-13 | P5, P6 | external list/show success; external remove blocked |
| `OD-014` | `WTP-RQ-005`, `WTP-RQ-012`; Design §9.2, §13.2 | P4-P6 | create/remove reject; list/show classification unavailable |
| `OD-015` | `WTP-RQ-018`; Design §16.3 | P8 | explicit vs ambiguous create skill cases |
| `OD-016` | `WTP-RQ-018`; Design §16.4 | P8 | show-before-remove; explicit-force-only skill cases |
| `OD-017` | `WTP-RQ-018`; Design §16.2 | P8 | fake PATH argv/stdout/stderr/exit propagation |
| `OD-018` | `WTP-RQ-018`; Design §16.3-16.5 | P8 | result report fields; no Codex lifecycle call |
| `OD-019` | planning status and Plan §1 | P0-P10 | proposed status; no implementation-complete claim |
| `OD-020` | `WTP-AC-020`; Design `INV-018`; Plan scope | all | repository/path scope review; no SpecDock phase |
| `OD-021` | `WTP-RQ-001`; document metadata, Design §17 | P0, P9 | repository/package identity checks |

### 17.2 Functional and non-functional requirements

| Requirement | Design section / decision | Implementation phase | Proving tests / evidence |
| --- | --- | --- | --- |
| `WTP-RQ-001` product identity/independence | Design §4-5, §17 | P2, P9 | package metadata, no `spec_dock_runtime`, installed CLI |
| `WTP-RQ-002` command family | Design §8 | P7 | top-level/leaf help, no `delete` alias |
| `WTP-RQ-003` repository resolution | Design §7.1, §8 | P3, P4 | subdirectory, outside repo, missing/file, bare, detached tests |
| `WTP-RQ-004` root configuration | Design §9.1 | P3, P4 | precedence, missing/blank, legacy-variable-negative tests |
| `WTP-RQ-005` root/namespace validation | Design §9.2-9.3 | P4-P6 | root matrix, namespace type/symlink, containment tests |
| `WTP-RQ-006` layout/naming | Design §10.1 | P4 | auto/label/branch-slash/linked-normalization tests |
| `WTP-RQ-007` collision/retry | Design §10.1-10.2 | P4 | path/branch/record/retryable/unknown/ceiling tests |
| `WTP-RQ-008` create artifacts | Design §10.3 | P4, P7 | fake partial-state matrix and JSON nullability |
| `WTP-RQ-009` bootstrap | Design §11 | P3, P4, P7 | disabled/skipped/succeeded/detection_failed/failed matrix |
| `WTP-RQ-010` inventory | Design §12.1-12.3 | P5 | main/managed/external/detached/bare/locked/stale records |
| `WTP-RQ-011` target resolution | Design §12.4 | P5 | id/path/basename/duplicate/ambiguity/branch/not-found tests |
| `WTP-RQ-012` remove eligibility | Design §12.3, §13.1-13.2 | P5, P6 | blocker × force matrix, external and unsafe namespace tests |
| `WTP-RQ-013` remove execution | Design §13.1, §13.3-13.5 | P6 | default/single-force argv, refresh, Git-first, cleanup partial |
| `WTP-RQ-014` human text | Design §15 | P7 | default mode, streams, prefix, absolute path, partial text |
| `WTP-RQ-015` JSON | Design §14 | P7 | schema-v1 ok/partial/error, one-document and type tests |
| `WTP-RQ-016` exit codes | Design §11, §13.5, §15 | P4, P6, P7 | exit 0/1/2 matrix |
| `WTP-RQ-017` codes/blockers | Design §6.7, §12.3, §14.5 | P4-P7 | at least one contract test per code/blocker |
| `WTP-RQ-018` skill/wrapper | Design §16 | P8 | intent, authorization, wrapper thinness and propagation tests |
| `WTP-RQ-019` packaging/install | Design §17 | P2, P9 | wheel/sdist, clean venv/uv-tool smoke, license inspection |
| `WTP-RQ-020` platform support | Design §17.3 | P9, P10 | macOS/Linux CI; Windows not advertised |
| `WTP-RQ-021` compatibility/provenance | Design §18, §21 | P1, P9 | scenario provenance; no differential parity/legacy support |
| `WTP-RQ-022` prototype disposition | Design §18, §21 | P2, P7, P9 | no create-only monolith/legacy behavior/dead copy |
| `WTP-NFR-001` safety | Design §3.3, §9.3, §13.1, §19 (`WTP-THREAT-001`) | P3-P10 | destructive matrix, subprocess/symlink/static audit, threat-boundary documentation test |
| `WTP-NFR-002` observability | Design §6, §11, §13.5, §14 | P4, P6, P7 | artifacts/partial/remove-state JSON tests |
| `WTP-NFR-003` maintainability | Design §4-5, §18 | P2-P9 | Ruff/Mypy/Pytest, worktree-only boundaries |
| `WTP-NFR-004` determinism | Design §12.2, §14 | P5, P7 | stable-id determinism and schema type tests |
| `WTP-NFR-005` performance | Design §10, §12, §13 | P4-P6 | bounded candidate and gateway call-count assertions |

### 17.3 Acceptance criteria

| Acceptance criterion | Design basis | Implementation phase | Concrete proof |
| --- | --- | --- | --- |
| `WTP-AC-001` package identity | Design §5, §17 | P2, P9 | installed package/help/version/no SpecDock import |
| `WTP-AC-002` basic create | Design §10-11 | P4, P7 | temp repo create text/JSON integration |
| `WTP-AC-003` label/collision | Design §10.1-10.2 | P4 | parametrized label and all collision classes |
| `WTP-AC-004` linked normalization | Design §9, §10 | P4 | linked checkout integration with main/current distinction |
| `WTP-AC-005` root/namespace | Design §9 | P4-P6 | precedence/root matrix/legacy-negative/namespace-symlink tests |
| `WTP-AC-006` bootstrap matrix | Design §11 | P3, P4, P7 | six-state matrix, partial exit 1, retained artifacts |
| `WTP-AC-007` Git partial artifacts | Design §10.3, §14.5 | P4, P7 | fake branch/path/record/null combinations; no cleanup |
| `WTP-AC-008` inventory coverage | Design §12.1-12.3 | P5 | full record-kind payload and blockers |
| `WTP-AC-009` target resolution | Design §12.4 | P5 | selector priority, duplicate IDs, candidates, branch rejection |
| `WTP-AC-010` remove namespace boundary | Design §13.1-13.2 | P6 | managed success; external/main/current/bare/stale/symlink reject |
| `WTP-AC-011` force/locked | Design §13.2-13.3 | P6 | default refusal, single force success, locked no-call matrix |
| `WTP-AC-012` refresh/cleanup | Design §13.1, §13.4-13.5; `WTP-THREAT-001` | P6, P9 | race cases, Git-first, no-follow target-only, partial cleanup, documented final-syscall boundary |
| `WTP-AC-013` JSON/streams | Design §14-15 | P7 | schema/stream/usage-error contract suite |
| `WTP-AC-014` human interface | Design §15 | P7 | default text, prefix/path, complete/partial streams and exits |
| `WTP-AC-015` skill create | Design §16.3 | P8 | explicit/ambiguous/auto-id/partial/no-task cases |
| `WTP-AC-016` skill remove | Design §16.4 | P8 | explicit target, show-before-remove, blockers, force intent |
| `WTP-AC-017` wrapper | Design §16.2 | P8 | fake PATH argv/stdout/stderr/exit/missing-CLI tests |
| `WTP-AC-018` package/platform | Design §17 | P9, P10 | build/install/macOS/Linux evidence |
| `WTP-AC-019` prototype replacement | Design §18, §21 | P7, P9 | no `core.py`/stale README/legacy lookup/exit-0 tests |
| `WTP-AC-020` scope integrity | Design §20 and invariants | all, P10 audit | no out-of-scope code and no SpecDock repository changes |

## 18. Verification command set

Implementation完了時のminimum command set:

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -q
uv build
git diff --check
```

Scope checks:

```bash
! rg -n 'spec_dock_runtime' src/worktree_provisioner
! rg -n 'SPEC_DOCK_WORKTREE_ROOT' src/worktree_provisioner
! rg -n '/Volumes/990p2t/workspace/worktrees' src/worktree_provisioner
! rg -n 'worktree (status|prune|repair)' src/worktree_provisioner
```

Installed wheel smokeとskill wrapper fake-PATH testを追加する。

## 19. Global stop conditions

次のいずれかが発生したら実装を止め、silent workaroundを行わない。

- owner decisionと異なるCLI/exit/safety behaviorが必要になった。
- exact repository ancestryを確認できない。
- external worktreeをremoveする設計になった。
- locked worktreeをforce/unlockする設計になった。
- bootstrap partialをcomplete successにする設計になった。
- legacy env compatibilityが再導入された。
- skillにbusiness logicが複製された。
- SpecDock repositoryへの変更が必要になった。
- out-of-scope commandまたはremote lifecycleが混入した。
- unexpected Git failureを安全に分類できず、retryを広げようとしている。
- `WTP-THREAT-001` の範囲で path containment / race guard を説明できず、または実装が非協調 final-syscall race の
  atomic prevention を主張しようとしている。

## 20. Final completion criteria

次の全てを満たすこと。

1. 4 commandsがstandalone installed CLIとして動く。
2. default textとexplicit schema v1 JSONが一致した状態を表す。
3. rootは`--root`/new envのみで、legacy envを受理しない。
4. create naming/collision/linked normalizationがpassする。
5. bootstrap failureはretained + partial + exit1 + no rollbackである。
6. list/showはexternalを観測できる。
7. removeは `WTP-THREAT-001` の脅威モデル内で managed namespaceだけを対象にする。
8. default non-force、single explicit force、locked不可である。
9. final refresh、Git-first、target-only no-follow cleanupがpassする。
10. all error/partial contractsがversioned JSON testsで固定される。
11. skill/wrapperのexplicit intent・force authorization・thinnessがpassする。
12. create後にCodex task lifecycleを変更しない。
13. prototype create-only/legacy behaviorがproduction pathに残らない。
14. macOS/Linux evidence、lint、type、tests、build、install smokeがpassする。
15. SpecDock repositoryを変更していない。
16. status/prune/repair/branch deletion/remote/GitHub/Workbenchを追加していない。
17. handoff evidenceが揃い、未確認事項が実装詳細として明記される。
