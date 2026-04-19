# OpenRouter Routing Proxy

OpenRouter の無料モデル + Ollama ローカルモデルを束ね、自動 Failover する OpenAI 互換プロキシサーバー。

## 初回セットアップ

ローカルのプロジェクトルートで初回のみ以下を実行してください：

```bash
# Git hooksの参照先をリポジトリ内の.githooksに変更し、実行権限を付与
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/pre-push .githooks/commit-msg
```

これにより、コミット時の文字コード、改行コード、命名規則、シークレット混入等のチェックが自動実行されます。

## 特徴

- **OpenAI 互換 API** (`/v1/chat/completions`)
- **動的モデルリスト取得** — OpenRouter の `:free` モデルを自動取得
- **優先順位ルーティング** — `qwen`, `nemotron` 等を優先
- **自動リトライ (Failover)** — 429 / タイムアウト時に次モデルへ切替
- **ローカル最終防衛線** — 全クラウドモデル失敗時は Ollama へフォールバック
- **ストリーミング対応** — SSE 形式でリアルタイム応答
- **ツール呼び出し自動検証** — 新規モデル検出時に function calling の可否を自動テストし、非対応モデルを自動除外

## ディレクトリ構造

```
openrouter-routing/
├── main.py                   # FastAPI サーバー本体
├── config.json               # タイムアウト・優先度設定
├── setup.sh                  # venv 作成・依存インストール・起動
├── requirements.txt          # Python 依存パッケージ
│
├── adapters/
│   ├── base.py               # 抽象アダプター
│   ├── openrouter.py         # OpenRouter 呼び出し
│   └── ollama.py             # Ollama ローカル呼び出し
│
└── router/
    ├── model_router.py            # モデルリスト取得・優先順位付け
    ├── failover.py                # 429/タイムアウト検知・次モデルへ切替
    ├── tool_verifier.py           # モデルのツール呼び出し対応を検証
    └── tool_support_registry.py   # ツール対応モデルのキャッシュ管理
```

## セットアップ

### 1. API キー設定

```bash
cp .env.example .env
# .env を編集して OPENROUTER_API_KEY を設定
```

### 2. 起動

```bash
./setup.sh
```

初回実行時は venv 作成・依存インストール後、`.env` が作成されます。  
2回目以降は直接サーバーが起動します（デフォルト: `http://127.0.0.1:4141`）。

### 3. 動作確認

```bash
curl -X POST http://127.0.0.1:4141/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

## 設定

`config.json` で以下を調整可能：

| 項目 | 説明 |
|---|---|
| `timeout_seconds` | 各モデルへのリクエストタイムアウト（秒） |
| `model_cache_ttl_seconds` | モデルリストキャッシュ有効期限（秒） |
| `exclude_keywords` | 除外するモデルのキーワード（日本語に弱いモデル等） |
| `priority_keywords` | モデル優先順位キーワード |
| `ollama_model` | ローカル Fallback モデル名 |
| `verify_tool_support` | 起動時に新規モデルのツール呼び出し対応を検証する（デフォルト `true`） |
| `verify_timeout_seconds` | 検証リクエストのタイムアウト（秒） |
| `tool_support_cache_file` | 検証結果のキャッシュファイル名 |
| `rate_limit_cooldown_seconds` | 429 を返したモデルをスキップする秒数（デフォルト `60`、`0` で無効化） |

### ツール呼び出し検証の動作

- 起動時、OpenRouter のモデルリスト中 **キャッシュに未記録のモデル** のみ検証します
- 簡単な function calling リクエストを送り、`tool_calls` が返るかを確認します
- 非対応と判定されたモデルは以降のリクエストから自動除外されます
- 検証に失敗（429 / タイムアウト等）した場合は判定保留とし、次回起動時に再試行します
- キャッシュファイル（`tool_support_cache.json`）は Git 管理外です

## Roo Code での使用

Roo Code の設定で以下を指定：

```json
{
  "openai.api.baseURL": "http://127.0.0.1:4141/v1",
  "openai.api.key": "dummy"
}
```

## 開発ルール

詳細は `AGENTS.md` を参照してください。

- Git Hookで物理的に強制される制約に従う
- 環境/Git制約違反時は `.githooks/` のエラーログに従い修正する
- 機密情報（APIキー等）は `.env` で管理しハードコードしない

## ライセンス

MIT
