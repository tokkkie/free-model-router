# .githooks/

Git Hook によるローカル制約と、機密情報・環境固有情報の検出スキャナを提供するディレクトリ。
`core.hooksPath` を `.githooks` に指定することで、リポジトリ内の hook が発火する。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `pre-commit` | コミット前チェック（main直接コミット禁止、.gitignore違反、改行/BOM、末尾改行、秘匿情報パターン、機密情報スキャン） |
| `commit-msg` | コミットメッセージ形式チェック（`prefix: 説明` 形式、**prefix は許可リストを強制**）、機密情報スキャン |
| `pre-push` | main直接push禁止、push対象 commit message range の機密情報スキャン（amend/rebase後の最終防壁） |
| `sensitive-patterns.txt` | 機密情報・環境固有情報の検出パターン定義（hooks と Actions の共通参照元） |
| `lib/scan-sensitive.sh` | 機密情報スキャナの共有ライブラリ。hook から source される |

## セットアップ（初回のみ）

プロジェクトルートで以下を実行:

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/pre-push .githooks/commit-msg .githooks/lib/*.sh
```

## ローカル gitleaks 推奨（optional）

パターンマッチ（`sensitive-patterns.txt`）は明示パターンのみ検出するため、
未登録の既知シークレット形式（AWS/GCP/Stripe/Slack token 等）や高エントロピー文字列を
[gitleaks](https://github.com/gitleaks/gitleaks) で補完することを推奨する。

### 役割

- 既知シークレット形式（トークンのプレフィックス等）の網羅的検出
- 高エントロピー文字列の検出
- `sensitive-patterns.txt` の明示パターンでは拾えないカテゴリをカバー

### インストール

| OS | コマンド |
|---|---|
| Linux | `curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_linux_x64.tar.gz \| tar -xz gitleaks && sudo mv gitleaks /usr/local/bin/` |
| macOS | `brew install gitleaks` |
| WSL | Linux と同じ手順を WSL シェル内で実行 |

詳細は公式: <https://github.com/gitleaks/gitleaks#installing>

### 挙動

- **インストール済み**: `pre-commit` で staged 差分、`pre-push` で push range を自動スキャン
- **未インストール**: ローカル hook は警告表示のみで通過し、CI (`Sensitive Info Guard / gitleaks`) が最終ゲートとして block する

ローカル install は **必須ではない** が、CI 待ちなく早期に検出できるため開発体験向上に寄与する。

## 自リポジトリ外への非公開情報漏洩防止（ビルトイン）

**作業中のリポジトリ** から動的に `owner` を検出し、**同 owner 配下の「他リポジトリ」への参照を自動的に block** します。パターン定義ファイルへの追記は不要で、新しいリポジトリを作成しても自動で保護対象になります。

### 動作

- 自リポ情報の取得源
  - hook 実行時: `git config --get remote.origin.url` から `owner/repo` を抽出
  - workflow 実行時: `$GITHUB_REPOSITORY` / `context.repo`
- スキャン前に自リポ参照（`owner/repo`、`owner/repo#N`、`https://github.com/owner/repo/...`）を placeholder にマスク
- マスク後のテキストに `${owner}/<任意>` 形式が残れば「同 owner 他リポ参照」として block
- 他 owner のリポ参照（OSS 等）は block しない（既存 warn-regex で警告のみ）

### 判定例（作業中リポが `acme/site-a` の場合）

| 書いた内容 | 判定 |
|---|---|
| `acme/site-a`, `acme/site-a#42` | 許可（自リポ） |
| `https://github.com/acme/site-a/pull/42` | 許可（自リポ） |
| `acme/site-b`, `acme/internal-tools` | block（同 owner 他リポ） |
| `torvalds/linux`, `https://github.com/octocat/hello-world` | 許可（警告のみ） |

自リポ以外の `owner/repo` を個別に許可/拒否したい場合のみ `sensitive-patterns.txt` に `literal:owner/repo` を追記してください。

## 機密情報・環境固有情報の多層ガード

Git/GitHub に永続化される全ての文字列への機密情報混入を、以下の層で防ぐ:

| 層 | 対象 | 発火タイミング |
|---|---|---|
| `pre-commit` | staged テキストファイル内容 | commit 実行時（ローカル） |
| `commit-msg` | コミットメッセージ | commit 実行時（ローカル） |
| `pre-push` | push 対象 commit range の message | push 実行時（ローカル） |
| `.github/workflows/sensitive-info-guard.yml` (scan) | PR本文・タイトル・Issue/PRコメント | GitHub上の PR open/edit / comment create/edit 時 |
| `.github/workflows/sensitive-info-guard.yml` (gitleaks) | コミット履歴全体（既知シークレット形式・高エントロピー） | PR 発火時（CI 必須ゲート） |
| `pre-commit` / `pre-push` の gitleaks（optional） | staged 差分 / push range | ローカル（インストール済みの場合のみ） |

全ての層が **同じ `sensitive-patterns.txt`** を参照するため、パターン定義は1箇所で管理される。

## パターン定義の追記

`sensitive-patterns.txt` を編集し、書式 `<type>:<pattern>` で追記する。

### `<type>` の種類

| type | 意味 | 動作 |
|---|---|---|
| `literal` | 固定文字列マッチ（`grep -F` 相当） | 検出時 **block** |
| `regex` | 正規表現マッチ（`grep -E` 相当） | 検出時 **block** |
| `warn-regex` | 正規表現マッチ | 警告のみ（block しない） |

### 追記例

```
# プロジェクト固有のプライベートリポジトリ
literal:your-org/your-private-repo

# 社内ホスト名・ドメイン
literal:internal.example.com
literal:ci.example.corp

# 内部プロジェクト識別子
regex:\bINTERNAL-[A-Z0-9]+\b

# 社内 Slack ワークスペース
regex:https?://[a-z0-9-]+\.slack\.com/
```

### 既定で検出される対象

- **ローカル絶対パス**: `/home/<user>/`, `/Users/<user>/`, `C:\Users\<user>\`
- **プライベートネットワーク IP** (RFC1918): `10.x.x.x`, `172.16-31.x.x`, `192.168.x.x`
- **GitHub 他リポジトリ参照の汎用パターン**（警告のみ・誤検知防止）: `owner/repo#N`, `https://github.com/owner/repo`

具体的な `owner/repo` を確実にブロックしたい場合は、上記 `warn-regex` に頼らず `literal:owner/repo` の形で明示追記すること。

## スキャナライブラリ API (`lib/scan-sensitive.sh`)

hook から `. "$(dirname "$0")/lib/scan-sensitive.sh"` で source し、以下の関数を利用する:

| 関数 | 引数 | 戻り値 |
|---|---|---|
| `scan_sensitive_text "<text>"` | スキャン対象の文字列 | 0 = 検出なし, 1 = block パターン検出 |
| `scan_sensitive_file <path>` | スキャン対象のファイルパス | 同上 |

`warn-regex` の検出は stderr に出力されるが戻り値には影響しない。

## Branch Protection への組み込み

リポジトリ設定 → Branch protection rules で **`Sensitive Info Guard / scan`** を必須ステータスチェックに登録することで、PR 本文に機密情報が含まれた場合は merge が物理的に阻止される。

## 誤検知・取りこぼしへの対処

- **誤検知**: `sensitive-patterns.txt` のパターンを調整。ブロックではなく警告で済ませたい場合は `warn-regex` に変更。
- **取りこぼし**: 新しいカテゴリを `literal` / `regex` で追記。
- **一時的な緊急回避**: `git commit --no-verify` でローカル hook をバイパス可能だが、サーバ側の Actions は回避不可。使用時は理由をコミットメッセージに明記すること。
