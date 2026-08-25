---
document: requirement
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

# worktree-provisioner 要件定義

## 1. エグゼクティブ決定

`worktree-provisioner` の初期完成スコープは、Git linked worktree の `create`、`list`、`show`、`remove` を一つの command family として提供することである。利用者は人間とエージェントの双方とし、既定出力は人間向け text、機械利用では明示的な `--json` による versioned contract を使用する。

SpecDock は機能・安全性・テスト観点の参照元に限定する。SpecDock の旧 CLI 構文、`SPEC_DOCK_WORKTREE_ROOT`、旧 JSON、message 文言、product identity への後方互換は提供しない。既存の create-only code は prototype として観察するが、採用済み architecture または完成実装とは扱わない。

今回の成果物は要件・設計・実装計画であり、standalone tool、Codex skill、wrapper の実装は後続タスクで行う。SpecDock 側の削除、shim、migration、deprecation、文書更新は本 product task の完全なスコープ外である。

## 2. 正本と適用順位

本要件の根拠は次の優先順位で扱う。

1. `chemitaro/worktree-provisioner` の `main`、commit `18c80a1f222a31df0617df5c8193388b3c301e0e`
2. 同 commit の `docs/interview.md` に記録された完了済み owner decisions
3. 同 commit の既存 `docs/specification/{requirement,design,plan}.md`
4. 同 commit の prototype code、tests、README、packaging metadata
5. `chemitaro/spec-dock@ff09fd05d9862c399d4e22e760170dcb8c46ec6a` に対する先行分析

`docs/interview.md` と既存提案文書が矛盾する場合は、インタビューを正本とする。owner decision と prototype behavior が矛盾する場合も owner decision を優先し、prototype behavior は修正対象を示す evidence としてのみ使用する。

## 3. 固定済み owner decisions

次の事項は確定済みであり、実装時に再質問・再選択しない。

| ID | 固定決定 |
| --- | --- |
| `OD-001` | 初期 product scope は `create` / `list` / `show` / `remove` の4コマンド一式とする。 |
| `OD-002` | 人間とエージェントの双方が利用する。agent-friendly を優先しつつ human usability を維持する。 |
| `OD-003` | 既定出力は human text、明示的な `--json` は stable versioned machine contract とする。 |
| `OD-004` | SpecDock は機能・安全性の参照元に限り、旧 CLI、環境変数、JSON、文言、product identity の互換性を提供しない。 |
| `OD-005` | `SPEC_DOCK_WORKTREE_ROOT` は受理しない。root source は `--root` または `WORKTREE_PROVISIONER_ROOT` のみとする。 |
| `OD-006` | 標準ローカル運用では `WORKTREE_PROVISIONER_ROOT=/Volumes/990p2t/workspace/worktrees` を設定するが、この path を product default に hard-code しない。 |
| `OD-007` | label は optional。未指定は `wt1`, `wt2`, ...、指定時は label、衝突時は連番候補とする。branch は `<current-branch>-<id>` とする。 |
| `OD-008` | Git worktree 作成成功後、`Makefile` に `init` target があれば `make init` を実行する。`--no-bootstrap` を提供する。 |
| `OD-009` | bootstrap failure は worktree を残し、process は non-zero、JSON は `status=partial` と observable artifacts を返す。自動 rollback しない。 |
| `OD-010` | 正式対応は macOS と Linux。Windows は初期版の対象外とする。 |
| `OD-011` | `remove` は default non-force。`--force` 指定時だけ一段階の explicit force を行う。 |
| `OD-012` | locked worktree は `--force` でも削除しない。先に利用者が `git worktree unlock` を実行する。 |
| `OD-013` | `list` / `show` は namespace-external worktree も表示できるが、`remove` が変更できるのは configured managed namespace 内だけとする。 |
| `OD-014` | managed namespace 自体が symlink の場合、`create` と `remove` を拒否する。 |
| `OD-015` | skill は明示的な create request を fact check 後に実行できる。create intent または対象 repository が曖昧なら確認する。 |
| `OD-016` | skill は対象 worktree の削除が明示された場合だけ remove を実行し、target と blockers を再確認する。`--force` は別途明示された場合だけ使う。 |
| `OD-017` | skill は PATH 上の installed `worktree-provisioner` を thin wrapper 経由で呼ぶ。skill に business logic を複製しない。 |
| `OD-018` | create 後、skill は id、branch、absolute path、bootstrap state を報告して終了する。Codex task 作成や current task の移動を自動実行しない。 |
| `OD-019` | standalone tool implementation は後続タスクである。 |
| `OD-020` | SpecDock-side deletion / migration は本 product task に含めず、本計画にも実装 phase を置かない。 |
| `OD-021` | repository は public の `chemitaro/worktree-provisioner` とする。 |

## 4. 目的

- Git linked worktree の provisioning、inventory、target resolution、removal を単一の standalone CLI に集約する。
- 長命の手動管理 worktree を central root 配下へ collision-safe に配置する。
- human operator と agent が同じ command contract を使い、agent は text scraping をせず versioned JSON で操作結果を判断できるようにする。
- `WTP-THREAT-001` の脅威モデル内で destructive operation を configured managed namespace に限定し、main/current/locked/bare/stale/external worktree の誤削除を防ぐ。
- create と project-local initialization を分離して観測し、bootstrap failure 時に作成済み worktree を失わない。
- SpecDock runtime や SpecDock固有 product contract に依存しない独立 package とする。

## 5. Product scope

### 5.1 必須スコープ

- `worktree-provisioner create`
- `worktree-provisioner list`
- `worktree-provisioner show`
- `worktree-provisioner remove`
- `--repo` による invocation checkout 指定
- `--root` / `WORKTREE_PROVISIONER_ROOT` による managed root 指定
- central root / repository namespace layout
- main worktree と invocation checkout の正規化
- optional label、collision-safe id/path/branch generation
- Git worktree porcelain inventory
- managed / external classification
- stable target resolution
- default non-force remove、explicit single-force remove
- locked/main/current/bare/stale/external/unsafe namespace blockers
- Git-first removal と guarded target-only cleanup
- default text output
- `--json` schema version `1`
- partial status と observable artifact state
- Python package、console entry point、wheel / sdist
- macOS / Linux verification
- Codex skill と thin wrapper

### 5.2 明示的な非スコープ

次は初期版にも本計画にも追加しない。

- `status`
- `prune`
- `repair`
- orphan worktree directory discovery / cleanup
- local branch deletion
- remote branch 操作
- GitHub Issue / Pull Request / release lifecycle
- GitHub API / `gh` CLI
- Workbench
- SpecDock node / active pointer / Artifact / dependency management
- Codex task 作成、task handoff、current task directory の自動切替
- Codex app 固有 metadata / lifecycle / cleanup
- project-specific `.env*` / secret / credential copy
- daemon、watcher、GUI
- Windows support
- SpecDock の CLI shim、deprecation、削除、移行、文書変更

## 6. Actors and use cases

### `UC-001` Human: label なしで作成

利用者は repository または linked checkout 内から `create` を実行する。tool は未使用の `wtN` を選び、central root の repository namespace 配下に worktree と新 local branch を作る。

### `UC-002` Human/Agent: label 付きで作成

利用者は `issue-369` のような label を指定する。最初の候補は `issue-369`、衝突時は `issue-3692`, `issue-3693`, ... となる。

### `UC-003` Linked checkout から作成

namespace と repository basename は Git の main worktree record を基準とし、branch prefix は command を実行した checkout の current branch を基準とする。

### `UC-004` Agent inventory

agent は `list --json` を使い、main、managed、external、detached、locked、bare、stale の各 record を一つの versioned schema で取得する。

### `UC-005` Target inspection

利用者は stable id、absolute path、directory basename のいずれかで `show` を実行する。branch name は target selector として認めない。

### `UC-006` Safe remove

利用者は managed namespace 内の対象を remove する。default は Git の通常削除であり、dirty/untracked state により Git が拒否できる。明示した `--force` だけが single force を有効にする。

### `UC-007` Bootstrap partial

Git worktree 作成は完了したが `make init` の detection または execution が失敗した場合、tool は worktree と branch を残し、non-zero と `status=partial` を返す。利用者は result の absolute path から remediation を続行できる。

### `UC-008` Skill-driven create

create intent と repository が明示されている場合、skill は fact checks の後に wrapper 経由で `--json` command を実行し、id、branch、path、bootstrap state を報告する。

### `UC-009` Skill-driven remove

対象削除が明示されている場合、skill は `show --json` で target/blockers を確認し、その後 remove を実行する。tool 自身も mutation 直前に inventory を refresh する。`--force` は利用者が明示した場合だけ付与する。

## 7. Functional requirements

### `WTP-RQ-001` Product identity and independence

- repository / distribution / CLI 名は `worktree-provisioner` とする。
- Python import package は `worktree_provisioner` とする。
- public repository は `chemitaro/worktree-provisioner` とする。
- runtime で `spec_dock_runtime` または SpecDock checkout を import / execute しない。
- runtime dependency は Python standard library を基本とし、external executable は Git、bootstrap 時の optional `make` に限定する。

### `WTP-RQ-002` Canonical CLI family

次を canonical command とする。

```bash
worktree-provisioner create [LABEL] [--repo PATH] [--root PATH] [--no-bootstrap] [--json]
worktree-provisioner list [--repo PATH] [--root PATH] [--json]
worktree-provisioner show <TARGET> [--repo PATH] [--root PATH] [--json]
worktree-provisioner remove <TARGET> [--repo PATH] [--root PATH] [--force] [--json]
```

- `--repo` default は current working directory とする。
- `--version` と各 command の `--help` を提供する。
- `delete` alias は提供しない。
- top-level `worktree` という中間 subcommand は設けない。

### `WTP-RQ-003` Repository resolution

- `--repo` は checkout root または checkout 内の directory を許可する。
- Git により invocation checkout root を absolute path へ正規化する。
- Git CLI unavailable、path missing、file path、Git checkout 外、bare repository は mutation 前に拒否する。
- `create` は named current branch を必須とし、detached HEAD を拒否する。
- `list` / `show` / `remove` は detached linked worktree からも inventory を取得できる。ただし current target 自体の remove は拒否する。

### `WTP-RQ-004` Root configuration

- 全 command は root value を必要とする。
- root source precedence は `--root`、次に `WORKTREE_PROVISIONER_ROOT` とする。
- higher-precedence value が blank / invalid でも lower source へ fallback しない。
- `SPEC_DOCK_WORKTREE_ROOT` は読み取らず、alias、warning付き fallback、deprecation window を提供しない。
- `SPEC_DOCK_WORKTREE_ROOT` だけが設定された状態は `root_required` と同等に扱う。
- `/Volumes/990p2t/workspace/worktrees` は標準ローカル運用例として文書化できるが、source code の default にしない。

### `WTP-RQ-005` Root and namespace validation

- root は `~` 展開後に absolute path でなければならない。
- existing directory、または directory を指す symlink root を許可する。
- relative path、regular file、broken symlink、利用不能 path を拒否する。
- `create` は root / namespace を必要に応じて作成できる。
- repository namespace は `<root>/<main-repo-basename>` とする。
- namespace path 自体が symlink の場合:
  - `create`: mutation 前に拒否する。
  - `remove`: mutation 前に拒否する。
  - `list` / `show`: Git inventory は表示してよいが、managed classification を unavailable とし、removal eligibility を与えない。
- containment 判定は lexical path だけでなく canonical path も確認する。
- containment は snapshot と再確認による race reduction であり、`WTP-THREAT-001` の final syscall window に対する
  atomic prevention または非協調 process への絶対保証を意味しない。

### `WTP-RQ-006` Layout and naming

- main worktree は `git worktree list --porcelain` の main record から決定する。
- `repo-basename` は main worktree path の basename とする。
- worktree path は `<root>/<repo-basename>/<repo-basename>-<id>` とする。
- label なし id は `wt1`, `wt2`, ... とする。
- label あり id は `<label>`, `<label>2`, `<label>3`, ... とする。
- label は `^[a-z0-9-]+$` に限定する。
- branch は `<invocation-current-branch>-<id>` とする。
- generated branch は Git ref validation を通す。
- main checkout 内の nested `.worktrees/` または `worktrees/` へ fallback しない。

### `WTP-RQ-007` Collision and retry

各 candidate について少なくとも次を検査する。

- Git worktree record path collision
- filesystem path collision
- local branch collision
- generated branch ref validity

preflight 後の `git worktree add` で発生した既知の path/branch/checked-out collision のみ次候補へ retry する。permission、ref lock、I/O、unknown Git failure は retry せず、observed artifacts とともに失敗する。無限 retry を避ける bounded ceiling を持つ。

### `WTP-RQ-008` Create mutation and artifact observation

- Git add は shell string ではなく argv list で実行する。
- create は `git worktree add -b <branch> <path>` 相当を使用する。
- non-retryable failure 後に branch、path、record、container を自動 rollback しない。
- machine result は、観測可能な範囲で次を返す。
  - `container_exists`
  - `worktree_path_exists`
  - `branch_exists`
  - `worktree_record_exists`
- 観測不能は `false` と断定せず `null` とする。

### `WTP-RQ-009` Bootstrap

- default では Git worktree 作成成功後に bootstrap detection を行う。
- `Makefile` に `init` target があれば created worktree root を `cwd` として `make init` を実行する。
- `--no-bootstrap` は detection と execution の両方を行わず `disabled` とする。
- `init` target がない場合は `skipped` とし、create は exit `0` の完全成功とする。
- `make init` 成功は `succeeded` とし、exit `0` とする。
- `make` unavailable、Makefile parse/include error、target detection error は `detection_failed` とする。
- `make init` non-zero は `failed` とする。
- `detection_failed` / `failed` の場合:
  - worktree / branch を残す。
  - automatic rollback を行わない。
  - process exit は `1` とする。
  - JSON `status` は `partial` とする。
  - created result、bootstrap detail、observable artifacts を返す。
- bootstrap command は arbitrary path で実行できる汎用 command interface にしない。

### `WTP-RQ-010` Inventory

- Git worktree records を inventory の正本とする。
- configured namespace は ownership database ではなく path classification context とする。
- `list` は main / managed / external を含む同一 repository の全 records を表示する。
- record payload は少なくとも次を含む。
  - `id`, `path`, `basename`, `branch`, `head`
  - `detached`, `bare`, `locked`, `lock_reason`
  - `main`, `current`, `path_exists`, `record_exists`
  - `managed`, `classification_available`, `classification_reason`, `origin`
  - `removable`, `remove_blockers`
- `origin` は `managed_namespace`, `external`, `classification_unavailable` とする。
- `managed=true` は「configured namespace 内に安全に containment される」という意味に限定し、この tool が作成した provenance を証明しない。

### `WTP-RQ-011` Stable target resolution

- target は stable id、absolute path、directory basename を許可する。
- branch name は target として扱わない。
- exact stable id を path/basename より優先する。
- basename が複数 record に一致する場合は candidate records を含む `ambiguous_target` とする。
- duplicate raw id は deterministic な `~2`, `~3`, ... suffix で一つの inventory snapshot 内で一意化する。
- stable id は inventory snapshot の selector であり、path move や record set 変更を跨ぐ永久 identifier とは説明しない。

### `WTP-RQ-012` Remove eligibility

`remove` は、`WTP-THREAT-001` の脅威モデル内で、次をすべて満たす target だけを変更できる。

- 同一 repository の Git linked worktree record である。
- configured managed namespace 内に canonical containment される。
- main worktree ではない。
- invocation current worktree ではない。
- bare worktree ではない。
- locked worktree ではない。
- path が存在する。
- final refresh 後も同じ canonical target record として解決できる。
- namespace が symlink ではない。
- target が root、namespace、main repo、またはそれらを包含する protected path ではない。

external worktree は `list` / `show` では可視だが、`outside_managed_namespace` blocker により `remove` を拒否する。`--force` はこの blocker を解除しない。

### `WTP-RQ-013` Remove execution

- default は `git worktree remove <path>` 相当とする。
- `--force` は `git worktree remove --force <path>` 相当の一段階だけとする。
- double force、自動 unlock、`git worktree prune`、branch deletion を実行しない。
- locked record は Git command を呼ぶ前に `locked_worktree` blocker で拒否する。
- initial resolve 後、mutation 直前に Git records を再取得し、target と blockers を再評価する。
- Git remove が失敗した場合は filesystem cleanup を実行しない。
- Git remove 成功後に target path が残る場合だけ、containment を再検証して target-only cleanup を行う。
- cleanup は `lstat` 相当で type を確認し、symlink target を follow しない。
- parent、root、namespace、main repository を削除しない。
- descriptor-bound operation が利用できる場合も、最後の bound check と Git / kernel mutation の間に発生する
  非協調 ancestor rename を原子的に防止するとは主張しない。検出した干渉は fail-closed または `partial` として返す。
- branch は削除せず、result の `branch_deleted` は常に `false` とする。
- Git record removal 後の cleanup failure は exit `1`、JSON `status=partial`、`removed_record=true`, `removed_directory=false` とする。

### `WTP-RQ-014` Human text interface

- default output は human-readable text とする。
- success facts は stdout、warning / error detail は stderr とする。
- absolute path を主表示する。
- prefix は `worktree-provisioner:` とする。
- create 完全成功は id、branch、absolute path、bootstrap state を表示する。
- create partial は stdout に作成済み facts、stderr に bootstrap failure を表示し、exit `1` とする。
- text wording の byte-for-byte compatibility は保証しない。

### `WTP-RQ-015` JSON interface

- `--json` は全4 command に提供する。
- schema は `schema_version: 1` から開始する。
- stdout に exactly one JSON document を出す。
- expected `ok` / `partial` / `error` response では stderr に human duplicate を出さない。
- common fields は `schema_version`, `status`, `operation`, `result`, `error`, `warnings` とする。
- `status` は `ok`, `partial`, `error` のいずれかとする。
- `ok`: `result` は object、`error` は `null`。
- `partial`: `result` と `error` の双方を持つ。
- `error`: `result` は `null`、`error` は object。
- path は absolute string、boolean は JSON boolean、観測不能値は `null` とする。
- parser usage error も `--json` が明示されていれば schema `1` の error document と exit `2` を返す。
- consumer は message text ではなく error/warning code と typed fields を使用する。

### `WTP-RQ-016` Exit-code contract

| condition | exit code |
| --- | ---: |
| command complete success | `0` |
| expected operational error | `1` |
| create bootstrap partial | `1` |
| remove post-cleanup partial | `1` |
| CLI usage / argument parsing error | `2` |
| `--help` / `--version` | `0` |

unexpected internal exception は secret-bearing traceback を user output に出さず、exit `1` と `internal_error` を返す。development test では original exception を cause として検証できるようにする。

### `WTP-RQ-017` Stable error and blocker codes

少なくとも次を machine contract とする。

Error codes:

- `usage_error`
- `git_unavailable`
- `repository_unavailable`
- `bare_repository_unsupported`
- `root_required`
- `invalid_root`
- `unsafe_namespace`
- `invalid_label`
- `detached_head`
- `git_worktree_list_failed`
- `candidate_exhausted`
- `container_create_failed`
- `git_worktree_add_failed`
- `bootstrap_detection_failed`
- `bootstrap_failed`
- `target_not_found`
- `ambiguous_target`
- `unsupported_branch_target`
- `remove_blocked`
- `git_worktree_remove_failed`
- `post_remove_cleanup_failed`
- `internal_error`

Remove blocker codes:

- `main_worktree`
- `current_worktree`
- `bare_worktree`
- `locked_worktree`
- `path_missing`
- `record_missing_after_refresh`
- `target_changed_after_refresh`
- `outside_managed_namespace`
- `classification_unavailable`
- `protected_cleanup_path`
- `unsafe_namespace`

### `WTP-RQ-018` Skill and thin wrapper

- repository は tool と別に Codex skill deliverable を含む。
- skill は command semantics、fact checks、authorization boundary、JSON interpretation を記述する。
- thin wrapper は PATH 上の `worktree-provisioner` を `exec` するだけとし、root selection、target resolution、Git、make、filesystem logic を持たない。
- wrapper は argv、stdout、stderr、exit status をそのまま伝播する。
- CLI が PATH にない場合だけ明確な installation error を返す。
- skill の automated invocation は常に `--json` を使用する。
- explicit create request では repository/root の事実確認後に実行できる。label 未指定は owner-approved auto-id であり、それだけを理由に確認しない。
- create intent、repository、または requested side effect が曖昧な場合は実行前に確認する。
- remove は explicit target removal のみ実行する。実行前に `show --json` で target と blockers を確認する。
- `--force` は user が force を明示した場合だけ使用する。
- create 後は id、branch、absolute path、bootstrap status を報告し、task lifecycle を変更しない。

### `WTP-RQ-019` Packaging and installation

- Python `>=3.10` を維持する。
- build backend は Hatchling を維持してよい。
- console entry point は `worktree-provisioner = worktree_provisioner.cli:main` とする。
- wheel / sdist を build できる。
- clean virtual environment または `uv tool` installation 後、PATH から実行できる。
- package metadata に MIT license を記載し、root `LICENSE` を配布物へ含める。
- package index publication は実装 completion の必須条件にしない。public Git repository の tag / exact SHA から install できる検証を持つ。
- skill source の installation location は tool runtime と分離できるが、wrapper は installed CLI だけに依存する。

### `WTP-RQ-020` Platform support

- macOS と Linux の test evidence を持つ。
- path、symlink、`lstat`、Git worktree、executable lookup の挙動を両 platform で検証する。
- Windows を supported と表示しない。

### `WTP-RQ-021` Compatibility and provenance

- SpecDock との compatibility contract は存在しない。
- behavior parity を acceptance requirement にしない。
- SpecDock の source / tests から、機能 coverage と安全 regression scenario を抽出してよい。
- source-to-destination mapping は provenance と reviewability のために保持するが、旧 symbol、旧 message、旧 output、旧 force behavior の維持義務を生じさせない。
- `SPEC_DOCK_WORKTREE_ROOT` migration / deprecation plan を作らない。
- existing worktree を move / rename / migrate しない。Git records と configured namespace classification により観測するだけとする。

### `WTP-RQ-022` Prototype disposition

- 現在の `src/worktree_provisioner/core.py`、create-only CLI、tests、README は prototype である。
- packaging identity、Python version、Hatchling、console entry point、dependency-free direction は再利用候補とする。
- create-only scope、legacy env lookup、bootstrap failure exit `0`、generic JSON error、incomplete porcelain parser は採用しない。
- production replacement 後、prototype implementation を `legacy/` 等へ残さない。
- prototype-specific test は owner-approved contract に書き換え、legacy env test と bootstrap exit `0` test は反転する。

## 8. Non-functional requirements

### `WTP-NFR-001` Safety

- destructive scope は、`WTP-THREAT-001` の脅威モデルの範囲で managed namespace に限定する。
- mutation 前 validation、mutation 直前 refresh、mutation 後 containment recheck を行う。
- unknown Git failure を collision と誤分類しない。
- subprocess は argv list、`shell=False` とする。
- namespace symlink と locked worktree を fail-closed に扱う。
- bootstrap failure、Git partial、cleanup partial を自動 rollback しない。
- secret / environment file を複製しない。

#### `WTP-THREAT-001` Namespace ancestor race boundary

「destructive scope は managed namespace に限定する」という保証は、管理 root / namespace を同時に
rename・replace しない協調的な tool operation と、通常の非協調 process がこの境界を変更しない脅威モデルを
前提とする。対象操作は次の safety guard を持つ。

- preflight で lexical / canonical containment、namespace type、symlink、protected path を検証する。
- mutation 直前に Git inventory と namespace を refresh し、target identity と blocker を再評価する。
- namespace symlink を拒否し、descriptor-bound Git / filesystem operation と no-follow cleanup を利用できる環境では
  開いた directory inode に操作を束縛する。
- mutation 後に canonical containment と namespace identity を再確認し、検出した干渉は fail-closed error または
  `status=partial` として公開する。部分成果物を自動削除しない。

任意の同一ユーザーの外部・非協調 process が、最後の check の後かつ Git CLI / kernel syscall の前に managed root または
namespace の ancestor inode を rename / replace することを、現在の Git CLI argv architecture のまま macOS / Linux
共通の非特権 primitive で原子的に禁止することはできない。この final syscall window は明示的に out of scope であり、
本 tool はその race の atomic prevention を主張しない。干渉を mutation 前に検出できた場合は fail-closed、Git 後の
再確認で検出した場合は partial とし、非協調 process による未検出の rename / replace について destructive scope の
絶対保証を与えない。

### `WTP-NFR-002` Observability

- result は id、path、branch、bootstrap、artifact state、remove state を構造化する。
- false と unknown を区別する。
- agent が exit code と JSON だけで complete / partial / error を識別できる。
- human text でも partial artifact の所在を把握できる。

### `WTP-NFR-003` Maintainability

- worktree-specific contracts と adapters に限定する。
- unnecessary generic command registry、broad service container、SpecDock domain types を導入しない。
- test は temp Git repositories と fake gateways を使い、live workspace を変更しない。
- Ruff、Mypy、Pytest を quality gate に含める。

### `WTP-NFR-004` Determinism

- stable id assignment は同一 Git inventory snapshot で deterministic とする。
- JSON field types と enum semantics を schema tests で固定する。
- host environment の legacy variable や unrelated root config が test に混入しない。

### `WTP-NFR-005` Performance

- ordinary inventory は必要最小限の Git subprocess で完了する。
- create candidate loop は bounded とする。
- remove final refresh は性能より安全性を優先する。

## 9. Acceptance criteria

### `WTP-AC-001` Product/package identity

- clean installation から `worktree-provisioner --version` と4 command の help が成功する。
- runtime import graph に `spec_dock_runtime` がない。
- public repository metadata と package identity が `worktree-provisioner` で一致する。

### `WTP-AC-002` Basic create

- temp Git repository、named current branch、valid root で label なし create を実行する。
- `<root>/<repo>/<repo>-wt1` と `<current-branch>-wt1` が作成される。
- text / JSON に id、branch、absolute path、bootstrap state がある。

### `WTP-AC-003` Label and collision matrix

- valid label first candidate、directory-only collision、branch-only collision、record collision、retryable Git add collisionを検証する。
- label なしは `wtN`、label ありは `<label>N` を選ぶ。
- invalid label matrix は mutation を一切起こさない。
- unknown Git error は次 candidate へ進まない。

### `WTP-AC-004` Linked-worktree normalization

- linked checkout から create する。
- namespace / basename は main worktree、branch prefix は invocation checkout branch を使う。
- chained namespace / basename を作らない。

### `WTP-AC-005` Root and namespace

- `--root` precedence、new env、missing、blank、relative、file、broken symlink、existing directory、directory symlink root、tilde expansion、mkdir failure を検証する。
- `SPEC_DOCK_WORKTREE_ROOT` だけを設定しても受理しない。
- source code に `/Volumes/990p2t/workspace/worktrees` の hard-coded default がない。
- namespace symlink は create/remove を mutation 前に拒否する。
- list/show は namespace symlink 時も Git inventory を返し、classification unavailable とする。

### `WTP-AC-006` Bootstrap matrix

- no Makefile / no init -> `skipped`, exit `0`, status `ok`
- `--no-bootstrap` -> `disabled`, exit `0`, status `ok`
- init success -> `succeeded`, exit `0`, status `ok`
- make unavailable -> `detection_failed`, exit `1`, status `partial`
- parse/include/detection failure -> `detection_failed`, exit `1`, status `partial`
- init execution non-zero -> `failed`, exit `1`, status `partial`
- partial response に id、branch、path、bootstrap、artifacts がある。
- worktree / branch は残り、rollback command は呼ばれない。

### `WTP-AC-007` Git add partial artifacts

- fake gateway が branch/path/record の各 partial combination を返す。
- command は exit `1`, status `error`, code `git_worktree_add_failed` を返す。
- observable artifact fields と nullability が正しい。
- automatic cleanup を行わない。

### `WTP-AC-008` Inventory coverage

- main、managed、external、detached、bare、locked、stale record を list する。
- record flags、origin、classification、blockers を typed JSON で確認できる。
- external record は observable だが `outside_managed_namespace` により `removable=false` となる。

### `WTP-AC-009` Target resolution

- stable id、absolute path、basename が同一 record を解決する。
- duplicate raw id は deterministic `~N` を持つ。
- basename ambiguity は candidates 付き error。
- branch-only selector は `unsupported_branch_target`。

### `WTP-AC-010` Remove namespace boundary

- clean managed target は削除できる。
- clean external target は default / `--force` の双方で Git remove 前に拒否する。
- namespace symlink、main、current、bare、stale は default / `--force` の双方で拒否する。

### `WTP-AC-011` Force and locked semantics

- dirty/untracked managed target は default remove で Git に拒否され、cleanup を行わない。
- 同じ target は explicit `--force` で削除できる。
- adapter argv は force なし、または single `--force` だけである。
- locked target は default / `--force` とも `locked_worktree` で拒否し、Git remove を呼ばない。

### `WTP-AC-012` Remove refresh and cleanup

- target disappearance、path change、new ambiguity、new blocker を final refresh で検出する。
- Git remove failure 後は filesystem inspection/cleanup を行わない。
- Git success 後の leftover directory / symlink / broken symlink / regular file を target-only で処理する。
- parent/root/namespace/main repo sentinel は残る。
- cleanup failure は `status=partial`, exit `1`, `removed_record=true`, `removed_directory=false`。
- local branch は残る。

### `WTP-AC-013` JSON schema and streams

- 全 command の ok / partial / error response が schema version `1` に適合する。
- `result` / `error` nullability と field types を固定する。
- expected JSON response は stdout の exactly one document で、stderr は空である。
- `--json` usage error は exit `2` と versioned error JSON を返す。

### `WTP-AC-014` Human interface

- default は text である。
- complete success は exit `0`。
- bootstrap partial は作成済み facts を表示しつつ exit `1`。
- absolute path と product prefix が確認できる。

### `WTP-AC-015` Skill create behavior

- explicit create request では wrapper が `create ... --json` を一度実行する。
- repo/root/intent が曖昧な request では実行せず確認を返す。
- label 省略だけでは確認を要求せず auto-id を利用する。
- result report は id、branch、absolute path、bootstrap state を含む。
- task creation / task directory mutation の呼び出しがない。

### `WTP-AC-016` Skill remove behavior

- explicit target removal だけを処理する。
- wrapper は最初に `show <target> --json`、次に `remove <target> --json` を呼ぶ。
- tool の final refresh contract も application test で確認する。
- user が force を明示しない限り `--force` を付けない。
- ambiguous target / blockers / external target では remove を実行しない。

### `WTP-AC-017` Thin wrapper

- fake PATH executable に対して argv が byte-for-byte で伝播する。
- stdout、stderr、exit status が伝播する。
- wrapper source に Git、make、root precedence、JSON parsing、target logic がない。
- CLI missing 時は clear non-zero error となる。

### `WTP-AC-018` Packaging and platform

- wheel / sdist build が成功する。
- clean venv / `uv tool` install 後に CLI smoke が成功する。
- macOS と Linux の CI / local evidence がある。
- Windows support を表示しない。

### `WTP-AC-019` Prototype replacement

- create-only README/help/code path が final product contract に残らない。
- production code に legacy env lookup がない。negative test は legacy variable を設定して無視されることを検証してよい。
- bootstrap failure exit `0` test が存在しない。
- incomplete porcelain parser が detached/bare/locked を失わない実装に置換される。
- dead prototype を `legacy/` に残さない。

### `WTP-AC-020` Scope integrity

- status/prune/repair/branch deletion/remote/GitHub/Workbench/Codex task lifecycle を実装しない。
- tool implementation task で SpecDock repository を編集しない。
- plan に SpecDock deletion/migration phase が存在しない。

## 10. Compatibility and migration policy

### 10.1 No SpecDock compatibility

次を互換対象としない。

- `spec-dock worktree ...` CLI shape
- `SPEC_DOCK_WORKTREE_ROOT`
- SpecDock JSON envelope / field names / enum names
- SpecDock message wording / prefix
- SpecDock default force-equivalent remove
- SpecDock product identity

### 10.2 Existing worktrees

- tool は既存 worktree を移動、rename、import、migration しない。
- same repository の Git records は list/show で観測する。
- configured managed namespace 内で safety invariant を満たす record だけ remove 対象になり得る。
- 「SpecDock が作成したか」は判定・保存しない。

### 10.3 JSON evolution

- schema version は integer `1` から開始する。
- field removal、rename、type change、enum semantic change、error code semantic changeは schema major update を必要とする。
- additive field は consumer が unknown fields を無視できる前提で schema `1` 内に追加可能とする。
- package SemVer と JSON schema version は別管理とする。

## 11. Implementation-level choices that remain local

owner decisions は完了している。次は product decision ではなく、外部 contract を変えない範囲で coder が locally 選択できる実装詳細である。

- worktree-specific application modules の細かな分割数
- Protocol / dataclass の内部名称
- Git version / locale 差を隔離する retryable collision classifier の内部実装
- namespace race を縮小する `lstat` / descriptor / canonical recheck の具体方式
- `WTP-THREAT-001` の脅威モデルを変更しない限り、非協調 ancestor rename の atomic prevention を追加実装しない
- skill source の repository 内配置と host skill directory への install packaging
- CI provider 上の macOS/Linux matrix version

これらの選択により CLI、JSON、exit code、safety invariant、skill authorization boundary を変更してはならない。
