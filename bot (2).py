import os
import re
import json
import telebot
from datetime import datetime

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
UPI_ID = os.getenv("UPI_ID", "taniyaraani55-3@okaxis")
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
QR_FILE = os.path.join(os.path.dirname(__file__), "qr.jpg")

bot = telebot.TeleBot(TOKEN)

EMPTY_DATA = {
    "players": {},
    "active_tables": [],
    "queue": [],
    "pending_deposits": [],
    "history": [],
    "last_finish": None,
    "next_table_id": 1,
    "next_history_id": 1,
}

PAYMENT_KEYWORDS = {"pay", "qr", "payment", "paying", "paid"}
TABLE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*([ft])$", re.IGNORECASE)
DONE_PATTERN = re.compile(r"^done\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return dict(EMPTY_DATA)
    with open(DATA_FILE, "r") as f:
        raw = json.load(f)
    if "players" not in raw:
        return {**EMPTY_DATA, "players": {k: v for k, v in raw.items()}}
    return {**EMPTY_DATA, **raw}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fmt(n) -> str:
    return f"{n:g}"


def normalize(name: str) -> str:
    return name.replace("@", "").lower()


def get_player_name(user) -> str:
    if user.username:
        return normalize(user.username)
    return normalize(user.first_name or str(user.id))


def is_admin(message) -> bool:
    try:
        if message.chat.type == "private":
            return True
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def require_admin(message) -> bool:
    if not is_admin(message):
        bot.reply_to(message, "❌ Only admins can use this command.")
        return False
    return True


def send_payment_info(message):
    text = (
        f"💳 Payment Details\n\n"
        f"UPI: {UPI_ID}\n\n"
        f"Minimum ₹100 deposit karo\n"
        f"Payment ke baad screenshot bhejo."
    )
    if os.path.exists(QR_FILE):
        with open(QR_FILE, "rb") as f:
            bot.send_photo(
                message.chat.id, f,
                caption=text,
                reply_to_message_id=message.message_id,
            )
    else:
        bot.reply_to(message, text)


def apply_finish(data: dict, table: dict, winner: str, loser: str) -> dict:
    players = data["players"]
    amount = table["amount"]
    locked = table.get("locked", False)

    winner_before = players.get(winner, 0)
    loser_before = players.get(loser, 0)

    winner_gain = round(amount * 1.9, 2)

    if locked:
        # Balance already deducted at table creation; just credit the winner
        players[winner] = round(winner_before + winner_gain, 2)
        loser_net = amount  # amount already deducted earlier
    else:
        # Admin-created table: deduct loser now
        loser_loss = amount
        if loser_before < loser_loss:
            return {"error": f"❌ {loser} only has {fmt(loser_before)}, cannot deduct {fmt(loser_loss)}."}
        players[winner] = round(winner_before + winner_gain, 2)
        players[loser] = round(loser_before - loser_loss, 2)
        loser_net = loser_loss

    data["active_tables"] = [t for t in data["active_tables"] if t["id"] != table["id"]]

    history_id = data["next_history_id"]
    data["history"].append({
        "id": history_id,
        "winner": winner,
        "loser": loser,
        "amount": amount,
        "winner_gain": winner_gain,
        "loser_loss": loser_net,
        "timestamp": datetime.now().strftime("%d/%m %H:%M"),
    })
    data["next_history_id"] += 1

    data["last_finish"] = {
        "history_id": history_id,
        "winner": winner,
        "loser": loser,
        "winner_balance_before": winner_before,
        "loser_balance_before": loser_before,
        "table": table,
    }

    return {
        "winner": winner,
        "loser": loser,
        "winner_gain": winner_gain,
        "loser_net": loser_net,
        "winner_balance": players[winner],
        "loser_balance": players.get(loser, loser_before),
    }


# ═══════════════════════════════════════════════════════════════
#  COMMAND HANDLERS  (registered first — highest priority)
# ═══════════════════════════════════════════════════════════════

# ── /add NAME AMOUNT ──────────────────────────────────────────
@bot.message_handler(commands=["add"])
def cmd_add(message):
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        bot.reply_to(message, "Usage: /add NAME AMOUNT")
        return
    name = normalize(parts[1])
    try:
        amount = float(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ AMOUNT must be a number.")
        return
    data = load_data()
    data["players"][name] = data["players"].get(name, 0) + amount
    save_data(data)
    sign = "+" if amount >= 0 else ""
    bot.reply_to(message, f"✅ {name}: {sign}{fmt(amount)} → balance: {fmt(data['players'][name])}")


# ── /balance NAME ─────────────────────────────────────────────
@bot.message_handler(commands=["balance"])
def cmd_balance(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        bot.reply_to(message, "Usage: /balance NAME")
        return
    name = normalize(parts[1])
    data = load_data()
    if name not in data["players"]:
        bot.reply_to(message, f"❌ Player '{name}' not found.")
        return
    bot.reply_to(message, f"💰 {name}: {fmt(data['players'][name])}")


# ── /list ─────────────────────────────────────────────────────
@bot.message_handler(commands=["list"])
def cmd_list(message):
    data = load_data()
    players = data["players"]
    if not players:
        bot.reply_to(message, "No players yet. Use /add NAME AMOUNT to add one.")
        return
    ranked = sorted(players.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for i, (name, bal) in enumerate(ranked, 1):
        sign = "+" if bal > 0 else ""
        lines.append(f"{i}. {name}: {sign}{fmt(bal)}")
    bot.reply_to(message, "📊 Player Balances:\n\n" + "\n".join(lines))


# ── /top ──────────────────────────────────────────────────────
@bot.message_handler(commands=["top"])
def cmd_top(message):
    data = load_data()
    players = data["players"]
    if not players:
        bot.reply_to(message, "No players yet.")
        return
    ranked = sorted(players.items(), key=lambda x: x[1], reverse=True)[:5]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = []
    for i, (name, bal) in enumerate(ranked):
        sign = "+" if bal > 0 else ""
        lines.append(f"{medals[i]} {name}: {sign}{fmt(bal)}")
    bot.reply_to(message, "🏆 Top Players:\n\n" + "\n".join(lines))


# ── /remove NAME (admin) ──────────────────────────────────────
@bot.message_handler(commands=["remove"])
def cmd_remove(message):
    if not require_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        bot.reply_to(message, "Usage: /remove NAME")
        return
    name = normalize(parts[1])
    data = load_data()
    if name not in data["players"]:
        bot.reply_to(message, "❌ Player not found.")
        return
    del data["players"][name]
    save_data(data)
    bot.reply_to(message, f"✅ {name} removed successfully")


# ── /history NAME ─────────────────────────────────────────────
@bot.message_handler(commands=["history"])
def cmd_history(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        bot.reply_to(message, "Usage: /history NAME")
        return
    name = normalize(parts[1])
    data = load_data()
    matches = [h for h in data["history"] if h["winner"] == name or h["loser"] == name]
    if not matches:
        bot.reply_to(message, f"No match history for {name}.")
        return
    lines = [f"📜 History for {name} (last {min(len(matches), 10)}):\n"]
    for m in matches[-10:]:
        if m["winner"] == name:
            lines.append(f"🏆 Won  vs {m['loser']} | {fmt(m['amount'])}F | {m['timestamp']}")
        else:
            lines.append(f"💀 Lost vs {m['winner']} | {fmt(m['amount'])}F | {m['timestamp']}")
    bot.reply_to(message, "\n".join(lines))


# ── /active ───────────────────────────────────────────────────
@bot.message_handler(commands=["active"])
def cmd_active(message):
    data = load_data()
    tables = data["active_tables"]
    if not tables:
        bot.reply_to(message, "No active tables.")
        return
    lines = [f"🎮 Active Tables ({len(tables)}):\n"]
    for t in tables:
        mode1 = t.get("mode1", "")
        mode2 = t.get("mode2", "")
        mode_str = f" ({mode1.upper()}/{mode2.upper()})" if mode1 else ""
        lines.append(f"#{t['id']}  {t['player1']} vs {t['player2']}{mode_str}  💰 {fmt(t['amount'])}F")
    bot.reply_to(message, "\n".join(lines))


# ── /table PLAYER1 PLAYER2 AMOUNT (admin) ────────────────────
@bot.message_handler(commands=["table"])
def cmd_table(message):
    if not require_admin(message):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) != 4:
        bot.reply_to(message, "Usage: /table PLAYER1 PLAYER2 AMOUNT")
        return
    p1 = normalize(parts[1])
    p2 = normalize(parts[2])
    try:
        amount = float(parts[3])
    except ValueError:
        bot.reply_to(message, "❌ AMOUNT must be a number.")
        return
    if amount <= 0:
        bot.reply_to(message, "❌ Amount must be positive.")
        return
    if p1 == p2:
        bot.reply_to(message, "❌ Players must be different.")
        return

    data = load_data()
    players = data["players"]

    errors = []
    if players.get(p1, 0) < amount:
        errors.append(f"❌ {p1} has {fmt(players.get(p1, 0))}, needs {fmt(amount)}")
    if players.get(p2, 0) < amount:
        errors.append(f"❌ {p2} has {fmt(players.get(p2, 0))}, needs {fmt(amount)}")
    if errors:
        bot.reply_to(message, "\n".join(errors))
        return

    for t in data["active_tables"]:
        if {t["player1"], t["player2"]} == {p1, p2}:
            bot.reply_to(message, f"⚠️ A table for {p1} vs {p2} already exists.")
            return

    table_id = data["next_table_id"]
    data["active_tables"].append({
        "id": table_id,
        "player1": p1,
        "player2": p2,
        "amount": amount,
        "locked": False,
    })
    data["next_table_id"] += 1
    save_data(data)

    bot.reply_to(message, f"🎮 Table Created\n👤 {p1} vs {p2}\n💰 Match:{fmt(amount)}F")


# ── /finish WINNER LOSER (admin) ─────────────────────────────
@bot.message_handler(commands=["finish"])
def cmd_finish(message):
    if not require_admin(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        bot.reply_to(message, "Usage: /finish WINNER LOSER")
        return
    winner = normalize(parts[1])
    loser = normalize(parts[2])

    data = load_data()
    table = next(
        (t for t in data["active_tables"] if {t["player1"], t["player2"]} == {winner, loser}),
        None,
    )
    if not table:
        bot.reply_to(message, f"⚠️ No active table found for {winner} vs {loser}.")
        return

    result = apply_finish(data, table, winner, loser)
    if "error" in result:
        bot.reply_to(message, result["error"])
        return

    save_data(data)
    bot.reply_to(
        message,
        f"🏆 Winner: {winner}\n"
        f"💰 {winner}: +{fmt(result['winner_gain'])} → {fmt(result['winner_balance'])}\n"
        f"💀 {loser}: -{fmt(result['loser_net'])} → {fmt(result['loser_balance'])}",
    )


# ── /undo (admin) ─────────────────────────────────────────────
@bot.message_handler(commands=["undo"])
def cmd_undo(message):
    if not require_admin(message):
        return
    data = load_data()
    lf = data.get("last_finish")
    if not lf:
        bot.reply_to(message, "❌ Nothing to undo.")
        return

    winner = lf["winner"]
    loser = lf["loser"]
    data["players"][winner] = lf["winner_balance_before"]
    data["players"][loser] = lf["loser_balance_before"]
    data["active_tables"].append(lf["table"])
    data["history"] = [h for h in data["history"] if h["id"] != lf["history_id"]]
    data["last_finish"] = None
    save_data(data)

    bot.reply_to(
        message,
        f"↩️ Last match undone.\n"
        f"💰 {winner}: {fmt(lf['winner_balance_before'])}\n"
        f"💰 {loser}: {fmt(lf['loser_balance_before'])}",
    )


# ── /resetall (admin) ─────────────────────────────────────────
@bot.message_handler(commands=["resetall"])
def cmd_resetall(message):
    if not require_admin(message):
        return
    save_data(dict(EMPTY_DATA))
    bot.reply_to(message, "✅ All player balances reset successfully")


# ═══════════════════════════════════════════════════════════════
#  PHOTO HANDLER — deposit screenshot detection
# ═══════════════════════════════════════════════════════════════

@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    player = get_player_name(message.from_user)
    data = load_data()
    # Replace any previous pending deposit from this user
    data["pending_deposits"] = [
        p for p in data["pending_deposits"] if p["player"] != player
    ]
    data["pending_deposits"].append({
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "player": player,
    })
    save_data(data)
    bot.reply_to(message, "📸 Screenshot received! Waiting for admin approval...")


# ═══════════════════════════════════════════════════════════════
#  ADMIN "done AMOUNT" REPLY — approve deposit
# ═══════════════════════════════════════════════════════════════

@bot.message_handler(func=lambda m: (
    m.text is not None
    and m.reply_to_message is not None
    and bool(DONE_PATTERN.match(m.text.strip()))
))
def handle_done(message):
    if not require_admin(message):
        return

    match = DONE_PATTERN.match(message.text.strip())
    amount = float(match.group(1))

    original = message.reply_to_message
    player = get_player_name(original.from_user)

    data = load_data()
    data["players"][player] = round(data["players"].get(player, 0) + amount, 2)
    # Clean up pending deposit entry if it exists
    data["pending_deposits"] = [
        p for p in data["pending_deposits"]
        if not (p["player"] == player and p["message_id"] == original.message_id)
    ]
    save_data(data)

    bot.reply_to(
        message,
        f"Deposit successful ✅\n"
        f"₹{fmt(amount)} added to {player}\n"
        f"New balance: {fmt(data['players'][player])}",
    )


# ═══════════════════════════════════════════════════════════════
#  GENERAL TEXT HANDLER — payment keywords / 100f/100t / win
#  Registered last so commands always take priority.
# ═══════════════════════════════════════════════════════════════

@bot.message_handler(func=lambda m: m.text is not None and not m.text.startswith("/"))
def handle_text(message):
    text = message.text.strip()
    lower = text.lower()

    # ── payment trigger ──────────────────────────────────────
    if lower in PAYMENT_KEYWORDS:
        send_payment_info(message)
        return

    # ── "win" ────────────────────────────────────────────────
    if lower == "win":
        _handle_win(message)
        return

    # ── "t" or "table" → join oldest waiting queue entry ─────
    if lower in ("t", "table"):
        _handle_join_table(message)
        return

    # ── "100f" / "100 f" / "100t" / "100 t" ─────────────────
    m = TABLE_PATTERN.match(text)
    if m:
        _handle_queue(message, float(m.group(1)), m.group(2).lower())
        return


def _handle_win(message):
    player = get_player_name(message.from_user)
    data = load_data()

    player_tables = [
        t for t in data["active_tables"]
        if t["player1"] == player or t["player2"] == player
    ]
    if not player_tables:
        bot.reply_to(message, f"❌ No active table found for {player}.")
        return

    # Use the highest-ID (most recent) table
    table = max(player_tables, key=lambda t: t["id"])
    opponent = table["player2"] if table["player1"] == player else table["player1"]

    # Announce and ask admin to confirm — do not auto-apply
    bot.send_message(
        message.chat.id,
        f"🏆 *{player}* claims victory against *{opponent}*!\n"
        f"💰 Table #{table['id']} | Amount: ₹{fmt(table['amount'])}\n\n"
        f"Admin, confirm with:\n"
        f"`/finish {player} {opponent}`\n"
        f"or if {opponent} won:\n"
        f"`/finish {opponent} {player}`",
        parse_mode="Markdown",
    )


def _handle_join_table(message):
    player = get_player_name(message.from_user)
    user_id = message.from_user.id
    data = load_data()
    players = data["players"]
    queue = data["queue"]

    # Already in an active table → silent ignore
    if any(t for t in data["active_tables"] if t["player1"] == player or t["player2"] == player):
        return

    # Already in queue → silent ignore
    if any(q.get("user_id") == user_id for q in queue):
    return

    # Find oldest entry from a different player
    eligible = [q for q in queue if q.get("user_id") != user_id]
    if not eligible:
        bot.reply_to(message, "⏳ No open tables right now. Send '100f' or '100t' to create one.")
        return

    entry = eligible[0]
    amount = entry["amount"]

    # Balance check for joiner
    if players.get(player, 0) < amount:
        send_payment_info(message)
        return

    queue.remove(entry)

    p1 = entry["player"]
    p2 = player
    mode1 = entry["mode"]
    mode2 = "t" if mode1 == "f" else "f"
    mode_label1 = "Fast" if mode1 == "f" else "Toss"
    mode_label2 = "Fast" if mode2 == "f" else "Toss"

    players[p1] = round(players.get(p1, 0) - amount, 2)
    players[p2] = round(players.get(p2, 0) - amount, 2)

    table_id = data["next_table_id"]
    data["active_tables"].append({
        "id": table_id,
        "player1": p1,
        "player2": p2,
        "amount": amount,
        "mode1": mode1,
        "mode2": mode2,
        "locked": True,
    })
    data["next_table_id"] += 1
    save_data(data)

    bot.send_message(
        message.chat.id,
        f"🏓 Table #{table_id}\n\n"
        f"Player1: @{p1} ({mode_label1})\n"
        f"Player2: @{p2} ({mode_label2})\n"
        f"Amount: ₹{fmt(amount)}\n\n"
        f"Send *win* when the match is over!",
        parse_mode="Markdown",
    )


def _handle_queue(message, amount: float, mode: str):
    player = get_player_name(message.from_user)
    user_id = message.from_user.id
    data = load_data()
    players = data["players"]
    queue = data["queue"]

    # Already in an active table → silent ignore
    if any(t for t in data["active_tables"] if t["player1"] == player or t["player2"] == player):
        return

    # Already in queue → silent ignore (handles repeated sends from same user)
    if any(q.get("user_id") == user_id or q["player"] == player for q in queue):
        return

    # Find any waiting opponent with same amount (any mode, different player)
    opponent_entry = next(
        (q for q in queue if q["amount"] == amount
         and q["player"] != player
         and q.get("user_id") != user_id),
        None,
    )

    if opponent_entry:
        # Balance check for both before locking
        p1, p2 = opponent_entry["player"], player
        errors = []
        if players.get(p1, 0) < amount:
            errors.append(f"❌ {p1} has ₹{fmt(players.get(p1, 0))}, needs ₹{fmt(amount)}")
        if players.get(p2, 0) < amount:
            # Joiner can't afford — send payment info, leave p1 in queue
            save_data(data)
            send_payment_info(message)
            return
        if errors:
            # Queued player can't afford — remove them, don't create table
            queue.remove(opponent_entry)
            save_data(data)
            bot.reply_to(message, "\n".join(errors) + "\nTable cancelled.")
            return

        queue.remove(opponent_entry)
        mode1, mode2 = opponent_entry["mode"], mode
        mode_label1 = "Fast" if mode1 == "f" else "Toss"
        mode_label2 = "Fast" if mode2 == "f" else "Toss"

        players[p1] = round(players.get(p1, 0) - amount, 2)
        players[p2] = round(players.get(p2, 0) - amount, 2)

        table_id = data["next_table_id"]
        data["active_tables"].append({
            "id": table_id,
            "player1": p1,
            "player2": p2,
            "amount": amount,
            "mode1": mode1,
            "mode2": mode2,
            "locked": True,
        })
        data["next_table_id"] += 1
        save_data(data)

        bot.send_message(
            message.chat.id,
            f"🏓 Table #{table_id}\n\n"
            f"Player1: @{p1} ({mode_label1})\n"
            f"Player2: @{p2} ({mode_label2})\n"
            f"Amount: ₹{fmt(amount)}\n\n"
            f"Send *win* when the match is over!",
            parse_mode="Markdown",
        )
    else:
        # No opponent yet — check balance then join queue
        if players.get(player, 0) < amount:
            send_payment_info(message)
            return

        mode_label = "Fast" if mode == "f" else "Toss"
        queue.append({
            "player": player,
            "user_id": user_id,
            "amount": amount,
            "mode": mode,
            "chat_id": message.chat.id,
            "message_id": message.message_id,
        })
        save_data(data)
        bot.reply_to(
            message,
            f"⏳ Looking for opponent...\n"
            f"Amount: ₹{fmt(amount)} | Mode: {mode_label}\n"
            f"Other player can send 'T' to join!",
        )


if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling(skip_pending=True)
