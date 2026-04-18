# OpenRouter Routing Proxy

OpenRouter の無料モデル + Ollama ローカルモデルを束ね、自動 Failover する OpenAI 互換プロキシサーバー。

## 特徴

- **OpenAI 互換 API** (`/v1/chat/completions`)
- **動的モデルリスト取得** — OpenRouter の `:free` モデルを自動取得
- **優先順位ルーティング** — `qwen`, `nemotron` 等を優先
- **自動リトライ (Failover)** — 429 / タイムアウト時に次モデルへ切替
- **ローカル最終防衛線** — 全クラウドモデル失敗時は Ollama へフォールバック
- **ストリーミング対応** — SSE 形式でリアルタイム応答

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
    ├── model_router.py       # モデルリスト取得・優先順位付け
    └── failover.py           # 429/タイムアウト検知・次モデルへ切替
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

## Roo Code での使用

Roo Code の設定で以下を指定：

```json
{
  "openai.api.baseURL": "http://127.0.0.1:4141/v1",
  "openai.api.key": "dummy"
}
```

## ライセンス

MIT
