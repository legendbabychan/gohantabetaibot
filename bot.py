import sqlite3
import random
import re
import os
import sys
import logging
from datetime import date
from threading import Thread
from flask import Flask
import discord
from discord.ext import commands
from sudachipy import dictionary, tokenizer

# --------------------------------------------------
# 1. ログ設定
# Renderのログ画面に詳細を出力するための基本設定
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------
# 2. Webサーバー設定 (Renderの常時起動対策)
# Renderがスリープするのを防ぐため、バックグラウンドで簡単なWebサーバーを動かす
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    # Renderから割り当てられるポート番号を取得 (無ければ8080)
    port = int(os.environ.get("PORT", 8080))
    # 不要なWebアクセスログを隠して、Discord側のログを見やすくする
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --------------------------------------------------
# 3. Botの基本設定・権限指定
# --------------------------------------------------
INTENTS = discord.Intents.default()
INTENTS.message_content = True  # メッセージの内容を読み取る権限を許可

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# 形態素解析器（SudachiPy）の初期化
try:
    tokenizer_obj = dictionary.Dictionary().create()
    mode = tokenizer.Tokenizer.SplitMode.C
    print("✅ SudachiPy の初期化に成功しました。")
except Exception as e:
    print(f"❌ SudachiPy の初期化エラー: {e}")

# --- 一時記憶（メモリー） ---
# サーバーが再起動すると消える一時的なデータ
last_food_dict = {}          # チャンネルごとの直近の食べ物 {チャンネルID: "単語"}
confirm_leaving_set = set()  # 「旅に出ろ」と言われて「いってらっしゃい」の返答待ちユーザーID

# --------------------------------------------------
# 4. ゲーム設定・各種データ定義（★ここで数値を調整できます）
# --------------------------------------------------
DEFAULT_PET_NAME = "ごはんたべたい"  # 初期表示される名前
MAX_TOILET_PER_DAY = 10              # 1日の最大トイレ回数

# 進化形態の設定（stage: {"name": 名前, "height": 身長}）
EVOLUTION_FORMS = {
    1: {"name": "なぞの幼体", "height": "5ごはん"},
    2: {"name": "成長期いきもの", "height": "7ごはん"},
    3: {"name": "かんぜんたい", "height": "10ごはん"}
}

# ごはんを食べさせた時のポイントと評価メッセージのテーブル
# (獲得ポイント, 表示されるメッセージ)
POINT_TABLE = [
    (1, "おいしくない"),
    (3, "ふつう"),
    (5, "おいしい"),
    (-3, "まずい"),
    (-5, "おなかを壊した")
]

# トイレに行かせた時のポイントと評価メッセージのテーブル
# (獲得ポイント, 表示されるメッセージ)
TOILET_TABLE = [
    (3, "すっきり！"),
    (1, "治った！"),
    (0, "すっきりしない……"),
    (-1, "出ない……"),
    (-3, "おなかいたい……")
]

# --------------------------------------------------
# 5. データベース処理 (SQLite)
# ユーザーのポイントやペットのデータを永続保存する仕組み
# --------------------------------------------------
def init_db():
    """データベースとテーブルの初期化（無ければ自動作成）"""
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    
    # テーブル作成
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id INTEGER PRIMARY KEY,
            points INTEGER DEFAULT 0,
            toilet_count INTEGER DEFAULT 0,
            last_toilet_date TEXT DEFAULT '',
            pet_name TEXT DEFAULT 'ごはんたべたい',
            gender TEXT DEFAULT 'オス',
            stage INTEGER DEFAULT 1
        )
    """)
    
    # テーブルの既存カラム（列）を確認し、足りない場合は追加する
    cursor.execute("PRAGMA table_info(user_points)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if "toilet_count" not in columns:
        cursor.execute("ALTER TABLE user_points ADD COLUMN toilet_count INTEGER DEFAULT 0")
    if "last_toilet_date" not in columns:
        cursor.execute("ALTER TABLE user_points ADD COLUMN last_toilet_date TEXT DEFAULT ''")
    if "pet_name" not in columns:
        cursor.execute("ALTER TABLE user_points ADD COLUMN pet_name TEXT DEFAULT 'ごはんたべたい'")
    if "gender" not in columns:
        cursor.execute("ALTER TABLE user_points ADD COLUMN gender TEXT DEFAULT 'オス'")
    if "stage" not in columns:
        cursor.execute("ALTER TABLE user_points ADD COLUMN stage INTEGER DEFAULT 1")

    conn.commit()
    conn.close()

def get_user_data(user_id: int):
    """ユーザーのデータを取得する（新規ユーザーの場合は自動作成）"""
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT points, pet_name, gender, stage, toilet_count, last_toilet_date FROM user_points WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        # 新規登録処理（性別はランダム）
        gender = random.choice(["オス", "メス"])
        cursor.execute(
            "INSERT INTO user_points (user_id, points, pet_name, gender, stage) VALUES (?, 0, ?, ?, 1)",
            (user_id, DEFAULT_PET_NAME, gender)
        )
        conn.commit()
        conn.close()
        return {"points": 0, "pet_name": DEFAULT_PET_NAME, "gender": gender, "stage": 1, "toilet_count": 0, "last_toilet_date": ""}
    
    conn.close()
    return {
        "points": row[0],
        "pet_name": row[1] if row[1] else DEFAULT_PET_NAME,
        "gender": row[2] if row[2] else "オス",
        "stage": row[3] if row[3] else 1,
        "toilet_count": row[4],
        "last_toilet_date": row[5]
    }

def set_pet_name(user_id: int, name: str):
    """ペットの名前を変更・保存する"""
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT points FROM user_points WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        gender = random.choice(["オス", "メス"])
        cursor.execute("INSERT INTO user_points (user_id, pet_name, gender) VALUES (?, ?, ?)", (user_id, name, gender))
    else:
        cursor.execute("UPDATE user_points SET pet_name = ? WHERE user_id = ?", (name, user_id))
        
    conn.commit()
    conn.close()

def update_points_and_check_evolution(user_id: int, add_pts: int) -> tuple[int, int, bool]:
    """ポイントを加算・減算し、条件を満たしていれば進化させる"""
    data = get_user_data(user_id)
    current_pts = data["points"]
    current_stage = data["stage"]
    
    new_pts = current_pts + add_pts
    new_stage = current_stage
    evolved = False

    # ★進化条件の閾値設定（ここを変更すると進化に必要なptが変わります）
    if current_stage == 1 and new_pts >= 10:
        new_stage = 2
        evolved = True
    elif current_stage == 2 and new_pts >= 1000:
        new_stage = 3
        evolved = True

    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE user_points SET points = ?, stage = ? WHERE user_id = ?", (new_pts, new_stage, user_id))
    conn.commit()
    conn.close()

    return new_pts, new_stage, evolved

def reset_pet_data(user_id: int) -> str:
    """「旅に出ろ」実行時にデータをリセット（初期化）する"""
    new_gender = random.choice(["オス", "メス"])
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_points 
        SET points = 0, stage = 1, pet_name = 'ごはんたべたい', gender = ?, toilet_count = 0, last_toilet_date = ''
        WHERE user_id = ?
    """, (new_gender, user_id))
    conn.commit()
    conn.close()
    return new_gender

def process_toilet(user_id: int) -> tuple[bool, int, str, int, int, bool]:
    """トイレ処理の判定と実行（1日10回制限）"""
    data = get_user_data(user_id)
    today_str = str(date.today())  # 今日の日付（YYYY-MM-DD）を取得

    count = data["toilet_count"]
    # 日付が変わっていたら回数を0にリセット
    if data["last_toilet_date"] != today_str:
        count = 0

    # 1日の最大制限数（10回）を超えていたら失敗として処理を抜ける
    if count >= MAX_TOILET_PER_DAY:
        return False, 0, f"今日はもうトイレに行けないよ！（1日{MAX_TOILET_PER_DAY}回まで）", data["points"], 0, False

    count += 1
    add_pts, comment = random.choice(TOILET_TABLE)
    new_pts, new_stage, evolved = update_points_and_check_evolution(user_id, add_pts)

    # データベースのトイレ回数と日付を更新
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_points SET toilet_count = ?, last_toilet_date = ? WHERE user_id = ?
    """, (count, today_str, user_id))
    conn.commit()
    conn.close()

    # 今日の残りトイレ回数を計算 (10 - 現在の回数)
    remaining_turns = MAX_TOILET_PER_DAY - count
    return True, add_pts, comment, new_pts, remaining_turns, evolved

# --------------------------------------------------
# 6. Discord イベントハンドラー (メッセージ受信時の処理)
# --------------------------------------------------
@bot.event
async def on_ready():
    """Bot起動時に実行される処理"""
    init_db()  # DBのセットアップ
    print("=" * 50)
    print(f"🎉 ログイン成功！ Bot名: {bot.user.name} (ID: {bot.user.id})")
    print("=" * 50)

@bot.event
async def on_message(message):
    """ユーザーからメッセージが届いた時の処理"""
    # Bot自身の発言には反応しない（無限ループ防止）
    if message.author.bot:
        return

    content = message.content.strip()
    user_id = message.author.id
    channel_id = message.channel.id

    # --- 処理1: 「ごはんのコマンド」（ヘルプ画面） ---
    if content == "ごはんのコマンド":
        embed = discord.Embed(
            title="🍚 ごはんBotのコマンド一覧",
            description="チャット内で使えるコマンドの一覧だよ！",
            color=0xff9900
        )
        embed.add_field(name="🍚 ごはんをあげる", value="会話に食べ物が出てきたら「〇〇食え」", inline=False)
        embed.add_field(name="🚽 トイレに行かせる", value=f"「トイレに行け」（1日{MAX_TOILET_PER_DAY}回まで）", inline=False)
        embed.add_field(name="📊 ステータス確認", value="「ごはんのステータス」", inline=False)
        embed.add_field(name="✨ 名前の変更", value="「ごはんの名前は〇〇」", inline=False)
        embed.add_field(name="✈️ 旅に出す", value="「旅に出ろ」（その後に「いってらっしゃい」で世代交代）", inline=False)
        
        await message.channel.send(embed=embed)
        return

    # --- 処理2: 「旅に出ろ」関連（ペットリセット処理） ---
    if content == "旅に出ろ":
        confirm_leaving_set.add(user_id)
        await message.channel.send("僕旅に出ちゃうよ")
        return

    if user_id in confirm_leaving_set:
        if content == "いってらっしゃい":
            confirm_leaving_set.remove(user_id)
            new_gender = reset_pet_data(user_id)
            await message.channel.send(
                f"バイバイ！今までありがとう！いってきます！✈️\n\n"
                f"✨ 新しい命が生まれました！（性別: **{new_gender}** / 体重: **0pt**）\n"
                f"たくさんご飯をあげて育ててあげてね！"
            )
            return
        else:
            confirm_leaving_set.remove(user_id)

    # --- 処理3: 「ごはんの名前は〇〇」（名前の変更） ---
    if content.startswith("ごはんの名前は"):
        new_name = content.replace("ごはんの名前は", "").strip()
        
        if not new_name:
            await message.channel.send("名前を指定してね！（例：ごはんの名前はぽち）")
            return
            
        if len(new_name) > 20:
            await message.channel.send("名前は20文字以内で指定してね！")
            return

        set_pet_name(user_id, new_name)
        await message.channel.send(f"✨ パートナーの名前を **「{new_name}」** に変更したよ！")
        return

    # --- 処理4: 「ごはんのステータス」（状態表示） ---
    if content == "ごはんのステータス":
        data = get_user_data(user_id)
        stage_info = EVOLUTION_FORMS.get(data["stage"], EVOLUTION_FORMS[1])
        
        await message.channel.send(
            f"🍚 **{message.author.display_name}** さんのパートナー情報\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"【なまえ】**{data['pet_name']}**\n"
            f"【姿・種族】**{stage_info['name']}** (第{data['stage']}段階)\n"
            f"【性別】**{data['gender']}**\n"
            f"【身長】**{stage_info['height']}**\n"
            f"【体重】**{data['points']} pt**\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return

    # --- 処理5: 「〇〇食え」（ご飯をあげる） ---
    target_food = last_food_dict.get(channel_id)

    if target_food and content == f"{target_food}食え":
        pts, comment = random.choice(POINT_TABLE)
        new_pts, new_stage, evolved = update_points_and_check_evolution(user_id, pts)
        
        del last_food_dict[channel_id]  # 一度食べたら記憶を消去
        data = get_user_data(user_id)

        msg = (
            f"むしゃむしゃ……！\n"
            f"{target_food}をたべた！（評価：{comment} / {pts:+d}pt）\n"
            f"現在の体重: **{new_pts}pt**"
        )
        
        if evolved:
            new_info = EVOLUTION_FORMS.get(new_stage, EVOLUTION_FORMS[1])
            msg += f"\n\n✨ **おや……！？ {data['pet_name']} のようすが……！**\n" \
                   f"**{new_info['name']}** に進化しました！（身長: {new_info['height']}）"

        await message.channel.send(msg)
        return

    if content == "食え":
        if target_food:
            await message.channel.send(f"「{target_food}食え」って言ってね！")
        else:
            await message.channel.send("なにをたべればいいの？")
        return

    # --- 処理6: 「トイレに行け」（トイレに行かせる） ---
    if content == "トイレに行け":
        success, pts, comment, total_pts, remaining, evolved = process_toilet(user_id)
        data = get_user_data(user_id)
        
        if success:
            msg = (
                f"トコトコ……🚽（{data['pet_name']}がトイレに行ってきた！）\n"
                f"結果：{comment}（{pts:+d}pt）\n"
                f"現在の体重: **{total_pts}pt**（今日の残りトイレ回数: {remaining}回）"
            )
            if evolved:
                new_info = EVOLUTION_FORMS.get(data["stage"], EVOLUTION_FORMS[1])
                msg += f"\n\n✨ **おや……！？ {data['pet_name']} のようすが……！**\n" \
                       f"スッキリして **{new_info['name']}** に進化しました！（身長: {new_info['height']}）"
            await message.channel.send(msg)
        else:
            await message.channel.send(f"🚽 {comment}")
        return

    # --- 処理7: 会話内の食べ物（名詞）抽出と「〇〇、食べたいなぁ」の反応 ---
    tokens = tokenizer_obj.tokenize(content, mode)
    detected_words = []

    # 除外したい言葉（ノイズ）の正規表現パターン
    NOISE_PATTERN = re.compile(r'^(w+|ｗ+|草+|笑+|あ|い|う|え|お|これ|それ|あれ|どれ|やつ|こと|もの|ため|よう|さん|ちゃん|くん|てす|テスト)$', re.IGNORECASE)

    for token in tokens:
        pos = token.part_of_speech()
        # 名詞（普通名詞・固有名詞）を抽出
        if pos[0] == '名詞' and pos[1] in ['普通名詞', '固有名詞']:
            surface = token.surface().strip()

            if len(surface) <= 1:
                continue

            # 記号や1文字の繰り返しなどのノイズを除外
            if re.match(r'^[a-zA-Z0-9\W_]+$', surface) or re.match(r'^(.)\1+$', surface):
                if surface not in ["もも", "みかん"]: 
                    continue

            if NOISE_PATTERN.match(surface):
                continue

            if pos[1] in ['非自立可能', '副詞可能']:
                continue

            detected_words.append(surface)

    # 最初に見つかった単語をチャンネルの「直近の食べ物」として記録
    if detected_words:
        selected_word = detected_words[0]
        last_food_dict[channel_id] = selected_word
        await message.channel.send(f"{selected_word}、食べたいなぁ")	

# --------------------------------------------------
# 7. アプリケーション起動処理
# --------------------------------------------------
if __name__ == "__main__":
    print("🚀 アプリケーションを起動しています...")
    
    # 1. Flask（Webサーバー）をバックグラウンドで起動
    t = Thread(target=run_flask, daemon=True)
    t.start()
    print("🌐 Webサーバー (Flask) を起動しました。")

    # 2. Discord Botのログインと起動
    TOKEN = os.environ.get("DISCORD_TOKEN")
    
    if not TOKEN:
        print("❌ 【重大なエラー】環境変数 'DISCORD_TOKEN' が取得できませんでした！ RenderのEnvironment画面を確認してください。")
    else:
        TOKEN = TOKEN.strip()
        print("🔑 トークンの取得に成功しました。Discordへの接続を開始します...")
        try:
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            print("❌ 【ログインエラー】トークンが無効です。Discord Developer PortalでReset Tokenをして貼り直し、Renderで保存したか確認してください。")
        except discord.errors.PrivilegedIntentsRequired:
            print("❌ 【Intentsエラー】Discord Developer Portalの'Bot'ページで'MESSAGE CONTENT INTENT'をONにしてください。")
        except Exception as e:
            print(f"❌ 【予期せぬエラーが発生しました】: {e}")
