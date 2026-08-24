---
document: plan
product: worktree-provisioner
status: proposed
baseline_repository: chemitaro/spec-dock
baseline_branch: main
baseline_sha: ff09fd05d9862c399d4e22e760170dcb8c46ec6a
verified_at: 2026-08-24
language: ja
---

# worktree-provisioner 実装計画

## 1. 計画の前提

- 本計画は implementation-ready proposal であり、現時点で file modification / commit / push / publication は行っていない。
- source behavior baseline は `chemitaro/spec-dock@ff09fd05d9862c399d4e22e760170dcb8c46ec6a` とする。
- destination prototype は evidence であり、accepted design ではない。
- tool delivery では `/Volumes/990p2t/workspace/tools/spec-dock` を変更しない。
- destination は commit / remote / upstream がないというユーザー提示情報を、実装開始時に local Git command で再確認する。
- `SEC-DEC-001`、`SEC-DEC-002`、`SEC-DEC-003` を Step 0 で owner decision にする。

## 2. 推奨実装順序

```text
P0 baseline / decision freeze
  -> P1 destination inventory / scaffold
  -> P2 test harness and parity matrix first
  -> P3 contracts / ports / infra extraction
  -> P4 create use case
  -> P5 list / show / target resolution
  -> P6 remove / containment / cleanup
  -> P7 CLI / text / JSON
  -> P8 docs / packaging / license
  -> P9 differential parity / full verification
  -> P10 local delivery handoff
  -> P11 separate SpecDock migration (not part of tool delivery)
```

## 3. Step P0 — Baseline and decision freeze

### 3.1 目的

実装対象 revision、prototype の実状態、security-sensitive contract を固定し、誤った branch / stale attachment / silent policy choice から実装を開始しない。

### 3.2 作業

1. source local repository を確認する。

```bash
git -C /Volumes/990p2t/workspace/tools/spec-dock rev-parse --show-toplevel
git -C /Volumes/990p2t/workspace/tools/spec-dock rev-parse HEAD
git -C /Volumes/990p2t/workspace/tools/spec-dock branch --show-current
git -C /Volumes/990p2t/workspace/tools/spec-dock status --short --untracked-files=all
```

2. source local `HEAD` が baseline と異なる場合、local checkout を silent authority にしない。
3. differential test 用には source repository を直接変更せず、local clone を作る。

```bash
tmp_source="$(mktemp -d)/spec-dock-baseline"
git clone --no-local /Volumes/990p2t/workspace/tools/spec-dock "$tmp_source"
git -C "$tmp_source" checkout --detach ff09fd05d9862c399d4e22e760170dcb8c46ec6a
```

network clone を必要としない。local source に baseline object がない場合は stop し、勝手に別 revision を使わない。

4. destination を完全 inventory する。

```bash
git -C /Volumes/990p2t/workspace/tools/worktree-provisioner rev-parse --show-toplevel
git -C /Volumes/990p2t/workspace/tools/worktree-provisioner rev-parse --verify HEAD
git -C /Volumes/990p2t/workspace/tools/worktree-provisioner remote -v
git -C /Volumes/990p2t/workspace/tools/worktree-provisioner status --short --untracked-files=all
find /Volumes/990p2t/workspace/tools/worktree-provisioner -maxdepth 4 -type f -print | sort
```

`rev-parse --verify HEAD` は no-commit repository なら failure が expected。unexpected commit / remote / unreviewed file があれば stop して scope を再評価する。

5. decision record を owner と確定する。

- `SEC-DEC-001`: remove force policy
- `SEC-DEC-002`: automatic bootstrap default
- `SEC-DEC-003`: namespace symlink create rejection
- `POL-DEC-001`: legacy env support window
- `PROD-DEC-001`: initial platform support

### 3.3 Test-first checkpoint

まだ production code を変更しない。decision table と baseline SHA が plan / issue に記録されていることを review する。

### 3.4 Stop conditions

- GitHub verified SHA と local parity source SHA が一致しない。
- source baseline object が local repository にない。
- destination に attachment 未掲載の重要 file / credentials / generated artifact がある。
- remove / bootstrap / namespace symlink policy が未決定のまま該当 production code を書こうとしている。
- source repository への変更が tool delivery scope に混入している。

### 3.5 完了条件

- baseline SHA が一意。
- destination inventory が完全。
- unresolved decision の owner / deadline / default assumption が明記される。
- before-state evidence が保存される。

## 4. Step P1 — Destination scaffold normalization

### 4.1 目的

prototype の packaging value を保持しつつ、create-only monolith を source-aligned bounded architecture に置換できる skeleton を作る。

### 4.2 作業

1. `pyproject.toml` を review し、次を維持する。
   - `name = "worktree-provisioner"`
   - `requires-python = ">=3.10"`
   - Hatchling
   - no runtime dependency
   - `worktree-provisioner = "worktree_provisioner.cli:main"`
2. dev dependency / tool config を追加する。
   - `pytest`
   - `ruff`
   - `mypy`
   - pytest marker `parity`
3. package directory skeleton を作る。
4. source MIT license notice を含む `LICENSE` を追加する。
5. prototype `core.py` はまだ削除せず、new test harness が replacement path を通るまで freeze する。

### 4.3 File ownership

- owner workstream: `foundation`
- files:
  - `pyproject.toml`
  - `LICENSE`
  - package `__init__.py` / `__main__.py`
  - empty package directories
- 他 step は P1 完了まで `pyproject.toml` を変更しない。

### 4.4 Checkpoint

```bash
cd /Volumes/990p2t/workspace/tools/worktree-provisioner
uv sync --all-groups
uv run python -c 'import worktree_provisioner'
uv run worktree-provisioner --help
```

この時点の help は temporary でもよいが、prototype create-only contract を final として固定しない。

### 4.5 Stop conditions

- package import が sibling SpecDock path に依存する。
- source file を copy したのに original MIT notice を失う。
- hidden prototype file を確認せず削除する。

### 4.6 完了条件

- build backend / package discovery が機能する。
- source / test layout が確定する。
- no runtime dependency を維持する。

## 5. Step P2 — Test harness and parity matrix first

### 5.1 目的

production copy より先に current behavior と intentional delta を executable test contract にする。

### 5.2 作業

1. `tests/conftest.py` に temp Git repository fixture を作る。
2. fixture は必ず次を設定する。
   - `git config gc.auto 0`
   - `git config maintenance.auto false`
   - local test user name / email
   - initial tracked commit
3. live source / destination repository に worktree を作らない。
4. subprocess CLI helper は exact env mode を持ち、host の root env leakage を防ぐ。
5. fake gateway helper を作り、retry / partial artifact / race を deterministic に再現する。
6. parity scenario manifest を test data として定義する。

```python
@dataclass(frozen=True)
class ParityScenario:
    id: str
    source_test_symbols: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    intentional_deltas: tuple[str, ...] = ()
```

### 5.3 最初に作る failing tests

- basic create
- invalid label no mutation
- linked-worktree normalization
- root missing / relative / file / broken symlink
- bootstrap success / failed / detection_failed
- non-retryable add failure no retry + artifact state
- list / show JSON record payload
- remove main/current/path_missing blockers
- Git failure before cleanup
- target-only cleanup
- JSON envelope schema

### 5.4 Source test mapping baseline

次の current source symbols を minimum parity source とする。

#### Create

- `test_worktree_create_requires_env_without_side_effects`
- `test_worktree_create_rejects_blank_env_without_side_effects`
- `test_worktree_create_uses_central_root_auto_id_and_branch`
- `test_worktree_create_retries_collisions_and_accepts_label`
- `test_worktree_create_retries_auto_id_collisions`
- `test_worktree_create_retries_git_add_collision`
- `test_worktree_create_uses_current_branch_with_slash_for_branch_prefix`
- `test_worktree_create_normalizes_container_from_linked_worktree`
- `test_worktree_create_rejects_relative_root_without_side_effects`
- `test_worktree_create_rejects_file_root_without_side_effects`
- `test_worktree_create_rejects_broken_symlink_root_without_side_effects`
- `test_worktree_create_accepts_directory_symlink_root`
- `test_worktree_create_expands_tilde_root`
- `test_worktree_create_rejects_invalid_labels_without_creating_worktree`
- `test_worktree_create_runs_make_init_when_available`
- `test_worktree_create_keeps_worktree_when_make_init_fails`
- `test_worktree_create_keeps_worktree_when_make_init_detection_fails`
- `test_worktree_create_fails_from_detached_head`
- `test_worktree_create_fails_outside_git_repo`
- `test_worktree_create_fails_when_namespace_path_is_file`
- `test_worktree_create_treats_non_collision_git_add_failure_as_fatal`

#### Inventory / show

- `test_worktree_record_payload_includes_classification_diagnostics`
- `test_worktree_list_and_show_json_resolve_agent_targets`
- `test_worktree_list_and_show_json_succeed_when_root_is_missing`
- `test_worktree_json_commands_report_unavailable_classification_for_invalid_root_variants`
- `test_worktree_list_json_classifies_unmanaged_worktree`
- `test_worktree_invalid_root_reads_git_records_before_classification`
- `test_worktree_inventory_reports_stale_records_and_duplicate_ids`
- `test_worktree_target_resolver_boundary_preserves_selector_semantics`

#### Remove

- `test_worktree_remove_clean_managed_target_keeps_branch`
- `test_worktree_remove_untracked_default_removes_directory_and_keeps_branch`
- `test_worktree_remove_tracked_modification_default_removes_directory_and_keeps_branch`
- `test_worktree_remove_force_compatibility_removes_dirty_directory`
- `test_worktree_remove_locked_default_and_force_share_contract`
- `test_worktree_remove_rejects_branch_target_and_invalid_root_without_side_effects`
- `test_worktree_remove_rejects_main_and_delete_alias`
- `test_worktree_remove_rejects_current_unmanaged_and_ambiguous_targets`
- `test_worktree_remove_external_paths_are_not_blocked_by_managed_namespace_containment`
- `test_worktree_remove_cleans_leftover_directory_and_reports_cleanup_failure`
- `test_worktree_remove_git_failure_does_not_cleanup_target`
- `test_worktree_remove_uses_target_only_cleanup_for_remaining_directory`
- `test_worktree_remove_reports_target_cleanup_failures`
- `test_worktree_remove_ambiguous_basename_stops_before_git_remove`
- `test_worktree_remove_re_resolves_target_after_final_git_refresh`
- `test_worktree_remove_hard_blockers_stop_before_git_remove_even_with_force`
- `test_worktree_remove_treats_broken_symlink_target_as_existing`
- `test_fs_remove_target_unlinks_symlink_broken_symlink_and_regular_file`
- `test_fs_remove_target_reports_lstat_unlink_rmtree_and_unsupported_failures`

### 5.5 Intentional delta test mapping

- source default dirty remove success
  - destination `remove --force` success と state parity を比較
- destination default remove
  - dirty / untracked で Git refusal、no cleanup を独立 safety test
- source `origin=spec_dock_managed`
  - destination `origin=managed_namespace` へ normalize
- source create text only
  - destination create JSON は independent schema test
- destination `--no-bootstrap`
  - source にない additive safety test
- namespace symlink create
  - current source behavior と分離し、destination は `invalid_root` で拒否する intentional safety test

### 5.6 Verification

```bash
uv run pytest tests/contract -q
```

この段階では failing expected。test names / assertions / fixtures が review 済みであることが gate。

### 5.7 Stop conditions

- test が live checkout / configured real worktree root を使う。
- source behavior を current test symbol ではなく historical report の stale name から推測する。
- force policy delta を parity failure として無視する。
- fake adapter が production error classifier と異なる文字列だけで都合よく pass する。

### 5.8 完了条件

- requirement AC ごとに test placeholder がある。
- parity / intentional delta が区別される。
- temp repo cleanup が `finally` / pytest fixture で保証される。

## 6. Step P3 — Contracts, ports, and infra extraction

### 6.1 目的

source use case を受け入れる最小 contract / adapter foundation を production package に作る。

### 6.2 Ordered work

1. `application/contracts.py`
2. `application/ports.py`
3. `infra/environment.py`
4. `infra/git_cli.py`
5. `infra/make_cli.py`
6. `infra/fs_cli.py`

### 6.3 Test-first checkpoints

#### Contracts

- enum validation
- `WorktreeRecordView` origin derivation
- `ArtifactState` nullability
- error object JSON-serializable field set

#### Git parser

- main / linked records
- detached
- bare
- locked with / without reason
- branch prefix removal
- final block without trailing blank line

#### Git adapter

- argv exactness
- stdout / stderr aggregation
- missing Git
- repo resolution from subdirectory / linked checkout
- failed repo resolution

#### Make adapter

- missing command
- missing target variants
- parse failure
- success / failure
- exact cwd

#### Filesystem adapter

- directory
- symlink
- broken symlink
- regular file
- FIFO / unsupported
- `lstat` / unlink / rmtree error

### 6.4 File ownership

| owner | files | rule |
| --- | --- | --- |
| `contracts` | `application/contracts.py`, `application/ports.py` | P3 中 single writer |
| `git-adapter` | `infra/git_cli.py` | remote / checkout symbols を追加しない |
| `bootstrap-adapter` | `infra/make_cli.py` | application policy を持たない |
| `filesystem-adapter` | `infra/fs_cli.py` | Workbench code を copy しない |
| `environment-adapter` | `infra/environment.py` | precedence policy を持たない |

### 6.5 Verification

```bash
uv run pytest \
  tests/contract/test_git_cli.py \
  tests/contract/test_make_cli.py \
  tests/contract/test_fs_cli.py \
  -q
uv run mypy src/worktree_provisioner/application src/worktree_provisioner/infra
uv run ruff check src/worktree_provisioner/application src/worktree_provisioner/infra
```

### 6.6 Stop conditions

- `spec_dock_runtime` import が残る。
- `GitGateway` に GitHub / checkout lifecycle methods を持ち込む。
- `FilesystemGateway` に Workbench methods を持ち込む。
- prototype parser を family parser として再利用する。
- source error text を parse して domain state にする設計が adapter 外へ漏れる。

### 6.7 完了条件

- adapter tests pass。
- production modules は standalone import できる。
- no SpecDock domain type。

## 7. Step P4 — Create use case extraction

### 7.1 目的

SpecDock current create behavior を bounded copy し、standalone root / JSON error contract と bootstrap disable を加える。

### 7.2 Ordered implementation

1. label validation
2. root selection / validation
3. current branch validation
4. main record / namespace normalization
5. candidate generation
6. preflight collision
7. namespace mkdir
8. Git add / retry classifier
9. structured partial artifact state
10. bootstrap aggregation

### 7.3 Test-first checkpoints

#### CP-C1: validation before mutation

- invalid label
- missing / blank / relative / file / broken symlink root
- detached HEAD
- outside repo

assert:

- no branch
- no worktree record
- no target path
- no bootstrap marker

#### CP-C2: naming

- auto ids
- label ids
- current branch containing `/`
- ref invalid after composition
- candidate ceiling

#### CP-C3: collision

- directory collision
- branch collision
- record collision
- retryable Git collision
- unknown Git failure no retry

#### CP-C4: linked normalization

- invocation from linked checkout
- main basename namespace
- invocation branch prefix
- no chained name

#### CP-C5: partial artifacts

fake adapter cases:

- branch created, record absent, path absent
- path created, record absent
- record visible after failure
- branch existence lookup unavailable
- record refresh failure

assert structured `ArtifactState` and no automatic cleanup.

#### CP-C6: bootstrap

- disabled
- skipped
- succeeded
- failed
- detection_failed
- warnings / exit 0

### 7.4 File ownership

- owner: `create-use-case`
- files:
  - `application/worktree.py` create section / shared root helpers
  - `tests/contract/test_create.py`
  - `tests/integration/test_cli_create.py` only after P7 wiring; until then application tests
- inventory / remove sections may be skeleton only; no parallel edits in same file。

### 7.5 Verification

```bash
uv run pytest tests/contract/test_create.py -q
uv run ruff check src/worktree_provisioner/application/worktree.py tests/contract/test_create.py
uv run mypy src/worktree_provisioner/application/worktree.py
```

### 7.6 Stop conditions

- unknown Git message を collision retry に含める。
- partial failure を success result にする。
- bootstrap failure で worktree を cleanup する。
- root missing で sibling placement へ fallback する。
- linked checkout basename を namespace として使う。

### 7.7 完了条件

- WP-AC-001..007 の application-level tests pass。
- create does not import CLI / presentation。
- artifact state is structured。

## 8. Step P5 — Inventory, show, and target resolution

### 8.1 目的

root-independent Git inventory と agent-safe target resolution を移植する。

### 8.2 Ordered implementation

1. `_git_worktree_list` error conversion
2. main / current identification
3. root classification context
4. managed containment
5. raw / stable id generation
6. `WorktreeRecordView`
7. `worktree_list`
8. `resolve_worktree_target`
9. `worktree_show`

### 8.3 Test-first checkpoints

#### CP-I1: classification

- root valid
- root missing
- root blank
- env invalid
- explicit root invalid fatal
- namespace symlink
- directory symlink root
- managed / external

#### CP-I2: record attributes

- detached / bare / locked retained
- stale path
- current / main flags
- `record_exists=true`

#### CP-I3: stable ids

- main
- managed suffix
- external basename
- duplicate `~2`
- output order unchanged

#### CP-I4: target resolution

- id
- absolute path
- basename
- id priority over ambiguous basename
- ambiguous candidates
- unsupported branch
- not found

### 8.4 File ownership

- owner: `inventory-use-case`
- files:
  - `application/worktree.py` inventory section
  - `application/worktree_target.py`
  - `tests/contract/test_inventory.py`
  - `tests/contract/test_target.py`

P4 owner から `application/worktree.py` の ownership handoff を明示してから開始する。

### 8.5 Verification

```bash
uv run pytest \
  tests/contract/test_inventory.py \
  tests/contract/test_target.py \
  -q
```

### 8.6 Stop conditions

- missing / invalid env root を list blocker にする。
- `managed=false` と classification unavailable を同一意味にする。
- branch name を target として許可する。
- duplicate basename を first match で選ぶ。
- configured namespace を creation proof と説明する。

### 8.7 完了条件

- WP-AC-008 / WP-AC-009 application tests pass。
- root unavailable でも Git inventory が返る。
- candidate payload に同じ record schema が使われる。

## 9. Step P6 — Remove, containment, and cleanup

### 9.1 目的

最も destructive な operation を hard blockers、final refresh、Git-first、target-only cleanup で実装する。

### 9.2 Ordered implementation

1. blocker calculation
2. non-bypassable blocker filter
3. protected path set
4. containment guard
5. initial target resolution
6. final Git record refresh
7. re-resolution / canonical identity check
8. Git remove policy
9. post-Git containment re-check
10. target-only cleanup
11. partial remove result / error

### 9.3 Test-first checkpoints

#### CP-R1: pre-mutation blockers

matrix × `force in {False, True}`:

- main
- current
- bare
- path missing
- record missing after refresh
- protected central root
- protected namespace
- ancestor containing root / namespace
- namespace symlink lexical path

assert Git remove call count `0`.

#### CP-R2: external eligibility

- external unmanaged worktree
- invalid / missing classification root
- branch retained

#### CP-R3: target races

- target disappears after initial inventory
- target path changes
- target becomes bare
- basename becomes ambiguous

assert no wrong-path remove。

#### CP-R4: Git-first

- Git failure -> no `path_exists` / `remove_target` call
- default dirty target -> Git refusal / no cleanup
- `--force` dirty target -> success
- locked target -> refusal under provisional policy

#### CP-R5: cleanup

- leftover directory
- symlink
- broken symlink
- regular file
- unsupported type
- lstat error
- unlink error
- rmtree error
- path disappears race

assert parent / root / namespace sentinel remains。

#### CP-R6: partial remove

Git record removed + cleanup failure:

- error code `post_remove_cleanup_failed`
- `removed_record=true`
- `removed_directory=false`
- branch remains

### 9.4 File ownership

- owner: `remove-use-case`
- files:
  - `application/worktree.py` remove section / containment helpers
  - `infra/git_cli.py` remove force branch only via adapter owner review
  - `infra/fs_cli.py` only bug fix via adapter owner review
  - `tests/contract/test_remove.py`

### 9.5 Verification

```bash
uv run pytest tests/contract/test_remove.py -q
```

### 9.6 Stop conditions

- `req.force` を無視する。
- current SpecDock の unconditional double force を owner approval なしで copy する。
- `managed=false` を blocker にする。
- Git failure 後に cleanup する。
- branch を削除する。
- `git worktree prune` を呼ぶ。
- target parent / namespace を削除する。
- symlink target を follow する。

### 9.7 完了条件

- WP-AC-010 / WP-AC-011 pass。
- destructive path tests all pass。
- source parity delta is documented。

## 10. Step P7 — CLI, presentation, and JSON contract

### 10.1 目的

family 全体を standalone command として公開し、human / agent interfaces を固定する。

### 10.2 Ordered implementation

1. parser / subcommands
2. shared options
3. request construction
4. concrete Ports wiring
5. text renderers
6. JSON payload builders
7. expected error rendering
8. stdout / stderr / exit code
9. `--version`

### 10.3 Test-first checkpoints

#### CP-CLI1: help / usage

- top-level help lists four commands
- each help has exact options
- no `delete` alias
- usage error exit `2`

#### CP-CLI2: text

- absolute paths
- product prefix
- bootstrap warning stderr
- fatal error stderr

#### CP-CLI3: JSON

for every operation:

- success schema
- expected error schema
- exactly one stdout document
- no human stderr for expected JSON response
- `schema_version=1`
- correct `operation`
- `result XOR error`
- path types strings
- boolean types booleans
- `null` consistency

#### CP-CLI4: stable codes

- every WP-RQ-017 code has at least one test
- message wording is not snapshot-stable
- code / field type is snapshot-stable

### 10.4 File ownership

- owner: `cli-presentation`
- files:
  - `src/worktree_provisioner/cli.py`
  - `src/worktree_provisioner/presentation/cli_text.py`
  - `tests/contract/test_json_schema.py`
  - `tests/integration/test_cli_create.py`
  - `tests/integration/test_cli_inventory.py`
  - `tests/integration/test_cli_remove.py`

### 10.5 Prototype replacement point

P7 tests が pass した後にのみ次を行う。

- prototype `core.py` を削除
- prototype `cli.py` を final implementation に置換
- prototype `tests/test_cli.py` の unique scenario が新 tests に存在することを確認してから削除 / split

### 10.6 Verification

```bash
uv run pytest tests/contract/test_json_schema.py tests/integration -q
uv run worktree-provisioner --help
uv run worktree-provisioner create --help
uv run worktree-provisioner list --help
uv run worktree-provisioner show --help
uv run worktree-provisioner remove --help
```

### 10.7 Stop conditions

- JSON error を stderr text だけで返す。
- JSON success stdout に warning text を追記する。
- `dataclasses.asdict` で Path serialization を偶然に依存する。
- command registry / broad UseCases framework を再導入する。
- create-only README / help が残る。

### 10.8 完了条件

- WP-AC-012 pass。
- all four commands are callable。
- prototype monolith is gone。

## 11. Step P8 — Documentation, compatibility, packaging, and license

### 11.1 目的

implementation contract を operator / agent / maintainer が再検証でき、installed artifact が independent に動く状態にする。

### 11.2 README requirements

README は少なくとも次を含む。

- purpose / scope
- four command examples
- `--repo` / `--root`
- env precedence
- layout / naming
- linked-worktree normalization
- bootstrap trust warning / `--no-bootstrap`
- JSON interface
- remove safety / force policy
- branch retention
- non-scope
- development / verification commands

### 11.3 `docs/compatibility.md`

- baseline SHA
- SpecDock -> new CLI mapping
- env migration
- JSON origin mapping
- intentional force delta
- legacy env deprecation window
- existing worktree non-migration

### 11.4 `docs/safety.md`

- mutation boundaries
- retry classifier
- partial artifacts / no auto cleanup
- bootstrap code execution
- remove hard blockers
- Git-first / target-only cleanup
- symlink behavior
- no secrets / no remote

### 11.5 Packaging

- root `LICENSE`
- wheel / sdist
- no package data from SpecDock
- version `0.1.0` until publication policy changes
- no remote metadata assumed

### 11.6 Verification

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -q
uv build
```

wheel install smoke:

```bash
tmp_venv="$(mktemp -d)/venv"
python3 -m venv "$tmp_venv"
"$tmp_venv/bin/pip" install dist/worktree_provisioner-*.whl
"$tmp_venv/bin/worktree-provisioner" --help
```

### 11.7 Stop conditions

- `pyproject.toml` は MIT だが `LICENSE` がない。
- README が create-only のまま。
- docs が SpecDock Workbench を tool scope に含める。
- docs が default remove behavior と実装で異なる。
- installed wheel が source checkout / sibling path を必要とする。

### 11.8 完了条件

- WP-AC-014 / WP-AC-015 pass。
- package artifact is standalone。
- docs / help / tests agree。

## 12. Step P9 — Differential parity and full verification

### 12.1 目的

copy correctness を source baseline と独立 test suite の双方で証明する。

### 12.2 Parity fixture

`tests/parity/test_spec_dock_baseline.py` は環境変数が与えられたときだけ走る。

```text
SPEC_DOCK_SOURCE
SPEC_DOCK_EXPECTED_SHA
```

fixture は次を fail-fast する。

- source path missing
- Git repository でない
- SHA mismatch
- source CLI / runtime missing

parity test は source repo 自体を target repo として使わず、両 CLI に同等の temp Git repo fixture を与える。

### 12.3 Comparison strategy

#### Create

比較:

- selected id
- path layout
- branch
- Git worktree record
- bootstrap state
- partial artifact state

normalize:

- command prefix
- env name
- JSON availability

#### List / show

比較:

- path / branch / head
- main / current / path_exists / record_exists
- managed availability / reason
- stable ids / candidate resolution
- blockers

normalize:

- `origin=spec_dock_managed` -> `managed_namespace`
- additive detached / bare / locked fields
- envelope

#### Remove

strict parity cases:

- main/current/bare/stale/protected blocker
- external eligible
- branch retained
- Git failure -> no cleanup
- cleanup failure partial result

force delta comparison:

- SpecDock default remove
- destination `remove --force`

standalone safety case:

- destination default dirty remove refusal

### 12.4 Commands

```bash
cd /Volumes/990p2t/workspace/tools/worktree-provisioner

SPEC_DOCK_SOURCE="$tmp_source" \
SPEC_DOCK_EXPECTED_SHA=ff09fd05d9862c399d4e22e760170dcb8c46ec6a \
uv run pytest -m parity -q

uv run pytest -m 'not parity' -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv build
git diff --check
```

### 12.5 Source non-mutation verification

P0 before-state と比較する。

```bash
git -C /Volumes/990p2t/workspace/tools/spec-dock rev-parse HEAD
git -C /Volumes/990p2t/workspace/tools/spec-dock status --short --untracked-files=all
```

pre-existing user changes がある場合、clean を要求するのではなく before / after equivalence を要求する。

### 12.6 Dependency hygiene

```bash
if rg -n 'spec_dock_runtime' src tests; then
  echo 'unexpected SpecDock runtime dependency' >&2
  exit 1
fi

rg -n 'SPEC_DOCK_WORKTREE_ROOT' src tests docs README.md
```

second command の match は legacy compatibility module / tests / docs に限定されることを review する。

### 12.7 Stop conditions

- parity source SHA mismatch を skip 扱いにする。
- unexplained parity mismatch を expected delta に追加する。
- cleanup safety mismatch を text-only difference として扱う。
- full test が real root env を継承する。
- source before / after state が変わる。

### 12.8 完了条件

- WP-AC-013 / WP-AC-016 pass。
- all non-parity tests pass without SpecDock checkout。
- all documented intentional deltas are exact and approved。
- wheel install smoke pass。

## 13. Step P10 — Local delivery and handoff

### 13.1 目的

publication authority を越えず、Codex / user が independent verification と commit decision を行える状態を渡す。

### 13.2 Handoff evidence

- baseline SHA
- decision ledger
- test counts / commands / results
- parity matrix
- intentional delta list
- build artifact names / hashes
- destination `git status --short`
- source before / after equality
- known limitations

### 13.3 Commit boundary

この plan は commit を自動承認しない。user が local commit を明示した場合のみ、reviewed files を one initial commit または logically separated local commits にする。

推奨 initial history:

1. `chore: initialize worktree-provisioner package`
2. `feat: extract worktree create and inventory contracts`
3. `feat: add guarded worktree removal and JSON CLI`
4. `test: add SpecDock parity and packaging verification`

destination に既存 commit がない前提が実装開始時に崩れた場合、この commit plan を適用しない。

### 13.4 Remote / publication

次は separate explicit authorization が必要。

- `git remote add`
- push
- GitHub repository creation
- package index publication
- release/tag

### 13.5 Completion criteria

- requirement / design / plan の completion criteria を満たす。
- no unreviewed prototype production path。
- no source mutation。
- no external publication。

## 14. Step P11 — Later SpecDock migration, separate task

### 14.1 Entry conditions

- standalone tool の approved version が存在する。
- install / invocation path が運用で利用可能。
- JSON consumer migration が確認済み。
- `SPEC_DOCK_WORKTREE_ROOT` compatibility policy が決定済み。

### 14.2 Ordered migration

1. SpecDock current consumers / docs / agent instructions を inventory する。
2. `application/workbench.py` dependency decision を行う。
3. Workbench を残す場合、read-only inventory / resolver を neutral internal module へ分離する。
4. SpecDock worktree CLI を deprecated とするか、direct removal するか決める。
5. optional shim を採用する場合、external binary availability / version mismatch / exit propagation を設計する。
6. parser / registry / commands / use case wiring を remove する。
7. worktree-only contracts / ports / adapters / renderers を remaining consumer から切り離す。
8. provider assets と dogfooding mirror の双方を source-of-truth process に従って更新する。
9. SpecDock tests / docs を更新する。
10. separate PR / review / release を行う。

### 14.3 Workbench coupling gate

current `application/workbench.py` は次に直接依存する。

- `WorktreeListRequest`
- `application.worktree.worktree_list`
- `application.worktree_target.resolve_worktree_target`
- `WorktreeRecordView`

この dependency が残る限り、worktree-related code 全削除はできない。tool delivery を待たせる理由にはしないが、later removal task の mandatory gate とする。

### 14.4 Migration stop conditions

- standalone tool release 前に SpecDock capability を削除する。
- Workbench dependency を見落として import error を作る。
- historical Epic docs を current command reference と同一扱いで書き換える。
- provider asset だけ、または dogfooding mirror だけを更新する。
- existing worktrees / branches を migration side effect で移動・削除する。

### 14.5 Completion criteria

- SpecDock no longer owns create/list/show/remove runtime capability。
- retained Workbench has independent read-only inventory boundary, or Workbench itself is separately removed。
- user / agent docs point to standalone CLI。
- no existing worktree migration required。

## 15. File ownership matrix

| path | primary owner step | allowed later writers | notes |
| --- | --- | --- | --- |
| `pyproject.toml` | P1 foundation | P8 packaging | tool config only |
| `LICENSE` | P1 foundation | none without policy review | original notice retained |
| `src/worktree_provisioner/__init__.py` | P1 | P8 version | no broad exports |
| `src/worktree_provisioner/__main__.py` | P1 | P7 CLI | thin `main()` call |
| `application/contracts.py` | P3 contracts | P4-P6 via contracts owner review | stable JSON-facing types |
| `application/ports.py` | P3 contracts | P4-P6 via contracts owner review | slim protocols only |
| `application/worktree.py` | P4 create | P5 inventory -> P6 remove sequential handoff | no parallel edits |
| `application/worktree_target.py` | P5 inventory | P6 remove bug fix only | pure resolver |
| `infra/environment.py` | P3 environment | P4 root bug fix | no policy |
| `infra/git_cli.py` | P3 git-adapter | P6 remove flag review | worktree subset only |
| `infra/make_cli.py` | P3 bootstrap-adapter | P4 bootstrap bug fix | no CLI policy |
| `infra/fs_cli.py` | P3 filesystem-adapter | P6 cleanup bug fix | no Workbench code |
| `presentation/cli_text.py` | P7 CLI | P8 docs consistency fix | schema builder |
| `cli.py` | P7 CLI | P8 packaging/help | no generic framework |
| `tests/contract/test_create.py` | P4 | P9 parity hardening | source scenario mapping |
| `tests/contract/test_inventory.py` | P5 | P9 | source scenario mapping |
| `tests/contract/test_target.py` | P5 | P9 | resolver semantics |
| `tests/contract/test_remove.py` | P6 | P9 | destructive safety |
| `tests/contract/test_json_schema.py` | P7 | P9 | schema lock |
| `tests/integration/*` | P7 | P9 | subprocess CLI |
| `tests/parity/*` | P9 | none without baseline update | pinned source SHA |
| `README.md` | P8 | P10 handoff corrections | family contract |
| `docs/compatibility.md` | P8 | P9 parity evidence | migration map |
| `docs/safety.md` | P8 | P9 safety evidence | security boundary |

## 16. Quality gates

### G0 — Authority gate

- source SHA exact
- `AGENTS.md` considered
- destination actual inventory complete

### G1 — Contract gate

- requirements mapped to tests
- security decisions recorded
- no production copy before failing tests exist

### G2 — Adapter gate

- parser / Git / make / filesystem unit tests pass
- no SpecDock import

### G3 — Create gate

- naming / root / linked normalization / bootstrap / partial failure pass

### G4 — Inventory gate

- classification / stable id / target resolution pass

### G5 — Remove safety gate

- hard blockers / refresh / Git-first / target-only cleanup pass
- force policy approved

### G6 — Interface gate

- all CLI / JSON schema tests pass
- expected JSON output is clean

### G7 — Distribution gate

- lint / format / type / full tests / build / wheel install pass
- license / docs present

### G8 — Parity gate

- pinned source differential tests pass
- only approved intentional deltas remain

### G9 — Handoff gate

- source unchanged
- destination evidence complete
- no unauthorized remote / publication

## 17. Traceability table

| requirement | design decisions / sections | implementation steps | proving tests / gates |
| --- | --- | --- | --- |
| `WP-RQ-001` product identity | DD-002, §3, §4 | P1, P8 | installed package test, G7 |
| `WP-RQ-002` family CLI | DD-001, §5 | P7 | CLI help / usage, G6 |
| `WP-RQ-003` repo validation | §4.5, §5.2 | P3, P4, P7 | repo outside / detached / subdir tests, G2/G3 |
| `WP-RQ-004` root precedence | §4.3, §5.3 | P4 | exact env matrix, G3 |
| `WP-RQ-005` root validation | §6, INV-003 | P4 | WP-AC-004 matrix, G3 |
| `WP-RQ-006` layout / normalization | §6.1, INV-001/002 | P4 | linked normalization, central root tests, G3 |
| `WP-RQ-007` naming | §6.2 | P4 | auto/label/ref tests, G3 |
| `WP-RQ-008` collision | §6.3, INV-004 | P4 | dir/branch/record/Git retry tests, G3 |
| `WP-RQ-009` partial artifacts | §11, INV-004 | P4 | fake partial artifact matrix, G3 |
| `WP-RQ-010` bootstrap | §7, INV-005 | P3/P4 | make matrix / cwd, G2/G3 |
| `WP-RQ-011` inventory | §8.1/8.2 | P5 | classification / record attribute tests, G4 |
| `WP-RQ-012` target resolution | §8.3/8.4 | P5 | id/path/basename/ambiguity tests, G4 |
| `WP-RQ-013` remove eligibility | §9.1/9.3, INV-007/008/009 | P6 | hard blocker × force matrix, G5 |
| `WP-RQ-014` remove execution | §9.2/9.4, INV-010/011/012 | P6 | Git-first / cleanup / partial remove, G5 |
| `WP-RQ-015` text / exit | §5.4 | P7 | subprocess stream/exit tests, G6 |
| `WP-RQ-016` JSON | §10 | P7 | schema success/error tests, G6 |
| `WP-RQ-017` errors | §11.1 | P4-P7 | one test per code, G3-G6 |
| `WP-RQ-018` compatibility | DD-005, §10.8, §12 | P2/P9 | differential parity, G8 |
| `WP-RQ-019` license | §12 mapping | P1/P8 | package/license inspection, G7 |
| `WP-RQ-020` independence | DD-002, ALT-002 | P1/P8/P9/P10 | no-import grep, wheel smoke, source non-mutation, G7-G9 |
| `WP-NFR-001` safety | §13 invariants | P3-P7 | destructive / injection / symlink tests, G2-G6 |
| `WP-NFR-002` observability | §10/11 | P4/P6/P7 | artifact state / partial remove / JSON tests |
| `WP-NFR-003` maintainability | §3/4 | P1/P3/P8 | Ruff/Mypy/Pytest/build |
| `WP-NFR-004` performance | §6/8/9 | P4-P6 | gateway call count assertions |
| `WP-NFR-005` platform | unresolved table | P8/P9 | macOS/Linux CI; Windows not claimed |
| `WP-AC-001` basic create | §6/7 | P4/P7 | `test_create_auto_id_and_branch` |
| `WP-AC-002` collisions | §6.3 | P4 | collision matrix |
| `WP-AC-003` linked normalization | §6.1 | P4 | linked checkout integration |
| `WP-AC-004` root matrix | §5.3/6 | P4 | root validation parametrized tests |
| `WP-AC-005` invalid context | §4.5 | P3/P4 | repo/detached tests |
| `WP-AC-006` make matrix | §7 | P3/P4 | bootstrap parametrized tests |
| `WP-AC-007` partial failure | §11.1 | P4 | fake adapter partial-state tests |
| `WP-AC-008` inventory | §8 | P5 | managed/external/stale/flags tests |
| `WP-AC-009` target | §8.4 | P5 | target resolver tests |
| `WP-AC-010` blockers | §9.1/9.3 | P6 | hard blocker matrix |
| `WP-AC-011` remove cleanup | §9.2 | P6 | Git-first / target-only / branch retention |
| `WP-AC-012` JSON | §10 | P7 | JSON schema suite |
| `WP-AC-013` parity | §12 mapping | P9 | parity marker suite, G8 |
| `WP-AC-014` package | §3/4 | P8/P9 | wheel install smoke, G7 |
| `WP-AC-015` prototype replacement | DD-004, §12.3 | P7/P8 | no `core.py`, full suite |
| `WP-AC-016` source non-mutation | §16 | P0/P9/P10 | before/after Git evidence, G9 |

## 18. Final completion criteria

worktree-provisioner delivery は次のすべてを満たしたとき complete とする。

1. `create` / `list` / `show` / `remove` が standalone CLI として存在する。
2. source baseline behavior が approved intentional delta を除いて parity test で確認される。
3. linked normalization、collision、detached HEAD、root validation、make init、partial artifacts、non-retryable Git failure が dedicated tests で pass する。
4. remove hard blockers、final refresh、Git-first、target-only cleanup、branch retention が pass する。
5. JSON schema version `1` の success / error が全 command で pass する。
6. prototype create-only monolith / README が残らない。
7. `spec_dock_runtime` runtime dependency がない。
8. Ruff / format / Mypy / full Pytest / build / wheel install smoke が pass する。
9. source SpecDock repository が tool task により変更されていない。
10. remote / publication は行われていない、または別の明示 authorization / evidence がある。
11. later SpecDock removal が別 migration task として記録され、Workbench coupling が blocker として明示されている。
