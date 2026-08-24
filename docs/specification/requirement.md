---
document: requirement
product: worktree-provisioner
status: proposed
baseline_repository: chemitaro/spec-dock
baseline_branch: main
baseline_sha: ff09fd05d9862c399d4e22e760170dcb8c46ec6a
verified_at: 2026-08-24
language: ja
---

# worktree-provisioner 要件定義

## 1. エグゼクティブ決定

### 1.1 推奨スコープ

独立ツールの初期完成スコープは、`create` だけではなく、現在の SpecDock が提供する次の command family 全体とする。

```text
worktree-provisioner create [LABEL]
worktree-provisioner list
worktree-provisioner show <target>
worktree-provisioner remove <target>
```

理由は次のとおりである。

1. `create`、inventory、target resolution、remove safety は同じ Git worktree record、path normalization、classification contract を共有している。
2. `create` だけを先に独立させると、作成は新ツール、確認・削除は SpecDock という二重所有になり、SpecDock の簡素化を完了できない。
3. `list` / `show` / `remove` は単なる付属機能ではなく、agent が stable target を選び、main/current/stale/bare worktree を誤削除しないための安全面である。
4. 現行実装・テストが既に family 全体を一つの境界として扱っており、後から追加するより一度に移植した方が差分と再検証を小さくできる。

### 1.2 Copy-as-is feasibility verdict

**判定: bounded extraction なら大部分を再利用可能。SpecDock runtime 全体の whole-file copy は不可。**

- ほぼそのまま移植できるもの:
  - `application/worktree_target.py`
  - `infra/make_cli.py`
  - `application/worktree.py` の worktree-specific use case、naming、collision、classification、remove guard の大部分
- symbol 単位で抽出すべきもの:
  - `application/contracts.py` の worktree dataclass / error 型
  - `application/ports.py` の `GitGateway` / `BootstrapGateway` / `EnvironmentGateway` / `FilesystemGateway` と slim `Ports`
  - `infra/git_cli.py` の worktree-related functions
  - `infra/fs_cli.py` の `path_exists` / `remove_tree` / `remove_target`
  - `presentation/cli_text.py` の worktree renderer / payload builder
- copy してはならないもの:
  - SpecDock 全 command を抱える `cli/parser.py`、`cli/registry.py`、`cli/bootstrap.py`、`cli/dispatch.py`
  - SpecDock domain、node、active pointer、GitHub Issue、Workbench、Artifact 関連 contract / adapter
  - provider asset / dogfooding mirror の二重配布構造

### 1.3 既存 prototype の扱い

`/Volumes/990p2t/workspace/tools/worktree-provisioner` の既存未 commit prototype は accepted implementation としない。

- 維持候補:
  - repository / distribution / package / CLI 名 `worktree-provisioner` / `worktree_provisioner`
  - `pyproject.toml` の `Python >= 3.10`、Hatchling、runtime dependency なし、console script
- rewrite:
  - `src/worktree_provisioner/core.py`
  - `src/worktree_provisioner/cli.py`
  - `tests/test_cli.py`
  - `README.md`
- discard:
  - create-only を canonical scope とする記述
  - generic message だけの JSON error contract
  - `detached` / `bare` / `locked` を捨てる porcelain parser
  - SpecDock family との parity を証明できない少数 smoke test だけの構成

prototype を `legacy/` 等へ残さない。採用可能な packaging 設定だけを残し、production code は source parity test を先に作成した上で置換する。

## 2. 根拠、正本、制約

### 2.1 検証済み baseline

- GitHub repository: `chemitaro/spec-dock`
- branch: `main`
- branch tip: `ff09fd05d9862c399d4e22e760170dcb8c46ec6a`
- repository root `AGENTS.md` を確認済み
- 基準日: 2026-08-24

独立ツールの parity baseline は、上記 revision の現行 source、現行 test、現行 shipped documentation とする。historical Epic 文書は設計意図の根拠として用いるが、現行 source / test / shipped documentation と矛盾する箇所では現行実装を優先し、矛盾を明記する。

### 2.2 提示情報として扱う事項

次の事項はユーザー提示情報であり、この planning task では事実として扱うが、GitHub connector では検証していない。

- source local path: `/Volumes/990p2t/workspace/tools/spec-dock`
- destination local path: `/Volumes/990p2t/workspace/tools/worktree-provisioner`
- destination は `git init` 済みだが commit / remote / upstream がない
- destination の添付 prototype は uncommitted

実装開始時には destination の全ファイルと Git 状態を改めて確認し、添付に存在しない file を推測で削除しない。

### 2.3 planning boundary

この文書は実装候補を定義する。次は実施済みとは扱わない。

- destination file の変更
- commit / remote 設定 / push / publication
- SpecDock 側の command 削除または shim 追加
- package release

## 3. 目的

- Git linked worktree の作成、inventory、target resolution、削除を SpecDock の specification authoring / management から分離する。
- 手動管理する長命 worktree を central root に一貫して配置し、衝突、partial failure、bootstrap failure を観測可能にする。
- human operator と agent の双方に、同じ Git repository の worktree を安定して確認・選択・削除できる standalone CLI を提供する。
- SpecDock の後続簡素化を可能にするが、独立ツールの delivery を SpecDock からの削除作業に依存させない。

## 4. スコープ

### 4.1 必須スコープ

- `create` / `list` / `show` / `remove`
- central root / repo namespace placement
- linked-worktree invocation normalization
- collision-safe id / directory / branch naming
- optional / non-fatal `make init`
- Git worktree record based inventory
- managed namespace classification
- stable target resolution
- main/current/bare/stale/protected path remove guard
- Git-first remove と target-only filesystem cleanup
- text output と versioned JSON output
- SpecDock から独立した package / CLI bootstrap
- source parity / intentional-delta tests

### 4.2 非スコープ

- `worktree status`
- `git worktree prune` / repair
- orphan directory discovery / cleanup
- local branch deletion
- remote branch、GitHub Issue、PR、release lifecycle
- SpecDock node / active pointer / spec tree mutation
- SpecDock Workbench copy
- Codex app 固有 metadata / Handoff / cleanup
- `.env*`、secret、credential の copy / projection
- nested `.worktrees/` placement
- legacy sibling `<repo-basename>-worktrees/` への fallback / migration
- GUI / daemon / watcher
- SpecDock 側の command removal

## 5. ユースケース

### UC-001: label なしで worktree を作成する

operator は任意の branch checkout またはその subdirectory から `create` を実行する。tool は main worktree basename を namespace に用い、`wt1`, `wt2`, ... の未使用 id を採用し、current checkout branch から新 branch を作る。

### UC-002: label 付きで worktree を作成する

operator は `issue-369` のような label を指定する。衝突しなければ id は `issue-369`、衝突時は `issue-3692`, `issue-3693`, ... となる。

### UC-003: linked worktree から新しい worktree を作成する

namespace / repo basename は invocation checkout 名ではなく Git が列挙する main worktree を基準とし、branch prefix は invocation checkout の current branch を基準とする。

### UC-004: agent が worktree を inventory / resolve する

agent は `list --json` で record を取得し、stable `id`、absolute path、basename のいずれかを `show` / `remove` target に用いる。branch name target は拒否する。

### UC-005: external linked worktree を確認・削除する

configured namespace 外で手動または別 tool が作成した同一 repository の linked worktree も inventory / show / remove 対象とする。`managed=false` は ownership blocker ではない。

### UC-006: bootstrap が失敗しても作成済み成果物を利用する

`make init` detection / execution が失敗しても worktree / branch は残し、create exit code は `0` とし、bootstrap status / warning を返す。

### UC-007: destructive remove を安全に拒否する

main、current、bare、path missing、record missing、protected cleanup path は force option の有無にかかわらず拒否する。Git remove が失敗した場合は filesystem cleanup を行わない。

## 6. 機能要件

### WP-RQ-001: product identity

- repository / distribution name は `worktree-provisioner` とする。
- Python package は `worktree_provisioner` とする。
- console command は `worktree-provisioner` とする。
- SpecDock runtime package を runtime dependency にしない。

### WP-RQ-002: command family

次の command を提供する。

```bash
worktree-provisioner create [LABEL] [--repo PATH] [--root PATH] [--no-bootstrap] [--json]
worktree-provisioner list [--repo PATH] [--root PATH] [--json]
worktree-provisioner show <target> [--repo PATH] [--root PATH] [--json]
worktree-provisioner remove <target> [--repo PATH] [--root PATH] [--force] [--json]
```

- `--repo` default は current working directory とする。
- `--repo` は checkout root または checkout 内の directory を許可し、`git rev-parse --show-toplevel` 相当で invocation checkout root に正規化する。
- `--version` と各 command の `--help` を提供する。
- `delete` alias は提供しない。

### WP-RQ-003: repository validation

- 全 command は non-bare Git checkout 内でのみ実行できること。
- Git repo 外、存在しない `--repo`、file、Git CLI unavailable は mutation 前に fatal error とすること。
- `create` は named current branch を必須とし、detached HEAD を拒否すること。
- `list` / `show` / `remove` は current branch 名を必要としないこと。ただし invocation checkout 自体は解決できなければならない。

### WP-RQ-004: root source and precedence

root source precedence は次の順とする。

1. `--root`
2. `WORKTREE_PROVISIONER_ROOT`
3. `SPEC_DOCK_WORKTREE_ROOT`（migration compatibility）

- higher-precedence source が存在するが blank の場合、lower-precedence source へ silently fallback しないこと。
- `SPEC_DOCK_WORKTREE_ROOT` 使用時は deprecation warning を返すこと。
- `create` では selected root は必須であること。
- `list` / `show` / `remove` では root は classification context のみであり、環境変数が missing / blank / invalid でも Git records を処理すること。
- explicit `--root` が invalid の場合は typo を隠さないため全 command で fatal とする。環境変数由来の invalid root は `list` / `show` / `remove` では classification unavailable diagnostic とする。

### WP-RQ-005: root validation

- `~` を展開すること。
- 展開後 absolute path であること。
- existing normal directory と directory を指す symlink を許可すること。
- relative path、regular file、broken symlink、directory として作成・利用できない path を拒否すること。
- root / namespace は必要に応じて作成できること。
- namespace path 自体が symlink の場合、`create` は拒否し、inventory classification は unavailable とすること。

### WP-RQ-006: directory layout and normalization

- main worktree は `git worktree list --porcelain` の main record として解釈すること。
- repo basename は main worktree path basename とすること。
- namespace は `<root>/<repo-basename>/` とすること。
- target path は `<root>/<repo-basename>/<repo-basename>-<id>` とすること。
- main checkout 内に `.worktrees/` / `worktrees/` を作らないこと。
- linked worktree からの invocation でも chained basename / namespace を作らないこと。
- branch prefix は invocation checkout の current branch とすること。

### WP-RQ-007: label and naming

- label は optional とする。
- validation regex は `^[a-z0-9-]+$` とする。
- uppercase、underscore、dot、space、slash、shell metacharacter、empty / blank label を拒否すること。
- label なし id は `wt1`, `wt2`, ... とすること。
- label あり id は `<label>`, `<label>2`, ... とすること。
- branch は `<current-branch>-<id>` とすること。
- generated branch は `git check-ref-format --branch` 相当で検証すること。
- candidate ceiling は `10000` とすること。

### WP-RQ-008: collision handling

candidate ごとに次を確認すること。

- canonical worktree record path collision
- target filesystem path collision
- local branch collision
- generated ref validity

`git worktree add` 実行時の既知の branch / path / checked-out collision のみ次候補へ retry する。unknown Git failure、permission、ref lock、I/O error は retry せず fatal とする。

### WP-RQ-009: create mutation and partial artifact reporting

- Git mutation は argv list、`shell=False` で実行すること。
- `git worktree add -b <branch> <path>` 相当を用いること。
- non-retryable failure 後に branch / path / record / container を自動削除しないこと。
- fatal result は attempted `id` / `path` / `branch` と observable artifact state を返すこと。
- artifact state は少なくとも `path_exists`、`branch_exists`、`record_exists` を machine-readable に表すこと。観測不能は `null` または `unknown` とし、false と断定しないこと。

### WP-RQ-010: bootstrap

- default compatibility behavior は create 成功後の automatic `make init` detection / execution とする。ただし `--no-bootstrap` を提供する。
- detection は created worktree root を `cwd` とし、`make -n init` を argv list で実行すること。
- target missing は `skipped` とすること。
- `--no-bootstrap` は `disabled` とすること。
- `make` missing、Makefile parse/include error 等は `detection_failed` とすること。
- `make init` success は `succeeded`、non-zero は `failed` とすること。
- `failed` / `detection_failed` は worktree creation を rollback せず exit code `0` とすること。
- bootstrap command は created worktree root 以外で実行しないこと。
- tool は `.env*` 等を copy しないこと。

### WP-RQ-011: inventory source and classification

- Git worktree records を inventory の正本とすること。
- configured root は ownership metadata ではなく namespace classification context とすること。
- record view は少なくとも次を持つこと。
  - `id`, `path`, `basename`, `branch`, `head`
  - `detached`, `bare`, `locked`
  - `managed`, `managed_classification_available`, `classification_reason`, `origin`
  - `main`, `current`, `path_exists`, `record_exists`
  - `removable`, `remove_blockers`
- classification reason は `root_valid`, `root_missing`, `root_blank`, `root_invalid`, `namespace_symlink` とすること。
- canonical `origin` は `managed_namespace`, `external`, `classification_unavailable` とすること。
- `managed_namespace` は「tool が作成した証明」ではなく、configured namespace 内にあるという path classification だけを意味すること。

### WP-RQ-012: stable target resolution

- main record の id は `main` とすること。
- managed namespace 内で `<repo-basename>-<suffix>` の basename を持つ record は `<suffix>` を raw id とすること。
- external record は basename を raw id とすること。
- duplicate raw id は canonical path order により `~2`, `~3`, ... を付与すること。
- target priority は exact stable id、absolute path、basename の順とすること。
- basename ambiguity は candidates 付き error とすること。
- branch name target は `unsupported_branch_target` として拒否すること。
- stable id は同一 inventory snapshot に対して deterministic であることを保証し、path move や duplicate set 変更を跨ぐ永久 ID とは説明しないこと。

### WP-RQ-013: remove eligibility

- 同一 repository の linked worktree record のみ対象とすること。
- managed / external は remove eligibility を左右しないこと。
- 次は `--force` でも削除しないこと。
  - main worktree
  - invocation current worktree
  - bare worktree
  - path missing / stale record
  - record missing
  - central root、namespace、repo root、またはそれらを包含する protected cleanup path
- mutation 直前に records を再取得し、target を再解決して TOCTOU window を縮小すること。
- related local branch を削除しないこと。

### WP-RQ-014: remove execution

本要件の provisional canonical は安全側の standalone contract とする。

- option なし: `git worktree remove <path>` 相当
- `--force`: `git worktree remove --force <path>` 相当
- locked worktree を二重 force で自動解除・削除する interface は初期版に提供しないこと。
- Git が失敗した場合、filesystem cleanup を実行しないこと。
- Git success 後に target path が残る場合だけ target-only cleanup を行うこと。
- directory は tree removal、symlink / broken symlink / regular file は target 自体を unlink し、symlink target を follow しないこと。
- parent、root、namespace、repo root を cleanup しないこと。
- Git record removal success 後の cleanup failure は partial success として `removed_record=true`, `removed_directory=false` を返すこと。

この要件は現行 SpecDock の「default force-equivalent / `--force` compatibility no-op」からの intentional safety delta であり、`SEC-DEC-001` の owner approval を release gate とする。

### WP-RQ-015: text output and exit codes

- success は `0`。
- expected input / operation failure は `1`。
- argparse usage error は `2`。
- text success は stdout、warning / fatal detail は stderr とすること。
- path は absolute path を主表示すること。
- human text は product prefix `worktree-provisioner:` を使うこと。
- machine consumer は text scraping ではなく JSON を使うこと。

### WP-RQ-016: JSON contract

全 command に `--json` を提供する。JSON は次を満たすこと。

- stdout に exactly one JSON document を出すこと。
- expected error も stdout JSON + exit code `1` とすること。
- expected JSON response では human line を stdout / stderr に混在させないこと。
- top-level に `schema_version: 1`, `status`, `operation`, `warnings` を持つこと。
- success は `result`、error は `error` を持つこと。
- path は absolute string とすること。
- error `code` と field type を stable contract とし、message wording は stable としないこと。
- additive field は schema version を上げずに追加可能とするが、field removal / rename / type change / code semantic change は major schema change とすること。

### WP-RQ-017: error contract

少なくとも次の stable code を定義すること。

- `invalid_label`
- `root_required`
- `invalid_root`
- `repository_unavailable`
- `git_unavailable`
- `detached_head`
- `git_worktree_list_failed`
- `candidate_exhausted`
- `container_create_failed`
- `git_worktree_add_failed`
- `target_not_found`
- `ambiguous_target`
- `unsupported_branch_target`
- `remove_blocked`
- `git_worktree_remove_failed`
- `post_remove_cleanup_failed`
- `internal_error`

bootstrap failure は fatal error code ではなく success result の status / warning とする。

### WP-RQ-018: compatibility policy

- parity baseline は `chemitaro/spec-dock@ff09fd05d9862c399d4e22e760170dcb8c46ec6a` とすること。
- naming、layout、normalization、collision、bootstrap status、inventory、target resolution、hard blockers、Git-first cleanup、branch retention は behavior parity を保つこと。
- product name、CLI prefix、primary env var、versioned JSON、product-neutral origin、safe force semantics、namespace symlink 経由 create の拒否は documented intentional delta とすること。
- `SPEC_DOCK_WORKTREE_ROOT` は `0.x` で migration compatibility として受理し、warning を出すこと。削除は `1.0.0` より前に行わないこと。
- no byte-for-byte text compatibility を約束しないこと。

### WP-RQ-019: licensing and attribution

- destination は MIT license を維持すること。
- SpecDock から substantial portions を copy するため、source `LICENSE` の original copyright notice を保持すること。
- package metadata だけを MIT と記載して license file を欠落させないこと。

### WP-RQ-020: independent delivery

- worktree-provisioner の build / test / execution は SpecDock checkout、sibling import、submodule、network accessを必要としないこと。
- parity verification のみ optional external baseline fixture を用いてよいこと。
- tool delivery では SpecDock repository を変更しないこと。
- remote creation、push、package publication は別 authorization とすること。

## 7. 非機能要件

### WP-NFR-001: safety

- mutation 前 validation を最大化する。
- unknown failure を collision として retry しない。
- remove は re-list / re-resolve / containment guard を通す。
- subprocess は argv list、`shell=False` とする。
- symlink cleanup は target を follow しない。
- secret-bearing file を tool 独自に複製しない。

### WP-NFR-002: reliability and observability

- partial mutation を成功に見せない。
- bootstrap outcome と worktree creation outcome を分離する。
- fatal create / partial remove は observable state を構造化して返す。
- warnings と error codes から operator / agent が次行動を判断できること。

### WP-NFR-003: maintainability

- Python `>=3.10` とする。
- runtime dependency は標準 library、Git CLI、optional `make` に限定する。
- worktree-specific contracts / ports だけを持ち、SpecDock broad runtime abstraction を複製しない。
- Ruff、Mypy、Pytest を quality gate に含める。

### WP-NFR-004: performance

- candidate preflight は通常利用で体感遅延を生まないこと。
- `list` / `show` / `remove` は必要最小限の `git worktree list --porcelain` 呼び出しとすること。
- remove safety の final refresh は性能より整合性を優先すること。

### WP-NFR-005: supported platform

- 初期 acceptance platform は macOS と Linux とする。
- Windows は code-level portability を妨げないが、symlink / path / remove semantics の acceptance evidence がない限り正式 support を表明しない。

## 8. 受け入れ条件

### WP-AC-001: basic create

- clean temp repository の current branch から label なしで実行する。
- `<root>/<repo>/<repo>-wt1` と `<branch>-wt1` が作成される。
- output / JSON に id、absolute path、branch、bootstrap status がある。

### WP-AC-002: naming and collision parity

- directory-only、branch-only、record-only collision を個別に用意する。
- label なし / ありの双方で次候補を選ぶ。
- recognized Git add collision は retry する。
- unknown Git error は一度で停止し、candidate exhaustion に偽装しない。

### WP-AC-003: linked-worktree normalization

- managed または external linked worktree から実行する。
- namespace / basename は main record、branch prefix は invocation checkout branch を用いる。
- chained namespace / directory name を作らない。

### WP-AC-004: root validation matrix

次を独立 case として検証する。

- missing
- blank
- relative
- regular file
- broken symlink
- existing directory
- directory symlink
- tilde expansion
- namespace is file
- namespace symlink
- permission / mkdir failure

failure case は branch / record / target path / bootstrap side effect を作らない。ただし namespace mkdir 後の Git failure 等、mutation 開始後の partial state は明示的に報告し、自動 cleanup しない。

### WP-AC-005: invalid context

- detached HEAD の `create` は fatal で mutation なし。
- Git repo 外 / missing repo / file repo path は全 command で fatal。
- `list` / `show` は detached linked checkout でも inventory 可能。

### WP-AC-006: make init matrix

- target success -> `succeeded`
- no Makefile / no target -> `skipped`
- `--no-bootstrap` -> `disabled`
- make unavailable -> `detection_failed`
- include / parse error -> `detection_failed`
- execution non-zero -> `failed`
- detection / execution failure は exit `0` かつ worktree / branch 保持
- detection / execution cwd は created worktree root

### WP-AC-007: partial artifact and non-retryable failure

- adapter fake が branch / record / path の一部を残して non-retryable failure を返す。
- exactly one candidate attempt で停止する。
- JSON error に attempted id/path/branch と artifact state がある。
- automatic cleanup を実行しない。

### WP-AC-008: list / show inventory

- main、managed namespace、external、detached、bare、locked、stale record を inventory する。
- root valid / missing / blank / invalid / namespace symlink classification を検証する。
- duplicate raw id に deterministic `~N` を付与する。

### WP-AC-009: target resolution

- id / absolute path / basename が同じ record を解決する。
- duplicate basename は candidates 付き `ambiguous_target`。
- exact stable id は basename ambiguity より優先する。
- branch-only target は `unsupported_branch_target`。

### WP-AC-010: remove hard blockers

- main、current、bare、path missing、record missing、central root、namespace、protected ancestor は option なし / `--force` の双方で Git remove 前に拒否する。
- external worktree は managed classification に関係なく eligible である。

### WP-AC-011: remove execution and cleanup

- default は dirty/untracked target を Git に拒否させ、cleanup しない。
- `--force` は dirty/untracked target を削除できる。
- branch は残る。
- Git failure では cleanup が呼ばれない。
- Git success + leftover directory / symlink / broken symlink / regular file を target-only cleanup する。
- cleanup failure は `removed_record=true`, `removed_directory=false`。
- parent / root / namespace sentinel が残る。

### WP-AC-012: JSON schema

- 全 command の success / expected failure が valid schema version `1` JSON を一つだけ出力する。
- error code、field type、absolute path、booleans、nullability を schema test で固定する。
- stderr に human output を混ぜない。

### WP-AC-013: behavioral parity

pinned SpecDock baseline と temp repo 上で differential test を行う。

- parity fields / state:
  - selected id
  - target path
  - branch
  - Git records
  - bootstrap status
  - inventory target resolution
  - hard blockers
  - branch retention
  - partial-state behavior
- normalize する intentional delta:
  - command / product prefix
  - primary env var
  - JSON envelope / schema version
  - `origin=spec_dock_managed` -> `origin=managed_namespace`
  - SpecDock default force-equivalent remove -> new tool `remove --force`
  - current source に専用 guard がない namespace symlink create -> new tool は `invalid_root` で拒否

### WP-AC-014: package independence

- wheel / sdist build が成功する。
- clean virtual environment へ wheel install 後に全 CLI help と temp repo smoke が成功する。
- installed package が `spec_dock_runtime` を import しない。
- sibling SpecDock checkout がなくても non-parity test 全体が成功する。

### WP-AC-015: prototype replacement

- create-only README / CLI / monolithic `core.py` が canonical implementation に残らない。
- prototype scenario は新 test matrix に吸収される。
- dead copy を `legacy/` に残さない。

### WP-AC-016: source non-mutation

- tool implementation 前後で `/Volumes/990p2t/workspace/tools/spec-dock` の status / revision に tool task 由来の差分がない。
- SpecDock removal は別 task / PR として扱う。

## 9. 互換性・移行要件

### 9.1 command migration

```text
./spec-dock/scripts/spec-dock worktree create ...
-> worktree-provisioner create ...

./spec-dock/scripts/spec-dock worktree list ...
-> worktree-provisioner list ...

./spec-dock/scripts/spec-dock worktree show ...
-> worktree-provisioner show ...

./spec-dock/scripts/spec-dock worktree remove ...
-> worktree-provisioner remove ...
```

### 9.2 environment migration

```text
SPEC_DOCK_WORKTREE_ROOT
-> WORKTREE_PROVISIONER_ROOT
```

`SPEC_DOCK_WORKTREE_ROOT` は migration window でのみ受け付ける。両方が設定されている場合は `WORKTREE_PROVISIONER_ROOT` を採用する。primary が blank の場合は legacy へ fallback せず、configuration error / unavailable classification とする。

### 9.3 existing worktrees

- existing central-root / sibling / external worktree を move / rename / prune しない。
- Git records が存在する限り `list` / `show` / `remove` で扱う。
- configured root outside の worktree は `external` classification となるが、remove blocker ではない。

### 9.4 JSON migration

- SpecDock JSON consumer は `schema_version=1` の新 envelope へ移行する。
- `managed` と `managed_classification_available` を主判定に用いる。
- old `origin=spec_dock_managed` は new `origin=managed_namespace` へ map する。
- message text に依存せず error `code` に依存する。

## 10. 未解決の Product / Policy / Security 決定

| ID | 種別 | 未解決事項 | 推奨 | 影響 / gate |
| --- | --- | --- | --- | --- |
| `SEC-DEC-001` | Security / Product | `remove` を SpecDock と同じ default force-equivalent にするか | **default non-force、`--force` で single force、locked は manual unlock** | 実装 Step 0 で owner 承認必須。承認なしで remove contract を確定しない |
| `SEC-DEC-002` | Security / Product | automatic `make init` を default に残すか | migration parity のため default auto + `--no-bootstrap`。untrusted repo では使用不可と明記 | public release 前に承認。opt-in へ変更する場合は CLI / AC-006 更新 |
| `SEC-DEC-003` | Security | `<root>/<repo-basename>` が symlink の場合に create を許可するか | **拒否**。configured namespace 外への checkout / bootstrap escape を許さない | source parity からの intentional delta。実装 Step 0 で承認 |
| `POL-DEC-001` | Compatibility | legacy env の終了時期 | `0.x` で warning 付き support、`1.0.0` 以降にのみ削除可能 | version / release policy に反映 |
| `PROD-DEC-001` | Product | Windows を初期正式 support に含めるか | 初期は macOS / Linux。Windows は evidence 取得後 | non-blocking。README support matrix に明記 |
| `POL-DEC-002` | Delivery | remote / package publication を行うか | 本 task は local verified repository まで。publication は別明示依頼 | publication / push の stop condition |

## 11. 既知の文書矛盾・stale evidence

1. Epic `report.md` は front matter が `in_progress`、PR が pending のままだが、現行 `main` には family implementation / tests / shipped docs が存在する。report は historical ledger として扱い、現在状態の正本にしない。
2. historical `design.md` の CLI-004 は normal remove と `--force` を区別しているが、現行 `application/worktree.py` は常に `force=True`、`infra/git_cli.py` は `--force --force` を使い、現行 `reference_worktree.md` は `--force` を compatibility input と説明する。remove policy は source/docs current behavior を認識した上で `SEC-DEC-001` として再決定する。
3. Epic report の `test_worktree_create_uses_sibling_container_auto_id_and_branch` は現行 test 名 `test_worktree_create_uses_central_root_auto_id_and_branch` と一致しない。
4. plan の issue 名 `worktree-list-show-delete-commands` は実際の command `remove` と一致しない。
5. current `reference_worktree.md` は worktree family と SpecDock Workbench handoff を同一 file に含む。Workbench section は独立 tool へ移植しない。
6. destination prototype README は create-only を明記しており、本要件の primary scope と矛盾する。
7. destination prototype porcelain parser は `detached` / `bare` / `locked` を保持しないため、family 全体、特に remove safety の基盤としては不十分である。
8. current create implementation は configured namespace path が symlink であることを dedicated preflight で拒否していない一方、inventory classification は `namespace_symlink` を unavailable とする。standalone では create 時も拒否する安全側 delta を `SEC-DEC-003` として明示する。
