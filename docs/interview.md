---
document: interview
product: worktree-provisioner
status: complete
baseline_repository: chemitaro/spec-dock
baseline_branch: main
baseline_sha: ff09fd05d9862c399d4e22e760170dcb8c46ec6a
started_at: 2026-08-24
language: ja
---

# worktree-provisioner 要件・設計インタビュー

## 1. 目的

SpecDock の worktree 機能を独立ツールへ移行する際の Product、Policy、Security、互換性、運用上の判断を所有者へ確認する。完了した回答は、ChatGPT Use に再共有して `requirement.md`、`design.md`、`plan.md` を更新するための正規入力とする。

## 2. 確認済みの事実

- 解析基準は `chemitaro/spec-dock@ff09fd05d9862c399d4e22e760170dcb8c46ec6a` である。
- 解析時点のローカル SpecDock `HEAD` と `origin/main` は一致し、作業ツリーは clean だった。
- 移行先は `/Volumes/990p2t/workspace/tools/worktree-provisioner` である。
- 移行先は Git 初期化済みだが、commit、remote、upstream はない。
- 移行先にある create-only コードは検証用プロトタイプであり、採用済み設計ではない。
- 現行 SpecDock は `create`、`list`、`show`、`remove` を同一の worktree capability として提供している。
- SpecDock の Workbench は inventory と target resolver に依存しているため、SpecDock 側の全削除は独立ツール完成後の別判断を必要とする。
- このインタビュー中は実装、commit、remote 作成、push、公開を行わない。

## 3. 設計木

```text
product boundary
├── command scope
│   ├── inventory/target contract
│   └── remove safety policy
├── consumer contract
│   ├── human text interface
│   ├── agent JSON interface
│   └── skill/wrapper behavior
├── compatibility policy
│   ├── environment migration
│   ├── intentional safety deltas
│   └── differential parity evidence
├── bootstrap policy
│   ├── automatic make execution
│   └── opt-out and trust warning
├── delivery boundary
│   ├── implementation task
│   ├── skill delivery
│   └── later SpecDock removal task
└── distribution boundary
    ├── supported platforms
    ├── repository/publication
    └── package installation
```

## 4. Round 1 — Product boundary

Status: answered on 2026-08-24

### Q1 — 初期完成スコープ

SpecDock から独立させる初期完成スコープを選ぶ。

- A: `create` / `list` / `show` / `remove` の4コマンド一式
- B: `create` のみ。確認と削除は当面 SpecDock に残す
- C: `create` / `list` / `show`。destructive な `remove` は後続版にする

推奨: A。作成、inventory、target resolution、remove safety は同じ Git worktree record と path classification を共有し、二重所有を避けられるため。

Answer: A。`create` / `list` / `show` / `remove` の4コマンド一式を初期完成スコープとする。

### Q2 — 主な利用者と公開インターフェース

誰がどの契約を使うツールにするかを選ぶ。

- A: 人間とエージェントの双方。human-readable text と versioned JSON を正式契約にする
- B: エージェント中心。JSON のみを正式契約にし、text は補助表示にする
- C: 人間中心。text のみを正式契約にし、スキルは text を解釈する

推奨: A。CLI 単体でも利用でき、Codex skill は安定した JSON を利用できるため。

Answer: A。人間とエージェントの双方が使用する。主な利用頻度はエージェントを想定し、agent-friendly でありながら人間にも使いやすいインターフェースにする。

### Q3 — SpecDock 互換性の強さ

移植時に現行挙動をどの程度維持するかを選ぶ。

- A: behavior parity を原則とし、安全性や製品名など承認済み差分だけ変更する
- B: ユースケースだけ維持し、CLI、JSON、環境変数、細かな失敗挙動は新規設計する
- C: 現行ファイルを可能な限りそのままコピーし、挙動変更を避ける

推奨: A。現行テストを移植根拠に使いつつ、SpecDock 固有runtimeや危険な既定値を持ち込まずに済むため。

Answer: Aを基礎にする。ただし SpecDock の旧CLI、環境変数、JSONなどとの後方互換性自体は要求しない。同じ種類の機能を持つことを要求する。behavior parity を検証材料として使い、互換性契約としては扱わない、という解釈を次ラウンドで確認する。

### Q4 — 実装作業のタスク境界

準備・仕様化後の実装と、SpecDock 側の削除をどう切り分けるかを選ぶ。

- A: 今回を調査・仕様化で閉じ、独立ツール実装を別タスク、SpecDock 削除をさらに別タスクにする
- B: このタスクで独立ツール実装まで続け、SpecDock 削除だけ別タスクにする
- C: このタスクで独立ツール実装と SpecDock 削除まで一括して行う

推奨: A。移行先の仕様判断と受入証拠を固定してから実装でき、Workbench coupling を別の移行リスクとして扱えるため。

Answer: A。今回のタスクは調査・仕様化で閉じ、独立ツール実装は別タスクとする。SpecDock 側の機能削除は本作業では行わず、別の作業として扱う。今回の設計・計画のスコープ外とする。

### Q5 — `make init` bootstrap の既定動作

worktree 作成後、対象リポジトリに `Makefile` と `init` target がある場合の動作を選ぶ。

- A: デフォルトで `make init` を実行し、`--no-bootstrap` で明示的に無効化できる
- B: デフォルトでは実行せず、`--bootstrap` 指定時だけ実行する
- C: standalone tool は bootstrap を行わず、skill または利用者が別途実行する

推奨: A。ただし任意コード実行であることを help と stderr に明示し、非対話利用では `--no-bootstrap` を選べるようにする。

Answer: A。`Makefile` に `init` target があれば、worktree作成後に基本的に `make init` を実行する。worktree作成時に必要な処理は各リポジトリの `Makefile` の `init` に定義する運用ルールとする。`--no-bootstrap` による明示的な無効化は許容する。

### Q6 — 初期配布対象プラットフォーム

初期版で正式対応を表明するOSを選ぶ。

- A: macOS と Linux。Windows は非対応と明記する
- B: macOS のみ
- C: macOS、Linux、Windows を初期版から正式対応する

推奨: A。Git CLI と Python の構成を保ちつつ、現在の利用環境以外にも過度な設計負債なく対応できるため。

Answer: A。macOS と Linux を正式対応とし、Windows 対応は不要とする。

## 5. Round 2 — Safety and interface contracts

Status: answered on 2026-08-24

### Q7 — 「同じ機能」と「互換性」の境界

- A: SpecDock を挙動調査とテストケースの基準には使うが、旧CLI構文、旧環境変数、JSON形式、文言の後方互換は提供しない
- B: CLI構文と環境変数だけは互換にし、JSONや文言は新規設計する
- C: 利用者から見える挙動も可能な限り互換にする

推奨: A。独立した製品契約を作りながら、機能漏れや安全性退行の検出には既存実装を利用できる。

Answer: A。SpecDock は機能・安全性・テストケースの調査基準にするが、旧CLI構文、旧環境変数、旧JSON形式、文言の後方互換は提供しない。

### Q8 — `remove` の強制削除

- A: 通常は非強制でGitの拒否を尊重し、`--force`指定時だけ一段階の強制削除を行う
- B: SpecDock同様、通常実行でも強制削除する
- C: `--force`自体を提供しない

推奨: A。dirty worktreeの誤削除を既定で防ぎ、明示的な意図がある場合だけ解除できる。

Answer: A。通常は非強制とし、`--force` 指定時だけ一段階の強制削除を行う。

### Q9 — locked worktree の削除

- A: `--force`でも削除せず、利用者が先に`git worktree unlock`する
- B: `--force`指定時だけunlockして削除する
- C: 通常実行でも自動unlockして削除する

推奨: A。Gitのlockを別主体が置いた保護境界として尊重できる。

Answer: A。locked worktree は `--force` でも削除せず、利用者が明示的に `git worktree unlock` してから削除する。

### Q10 — 管理namespace外のworktree

- A: `list` / `show`には表示するが、`remove`はこのツールが管理するnamespace内だけ許可する
- B: Git worktree recordがあり、main/current/bare/locked等のblockerがなければnamespace外も削除できる
- C: namespace外は`list` / `show`からも除外する

推奨: A。観測性を失わず、ツールの破壊的変更範囲を自身の管理領域に限定できる。

Answer: A。namespace外のworktreeは `list` / `show` には表示するが、このツールの `remove` 対象にはしない。

### Q11 — 管理namespaceがsymlinkの場合

- A: `create`と`remove`を拒否し、実ディレクトリへの修正を要求する
- B: symlinkの解決先がcentral root配下なら許可する
- C: symlinkを通常ディレクトリと同様に扱う

推奨: A。lexical pathとresolved pathの差によるcontainment判定の抜け道を避けられる。

Answer: A。管理namespaceがsymlinkの場合は `create` と `remove` を拒否する。

### Q12 — `make init`失敗時の扱い

- A: 作成済みworktreeを残し、partial successとしてpathと失敗内容を返す。自動削除しない
- B: worktreeを自動削除して作成前の状態へ戻す
- C: 成功扱いにしてwarningだけを出す

推奨: A。初期化処理が作ったファイルや外部状態を安全にrollbackできる保証がないため。

Answer: A。`make init` 失敗時は作成済みworktreeを残し、partial successとしてpathと失敗内容を返す。自動rollbackしない。

### Q13 — CLIの標準出力モード

- A: デフォルトは人間向けtext、エージェントは明示的な`--json`を使う
- B: TTYならtext、非TTYなら自動的にJSONへ切り替える
- C: デフォルトをJSONにし、人間向けtextは`--text`で選ぶ

推奨: A。スクリプトの出力形式が実行環境で暗黙に変わらず、人間にも自然なCLIになる。

Answer: A。デフォルトは人間向けtextとし、エージェントは明示的に `--json` を指定する。

### Q14 — Codex skillのcreate実行権限

- A: 利用者がworktree作成を明示した場合は、事実確認後にskillが実行する。曖昧な依頼では実行前に確認する
- B: createであっても、skillは常に最終確認を求める
- C: skillはコマンド例だけ示し、実行しない

推奨: A。明示依頼では余分な対話を増やさず、意図が不明な副作用だけを防げる。

Answer: A。利用者がworktree作成を明示した場合は事実確認後にskillが実行し、曖昧な依頼の場合だけ実行前に確認する。

### Q15 — Codex skillのremove実行権限

- A: 利用者が対象worktreeの削除を明示した場合だけ実行し、対象とblockerを再確認する。`--force`は別途明示が必要
- B: removeは常に実行直前の確認を求める
- C: skillからremoveは実行しない

推奨: A。通常の明示的削除を自動化しつつ、対象違いと暗黙の強制削除を防げる。

Answer: A。利用者が対象worktreeの削除を明示した場合だけskillが実行する。対象とblockerを再確認し、`--force` は別途明示されなければ使用しない。

## 6. Round 3 — Configuration and skill boundary

Status: answered on 2026-08-24; Q18-Q21 interpretation awaiting confirmation in Round 4

### Q16 — 管理rootの指定

- A: `--root` または `WORKTREE_PROVISIONER_ROOT` を必須とし、machine-specificなhard-coded defaultを持たない
- B: `/Volumes/990p2t/workspace/worktrees` を既定値にする
- C: OSごとのユーザーデータディレクトリを既定値にする

推奨: A。複数環境で誤った場所へ作成せず、skill側では設定不足を明確に案内できる。

Answer: A。`--root` または `WORKTREE_PROVISIONER_ROOT` を使用する。標準運用では `WORKTREE_PROVISIONER_ROOT=/Volumes/990p2t/workspace/worktrees` を設定し、このworkspace rootを使う。hard-coded product defaultにはしないという解釈を採る。

### Q17 — SpecDock旧環境変数

- A: `SPEC_DOCK_WORKTREE_ROOT` は受理せず、新しい `WORKTREE_PROVISIONER_ROOT` だけを使う
- B: `0.x`の間だけwarning付きで受理する
- C: 無期限にaliasとして受理する

推奨: A。後方互換不要というQ7の判断と一致し、独立ツールへSpecDock固有名を残さない。

Answer: A。`SPEC_DOCK_WORKTREE_ROOT` は受理せず、`WORKTREE_PROVISIONER_ROOT` だけを使う。

### Q18 — create時のidとbranch命名

- A: labelは任意。未指定なら`wt1`、指定時は`<label>`、衝突時は連番。branchは`<現在branch>-<id>`
- B: labelを必須にし、labelをそのままbranch名にする
- C: id、path、branchをすべて利用者が個別指定する

推奨: A。現行の衝突回避と親branchの文脈を維持し、通常利用の入力を最小化できる。

Answer: Aと暫定解釈。labelは任意、未指定は`wt1`、指定時は`<label>`、衝突時は連番、branchは`<現在branch>-<id>`とする。音声認識の曖昧さがあるためRound 4で確認する。

### Q19 — bootstrap失敗時のprocess exit code

- A: worktree作成済みでも初期化未完了なのでnon-zeroとし、JSONには`partial`とartifact stateを返す
- B: worktree作成自体は完了したためexit `0`とし、warningと`bootstrap.status=failed`を返す
- C: text利用はexit `0`、`--json`利用はnon-zeroにする

推奨: A。エージェントやshell scriptが未初期化worktreeを完全成功と誤認せず、残存pathから復旧できる。

Answer: Aと暫定解釈。bootstrap失敗時はnon-zeroを返し、JSONに`partial`とartifact stateを返す。音声認識の曖昧さがあるためRound 4で確認する。

### Q20 — skillからのツール呼び出し

- A: PATH上にinstallされた`worktree-provisioner`を薄いwrapperから呼び、skill内に業務ロジックを複製しない
- B: `/Volumes/990p2t/workspace/tools/worktree-provisioner`を固定して毎回`uv run`する
- C: skill内へツール実装をコピーし、単独で動かす

推奨: A。ツールを唯一の実装にし、skillは操作方法、事実確認、安全確認、JSON解釈だけを担当できる。

Answer: Aと暫定解釈。PATH上にinstallされた`worktree-provisioner`を薄いwrapperから呼ぶ。音声認識の曖昧さがあるためRound 4で確認する。

### Q21 — create後のskillの責務

- A: 作成結果のid、branch、absolute path、bootstrap状態を報告して終了し、別task作成や自動移動はしない
- B: 作成後、そのpathを使う新しいCodex taskを自動作成する
- C: 現在のtaskの作業ディレクトリを自動的に新worktreeへ切り替える

推奨: A。worktree provisionとCodex task lifecycleを分離し、利用者が次の作業方法を選べる。

Answer: A。create後はid、branch、absolute path、bootstrap状態を報告して終了し、別task作成や自動移動は行わない。

### Q22 — ChatGPT Use Strictの準備方法

現在の移行先にはcommit、remote、upstreamがなく、Strictはそのままでは起動できない。Strictの明示指定は既存upstreamへのtask-scoped commit/pushを許可するが、GitHub repositoryやremoteの新規作成までは許可しない。

- A: 今回は通常のChatGPT Useで、interviewとR/D/Pをlocal attachmentとして渡す。実装用repositoryが公開準備された後のreviewでStrictを使う
- B: 移行先のGitHub repository作成、remote設定、初回commit/pushも今回の追加スコープとして承認し、その後Strictを使う
- C: SpecDockのcleanなGitHub branchをStrictの固定点にし、移行先文書とinterviewはattachmentとして渡す

推奨: A。未確定の提案仕様を公開履歴へ固定せず、今回のローカル仕様作成と将来のexact-SHA reviewを分離できる。Strictを今回必須にする場合はBを選び、repository名と公開範囲を次ラウンドで決める。

Answer: B。移行先のGitHub repositoryを作成し、remote設定、初回commit/pushを行った後、同じChatGPT sessionをStrict wrapperで継続する。

## 7. Round 4 — Publication preparation confirmation

Status: answered on 2026-08-24

### Q23 — 音声認識されたQ18〜Q21の回答確認

次の解釈がすべて正しいか確認する。

- Q18: A。optional label、`wt1` / label連番、`<current-branch>-<id>`
- Q19: A。bootstrap失敗はnon-zero + JSON `partial`、worktreeは残す
- Q20: A。PATH上のinstalled CLIをthin wrapperから呼ぶ
- Q21: A。結果を報告して終了し、task作成や自動移動はしない

推奨: A（4件すべてこの解釈で確定）。違う項目だけ個別に訂正してよい。

Answer: A。Q18、Q19、Q20、Q21の4件すべてを記載どおり確定する。前回回答の曖昧さは音声入力の誤りだった。

### Q24 — GitHub repositoryのvisibility

- A: private repositoryとして作成する
- B: public repositoryとして作成する

推奨: A。提案仕様とcreate-only prototypeを含む初期状態であり、実装・security review完了前に公開しない方が安全である。

Answer: B。`chemitaro/worktree-provisioner` を public repository として作成する。

### Q25 — Strict用initial commitの範囲

- A: 現在の移行先全体（scaffold、create-only prototype、tests、interview、proposed R/D/P）をprototypeであることが分かる初期commitとしてpushする
- B: proposed documentsだけをcommitし、prototype codeは別の場所へ退避してGit管理対象から外す

推奨: A。ChatGPTが実際のprototypeと文書の不一致をexact GitHub commit上で確認でき、Strict終了後の実装taskも同じ履歴から開始できる。

Answer: A。現在の移行先全体をinitial commitに含め、GitHubへpushしてStrictの固定点にする。

## 8. Round 5 — Shared understanding confirmation

Status: confirmed on 2026-08-24

### Q26 — インタビュー完了確認

Round 1〜4の回答、既知事実、scope外、GitHub publication authorityを含む設計木のfrontierは空になった。`docs/interview.md`をChatGPT Use Strictへ渡す正規のowner decision artifactとして確定してよいか。

推奨: A。共通理解に到達したと確認し、repository作成、initial commit/push、同じChatGPT sessionへのStrict follow-upへ進む。

Answer: A。Round 1〜4の回答を正規のowner decision artifactとして確定し、共通理解に到達した。repository作成、initial commit/push、同じChatGPT sessionへのStrict follow-upへ進む。

## 9. 回答後に展開する判断

- command scope に応じた remove force、locked、stale、protected path policy
- JSON schema、error code、stdout/stderr、exit code contract
- root path、環境変数、legacy compatibility policy
- symlink、containment、race、partial artifact policy
- skill の自動実行範囲、確認事項、wrapper の責務
- packaging、install method、repository/publication policy
- parity test、acceptance gate、SpecDock removal sequence

## 10. ChatGPT Use 再共有条件

- 設計木の frontier が空である。
- 全回答について、選択肢、自由記述、理由、例外を記録している。
- ユーザーが shared understanding に到達したことを明示的に確認している。
- 未決事項を ChatGPT 側で推測して埋めないよう明記できる。
