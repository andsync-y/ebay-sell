# yafu2ebay

ヤフオク・楽天などのECサイトから商品を取得し、**eBayへの出品を支援**する Python アプリです。
リサーチ → 相場突合 → 価格算出 → カタログ/写真判定 → オリジナル出品文生成 → 出品ルーティング
を一気通貫で行います。

> ⚠️ **これは「出品支援ツール」であり、利益や収益（例: 年間◯万円）を保証するものではありません。**
> 無在庫販売の規約適合性・関税・消費税還付などは必ず税理士／専門家に確認してください（§2.14）。

## 守っているハード要件（事故・アカウント停止の防止）

- **出品文は仕入れ元の説明をコピーしません。** 商品属性からオリジナル英文のみ生成（`generation.py`）。
- **写真の自動取得は eBay 公式カタログ画像だけ。** 新品かつ UPC/EAN で特定でき、画像に厳しい
  ブランド除外に当たらない商品のみ全自動（`ebay.py` の決定木）。
- メーカー公式・検索結果・出品者の画像を取得/加工/転用する実装は**入れていません**。
- 中古・ノーブランド・カタログ外・要注意/VeROブランドは「手動写真の下書き」に回します。
- 採算割れ（必要価格 > 相場）・売れやすさ低は自動で除外（`pipeline.py` のフィルタ）。
- スクレイピングはレート制御・robots 尊重（`monitoring.py`）。公式APIがあれば優先（楽天）。
- **マルチユーザー前提。** 資格情報・トークンはユーザーごとに分離し暗号化保存。eBay は
  パスワードを保存せず OAuth トークン方式（`auth/`）。
- **鍵が無くてもオフラインで end-to-end 動作**します（サンプル/スタブにフォールバック）。

## クイックスタート

```bash
# 依存（コアは pyyaml / requests / cryptography）
pip install pyyaml requests cryptography
pip install pytest          # テスト用
pip install anthropic       # 任意: 出品文のAI生成（無くてもテンプレ生成にフォールバック）
pip install keyring         # 任意: OS資格庫バックエンド

# テスト
python -m pytest -q

# 出品プランをオフラインで生成（サンプルデータ使用）
python -m src.cli run --user demo --source rakuten --dest US --offline

# 利用可能な仕入れ元
python -m src.cli sources

# 在庫/価格同期（売切れ・値上げ検知のデモ）
python -m src.cli sync --user demo --offline

# eBay受注 → ワンタップ承認用の通知（デモ）
python -m src.cli orders --user demo
```

`--offline` を外すと為替の自動取得を試み、失敗時は `config/settings.yaml` の値に戻ります。

## 初期設定（アカウント連携）

```bash
python -m src.cli setup
```

ユーザー作成 → eBay OAuth 連携 → 楽天 App ID 登録 → （任意）ヤフオク/Anthropic →
ユーザー別パラメータ（目標利益率・既定仕向地など）の保存、という流れです。
eBay は **OAuth 2.0 認可コードフロー**で、ブラウザ同意 → 認可コード → アクセス/リフレッシュ
トークンを暗号化保存します（パスワードは保存しません）。

### 本番APIを使うための環境変数（任意）

| 変数 | 用途 |
|------|------|
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` / `EBAY_RU_NAME` | eBay OAuth アプリ資格情報 |
| `EBAY_ENV` | `production`（既定）/ `sandbox` |
| `ANTHROPIC_API_KEY` | 出品文のAI生成（未設定ならテンプレ生成） |
| `YAFU2EBAY_KEY` | 資格情報暗号化用 Fernet 鍵（未設定なら `~/.yafu2ebay/key` を生成） |
| `YAFU2EBAY_HOME` | データ保存先（既定 `~/.yafu2ebay`） |
| `YAFU2EBAY_CRED_BACKEND` | `file`（既定・暗号化ファイル）/ `keyring`（OS資格庫） |
| `YAFU2EBAY_WEBHOOK_URL` | Slack/LINE 互換 Webhook（通知先） |

これらが未設定でも、各アダプタはサンプル/スタブで動作します。

## アーキテクチャ

ロジックとI/Oを分離しているため、同じモジュール群を将来のWeb化からも再利用できます。
CLI（`cli.py`）は薄いI/O層で、ビジネスロジックは各モジュールにあります。

```
config/settings.yaml   料金表・手数料・パラメータ・除外ブランド（秘密情報は入れない）
sample_data/           オフライン動作用サンプル
src/
  models.py            データモデル（dataclass）
  config.py            設定ロード + ユーザー別オーバーライド合成
  auth/
    credentials.py     資格情報ストア（暗号化ファイル / OS資格庫）。ユーザー完全分離
    ebay_oauth.py      eBay OAuth フロー + トークン自動リフレッシュ
    onboarding.py      初期設定フロー（setup）
  users.py             ユーザー / プロファイル管理
  sources.py           仕入れ元アダプタ（BaseSource + レジストリ）
  pricing.py           サイズ推定・キャリア選定・送料込み価格算出
  fx.py                為替の自動取得＆バッファ
  comps.py             eBay sold ベースの相場・回転率
  repricer.py          競合追従の再価格設定（最低利益ライン死守）
  compliance.py        禁止/規制商品・VeRO・アカウント健全性
  sync.py              仕入れ元の在庫/価格同期（売切れ→出品停止）
  orders.py            eBay受注取得 → ワンタップ承認で仕入れ
  notify.py            通知（Console / Slack・LINE Webhook）
  analytics.py         損益ダッシュボード・見込みvs実績
  monitoring.py        スクレイパ監視・レート制御・robots
  exporter.py          輸出記録エクスポート（消費税還付エビデンス）
  ebay.py              カタログ判定・写真ルーティング・出品（API差し替え点）
  generation.py        出品文オリジナル生成（Anthropic or テンプレ）
  pipeline.py          全段オーケストレーション（user_id 単位）
  cli.py               CLI
tests/                 pytest
```

## 価格・配送ロジック（要点）

```
profit         = cost × target_margin                     （原価基準。margin_basis=price も可）
list_price_jpy = ⌈ (cost + shipping + profit) ÷ (1 − fee_rate − payment_fx_rate) ⌉
list_price_usd = list_price_jpy ÷ usd_jpy
```

- 課金重量: EMS=実重量 / FedEx・DHL=`max(実重量, 容積重量)`、容積重量=`L×W×H ÷ divisor × 1000`
- 有効キャリアの中から**最安**を採用（小物=クーリエ、大きく軽い=EMS に自然に分かれる）

## 写真ルーティング決定木（§2.6）

```
brand ∈ 除外/VeRO              → manual（下書き）
condition=new かつ (upc or ean) → catalog（全自動公開）
condition≠new                  → manual
upc/ean 無し                   → manual
```

## 仕入れ元（ECサイト）の追加

`src/sources.py` で `BaseSource` を継承し `@register("名前")` するだけ:

```python
@register("mercari")
class MercariSource(BaseSource):
    name = "mercari"
    def search(self, keyword=None, limit=20) -> list[SourceItem]:
        ...  # 属性のみ返す。説明文・画像は転用しない
```

## セキュリティ

- 秘密情報（トークン/セッション/APIキー）は**平文でリポジトリ・YAML・ログに出しません**。
  `cryptography` の Fernet で暗号化保存し、ユーザーごとにファイルを分離します。
- eBay は OAuth トークン方式・最小スコープ。期限前に refresh、失敗時は再連携を促します。
- 配布/マルチユーザー版では DB 暗号化保存・ユーザー鍵運用へ拡張できる設計です（TODO §2.14）。

## 本番投入前のTODO（§2.14）

`config/settings.yaml` とコード内 `TODO` を参照。主なもの:

- [ ] 送料テーブル・`payment_fx_rate`・カテゴリ別手数料率・`volumetric_divisor` を実値に
- [ ] eBay OAuth アプリ登録、Sell/Browse・楽天API・ヤフオク取得の本番実装
- [ ] Marketplace Insights / Terapeak のアクセス申請（sold相場）
- [ ] 通知チャネル（LINE/Slack/メール）・為替データソースの確定
- [ ] 禁止/規制商品ルール・VeRO参加ブランドの初期データ拡充
- [ ] 無在庫運用の規約適合・消費税還付/輸出免税は**税理士確認**

## ライセンス / 免責

本ソフトウェアは出品準備の支援を目的とし、各サイトの規約・各国法令・税務の遵守は利用者の
責任です。
