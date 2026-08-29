# 実装・Final Quality Gate レポート

## 1. このレポートの証拠境界

この文書は、`worktree-provisioner` の実装候補、既出レビューfinding、修正内容、検証状態を記録する。
過去の候補SHAや過去のCI runを最終証拠として流用しない。

Git commit は自分自身のSHAを内容へ埋め込めないため、最終提出SHAは「このレポートを含む提出commit」を
`git rev-parse HEAD` で解決する。最終SHAに紐づくGitHub Actions runとChatGPT Use Strict結果は、GitHubのcheckと
最終提出メッセージを外部証拠とする。この文書内へ古いrun IDを固定しない。

## 2. 固定点

- repository: `chemitaro/worktree-provisioner`
- branch: `codex/implement-worktree-provisioner`
- 修正開始時のHEAD / upstream: `f59d43a489d1a64e7bd3daa4da675970a2238b2a`
- 比較baseline: `origin/main` の `18c80a1f222a31df0617df5c8193388b3c301e0e`
- 初回Strict reviewer session: `required-strict-github-connector-verificati-447`
- Strict thinking time: `extra-high`

このレポート更新時点の候補は未commitである。したがって `f59d43a...` やそれ以前のCIを、現候補の最終成功証拠とは
扱わない。

## 3. 初回 Final Quality Gate

開始時のexact SHA `f59d43a...` に対するChatGPT Use Strict Extra Highは `review_status=fail` だった。

- P0: 0
- P1: 4
- P2: 1

既出の主要findingは次のとおり。

1. removeがpath一致だけを比較し、branch/head変更や同basename置換を見逃す。
2. `git worktree add` が起動し得るcheckout hook/filterのauthorityが未決。
3. Make target検出時にもrepository-controlled codeが評価・実行され得るauthorityが未決。
4. 実装レポートが古いSHA、CI、test件数を最終証拠としていた。
5. Git診断がcredential-shaped valueを公開し得た。

独立レッドチームは、上記に加えてunknown path observation、traceability、schema evolution、nested remove、
型境界・重複・診断上限の問題を再現または監査した。

## 4. ブルーチームの修正

### 4.1 remove identity safety

- initial / refreshed Git factsとしてcanonical path、branch、head、detached/bare/locked、lock reasonを比較する。
- target entryをno-follow `lstat`由来の `(st_dev, st_ino)` で比較する。
- refresh前後の変更・消失・観測不能はGit mutation前にfail-closedにする。
- Git成功後に別identityの同basename targetがある場合はcleanupせず、`status=partial` として残す。
- Git成功後にtargetがmissingならcleanup不要として扱う。

### 4.2 inventory / JSON

- `path_exists` を `bool | None` とし、観測不能をmissingと区別する。
- 観測不能時は `path_observation_unavailable` blockerを返し、remove不可にする。
- JSONでは観測不能を `null` として保持する。
- このtype changeに必要なschema majorはowner decision Round 6で確定する。

### 4.3 diagnostic safety

- Git / Makeの外部診断を共通utilityでredactしてから4096文字へ制限する。
- TOKEN、PASSWORD、SECRET、API key、Authorization/Bearer形式のcredential-shaped valueを伏せる。
- 全limit値で `len(result) <= max(limit, 0)` を保証する。
- collision classificationはredaction前のraw Git diagnosticで行い、公開値だけをredactする。

### 4.4 code boundary / maintainability

- descriptor-bound directory operationをformal Protocolへ昇格し、production mutation pathの`getattr` fallbackを削除した。
- Git porcelain parserをbytes-onlyのNUL-delimited `-z` contractへ限定した。
- public error/status/operation境界をLiteral typeで表現した。
- bootstrap gatewayのtyped resultを直接利用し、coercionとcompatibility aliasesを削除した。
- inventoryの8要素tupleをnamed dataclassへ置換した。
- duplicate diagnostic helper、dead wrapper、unused field、optional-only branch、重複test fixtureを整理した。

### 4.5 acceptance / traceability

- manifestの `path::symbol` が実在することをtestで検証する。
- AC-015をskill create、AC-018をdistribution/CI contractへ対応させた。
- macOS/Linux × Python 3.10〜3.13、locked sync、quality commands、build、fresh-wheel smokeをCI static contractとして固定した。
- remove identity、unknown path、diagnostic redaction、nullable JSONの回帰testを追加した。

## 5. レッドチーム閉鎖状態

既出のコード標準finding ST-1〜ST-10は、修正後のread-only closure checkで全件closedとなった。
このclosure checkは新規全面レビューではなく、既出findingの解消確認である。

仕様側では次をclosed確認済み。

- unknown path observationとmissingの区別
- AC-015 / AC-018 traceabilityおよびmanifest symbol検証
- removeのGit facts / target identity比較
- credential-shaped Git diagnosticの非公開化

Round 6のowner authorityと仕様同期もclosed確認済みである。残る作業は、現在のレポートを含む最終候補の検証、
最終SHAのCI、同一Strict sessionのfollow-upである。

## 6. Owner decision closure

2026-08-30にownerが `docs/interview.md` Round 6のQ27〜Q31をすべてAとして確定した。

- OD-022: nested managed worktreeはlist/showで観測可能とし、removeはdirect childだけを許可する。`--force`でも迂回しない。
- OD-023: trusted repositoryのみを対象とし、Git checkout hook / clean・smudge・process filterは抑止しない。
  `--no-bootstrap` はMake bootstrapだけを無効化する。
- OD-024: Make target検出自体もrepository-controlled behaviorを評価・実行し得るものとして、trusted repositoryだけを対象とする。
- OD-025: 検出可能なGit/target identity変更はfail-closedまたはpartialとし、最終checkからGit/kernel syscallまでの
  same-user noncooperative raceは非atomicな保証上限としてscope外にする。
- OD-026: nullable `path_exists` はJSON schema v2とし、v1互換を維持しない。

requirement / design / plan / README / CLI help / skill / JSON schema / testsは、上記決定へ同期済みである。

### 6.1 ChatGPT Use advisory analysis

2026-08-30に新規ChatGPT Use session `required-repository-connector-context-repository-133` をGPT-5.6 Sol / Extra Highで
実行し、現行仕様・実装・テスト・レポートの19ファイルを分析した。最終Strict reviewer sessionとは分離している。

分析結果はQ27〜Q31のすべてで推奨案Aを支持した。

- Q27: 現行のdirect-child blocker実装を維持し、core production追加修正は不要。
- Q28: Git hook/filterを抑止せず、trusted repository境界と `--no-bootstrap` の限定された意味をhelp/README/skillへ同期する。
- Q29: Make detection方式を維持し、detection自体もtrust-freeではないことをhelp/README/skillへ同期する。
- Q30: 現行identity hardeningを維持し、final syscall windowの保証上限を明記する。
- Q31: schema version `2` へのmachine-visible production変更と、version-labelled module/docs/testsの同期が必要。

この分析はadvisoryとして扱い、その後ownerが同じ5案を独立に承認したため、実装契約へ採用した。

## 7. 現候補の暫定ローカル証拠

コード標準修正後、Round 6のpending metadata追加前にブルーチームが次を実行した。

| Command | Result |
| --- | --- |
| focused tests | PASS、70 passed |
| `uv run ruff format --check .` | PASS |
| `uv run ruff check .` | PASS |
| `uv run mypy src tests` | PASS |
| `uv run pytest -q` | PASS、348 passed / 1 skipped |
| `git diff --check` | PASS |

これは途中候補の証拠であり、owner decision反映後の最終ローカル品質ゲートを代替しない。

それ以前の途中候補では `uv build` がwheel / sdistを生成し、新規 `worktree_provisioner/diagnostics.py` が両配布物へ
収録されることを確認した。このbuildも最終配布物の証拠には使わず、最終候補で再実行する。

Round 6反映後の最終候補では、locked sync、Ruff format、Ruff lint、Mypy、Pytest（351 passed / 1 skipped）、
build、fresh-wheel smoke、wrapper shell syntax、`git diff --check`を再実行した。生成物のSHA-256は次のとおり。

- wheel: `854a976b7be03284b738eb3d8abac945b45ddb3a2762c0eb7b84a4703d0eb0f6`
- sdist: `38454dfa155b860824d3589e4eb88f21be3fc3ec9ece97134f6b41bdec94b0dd`

## 8. 最終完了条件

次をすべて満たした場合だけ完成とする。

- [x] Round 6のQ27〜Q31がowner answerとして確定している。
- [x] requirement / design / plan / README / help / skill / JSON schema / testsが回答と一致する。
- [x] 最終候補でRuff format、Ruff lint、Mypy、Pytest、build、fresh-wheel smoke、`git diff --check`が成功する。
- [ ] 最終候補をtask branchへcommit / pushする。
- [ ] 最終SHAに対するGitHub Actions macOS/Linux × Python 3.10〜3.13が全job成功する。
- [ ] 同一Strict reviewer sessionをExtra Highでfollow-upし、P0=0、P1=0、`review_status=pass`を得る。
- [ ] 最終提出でexact SHA、CI run、Strict結果、配布物hash、残るscope外を報告する。

現時点のstatusは **in progress** であり、Final Quality Gate passをまだ主張しない。
