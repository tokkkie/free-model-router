# .githooks/

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

| OS    | コマンド |
|-------|---------|
| Linux | `curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_linux_x64.tar.gz \| tar -xz gitleaks && sudo mv gitleaks /usr/local/bin/` |
| macOS | `brew install gitleaks` |

詳細は公式: <https://github.com/gitleaks/gitleaks#installing>
