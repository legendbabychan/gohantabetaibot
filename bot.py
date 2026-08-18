import sqlite3
import random
import re
import os
from datetime import date
from threading import Thread
from flask import Flask
import discord
from discord.ext import commands
from janome.tokenizer import Tokenizer

# --------------------------------------------------
# Webサーバーの設定（Render常時起動用）
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --------------------------------------------------
# Botの基本設定
# --------------------------------------------------
INTENTS = discord.Intents.default()
INTENTS.message_content = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# 形態素解析器の初期化
tokenizer = Tokenizer()

# 一時的な記憶（メモリー）
last_food_dict = {}          # チャンネルごとの直近の食べ物 {channel_id: "単語"}
confirm_leaving_set = set()  # 「旅に出ろ」と言って返答待ちのユーザーIDセット

# --------------------------------------------------
# 生き物の設定・データ定義
# --------------------------------------------------
DEFAULT_PET_NAME = "ごはんたべたい"

EVOLUTION_FORMS = {
    1: {"name": "なぞの幼体", "height": "5ごはん"},
    2: {"name": "成長期いきもの", "height": "7ごはん"},
    3: {"name": "かんぜんたい", "height": "10ごはん"}
}

POINT_TABLE = [
    (1, "おいしくない"),
    (3, "ふつう"),
    (5, "おいしい"),
    (-3, "まずい"),
    (-5, "おなかを壊した")
]

TOILET_TABLE = [
    (3, "すっきり！"),
    (1, "治った！"),
    (0, "すっきりしない……"),
    (-1, "出ない……"),
    (-3, "おなかいたい……")
]

# --------------------------------------------------
# データベースの処理 (SQLite)
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
            pet_name TEXT DEFAULT 'ごはんたべたい',
            gender TEXT DEFAULT 'オス',
            stage INTEGER DEFAULT 1
        )
    """)
    
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
    conn = sqlite3.connect("gohan_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT points, pet_name, gender, stage, toilet_count, last_toilet_date FROM user_points WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
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
    data = get_user_data(user_id)
    current_pts = data["points"]
    current_stage = data["stage"]
    
    new_pts = current_pts + add_pts
    new_stage = current_stage
    evolved = False

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
    data = get_user_data(user_id)
    today_str = str(date.today())

    count = data["toilet_count"]
    if data["last_toilet_date"] != today_str:
        count = 0

    if count >= 10:
        return False, 0, "今日はもうトイレに行けないよ！（1日10回まで）", data["points"], 0, False

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

    remaining_turns = 3 - count
    return True, add_pts, comment, new_pts, remaining_turns, evolved

# --------------------------------------------------
# イベントハンドラー
# --------------------------------------------------
@bot.event
async def on_ready():
    init_db()
    print(f"Logged in as {bot.user.name}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()
    user_id = message.author.id
    channel_id = message.channel.id

    # --- 1. 「ごはんのコマンド」確認処理 ---
    if content == "ごはんのコマンド":
        embed = discord.Embed(
            title="🍚 ごはんBotのコマンド一覧",
            description="チャット内で使えるコマンドの一覧だよ！",
            color=0xff9900
        )
        embed.add_field(name="🍚 ごはんをあげる", value="会話に食べ物が出てきたら「〇〇食え」", inline=False)
        embed.add_field(name="🚽 トイレに行かせる", value="「トイレに行け」（1日3回まで）", inline=False)
        embed.add_field(name="📊 ステータス確認", value="「ごはんのステータス」", inline=False)
        embed.add_field(name="✨ 名前の変更", value="「ごはんの名前は〇〇」", inline=False)
        embed.add_field(name="✈️ 旅に出す", value="「旅に出ろ」（その後に「いってらっしゃい」で交代）", inline=False)
        
        await message.channel.send(embed=embed)
        return

    # --- 2. 「旅に出ろ」関連の処理 ---
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

    # --- 3. 「ごはんの名前は〇〇」変更処理 ---
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

    # --- 4. 「ごはんのステータス」確認処理 ---
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

    # --- 5. 「〇〇食え」の反応処理 ---
    target_food = last_food_dict.get(channel_id)

    if target_food and content == f"{target_food}食え":
        pts, comment = random.choice(POINT_TABLE)
        new_pts, new_stage, evolved = update_points_and_check_evolution(user_id, pts)
        
        del last_food_dict[channel_id]
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

    # --- 6. 「トイレに行け」の反応処理 ---
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

    # --- 7. 会話内の食べ物（名詞）抽出と反応 ---
    tokens = tokenizer.tokenize(content)
    detected_words = []

    NOISE_PATTERN = re.compile(r'^(w+|ｗ+|草+|笑+|あ|い|う|え|お|これ|それ|あれ|どれ|やつ|こと|もの|ため|よう|さん|ちゃん|くん|てす|テスト)$', re.IGNORECASE)

    for token in tokens:
        pos = token.part_of_speech.split(',')

        if pos[0] == '名詞' and pos[1] in ['一般', '固有名詞', 'サ変接続']:
            surface = token.surface.strip()

            if len(surface) <= 1:
                continue

            if re.match(r'^[a-zA-Z0-9\W_]+$', surface) or re.match(r'^(.)\1+$', surface):
                if surface not in ["もも", "みかん"]: 
                    continue

            if NOISE_PATTERN.match(surface):
                continue

            if pos[1] in ['非自立', '数接続', '代名詞']:
                continue

            detected_words.append(surface)

    if detected_words:
        selected_word = detected_words[0]
        last_food_dict[channel_id] = selected_word
        await message.channel.send(f"{selected_word}、食べたいなぁ")

# --------------------------------------------------
# アプリケーション起動処理
# --------------------------------------------------
# 1. Flask（Webサーバー）を別スレッドでバックグラウンド起動
Thread(target=run_flask).start()

# 2. Discord Botの起動
# トークンを直書きせず、一時的にダミーにしておく
bot.run("YOUR_BOT_TOKEN")