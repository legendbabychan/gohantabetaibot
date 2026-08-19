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
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------
# 2. Webサーバー設定 (Render常時起動用)
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --------------------------------------------------
# 3. Botの基本設定・権限指定
# --------------------------------------------------
INTENTS = discord.Intents.default()
INTENTS.message_content = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

try:
    tokenizer_obj = dictionary.Dictionary().create()
    mode = tokenizer.Tokenizer.SplitMode.C
    print("✅ SudachiPy の初期化に成功しました。")
except Exception as e:
    print(f"❌ SudachiPy の初期化エラー: {e}")

# 一時記憶
last_food_dict = {}          # {channel_id: "単語"}
confirm_leaving_set = set()  # 返答待ちユーザーID

# --------------------------------------------------
# 4. ゲーム設定・各種データ定義
# --------------------------------------------------
DEFAULT_PET_NAME = "なぞのたまご"  # 初期表示される名前
MAX_TOILET_PER_DAY = 10           # 1日の最大トイレ回数

# ★ 進化フォームの詳細データ構造
EVOLUTION_FORMS = {
    # 第1段階（共通）
    "stage1": {
        "name": "なぞのたまご",
        "height": "3ごはん",
        "desc": "なにが生まれるかわからない不思議データ"
    },
    
    # 第2段階（タイプA〜E）
    "stage2": {
        "A": {"name": "ぷるぷるゼリー", "height": "5ごはん", "desc": "丸くてぷるぷる、ゼリー状の小さい青色の生き物"},
        "B": {"name": "ちびドラゴン",   "height": "8ごはん", "desc": "赤色の小さなドラゴン。人間の肩に乗るサイズ"},
        "C": {"name": "ふたばボール",   "height": "4ごはん", "desc": "小さな双葉が生えた緑色の球体"},
        "D": {"name": "みずのさかな",   "height": "6ごはん", "desc": "水でできた少し透けている魚"},
        "E": {"name": "かみなりぐも",   "height": "6ごはん", "desc": "黄色い電気がぴりぴりしている雷雲"}
    },
    
    # 第3段階（タイプ × 性別）
    "stage3": {
        "A": {
            "オス": {"name": "ぷるぷるキング", "height": "9ごはん", "desc": "王様の冠をつけた少し固めの青いゼリー"},
            "メス": {"name": "ぷるぷるプリンセス", "height": "8ごはん", "desc": "お姫様の冠をつけた少し固めの紫ゼリー"}
        },
        "B": {
            "オス": {"name": "レッドドラゴン", "height": "15ごはん", "desc": "人間より少し大きい赤い龍。赤い炎を吐く"},
            "メス": {"name": "イエロードラゴン", "height": "13ごはん", "desc": "人間より少し大きい黄色い龍。黄色の炎を吐く"}
        },
        "C": {
            "オス": {"name": "たいぼくのたぬき", "height": "10ごはん", "desc": "トトロの木のような立派な大樹の姿"},
            "メス": {"name": "サンフラワー",     "height": "8ごはん", "desc": "ひまわりのようなきれいなお花の姿"}
        },
        "D": {
            "オス": {"name": "ディープイルカ", "height": "12ごはん", "desc": "深い青色の水でできたイルカ"},
            "メス": {"name": "アクアイルカ",   "height": "12ごはん", "desc": "鮮やかな水色でできたイルカ"}
        },
        "E": {
            "オス": {"name": "ブルーサンダー",   "height": "14ごはん", "desc": "水色の雷をまとった2足歩行の電撃戦士"},
            "メス": {"name": "ピンクサンダー",   "height": "14ごはん", "desc": "ピンク色の雷をまとった2足歩行の電撃戦士"}
        }
    }
}

# ごはん評価テーブル (獲得せいちょう, コメント)
POINT_TABLE = [
    (1, "おいしくない"),
    (3, "ふつう"),
    (5, "おいしい"),
    (-3, "まずい"),
    (-5, "おなかを壊した")
]

# トイレ評価テーブル (獲得せいちょう, コメント)
TOILET_TABLE = [
    (3, "すっきり！"),
    (1, "治った！"),
    (0, "すっきりしない……"),
    (-1, "出ない……"),
    (-3, "おなかいたい……")
]

# --------------------------------------------------
# 5. データベース処理 (SQLite)
# --------------------------------------------------
def init_db():
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id INTEGER PRIMARY KEY,
            points INTEGER DEFAULT 0,
            toilet_count INTEGER DEFAULT 0,
            last_toilet_date TEXT DEFAULT '',
            pet_name TEXT DEFAULT '',
            gender TEXT DEFAULT 'オス',
            stage INTEGER DEFAULT 1,
            pet_type TEXT DEFAULT ''
        )
    """)
    
    # 既存のカラムチェック＆自動追加
    cursor.execute("PRAGMA table_info(user_points)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if "pet_type" not in columns:
        cursor.execute("ALTER TABLE user_points ADD COLUMN pet_type TEXT DEFAULT ''")

    conn.commit()
    conn.close()

def get_user_data(user_id: int):
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT points, pet_name, gender, stage, toilet_count, last_toilet_date, pet_type 
        FROM user_points WHERE user_id = ?
    """, (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        gender = random.choice(["オス", "メス"])
        cursor.execute(
            "INSERT INTO user_points (user_id, points, pet_name, gender, stage, pet_type) VALUES (?, 0, '', ?, 1, '')",
            (user_id, gender)
        )
        conn.commit()
        conn.close()
        return {
            "points": 0, "pet_name": "", "gender": gender, 
            "stage": 1, "toilet_count": 0, "last_toilet_date": "", "pet_type": ""
        }
    
    conn.close()
    return {
        "points": row[0],
        "pet_name": row[1],
        "gender": row[2] if row[2] else "オス",
        "stage": row[3] if row[3] else 1,
        "toilet_count": row[4],
        "last_toilet_date": row[5],
        "pet_type": row[6] if row[6] else ""
    }

def get_current_form_info(stage: int, pet_type: str, gender: str):
    """現在のステージ・タイプ・性別から表示用データ（デフォルト名、身長、説明）を取得"""
    if stage == 1:
        return EVOLUTION_FORMS["stage1"]
    elif stage == 2:
        return EVOLUTION_FORMS["stage2"].get(pet_type, EVOLUTION_FORMS["stage2"]["A"])
    elif stage == 3:
        type_dict = EVOLUTION_FORMS["stage3"].get(pet_type, EVOLUTION_FORMS["stage3"]["A"])
        return type_dict.get(gender, type_dict["オス"])
    return EVOLUTION_FORMS["stage1"]

def set_pet_name(user_id: int, name: str):
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
    """ポイントの加算と進化チェック"""
    data = get_user_data(user_id)
    current_pts = data["points"]
    current_stage = data["stage"]
    current_type = data["pet_type"]
    
    new_pts = current_pts + add_pts
    new_stage = current_stage
    new_type = current_type
    evolved = False

    # ★ 進化判定（10せいちょうで第2段階、1000せいちょうで第3段階）
    if current_stage == 1 and new_pts >= 10:
        new_stage = 2
        new_type = random.choice(["A", "B", "C", "D", "E"])  # タイプA〜Eをランダム決定
        evolved = True
    elif current_stage == 2 and new_pts >= 1000:
        new_stage = 3
        evolved = True

    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_points SET points = ?, stage = ?, pet_type = ? WHERE user_id = ?
    """, (new_pts, new_stage, new_type, user_id))
    conn.commit()
    conn.close()

    return new_pts, new_stage, evolved

def reset_pet_data(user_id: int) -> str:
    """「旅に出ろ」実行時のリセット処理"""
    new_gender = random.choice(["オス", "メス"])
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_points 
        SET points = 0, stage = 1, pet_name = '', gender = ?, toilet_count = 0, last_toilet_date = '', pet_type = ''
        WHERE user_id = ?
    """, (new_gender, user_id))
    conn.commit()
    conn.close()
    return new_gender

def process_toilet(user_id: int) -> tuple[bool, int, str, int, int, bool]:
    data = get_user_data(user_id)
    today_str = str(date.today())

    count = data["toilet_count"]
    if data["last_toilet_date"] != today_str:
        count = 0

    if count >= MAX_TOILET_PER_DAY:
        return False, 0, f"今日はもうトイレに行けないよ！（1日{MAX_TOILET_PER_DAY}回まで）", data["points"], 0, False

    count += 1
    add_pts, comment = random.choice(TOILET_TABLE)
    new_pts, new_stage, evolved = update_points_and_check_evolution(user_id, add_pts)

    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_points SET toilet_count = ?, last_toilet_date = ? WHERE user_id = ?
    """, (count, today_str, user_id))
    conn.commit()
    conn.close()

    remaining_turns = MAX_TOILET_PER_DAY - count
    return True, add_pts, comment, new_pts, remaining_turns, evolved

# --------------------------------------------------
# 6. Discord イベントハンドラー
# --------------------------------------------------
@bot.event
async def on_ready():
    init_db()
    print("=" * 50)
    print(f"🎉 ログイン成功！ Bot名: {bot.user.name} (ID: {bot.user.id})")
    print("=" * 50)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()
    user_id = message.author.id
    channel_id = message.channel.id

    # --- 1. 「ごはんのコマンド」（ヘルプ画面） ---
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

    # --- 2. 「旅に出ろ」関連 ---
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
                f"✨ 新しい命が生まれました！（性別: **{new_gender}** / 体重: **0せいちょう**）\n"
                f"たくさんご飯をあげて育ててあげてね！"
            )
            return
        else:
            confirm_leaving_set.remove(user_id)

    # --- 3. 「ごはんの名前は〇〇」（名前の変更） ---
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

    # --- 4. 「ごはんのステータス」（状態表示） ---
    if content == "ごはんのステータス":
        data = get_user_data(user_id)
        form_info = get_current_form_info(data["stage"], data["pet_type"], data["gender"])
        
        # ユーザー設定の名前がなければデフォルト名を表示
        display_name = data["pet_name"] if data["pet_name"] else form_info["name"]
        
        await message.channel.send(
            f"🍚 **{message.author.display_name}** さんのパートナー情報\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"【なまえ】**{display_name}**\n"
            f"【姿・種族】**{form_info['name']}** (第{data['stage']}段階)\n"
            f"【性別】**{data['gender']}**\n"
            f"【身長】**{form_info['height']}**\n"
            f"【体重】**{data['points']} せいちょう**\n"
            f"【とくちょう】{form_info['desc']}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return

    # --- 5. 「〇〇食え」（ご飯をあげる） ---
    target_food = last_food_dict.get(channel_id)

    if target_food and content == f"{target_food}食え":
        pts, comment = random.choice(POINT_TABLE)
        new_pts, new_stage, evolved = update_points_and_check_evolution(user_id, pts)
        
        del last_food_dict[channel_id]
        data = get_user_data(user_id)

        msg = (
            f"むしゃむしゃ……！\n"
            f"{target_food}をたべた！（評価：{comment} / {pts:+d}せいちょう）\n"
            f"現在の体重: **{new_pts}せいちょう**"
        )
        
        if evolved:
            form_info = get_current_form_info(new_stage, data["pet_type"], data["gender"])
            call_name = data["pet_name"] if data["pet_name"] else "パートナー"
            msg += f"\n\n✨ **おや……！？ {call_name} のようすが……！**\n" \
                   f"**{form_info['name']}** に進化しました！（身長: {form_info['height']}）\n" \
                   f"📝 {form_info['desc']}"

        await message.channel.send(msg)
        return

    if content == "食え":
        if target_food:
            await message.channel.send(f"「{target_food}食え」って言ってね！")
        else:
            await message.channel.send("なにをたべればいいの？")
        return

    # --- 6. 「トイレに行け」（トイレに行かせる） ---
    if content == "トイレに行け":
        success, pts, comment, total_pts, remaining, evolved = process_toilet(user_id)
        data = get_user_data(user_id)
        form_info = get_current_form_info(data["stage"], data["pet_type"], data["gender"])
        call_name = data["pet_name"] if data["pet_name"] else form_info["name"]
        
        if success:
            msg = (
                f"トコトコ……🚽（{call_name}がトイレに行ってきた！）\n"
                f"結果：{comment}（{pts:+d}せいちょう）\n"
                f"現在の体重: **{total_pts}せいちょう**（今日の残りトイレ回数: {remaining}回）"
            )
            if evolved:
                new_data = get_user_data(user_id)
                new_form_info = get_current_form_info(new_data["stage"], new_data["pet_type"], new_data["gender"])
                msg += f"\n\n✨ **おや……！？ {call_name} のようすが……！**\n" \
                       f"スッキリして **{new_form_info['name']}** に進化しました！（身長: {new_form_info['height']}）\n" \
                       f"📝 {new_form_info['desc']}"
            await message.channel.send(msg)
        else:
            await message.channel.send(f"🚽 {comment}")
        return

    # --- 7. 会話内の食べ物（名詞）抽出 ---
    tokens = tokenizer_obj.tokenize(content, mode)
    detected_words = []

    NOISE_PATTERN = re.compile(r'^(w+|ｗ+|草+|笑+|あ|い|う|え|お|これ|それ|あれ|どれ|やつ|こと|もの|ため|よう|さん|ちゃん|くん|てす|テスト)$', re.IGNORECASE)

    for token in tokens:
        pos = token.part_of_speech()
        if pos[0] == '名詞' and pos[1] in ['普通名詞', '固有名詞']:
            surface = token.surface().strip()

            if len(surface) <= 1:
                continue

            if re.match(r'^[a-zA-Z0-9\W_]+$', surface) or re.match(r'^(.)\1+$', surface):
                if surface not in ["もも", "みかん"]: 
                    continue

            if NOISE_PATTERN.match(surface):
                continue

            if pos[1] in ['非自立可能', '副詞可能']:
                continue

            detected_words.append(surface)

    if detected_words:
        selected_word = detected_words[0]
        last_food_dict[channel_id] = selected_word
        await message.channel.send(f"{selected_word}、食べたいなぁ")	

# --------------------------------------------------
# 7. アプリケーション起動処理
# --------------------------------------------------
if __name__ == "__main__":
    print("🚀 アプリケーションを起動しています...")
    
    t = Thread(target=run_flask, daemon=True)
    t.start()
    print("🌐 Webサーバー (Flask) を起動しました。")

    TOKEN = os.environ.get("DISCORD_TOKEN")
    
    if not TOKEN:
        print("❌ 【重大なエラー】環境変数 'DISCORD_TOKEN' が取得できませんでした！")
    else:
        TOKEN = TOKEN.strip()
        print("🔑 トークンの取得に成功しました。Discordへの接続を開始します...")
        try:
            bot.run(TOKEN)
        except discord.errors.LoginFailure:
            print("❌ 【ログインエラー】トークンが無効です。")
        except discord.errors.PrivilegedIntentsRequired:
            print("❌ 【Intentsエラー】MESSAGE CONTENT INTENTをONにしてください。")
        except Exception as e:
            print(f"❌ 【予期せぬエラーが発生しました】: {e}")
