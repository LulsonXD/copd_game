import html
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "copd_game.db"
USER_ID = "demo-user"

# Режим разработки шутера: попытки не ограничены, можно запускать любой уровень.
# Для релиза достаточно поставить False.
SHOOTER_DEV_MODE = False

st.set_page_config(page_title="Путь дыхания", page_icon="🫁", layout="wide")

# -----------------------------
# Database
# -----------------------------

def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        xp INTEGER NOT NULL DEFAULT 0,
        coins INTEGER NOT NULL DEFAULT 100,
        level INTEGER NOT NULL DEFAULT 1,
        streak INTEGER NOT NULL DEFAULT 0,
        weekly_goal INTEGER NOT NULL DEFAULT 4,
        last_activity_date TEXT,
        goals TEXT NOT NULL DEFAULT 'regularity,activity,self_management,education',
        equipped_top TEXT NOT NULL DEFAULT 'hoodie_green',
        equipped_bottom TEXT NOT NULL DEFAULT 'pants_black',
        equipped_shoes TEXT NOT NULL DEFAULT 'sneakers_white',
        room_theme TEXT NOT NULL DEFAULT 'living_room',
        location_theme TEXT NOT NULL DEFAULT 'home',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mission_completions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        mission_type TEXT NOT NULL,
        mission_date TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        xp_earned INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, mission_type, mission_date)
    );
    CREATE TABLE IF NOT EXISTS checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        checkin_date TEXT NOT NULL,
        breathlessness TEXT NOT NULL,
        cough TEXT NOT NULL,
        sputum TEXT NOT NULL,
        energy TEXT NOT NULL,
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(user_id, checkin_date)
    );
    CREATE TABLE IF NOT EXISTS workout_sessions (
        user_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'idle',
        step INTEGER NOT NULL DEFAULT 0,
        remaining_seconds INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS inventory (
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        purchased_at TEXT NOT NULL,
        PRIMARY KEY(user_id, item_id)
    );
    CREATE TABLE IF NOT EXISTS achievements (
        user_id TEXT NOT NULL,
        code TEXT NOT NULL,
        unlocked_at TEXT NOT NULL,
        PRIMARY KEY(user_id, code)
    );

    CREATE TABLE IF NOT EXISTS shooter_state (
        user_id TEXT PRIMARY KEY,
        attempts INTEGER NOT NULL DEFAULT 1,
        best_level INTEGER NOT NULL DEFAULT 0,
        last_daily_attempt_date TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS shooter_level_rewards (
        user_id TEXT NOT NULL,
        level INTEGER NOT NULL,
        coins INTEGER NOT NULL,
        completed_at TEXT NOT NULL,
        PRIMARY KEY(user_id, level)
    );
    """)
    # Safe migration for old versions.
    migrations = {
        "users": {
            "coins": "INTEGER NOT NULL DEFAULT 100",
            "goals": "TEXT NOT NULL DEFAULT 'regularity,activity,self_management,education'",
            "equipped_top": "TEXT NOT NULL DEFAULT 'hoodie_green'",
            "equipped_bottom": "TEXT NOT NULL DEFAULT 'pants_black'",
            "equipped_shoes": "TEXT NOT NULL DEFAULT 'sneakers_white'",
            "room_theme": "TEXT NOT NULL DEFAULT 'living_room'",
            "location_theme": "TEXT NOT NULL DEFAULT 'home'",
        },
        "mission_completions": {"mission_date": "TEXT"},
        "checkins": {
            "breathlessness": "TEXT NOT NULL DEFAULT ''",
            "cough": "TEXT NOT NULL DEFAULT ''",
            "sputum": "TEXT NOT NULL DEFAULT ''",
            "energy": "TEXT NOT NULL DEFAULT ''",
            "note": "TEXT DEFAULT ''",
        },
        "workout_sessions": {
            "status": "TEXT NOT NULL DEFAULT 'idle'",
            "step": "INTEGER NOT NULL DEFAULT 0",
            "remaining_seconds": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "started_at": "TEXT",
            "completed_at": "TEXT",
        },
    }
    for table, wanted in migrations.items():
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in wanted.items():
            if name not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    c.execute("UPDATE mission_completions SET mission_date = COALESCE(NULLIF(mission_date,''), substr(completed_at,1,10)) WHERE mission_date IS NULL OR mission_date=''" )
    # remove duplicate mission records before unique index creation
    c.execute("""
        DELETE FROM mission_completions
        WHERE id NOT IN (
            SELECT MAX(id) FROM mission_completions
            GROUP BY user_id, mission_type, mission_date
        )
    """)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_mission_day ON mission_completions(user_id, mission_type, mission_date)")
    now = datetime.now().isoformat(timespec="seconds")
    c.execute("""
        INSERT OR IGNORE INTO users
        (id, xp, coins, level, streak, weekly_goal, last_activity_date, goals,
         equipped_top, equipped_bottom, equipped_shoes, room_theme, location_theme,
         created_at, updated_at)
        VALUES (?,0,100,1,0,4,NULL,
                'regularity,activity,self_management,education',
                'hoodie_green','pants_black','sneakers_white','living_room','home',?,?)
    """, (USER_ID, now, now))

    c.execute("""
        INSERT OR IGNORE INTO shooter_state
        (user_id, attempts, best_level, last_daily_attempt_date, updated_at)
        VALUES (?, 1, 0, ?, ?)
    """, (USER_ID, date.today().isoformat(), now))

    c.commit()
    c.close()


init_db()


def get_user():
    c = db(); row = c.execute("SELECT * FROM users WHERE id=?", (USER_ID,)).fetchone(); c.close(); return row


def save_user(field, value):
    allowed = {"coins", "goals", "equipped_top", "equipped_bottom", "equipped_shoes", "room_theme", "location_theme"}
    if field not in allowed:
        raise ValueError(field)
    c = db(); c.execute(f"UPDATE users SET {field}=?, updated_at=? WHERE id=?", (value, datetime.now().isoformat(timespec="seconds"), USER_ID)); c.commit(); c.close()


def mission_done(kind, day=None):
    day = day or date.today()
    c = db(); row = c.execute("SELECT 1 FROM mission_completions WHERE user_id=? AND mission_type=? AND mission_date=?", (USER_ID, kind, day.isoformat())).fetchone(); c.close(); return row is not None


def completed_missions_count():
    c = db()
    row = c.execute(
        "SELECT COUNT(*) AS cnt FROM mission_completions WHERE user_id=?",
        (USER_ID,)
    ).fetchone()
    c.close()
    return int(row["cnt"])



# -----------------------------
# Shooter
# -----------------------------

def get_shooter_state():
    c = db()
    row = c.execute(
        """
        SELECT attempts, best_level, last_daily_attempt_date
        FROM shooter_state
        WHERE user_id=?
        """,
        (USER_ID,)
    ).fetchone()

    if not row:
        now = datetime.now().isoformat(timespec="seconds")
        c.execute(
            """
            INSERT INTO shooter_state
            (user_id, attempts, best_level, last_daily_attempt_date, updated_at)
            VALUES (?, 1, 0, ?, ?)
            """,
            (USER_ID, date.today().isoformat(), now)
        )
        c.commit()
        c.close()
        return {
            "attempts": 1,
            "best_level": 0,
            "last_daily_attempt_date": date.today().isoformat()
        }

    today = date.today().isoformat()
    attempts = int(row["attempts"])
    last_daily = row["last_daily_attempt_date"]

    # Каждый новый день добавляет одну базовую попытку.
    if last_daily != today:
        attempts += 1
        c.execute(
            """
            UPDATE shooter_state
            SET attempts=?,
                last_daily_attempt_date=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                attempts,
                today,
                datetime.now().isoformat(timespec="seconds"),
                USER_ID
            )
        )
        c.commit()

    c.close()

    return {
        "attempts": attempts,
        "best_level": int(row["best_level"]),
        "last_daily_attempt_date": today
    }


def add_shooter_attempts(amount=1):
    c = db()
    c.execute(
        """
        UPDATE shooter_state
        SET attempts=attempts+?,
            updated_at=?
        WHERE user_id=?
        """,
        (
            int(amount),
            datetime.now().isoformat(timespec="seconds"),
            USER_ID
        )
    )
    c.commit()
    c.close()


def use_shooter_attempt():
    # Сначала синхронизируем ежедневную попытку.
    state = get_shooter_state()

    if state["attempts"] <= 0:
        return False

    c = db()
    cur = c.execute(
        """
        UPDATE shooter_state
        SET attempts=attempts-?,
            updated_at=?
        WHERE user_id=? AND attempts>0
        """,
        (
            1,
            datetime.now().isoformat(timespec="seconds"),
            USER_ID
        )
    )

    c.commit()
    c.close()

    return cur.rowcount == 1


def shooter_level_reward(level):
    return 25 + (int(level) - 1) * 15


def shooter_level_reward_given(level):
    c = db()
    row = c.execute(
        """
        SELECT 1
        FROM shooter_level_rewards
        WHERE user_id=? AND level=?
        """,
        (USER_ID, int(level))
    ).fetchone()
    c.close()
    return row is not None


def give_shooter_level_reward(level):
    level = int(level)

    if shooter_level_reward_given(level):
        return 0

    reward = shooter_level_reward(level)
    now = datetime.now().isoformat(timespec="seconds")

    c = db()

    c.execute(
        """
        INSERT INTO shooter_level_rewards
        (user_id, level, coins, completed_at)
        VALUES (?, ?, ?, ?)
        """,
        (USER_ID, level, reward, now)
    )

    c.execute(
        """
        UPDATE users
        SET coins=coins+?,
            updated_at=?
        WHERE id=?
        """,
        (reward, now, USER_ID)
    )

    c.execute(
        """
        UPDATE shooter_state
        SET best_level=MAX(best_level, ?),
            updated_at=?
        WHERE user_id=?
        """,
        (level, now, USER_ID)
    )

    c.commit()
    c.close()

    return reward


def reset_shooter_run():
    for key in [
        "shooter_level",
        "shooter_running",
        "shooter_started",
        "shooter_finished",
        "shooter_reward",
        "shooter_game_token",
        "shooter_boss_defeated",
    ]:
        st.session_state.pop(key, None)


def start_shooter_level(level):
    # В режиме разработки попытки не списываются и их количество не ограничивает запуск.
    if not SHOOTER_DEV_MODE and not use_shooter_attempt():
        return False

    st.session_state.shooter_level = int(level)
    st.session_state.shooter_running = True
    st.session_state.shooter_started = True
    st.session_state.shooter_finished = False
    st.session_state.shooter_reward = 0
    # Сбрасываем флаг победы от предыдущего запуска, иначе кнопка
    # "Забрать награду" могла бы появиться раньше времени на новом уровне.
    st.session_state.shooter_boss_defeated = False
    st.session_state.shooter_game_token = int(time.time() * 1000)

    return True


def shooter_game_html(level, game_token):
    level = int(level)

    # Каждый уровень длится несколько минут. С ростом уровня увеличивается время,
    # скорость врагов и их запас здоровья.
    level_duration = 90 + (level - 1) * 15
    enemy_speed = 0.70 + level * 0.08
    enemy_hp = 2 + ((level - 1) // 2)
    # Новые типы врагов открываются постепенно.
    shooter_types = {
        "normal": True,
        "shooter": level >= 3,
        "runner": level >= 2,
        "burrower": level >= 5,
    }
    spawn_interval = max(950, 1900 - level * 70)
    boss_hp = 25 + level * 10

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    html, body {{
        margin: 0;
        padding: 0;
        background: transparent;
        overflow: hidden;
        font-family: Arial, sans-serif;
    }}

    #game {{
        position: relative;
        width: 100%;
        max-width: 900px;
        margin: 0 auto;
    }}

    canvas {{
        display: block;
        width: 100%;
        height: auto;
        border-radius: 18px;
        border: 2px solid rgba(0,0,0,.16);
        background: #dfe9df;
        cursor: crosshair;
        box-sizing: border-box;
        outline: none;
         touch-action: none;
        user-select: none;
        -webkit-user-select: none;
    }}

    #mobileControls {{
        display: none;
        position: absolute;
        inset: 0;
        z-index: 4;
        pointer-events: none;
        touch-action: none;
    }}
    #joystick {{
        position: absolute;
        left: 18px; bottom: 18px;
        width: 118px; height: 118px;
        border-radius: 50%;
        background: rgba(255,255,255,.18);
        border: 2px solid rgba(255,255,255,.55);
        box-sizing: border-box;
        pointer-events: auto;
        touch-action: none;
    }}
    #joystickKnob {{
        position: absolute;
        left: 50%; top: 50%;
        width: 48px; height: 48px;
        margin-left: -24px; margin-top: -24px;
        border-radius: 50%;
        background: rgba(70,100,80,.72);
        border: 2px solid rgba(255,255,255,.8);
        box-sizing: border-box;
        pointer-events: none;
    }}
    #aimPad {{
        position: absolute;
        right: 0; bottom: 0;
        width: 54%; height: 58%;
        pointer-events: auto;
        touch-action: none;
    }}
    #aimHint {{
        position: absolute;
        right: 22px; bottom: 24px;
        padding: 7px 10px;
        border-radius: 12px;
        background: rgba(255,255,255,.18);
        color: rgba(38,51,44,.72);
        font-size: 12px;
        font-weight: 700;
        pointer-events: none;
    }}
    @media (max-width: 700px) {{
        #mobileControls {{ display: block; }}
        #hud, #hudBottom {{
            left: 9px; right: 9px; font-size: 12px;
        }}
        #message {{ font-size: 22px; }}
    }}

    #hud {{
        position: absolute;
        left: 14px;
        right: 14px;
        top: 12px;
        display: flex;
        justify-content: space-between;
        pointer-events: none;
        font-weight: 700;
        color: #26332c;
        text-shadow: 0 1px 2px rgba(255,255,255,.8);
        z-index: 3;
    }}

    #hudBottom {{
        position: absolute;
        left: 14px;
        right: 14px;
        bottom: 12px;
        display: flex;
        justify-content: space-between;
        pointer-events: none;
        font-weight: 700;
        color: #26332c;
        text-shadow: 0 1px 2px rgba(255,255,255,.8);
        z-index: 3;
    }}

    #skills {{
        position: absolute;
        left: 50%;
        transform: translateX(-50%);
        bottom: 12px;
        display: flex;
        gap: 8px;
        z-index: 6;
        pointer-events: auto;
    }}
    .skillBtn {{
        min-width: 92px;
        padding: 8px 10px;
        border: 1px solid rgba(255,255,255,.55);
        border-radius: 12px;
        background: rgba(38,51,44,.78);
        color: white;
        font-size: 11px;
        font-weight: 800;
        cursor: pointer;
        touch-action: manipulation;
    }}
    .skillBtn.ready {{
        background: rgba(53,91,67,.92);
    }}
    .skillBtn.cooldown {{
        opacity: .55;
        cursor: default;
    }}
    @media (max-width: 700px) {{
        #skills {{ bottom: 10px; gap: 5px; }}
        .skillBtn {{ min-width: 82px; padding: 9px 6px; font-size: 10px; }}
    }}

    #message {{
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        flex-direction: column;
        background: rgba(255,255,255,.66);
        border-radius: 18px;
        font-size: 28px;
        font-weight: 800;
        color: #26332c;
        pointer-events: none;
        z-index: 5;
        text-align: center;
    }}

    #message small {{
        margin-top: 8px;
        font-size: 15px;
        font-weight: 500;
    }}
</style>
</head>

<body>

<div id="game">
    <canvas id="canvas" width="900" height="600" tabindex="0"></canvas>

    <div id="mobileControls">
        <div id="joystick"><div id="joystickKnob"></div></div>
        <div id="aimPad"></div>
        <div id="aimHint">Веди пальцем → стрельба</div>
    </div>

    <div id="skills">
        <button id="skillBreath" class="skillBtn ready">🫁 Дыхание с усилием<br><span>Q · готово</span></button>
        <button id="skillCalm" class="skillBtn ready">🌬️ Спокойный ритм<br><span>E · готово</span></button>
        <button id="skillPulse" class="skillBtn ready">🫁 Позиционное дренирование<br><span>R · готово</span></button>
    </div>

    <div id="hud">
        <span>🎯 Уровень {level}/10</span>
        <span id="enemyCounter">Врагов: 0</span>
    </div>

    <div id="hudBottom">
        <span id="timerText">Время: {level_duration}:00</span>
        <span id="bossText"></span>
    </div>

    <div id="message">
        <div id="messageTitle"></div>
        <small id="messageText"></small>
    </div>
</div>



<script>
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

const W = canvas.width;
const H = canvas.height;

const LEVEL = {level};
const GAME_TOKEN = {int(game_token)};
const LEVEL_DURATION = {level_duration};
const ENEMY_SPEED = {enemy_speed};
const ENEMY_HP = {enemy_hp};
const SPAWN_INTERVAL = {spawn_interval};
const BOSS_HP = {boss_hp};
const HAS_SHOOTER = {str(level >= 3).lower()};
const HAS_RUNNER = {str(level >= 2).lower()};
const HAS_BURROWER = {str(level >= 5).lower()};

let keys = {{}};
let bullets = [];
let enemyBullets = [];
let hazards = [];
let enemies = [];
let gameOver = false;
let victory = false;
let boss = null;
let bossSpawned = false;
let lastShot = 0;
let kills = 0;
let startTime = Date.now();
let lastSpawn = Date.now();
let elapsedSeconds = 0;
let victorySignalSent = false;

// ---------- Навыки ----------
let skills = {{
    breath: {{ cooldown: 0, duration: 0 }},
    calm: {{ cooldown: 0, duration: 0 }},
    pulse: {{ cooldown: 0 }}
}};
const SKILL_CD = 18000;
const SKILL_BREATH_DURATION = 7000;
const SKILL_CALM_DURATION = 6000;
const SKILL_PULSE_CD = 22000;

const skillBreathBtn = document.getElementById("skillBreath");
const skillCalmBtn = document.getElementById("skillCalm");
const skillPulseBtn = document.getElementById("skillPulse");

function skillReady(skill) {{
    const now = Date.now();
    return !gameOver && (skill === "pulse"
        ? now >= skills.pulse.cooldown
        : now >= skills[skill].cooldown);
}}

function activateSkill(skill) {{
    if (!skillReady(skill)) return;
    const now = Date.now();

    if (skill === "breath") {{
        // Дыхательная гимнастика: временно ускоряет темп стрельбы.
        skills.breath.duration = now + SKILL_BREATH_DURATION;
        skills.breath.cooldown = now + SKILL_CD;
    }} else if (skill === "calm") {{
        // Спокойный ритм: временно замедляет противников.
        skills.calm.duration = now + SKILL_CALM_DURATION;
        skills.calm.cooldown = now + SKILL_CD;
    }} else if (skill === "pulse") {{
        // Активный цикл дыхания (ACBT): создаёт короткий импульс, отталкивающий ближайших противников.
        const range = 230;
        for (const enemy of enemies) {{
            const dx = enemy.x - player.x;
            const dy = enemy.y - player.y;
            const d = Math.hypot(dx, dy) || 1;
            if (d < range) {{
                enemy.x += dx / d * (70 * (1 - d / range));
                enemy.y += dy / d * (70 * (1 - d / range));
            }}
        }}
        if (boss) {{
            const dx = boss.x - player.x;
            const dy = boss.y - player.y;
            const d = Math.hypot(dx, dy) || 1;
            if (d < range) {{
                boss.x += dx / d * 35;
                boss.y += dy / d * 35;
            }}
        }}
        skills.pulse.cooldown = now + SKILL_PULSE_CD;
    }}
    updateSkillButtons();
}}

function updateSkillButtons() {{
    const now = Date.now();
    const defs = [
        [skillBreathBtn, "breath", "Q", "🫁 Дыхание с усилием"],
        [skillCalmBtn, "calm", "E", "🌬️ Спокойный ритм"],
        [skillPulseBtn, "pulse", "R", "🫁 Позиционное дренирование"]
    ];
    for (const [btn, skill, key, label] of defs) {{
        const cd = skill === "pulse" ? skills.pulse.cooldown : skills[skill].cooldown;
        const remain = Math.max(0, cd - now);
        const ready = remain <= 0 && !gameOver;
        btn.classList.toggle("ready", ready);
        btn.classList.toggle("cooldown", !ready);
        btn.innerHTML = label + "<br><span>" + key + " · " +
            (ready ? "готово" : Math.ceil(remain / 1000) + "с") + "</span>";
    }}
}}

skillBreathBtn.addEventListener("pointerdown", function(e) {{ e.preventDefault(); activateSkill("breath"); }});
skillCalmBtn.addEventListener("pointerdown", function(e) {{ e.preventDefault(); activateSkill("calm"); }});
skillPulseBtn.addEventListener("pointerdown", function(e) {{ e.preventDefault(); activateSkill("pulse"); }});

document.addEventListener("keydown", function(e) {{
    if (e.code === "KeyQ") activateSkill("breath");
    if (e.code === "KeyE") activateSkill("calm");
    if (e.code === "KeyR") activateSkill("pulse");
}});

const player = {{
    x: W / 2,
    y: H / 2,
    radius: 18,
    speed: 4.2,
    hp: 3
}};

const mouse = {{
    x: W / 2,
    y: H / 2,
    down: false
}};

// ---------- Управление ----------

function focusGame() {{
    canvas.focus();
}}

const controlKeys = new Set([
    "KeyW", "KeyA", "KeyS", "KeyD",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space"
]);

canvas.addEventListener("mousedown", function(e) {{
    focusGame();
    mouse.down = true;
}});
canvas.addEventListener("mouseup", function() {{ mouse.down = false; }});
canvas.addEventListener("mouseleave", function() {{ mouse.down = false; }});

document.addEventListener("keydown", function(e) {{
    if (controlKeys.has(e.code)) {{
        e.preventDefault();
        keys[e.code] = true;
        focusGame();
    }}
}});
document.addEventListener("keyup", function(e) {{
    if (controlKeys.has(e.code)) {{
        e.preventDefault();
        keys[e.code] = false;
    }}
}});
window.addEventListener("blur", function() {{
    mouse.down = false;
    keys = {{}};
    joystick.active = false;
    joystick.x = 0;
    joystick.y = 0;
    resetJoystickVisual();
}});

canvas.addEventListener("mousemove", function(e) {{
    const rect = canvas.getBoundingClientRect();
    mouse.x = (e.clientX - rect.left) * W / rect.width;
    mouse.y = (e.clientY - rect.top) * H / rect.height;
}});

// ---------- Мобильное управление ----------
const joystick = {{ active:false, pointerId:null, x:0, y:0 }};
const joystickEl = document.getElementById("joystick");
const joystickKnob = document.getElementById("joystickKnob");
const aimPad = document.getElementById("aimPad");

function resetJoystickVisual() {{
    joystickKnob.style.transform = "translate(0px, 0px)";
}}

function updateJoystick(e) {{
    const rect = joystickEl.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const maxRadius = rect.width * 0.34;
    let dx = e.clientX - cx;
    let dy = e.clientY - cy;
    const distance = Math.hypot(dx, dy) || 1;
    if (distance > maxRadius) {{
        dx = dx / distance * maxRadius;
        dy = dy / distance * maxRadius;
    }}
    joystick.x = dx / maxRadius;
    joystick.y = dy / maxRadius;
    joystickKnob.style.transform = `translate(${{dx}}px, ${{dy}}px)`;
}}

joystickEl.addEventListener("pointerdown", function(e) {{
    e.preventDefault(); e.stopPropagation();
    joystick.active = true;
    joystick.pointerId = e.pointerId;
    joystickEl.setPointerCapture(e.pointerId);
    updateJoystick(e);
}});
joystickEl.addEventListener("pointermove", function(e) {{
    if (joystick.active && e.pointerId === joystick.pointerId) {{
        e.preventDefault();
        updateJoystick(e);
    }}
}});
function releaseJoystick(e) {{
    if (joystick.active && (!e || e.pointerId === joystick.pointerId)) {{
        joystick.active = false;
        joystick.pointerId = null;
        joystick.x = 0; joystick.y = 0;
        resetJoystickVisual();
    }}
}}
joystickEl.addEventListener("pointerup", releaseJoystick);
joystickEl.addEventListener("pointercancel", releaseJoystick);
joystickEl.addEventListener("lostpointercapture", function() {{ releaseJoystick(); }});

function setAimFromPointer(e) {{
    const rect = canvas.getBoundingClientRect();
    mouse.x = (e.clientX - rect.left) * W / rect.width;
    mouse.y = (e.clientY - rect.top) * H / rect.height;
    mouse.down = true;
}}
aimPad.addEventListener("pointerdown", function(e) {{
    e.preventDefault(); e.stopPropagation();
    aimPad.setPointerCapture(e.pointerId);
    setAimFromPointer(e);
}});
aimPad.addEventListener("pointermove", function(e) {{
    if (e.pressure > 0 || e.buttons) {{
        e.preventDefault();
        setAimFromPointer(e);
    }}
}});
function releaseAim() {{ mouse.down = false; }}
aimPad.addEventListener("pointerup", releaseAim);
aimPad.addEventListener("pointercancel", releaseAim);
aimPad.addEventListener("lostpointercapture", releaseAim);

document.addEventListener("touchmove", function(e) {{
    if (joystick.active) e.preventDefault();
}}, {{ passive:false }});

// ---------- Враги ----------

function spawnEnemy() {{
    const side = Math.floor(Math.random() * 4);
    let x, y;
    if (side === 0) {{ x = Math.random() * W; y = -35; }}
    else if (side === 1) {{ x = W + 35; y = Math.random() * H; }}
    else if (side === 2) {{ x = Math.random() * W; y = H + 35; }}
    else {{ x = -35; y = Math.random() * H; }}

    let type = "normal";
    const roll = Math.random();
    if (HAS_BURROWER && roll < 0.16) type = "burrower";
    else if (HAS_SHOOTER && roll < 0.34) type = "shooter";
    else if (HAS_RUNNER && roll < 0.52) type = "runner";

    const hpBonus = Math.floor((LEVEL - 1) / 2);
    let hp = 2 + hpBonus;
    let speed = ENEMY_SPEED * (0.8 + Math.random() * 0.4);
    let radius = 18;
    if (type === "runner") {{ hp = Math.max(1, hp - 1); speed *= 1.85; radius = 15; }}
    if (type === "shooter") {{ hp += 1; speed *= 0.65; radius = 20; }}
    if (type === "burrower") {{ hp += 1; speed *= 1.45; radius = 16; }}

    enemies.push({{
        x, y, radius, speed, hp, maxHp: hp, type,
        hitCooldown: 0, shootCooldown: 80 + Math.random() * 80,
        burrowed: type === "burrower", emergeDistance: 125,
        burrowPhase: Math.random() * Math.PI * 2
    }});
}}
function spawnBoss() {{
    if (bossSpawned) return;

    bossSpawned = true;

    // Мини-босс появляется сверху и имеет значительно больше HP.
    boss = {{
        x: W / 2,
        y: -70,
        radius: 34,
        speed: ENEMY_SPEED * 0.55,
        hp: BOSS_HP,
        maxHp: BOSS_HP,
        hitCooldown: 0,
        summonCooldown: 260, poisonCooldown: 360, dashCooldown: 500,
        shield: 0
    }};

    document.getElementById("bossText").innerText =
        "☠️ МИНИ-БОСС: " + BOSS_HP + " HP";
}}

function updateSpawning(now) {{
    // До окончания времени враги постепенно заходят с краёв.
    if (elapsedSeconds >= LEVEL_DURATION) {{
        if (!bossSpawned) {{
            // Оставшиеся обычные враги продолжают существовать,
            // затем появляется мини-босс.
            spawnBoss();
        }}
        return;
    }}

    if (now - lastSpawn >= SPAWN_INTERVAL) {{
        // На поздних уровнях может заходить небольшая группа,
        // но не весь уровень сразу.
        const amount = LEVEL >= 7 && Math.random() < 0.35 ? 2 : 1;

        for (let i = 0; i < amount; i++) {{
            spawnEnemy();
        }}

        lastSpawn = now;
    }}
}}

// Первые враги заходят не сразу все.
for (let i = 0; i < Math.min(2, LEVEL); i++) {{
    spawnEnemy();
}}

// ---------- Стрельба ----------

function shoot() {{
    const now = Date.now();

    // Навык «Дыхательная гимнастика» временно ускоряет темп стрельбы.
    const shotCooldown = now < skills.breath.duration ? 130 : 380;
    if (!mouse.down || now - lastShot < shotCooldown || gameOver) {{
        return;
    }}

    lastShot = now;

    const dx = mouse.x - player.x;
    const dy = mouse.y - player.y;
    const len = Math.hypot(dx, dy) || 1;

    bullets.push({{
        x: player.x,
        y: player.y,
        vx: dx / len * 10,
        vy: dy / len * 10,
        radius: 4,
        damage: 1
    }});
}}

// ---------- Служебное ----------

function showMessage(title, text) {{
    document.getElementById("message").style.display = "flex";
    document.getElementById("messageTitle").innerText = title;
    document.getElementById("messageText").innerText = text;
}}

// Сообщаем родительской странице (Streamlit), что босс побеждён.
// Основной способ — клик по скрытой Streamlit-кнопке с key="shooter_victory_confirm",
// которая находится в родительском DOM. Это гарантирует нормальный st.rerun()
// и обновление session_state, в отличие от одного лишь postMessage.
function sendVictorySignal() {{
    if (victorySignalSent) return;
    victorySignalSent = true;

    try {{
        const btn = window.parent.document.querySelector(
            ".st-key-shooter_victory_confirm button"
        );
        if (btn) {{
            btn.click();
        }} else {{
            window.parent.postMessage("boss_defeated", "*");
        }}
    }} catch (e) {{
        window.parent.postMessage("boss_defeated", "*");
    }}
}}

function updateTimer() {{
    elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);
    const remaining = Math.max(0, LEVEL_DURATION - elapsedSeconds);
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;

    document.getElementById("timerText").innerText =
        "До босса: " + mins + ":" + String(secs).padStart(2, "0");
}}

// ---------- Игровая логика ----------

function update() {{
    if (gameOver) {{
        return;
    }}

    const now = Date.now();

    updateTimer();
    updateSkillButtons();
    updateSpawning(now);

    if (keys["KeyW"] || keys["ArrowUp"]) {{
        player.y -= player.speed;
    }}

    if (keys["KeyS"] || keys["ArrowDown"]) {{
        player.y += player.speed;
    }}

    if (keys["KeyA"] || keys["ArrowLeft"]) {{
        player.x -= player.speed;
    }}

    if (keys["KeyD"] || keys["ArrowRight"]) {{
        player.x += player.speed;
    }}

    if (joystick.active) {{
        player.x += joystick.x * player.speed;
        player.y += joystick.y * player.speed;
    }}

    player.x = Math.max(player.radius, Math.min(W - player.radius, player.x));
    player.y = Math.max(player.radius, Math.min(H - player.radius, player.y));

    shoot();

    for (const eb of enemyBullets) {{
        ctx.beginPath(); ctx.arc(eb.x,eb.y,eb.radius,0,Math.PI*2); ctx.fillStyle="#8b4f55"; ctx.fill();
    }}

    for (const bullet of bullets) {{
        bullet.x += bullet.vx;
        bullet.y += bullet.vy;
    }}

    bullets = bullets.filter(
        b => b.x > -30 && b.x < W + 30 && b.y > -30 && b.y < H + 30
    );

    // Опасные зоны босса: стоять на них нельзя.
    for (const h of hazards) {{
        h.life--;
        if (Math.hypot(player.x-h.x, player.y-h.y) < h.radius + player.radius) {{
            if (!h.damageCooldown || h.damageCooldown <= 0) {{ player.hp--; h.damageCooldown = 35; }}
        }}
        h.damageCooldown = Math.max(0, (h.damageCooldown || 0) - 1);
    }}
    hazards = hazards.filter(h => h.life > 0);

    // Обычные враги и новые типы.
    for (const enemy of enemies) {{
        const dx = player.x - enemy.x;
        const dy = player.y - enemy.y;
        const dist = Math.hypot(dx, dy) || 1;

        const speedFactor = Date.now() < skills.calm.duration ? 0.48 : 1.0;

        if (enemy.type === "burrower") {{
            enemy.burrowPhase += 0.06;
            if (enemy.burrowed && dist <= enemy.emergeDistance) enemy.burrowed = false;
            if (!enemy.burrowed) {{ enemy.x += dx / dist * enemy.speed * speedFactor; enemy.y += dy / dist * enemy.speed * speedFactor; }}
            else {{ enemy.x += dx / dist * enemy.speed * 0.55 * speedFactor; enemy.y += dy / dist * enemy.speed * 0.55 * speedFactor; }}
        }} else {{
            enemy.x += dx / dist * enemy.speed * speedFactor;
            enemy.y += dy / dist * enemy.speed * speedFactor;
        }}

        if (enemy.type === "shooter" && dist < 420) {{
            enemy.shootCooldown--;
            if (enemy.shootCooldown <= 0) {{
                enemyBullets.push({{x:enemy.x,y:enemy.y,vx:dx/dist*5,vy:dy/dist*5,radius:5,damage:1}});
                enemy.shootCooldown = 95;
            }}
        }}

        if (enemy.hitCooldown > 0) {{
            enemy.hitCooldown--;
        }}

        if (
            dist < player.radius + enemy.radius &&
            enemy.hitCooldown <= 0
        ) {{
            player.hp--;
            enemy.hitCooldown = 50;

            if (player.hp <= 0) {{
                gameOver = true;
                showMessage(
                    "Уровень не пройден",
                    "Попытка использована. Можно запустить уровень заново."
                );
            }}
        }}
    }}

    // Мини-босс использует набор способностей.
    if (boss) {{
        boss.summonCooldown--;
        boss.poisonCooldown--;
        boss.dashCooldown--;
        if (boss.summonCooldown <= 0) {{
            for (let i=0;i<2 + Math.floor(LEVEL/4);i++) spawnEnemy();
            boss.summonCooldown = Math.max(180, 300 - LEVEL*8);
        }}
        if (boss.poisonCooldown <= 0) {{
            hazards.push({{x:player.x,y:player.y,radius:55 + LEVEL*2,life:260,damageCooldown:0}});
            boss.poisonCooldown = Math.max(250, 430 - LEVEL*10);
        }}
        if (boss.dashCooldown <= 0) {{
            boss.x += (player.x-boss.x)*0.28;
            boss.y += (player.y-boss.y)*0.28;
            boss.dashCooldown = Math.max(320, 620 - LEVEL*15);
        }}

        // Мини-босс медленно движется к игроку.
        if (boss) {{
        const dx = player.x - boss.x;
        const dy = player.y - boss.y;
        const dist = Math.hypot(dx, dy) || 1;

        boss.x += dx / dist * boss.speed;
        boss.y += dy / dist * boss.speed;

        if (boss.hitCooldown > 0) {{
            boss.hitCooldown--;
        }}

        if (
            dist < player.radius + boss.radius &&
            boss.hitCooldown <= 0
        ) {{
            player.hp--;
            boss.hitCooldown = 70;

            if (player.hp <= 0) {{
                gameOver = true;
                showMessage(
                    "Уровень не пройден",
                    "Мини-босс добрался до игрока."
                );
            }}
        }}
    }}

    }}

    // Снаряды стреляющих врагов.
    for (const eb of enemyBullets) {{ eb.x += eb.vx; eb.y += eb.vy; }}
    for (let i=enemyBullets.length-1;i>=0;i--) {{
        const eb=enemyBullets[i];
        if (Math.hypot(eb.x-player.x, eb.y-player.y) < eb.radius+player.radius) {{ player.hp--; enemyBullets.splice(i,1); }}
    }}
    enemyBullets = enemyBullets.filter(b=>b.x>-20&&b.x<W+20&&b.y>-20&&b.y<H+20);
    if (player.hp <= 0 && !gameOver) {{ gameOver=true; showMessage("Уровень не пройден","Игрок получил слишком много урона."); }}

    // Попадания по обычным врагам.
    for (let bi = bullets.length - 1; bi >= 0; bi--) {{
        const bullet = bullets[bi];
        let bulletUsed = false;

        for (let ei = enemies.length - 1; ei >= 0; ei--) {{
            const enemy = enemies[ei];

            if (!enemy.burrowed &&
                Math.hypot(
                    bullet.x - enemy.x,
                    bullet.y - enemy.y
                ) < bullet.radius + enemy.radius
            ) {{
                enemy.hp -= bullet.damage;
                bullets.splice(bi, 1);
                bulletUsed = true;

                if (enemy.hp <= 0) {{
                    enemies.splice(ei, 1);
                    kills++;
                }}

                break;
            }}
        }}

        if (bulletUsed) continue;

        // Попадание по мини-боссу.
        if (boss && Math.hypot(
            bullet.x - boss.x,
            bullet.y - boss.y
        ) < bullet.radius + boss.radius) {{
            boss.hp -= bullet.damage;
            bullets.splice(bi, 1);

            if (boss.hp <= 0) {{
                boss = null;
                victory = true;
                gameOver = true;

                document.getElementById("bossText").innerText =
                    "☠️ Мини-босс побеждён";

                showMessage(
                    "🏆 Уровень пройден!",
                    "Мини-босс побеждён. Нажми «Забрать награду» под игрой."
                );

                sendVictorySignal();
            }}
        }}
    }}

    document.getElementById("enemyCounter").innerText =
        "Врагов: " + enemies.length +
        (boss ? " + БОСС" : "");
}}

// ---------- Отрисовка ----------

function drawBackground() {{
    ctx.fillStyle = "#dfe9df";
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = "rgba(80,100,80,.10)";
    ctx.lineWidth = 1;

    for (let x = 0; x <= W; x += 40) {{
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
    }}

    for (let y = 0; y <= H; y += 40) {{
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
    }}

    ctx.fillStyle = "rgba(86,130,91,.18)";

    for (let x = 60; x < W; x += 180) {{
        ctx.beginPath();
        ctx.arc(x, 95, 28, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(x + 22, 105, 22, 0, Math.PI * 2);
        ctx.fill();
    }}
}}

function drawPlayer() {{
    const angle = Math.atan2(
        mouse.y - player.y,
        mouse.x - player.x
    );

    ctx.beginPath();
    ctx.ellipse(
        player.x,
        player.y + 20,
        23,
        9,
        0,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "rgba(0,0,0,.16)";
    ctx.fill();

    ctx.beginPath();
    ctx.arc(
        player.x,
        player.y,
        player.radius,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "#4f8f7b";
    ctx.fill();

    ctx.beginPath();
    ctx.arc(
        player.x,
        player.y - 21,
        9,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "#efbb91";
    ctx.fill();

    ctx.save();
    ctx.translate(player.x, player.y);
    ctx.rotate(angle);

    ctx.fillStyle = "#30343b";
    ctx.fillRect(8, -4, 28, 8);

    ctx.restore();

    // HP игрока.
    for (let i = 0; i < 3; i++) {{
        ctx.fillStyle = i < player.hp
            ? "#6e9f7b"
            : "rgba(60,70,60,.18)";

        ctx.fillRect(
            player.x - 24 + i * 17,
            player.y + 30,
            13,
            5
        );
    }}
}}

function drawEnemy(enemy) {{
    if (enemy.type === "burrower" && enemy.burrowed) {{
        ctx.beginPath(); ctx.arc(enemy.x,enemy.y,enemy.radius,0,Math.PI*2);
        ctx.fillStyle="rgba(70,65,55,.45)"; ctx.fill();
        return;
    }}
    ctx.beginPath();
    ctx.ellipse(
        enemy.x,
        enemy.y + 20,
        22,
        8,
        0,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "rgba(0,0,0,.14)";
    ctx.fill();

    ctx.beginPath();
    ctx.arc(
        enemy.x,
        enemy.y,
        enemy.radius,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = enemy.type === "shooter" ? "#596f8a" : enemy.type === "runner" ? "#9a714d" : enemy.type === "burrower" ? "#6c675f" : "#7c6861";
    ctx.fill();

    ctx.beginPath();
    ctx.arc(
        enemy.x,
        enemy.y - 20,
        9,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "#e0b58d";
    ctx.fill();

    // Сигарета и дым.
    ctx.strokeStyle = "#555";
    ctx.lineWidth = 3;

    ctx.beginPath();
    ctx.moveTo(enemy.x + 6, enemy.y - 18);
    ctx.lineTo(enemy.x + 21, enemy.y - 23);
    ctx.stroke();

    ctx.strokeStyle = "rgba(100,100,100,.38)";
    ctx.lineWidth = 2;

    ctx.beginPath();
    ctx.arc(
        enemy.x + 24,
        enemy.y - 29,
        5,
        0,
        Math.PI
    );
    ctx.stroke();

    // Полоска HP у каждого врага.
    const barW = 34;
    const barH = 5;
    const hpRatio = Math.max(0, enemy.hp / enemy.maxHp);

    ctx.fillStyle = "rgba(80,50,50,.25)";
    ctx.fillRect(
        enemy.x - barW / 2,
        enemy.y + 28,
        barW,
        barH
    );

    ctx.fillStyle = "#8a625e";
    ctx.fillRect(
        enemy.x - barW / 2,
        enemy.y + 28,
        barW * hpRatio,
        barH
    );
}}

function drawBoss() {{
    if (!boss) return;

    // Большая тень.
    ctx.beginPath();
    ctx.ellipse(
        boss.x,
        boss.y + 37,
        43,
        13,
        0,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "rgba(0,0,0,.18)";
    ctx.fill();

    // Корпус мини-босса.
    ctx.beginPath();
    ctx.arc(
        boss.x,
        boss.y,
        boss.radius,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "#5e4f59";
    ctx.fill();

    // Голова.
    ctx.beginPath();
    ctx.arc(
        boss.x,
        boss.y - 37,
        15,
        0,
        Math.PI * 2
    );
    ctx.fillStyle = "#d5a27e";
    ctx.fill();

    // Отличительный знак босса.
    ctx.fillStyle = "#9a6d68";
    ctx.fillRect(
        boss.x - 22,
        boss.y - 5,
        44,
        9
    );

    // Большая полоса HP босса.
    const barW = 100;
    const barH = 9;
    const ratio = Math.max(0, boss.hp / boss.maxHp);

    ctx.fillStyle = "rgba(70,40,40,.28)";
    ctx.fillRect(
        boss.x - barW / 2,
        boss.y + 48,
        barW,
        barH
    );

    ctx.fillStyle = "#7d4548";
    ctx.fillRect(
        boss.x - barW / 2,
        boss.y + 48,
        barW * ratio,
        barH
    );

    ctx.fillStyle = "#4d3a3d";
    ctx.font = "bold 13px Arial";
    ctx.textAlign = "center";
    ctx.fillText(
        "МИНИ-БОСС",
        boss.x,
        boss.y - 58
    );
}}

function draw() {{
    ctx.clearRect(0, 0, W, H);
    drawBackground();
    for (const h of hazards) {{
        ctx.beginPath(); ctx.arc(h.x,h.y,h.radius,0,Math.PI*2);
        ctx.fillStyle = "rgba(90,140,70,.35)"; ctx.fill();
        ctx.strokeStyle = "rgba(60,100,50,.55)"; ctx.stroke();
    }}

    for (const bullet of bullets) {{
        ctx.beginPath();
        ctx.arc(
            bullet.x,
            bullet.y,
            bullet.radius,
            0,
            Math.PI * 2
        );
        ctx.fillStyle = "#30343b";
        ctx.fill();
    }}

    for (const enemy of enemies) {{
        drawEnemy(enemy);
    }}

    drawBoss();
    drawPlayer();
}}

function loop() {{
    update();
    draw();
    requestAnimationFrame(loop);
}}

focusGame();
loop();
</script>

</body>
</html>
"""

def shooter_page():

    # Оставлено на случай fallback-postMessage (см. sendVictorySignal в игре),
    # но основной путь теперь — скрытая кнопка ниже.
    if st.query_params.get("shooter_result") == "boss_defeated":
        st.session_state.shooter_boss_defeated = True
        st.query_params.clear()

    state = get_shooter_state()

    st.subheader("🎯 Защита лёгких")

    st.markdown(
        """
        <div class='card'>
        <b>2D top-down режим</b><br>
        Перемещение — <b>WASD</b> или стрелки. 
        Прицеливайся мышью и стреляй левой кнопкой.
        Каждый уровень становится сложнее.
        </div>
        """,
        unsafe_allow_html=True
    )

    a, b, c = st.columns(3)

    with a:
        st.metric("Попытки", state["attempts"])

    with b:
        st.metric("Лучший уровень", f"{state['best_level']}/10")

    with c:
        next_level = min(state["best_level"] + 1, 10)
        st.metric("Следующий", f"{next_level}/10")

    if state["best_level"] >= 10:
        st.success("🏆 Все 10 уровней пройдены!")

    if SHOOTER_DEV_MODE:
        current_level = st.selectbox(
            "🛠️ Уровень для тестирования",
            options=list(range(1, 11)),
            index=max(0, min(9, int(st.session_state.get("shooter_level", 1)) - 1)),
            key="shooter_dev_level"
        )
        current_level = int(current_level)
    else:
        current_level = int(
            st.session_state.get(
                "shooter_level",
                min(state["best_level"] + 1, 10)
            )
        )

        if current_level < 1:
            current_level = 1

        if current_level > 10:
            current_level = 10

    st.markdown(
        f"""
        <div class='card'>
            <div class='bigstage'>Уровень {current_level} / 10</div>
            <div class='muted'>
                Противников: {3 + current_level * 2} ·
                Скорость: {0.75 + current_level * 0.13:.2f}x ·
                Награда: {shooter_level_reward(current_level)} 🪙
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    running = st.session_state.get("shooter_running", False)

    if not running:

        if SHOOTER_DEV_MODE:
            st.info(
                "🛠️ Режим разработки: попытки не списываются. "
                "Можно запускать любой из 10 уровней сколько угодно раз."
            )

        elif state["attempts"] <= 0:
            st.warning(
                "Попыток пока нет. Выполняй миссии, и попытки будут начисляться"
            )
            return

        if st.button(
            f"▶ Начать уровень {current_level}",
            type="primary",
            use_container_width=True
        ):
            if start_shooter_level(current_level):
                st.rerun()
            else:
                st.error("Не удалось списать попытку.")

        st.caption(
            "Режим разработки: попытка не списывается. Можно тестировать любой уровень."
            if SHOOTER_DEV_MODE
            else "Одна попытка списывается при запуске уровня. Ежедневно добавляется одна базовая попытка."
        )

        return

    # --------------------------------------------------------
    # Running game
    # --------------------------------------------------------

    token = st.session_state.get(
        "shooter_game_token",
        int(time.time() * 1000)
    )

    components.html(
        shooter_game_html(current_level, token),
        height=650,
        scrolling=False
    )

    # Скрытая кнопка-триггер: по ней кликает JS внутри игрового iframe,
    # когда мини-босс побеждён (см. sendVictorySignal). Так мы получаем
    # обычный, надёжный Streamlit rerun с обновлением session_state —
    # без непредсказуемого postMessage/query_params.
    st.markdown(
        """
        <style>
        .st-key-shooter_victory_confirm {
            position: absolute;
            width: 1px;
            height: 1px;
            overflow: hidden;
            opacity: 0;
            pointer-events: none;
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    if st.button("victory-signal", key="shooter_victory_confirm"):
        st.session_state.shooter_boss_defeated = True
        st.rerun()

    st.warning(
        "После полной победы над всеми противниками нажми "
        "«Забрать награду» ниже."
    )

    col1, col2 = st.columns(2)

    boss_defeated = st.session_state.get(
        "shooter_boss_defeated",
        False
    )

    with col1:
        if boss_defeated:
            if st.button(
                "🏆 Я прошёл уровень — забрать награду",
                type="primary",
                use_container_width=True
            ):
                reward = give_shooter_level_reward(current_level)

                st.session_state.shooter_reward = reward
                st.session_state.shooter_running = False
                st.session_state.shooter_boss_defeated = False

                if current_level < 10:
                    st.session_state.shooter_level = current_level + 1
                else:
                    st.session_state.shooter_level = 10

                st.success(
                    f"Уровень {current_level} завершён! "
                    f"+{reward} 🪙"
                )
                add_shooter_attempts(1)
                st.rerun()

    with col2:
        if st.button(
            "🚪 Выйти из игры",
            use_container_width=True
        ):
            st.session_state.shooter_running = False
            st.rerun()

    reward = st.session_state.get("shooter_reward", 0)

    if reward:
        st.success(
            f"Последняя награда: +{reward} 🪙"
        )

    st.caption(
        "Важно: награда за каждый уровень выдаётся только один раз."
    )

def render_dynamic_background():
    completed = completed_missions_count()

    # Чем больше заданий, тем сильнее раздвигаются тучи
    progress = min(completed / 4, 1.0)

    # 0 -> серое небо
    # 1 -> голубое небо
    gray_alpha = 0.75 - progress * 0.65
    cloud_opacity = 0.95 - progress * 0.8

    # Тучи постепенно уходят в стороны
    cloud_left = -120 - int(progress * 300)
    cloud_right = -100 - int(progress * 300)

    st.markdown(
        f"""
        <style>
        .stApp {{
            background:
                linear-gradient(
                    to bottom,
                    rgba(120, 130, 140, {gray_alpha}),
                    rgba(190, 215, 225, {gray_alpha * 0.55})
                ),
                linear-gradient(
                    to bottom,
                    #9fd5e8 0%,
                    #c9e9f2 55%,
                    #eef7f5 100%
                );
            position: relative;
        }}

        .stApp::before,
        .stApp::after {{
            content: "";
            position: fixed;
            top: 40px;
            width: 520px;
            height: 230px;
            border-radius: 50%;
            pointer-events: none;
            z-index: 0;

            background:
                radial-gradient(
                    ellipse at center,
                    rgba(75, 82, 88, {cloud_opacity}) 0%,
                    rgba(105, 112, 118, {cloud_opacity * 0.8}) 45%,
                    rgba(140, 145, 148, 0) 72%
                );

            filter: blur(12px);
            transition: all 2s ease;
        }}

        .stApp::before {{
            left: {cloud_left}px;
        }}

        .stApp::after {{
            right: {cloud_right}px;
            top: 90px;
        }}

        /* Контент находится поверх неба */
        .stApp > div {{
            position: relative;
            z-index: 1;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )
render_dynamic_background()

def complete_mission(kind, xp, coins):
    now = datetime.now().isoformat(timespec="seconds")
    day = date.today().isoformat()

    c = db()

    cur = c.execute("""
        INSERT OR IGNORE INTO mission_completions
        (user_id, mission_type, mission_date, completed_at, xp_earned)
        VALUES (?, ?, ?, ?, ?)
    """, (USER_ID, kind, day, now, xp))

    if cur.rowcount != 1:
        c.close()
        return False

    u = c.execute("""
        SELECT xp, coins, level, streak, last_activity_date
        FROM users
        WHERE id=?
    """, (USER_ID,)).fetchone()

    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if u["last_activity_date"] == yesterday:
        streak = int(u["streak"]) + 1
    elif u["last_activity_date"] == day:
        streak = int(u["streak"])
    else:
        streak = 1

    new_xp = int(u["xp"]) + xp

    level = (
        5 if new_xp >= 1800 else
        4 if new_xp >= 1200 else
        3 if new_xp >= 700 else
        2 if new_xp >= 300 else
        1
    )

    new_coins = int(u["coins"]) + int(coins)

    c.execute("""
        UPDATE users
        SET xp=?,
            coins=?,
            level=?,
            streak=?,
            last_activity_date=?,
            updated_at=?
        WHERE id=?
    """, (
        new_xp,
        new_coins,
        level,
        streak,
        day,
        now,
        USER_ID
    ))

    c.commit()
    c.close()

    # Каждое выполненное задание добавляет одну попытку шутера.
    add_shooter_attempts(1)

    return True


def week_training_count():
    t = date.today(); monday = t - timedelta(days=t.weekday())
    c = db(); n = c.execute("SELECT COUNT(*) n FROM mission_completions WHERE user_id=? AND mission_type='exercise' AND mission_date BETWEEN ? AND ?", (USER_ID, monday.isoformat(), t.isoformat())).fetchone()["n"]; c.close(); return int(n)


def save_checkin(values):
    now = datetime.now().isoformat(timespec="seconds"); day = date.today().isoformat(); c = db()
    c.execute("""INSERT INTO checkins(user_id,checkin_date,breathlessness,cough,sputum,energy,note,created_at)
                 VALUES (?,?,?,?,?,?,?,?)
                 ON CONFLICT(user_id,checkin_date) DO UPDATE SET
                 breathlessness=excluded.breathlessness,cough=excluded.cough,sputum=excluded.sputum,
                 energy=excluded.energy,note=excluded.note,created_at=excluded.created_at""",
              (USER_ID,day,values["breathlessness"],values["cough"],values["sputum"],values["energy"],values.get("note", ""),now))
    c.commit(); c.close()


def checkin_history(limit=14):
    c = db(); rows = c.execute("SELECT checkin_date,breathlessness,cough,sputum,energy,note FROM checkins WHERE user_id=? ORDER BY checkin_date DESC LIMIT ?", (USER_ID, limit)).fetchall(); c.close(); return rows


def owns(item_id):
    c = db(); row = c.execute("SELECT 1 FROM inventory WHERE user_id=? AND item_id=?", (USER_ID,item_id)).fetchone(); c.close(); return row is not None


def buy(item_id, price):
    c = db()

    u = c.execute(
        "SELECT coins FROM users WHERE id=?",
        (USER_ID,)
    ).fetchone()

    if not u:
        c.close()
        return False

    coins = int(u["coins"])

    if coins < price:
        c.close()
        return False

    # Списываем монеты
    c.execute(
        "UPDATE users SET coins = coins - ?, updated_at=? WHERE id=?",
        (int(price), datetime.now().isoformat(timespec="seconds"), USER_ID)
    )

    # Добавляем предмет в инвентарь
    c.execute(
        "INSERT OR IGNORE INTO inventory(user_id, item_id, purchased_at) VALUES (?,?,?)",
        (USER_ID, item_id, datetime.now().isoformat(timespec="seconds"))
    )

    c.commit()
    c.close()

    return True

# -----------------------------
# Game content
# -----------------------------
TOPS = {
    "hoodie_green": ("Мятная худи", "#8ED1C2", 100),
    "hoodie_purple": ("Фиолетовая худи", "#A98BEA", 200),
    "hoodie_orange": ("Оранжевая худи", "#F19B63", 250),
    "jacket_blue": ("Спортивная куртка", "#436A96", 300),
}
BOTTOMS = {"pants_black": ("Чёрные брюки", "#30343B", 0), "pants_beige": ("Бежевые брюки", "#D6C1A5", 180), "pants_blue": ("Синие брюки", "#4C6685", 220)}
SHOES = {"sneakers_white": ("Белые кроссовки", "#F3F3F0", 0), "sneakers_green": ("Мятные кроссовки", "#9AD9C7", 140), "sneakers_red": ("Красные кроссовки", "#C9625E", 180)}
ROOMS = {"living_room": ("Уютная гостиная", 0, "#EEE7DB", "#83A08A"), "bright_room": ("Светлая комната", 300, "#EAF2EF", "#A9C7BB"), "balcony": ("Балкон", 500, "#D9EEF5", "#88A999")}
LOCATIONS = {"home": ("Дом", 0), "park": ("Парк", 500), "mountains": ("Горы", 700)}

WORKOUT_STEPS = [
    ("Подготовка", 45, "Сядьте или встаньте удобно. Расслабьте плечи и приготовьтесь следовать следующему интервалу."),
    ("Аэробная часть", 90, "Выполняйте назначенную вам активность в комфортном темпе согласно своей программе реабилитации."),
    ("Восстановление", 30, "Снизьте темп и спокойно восстановитесь перед следующим этапом."),
    ("Вдох", 2, "Мягко вдохните через нос. Не форсируйте вдох."),
    ("Выдох", 4, "Плавно выдыхайте через слегка сомкнутые губы."),
    ("Вдох", 2, "Следующий мягкий вдох через нос."),
    ("Выдох", 4, "Плавный выдох через слегка сомкнутые губы."),
    ("Восстановление", 30, "Спокойно завершите текущую часть занятия."),
]


def avatar_svg(top, bottom, shoes):
    return f"""<svg viewBox='0 0 420 500' width='100%' xmlns='http://www.w3.org/2000/svg'>
    <rect width='420' height='500' rx='30' fill='#F7F2EA'/>
    <ellipse cx='210' cy='457' rx='122' ry='20' fill='#DDD3C5'/>
    <circle cx='210' cy='116' r='54' fill='#EFB991'/><path d='M154 115Q175 45 210 53Q258 53 270 120L250 94Q210 77 174 99Z' fill='#50342C'/>
    <circle cx='191' cy='121' r='5' fill='#3d3130'/><circle cx='229' cy='121' r='5' fill='#3d3130'/>
    <path d='M198 147Q210 157 222 147' stroke='#9C5D58' stroke-width='4' fill='none'/>
    <path d='M145 195Q210 168 275 195L304 310Q210 334 116 310Z' fill='{top}'/>
    <rect x='157' y='209' width='31' height='108' rx='15' fill='{top}' transform='rotate(9 157 209)'/>
    <rect x='232' y='209' width='31' height='108' rx='15' fill='{top}' transform='rotate(-9 232 209)'/>
    <path d='M155 300L202 300L194 416L151 416Z' fill='{bottom}'/><path d='M217 300L264 300L272 416L229 416Z' fill='{bottom}'/>
    <path d='M145 406Q170 398 198 414L194 439Q161 446 135 432Z' fill='{shoes}' stroke='#aaa'/>
    <path d='M229 414Q255 398 284 414L292 436Q257 447 229 432Z' fill='{shoes}' stroke='#aaa'/>
    <text x='210' y='478' text-anchor='middle' font-family='Arial' font-size='18' font-weight='700' fill='#5d625f'>Твой персонаж</text>
    </svg>"""


def room_svg(bg, sofa):
    return f"""<svg viewBox='0 0 760 410' width='100%' xmlns='http://www.w3.org/2000/svg'>
    <rect width='760' height='410' fill='{bg}'/><rect y='300' width='760' height='110' fill='#D6C6B2'/>
    <rect x='70' y='55' width='190' height='120' rx='10' fill='#FAF8F2' stroke='#C9C1B4' stroke-width='4'/>
    <rect x='85' y='70' width='160' height='90' fill='#BBDCE1'/><path d='M90 155L135 116L164 138L202 102L242 155Z' fill='#98B89D'/>
    <rect x='420' y='218' width='230' height='70' rx='26' fill='{sofa}'/><rect x='440' y='180' width='70' height='65' rx='22' fill='{sofa}'/><rect x='560' y='180' width='70' height='65' rx='22' fill='{sofa}'/>
    <rect x='475' y='315' width='115' height='23' rx='11' fill='#9D7D59'/><circle cx='532' cy='316' r='4' fill='#6c5138'/>
    <path d='M120 315Q145 255 170 315' stroke='#6D9B71' stroke-width='16' fill='none'/><circle cx='150' cy='265' r='24' fill='#84AE78'/><circle cx='180' cy='278' r='22' fill='#75A66C'/>
    <text x='380' y='44' text-anchor='middle' font-family='Arial' font-size='26' font-weight='700' fill='#4A4E4B'>Моё пространство</text>
    </svg>"""


def item_tile(item_id, name, color, price, category):
    locked = not owns(item_id) and price > 0
    st.markdown(f"<div class='shopitem'><div style='height:58px;border-radius:12px;background:{color};margin-bottom:8px'></div><b>{html.escape(name)}</b><div class='muted'>{'Бесплатно' if price==0 else '🪙 '+str(price)}</div></div>", unsafe_allow_html=True)
    if owns(item_id):
        st.success("Куплено", icon="✅")
    elif price == 0:
        if st.button("Получить", key="get_"+item_id, use_container_width=True): buy(item_id,0); st.rerun()
    else:
        if st.button("Купить", key="buy_"+item_id, disabled=get_user()["coins"] < price, use_container_width=True): buy(item_id,price); st.rerun()


# -----------------------------
# UI state
# -----------------------------
for k,v in {"page":"Главная","selected_mission":"exercise","result":None,"knowledge_checked":False,"weekly_step":1}.items():
    if k not in st.session_state: st.session_state[k]=v


def reset_mission_state():
    st.session_state.result=None
    st.session_state.knowledge_checked=False
    st.session_state.weekly_step=1
    st.session_state.pop("knowledge_answer",None)
    st.session_state.pop("weekly_barrier",None)
    st.session_state.pop("weekly_strategy",None)


def go(page):
    st.session_state.page=page


# -----------------------------
# Style + sidebar
# -----------------------------
st.markdown("""<style>
.block-container{max-width:1220px;padding-top:1.2rem}
.hero{background:linear-gradient(135deg,#f2f7f4,#fbf5ed);border:1px solid #dce7df;border-radius:24px;padding:24px 28px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e2e7e3;border-radius:18px;padding:18px;margin-bottom:12px}
.shopitem{border:1px solid #e1e5e2;border-radius:16px;padding:12px;background:#fff;text-align:center;min-height:135px}
.muted{color:#68726e}.pill{display:inline-block;background:#edf4ef;border-radius:999px;padding:5px 10px;color:#46614e;font-size:.84rem}
.bigstage{font-size:2.2rem;font-weight:800;margin:5px 0}.timer{font-size:5rem;font-weight:900;text-align:center;line-height:1;margin:14px 0}.instruction{background:#f5faf6;border-left:4px solid #73a27d;border-radius:12px;padding:14px 16px}
</style>""", unsafe_allow_html=True)

u=get_user()
st.markdown("""<script>
(function() {
    if (window.innerWidth > 700) return;
    function closeSidebar() {
        try {
            const doc = window.parent.document;
            const sidebar = doc.querySelector('[data-testid="stSidebar"]');
            if (!sidebar) return;
            const btn = sidebar.querySelector('[data-testid="stSidebarCollapseButton"]') ||
                        sidebar.querySelector('button[aria-label*="Close"]');
            if (btn) btn.click();
        } catch (e) {}
    }
    setTimeout(closeSidebar, 100);
    setTimeout(closeSidebar, 400);
})();
</script>""", unsafe_allow_html=True)

st.sidebar.title("🫁 Путь дыхания")
st.sidebar.caption("COPD self-management • Gamified Digital Health PoC")
for nav in ["Главная","Миссии","Шутер","Симптомы","Знания о ХОБЛ","Прогресс","Персонаж","Магазин"]:
    if st.sidebar.button(nav,key="nav_"+nav,use_container_width=True): go(nav); st.rerun()
st.sidebar.divider()
st.sidebar.metric("Уровень",u["level"])
st.sidebar.metric("XP",u["xp"])
st.sidebar.metric("Монеты",f"🪙 {u['coins']}")
st.sidebar.metric("Серия",f"🔥 {u['streak']} дн.")
st.sidebar.metric("Попытки шутера",get_shooter_state()["attempts"])

st.markdown("<div class='hero'><span class='pill'>COPD • Digital Health</span><h1>Путь дыхания</h1><p class='muted'>Поддержка домашней лёгочной реабилитации и самоменеджмента при ХОБЛ с игровым слоем.</p></div>",unsafe_allow_html=True)

page=st.session_state.page

# -----------------------------
# Home
# -----------------------------
if page=="Главная":
    a,b,c,d=st.columns(4); a.metric("XP",u["xp"]); b.metric("Уровень",u["level"]); c.metric("Монеты",f"🪙 {u['coins']}"); d.metric("Занятий за неделю",f"{week_training_count()}/{u['weekly_goal']}")
    st.subheader("🎯 Мои цели")
    goals={"regularity":("Регулярность реабилитации","Поддерживать выполнение согласованной программы."),"activity":("Повседневная активность","Поддерживать переносимость активности в рамках плана."),"self_management":("Самоменеджмент","Лучше понимать симптомы и свой план действий."),"education":("Знания о ХОБЛ","Разбираться в реабилитации и самонаблюдении.")}
    cols=st.columns(2)
    for i,g in enumerate([x for x in u["goals"].split(",") if x]):
        t,desc=goals.get(g,goals["regularity"])
        with cols[i%2]: st.markdown(f"<div class='card'><b>{t}</b><br><span class='muted'>{desc}</span></div>",unsafe_allow_html=True)
    st.subheader("Сегодня")
    missions=[("exercise","🏃","Лёгочная реабилитация","Guided-сессия с пошаговыми интервалами.",100),("education","🧠","Знание дня","Обучение с разбором правильного ответа.",30),("checkin","❤️","Самонаблюдение","Одышка, кашель, мокрота и энергия.",20),("weekly","🎯","Задача недели","Барьер → стратегия → реалистичный план.",60)]
    for typ,icon,title,desc,xp in missions:
        st.markdown(f"<div class='card'><h3>{icon} {title}</h3><p>{desc}</p><span class='small'>+{xp} XP</span></div>",unsafe_allow_html=True)
        if mission_done(typ): st.success("Уже выполнено / учтено")
        elif st.button("Открыть",key="home_"+typ): reset_mission_state(); st.session_state.selected_mission=typ; go("Детальная миссия"); st.rerun()
    st.subheader("👤 Твой персонаж")
    st.markdown(
        avatar_svg(
            TOPS[u["equipped_top"]][1],
            BOTTOMS[u["equipped_bottom"]][1],
            SHOES[u["equipped_shoes"]][1]
        ),
        unsafe_allow_html=True
    )

# -----------------------------
# Missions
# -----------------------------
elif page=="Миссии":
    st.subheader("🎯 Миссии")
    cards=[("exercise","🏃","Лёгочная реабилитация",100,"Guided workout"),("education","🧠","Знание дня",30,"Образование"),("checkin","❤️","Самонаблюдение",20,"Дневник симптомов"),("weekly","🎯","Задача недели",60,"Problem-solving")]
    for typ,icon,title,xp,desc in cards:
        st.markdown(f"### {icon} {title}")
        st.caption(f"{desc} • +{xp} XP")
        if mission_done(typ): st.success("Выполнено")
        elif st.button("Открыть",key="m_"+typ): reset_mission_state(); st.session_state.selected_mission=typ; go("Детальная миссия"); st.rerun()

# -----------------------------
# Detailed mission
# -----------------------------
elif page=="Детальная миссия":
    typ=st.session_state.selected_mission
    if typ=="exercise":
        st.subheader("🏃 Лёгочная реабилитация")
        st.caption("Демонстрационный guided-протокол. Не является индивидуальным медицинским назначением.")
        if mission_done("exercise"):
            st.success("Тренировка сегодня уже учтена.")
        else:
            if "w_step" not in st.session_state:
                st.session_state.w_step=0; st.session_state.w_remaining=WORKOUT_STEPS[0][1]; st.session_state.w_running=True
            step=st.session_state.w_step; remaining=st.session_state.w_remaining; title,sec,instr=WORKOUT_STEPS[step]
            st.markdown(f"**Этап {step+1} из {len(WORKOUT_STEPS)}**")
            st.progress((step+1)/len(WORKOUT_STEPS))
            st.markdown(f"<div class='bigstage'>{title}</div>",unsafe_allow_html=True)
            st.markdown(f"<div class='instruction'><b>Сейчас:</b><br>{html.escape(instr)}</div>",unsafe_allow_html=True)
            st.markdown(f"<div class='timer'>{remaining:02d}</div>",unsafe_allow_html=True)
            if st.session_state.w_running:
                if remaining>1:
                    st.session_state.w_remaining-=1; time.sleep(1); st.rerun()
                elif step+1 < len(WORKOUT_STEPS):
                    st.session_state.w_step+=1; st.session_state.w_remaining=WORKOUT_STEPS[step+1][1]; st.rerun()
                else:
                    inserted=complete_mission("exercise",100,50)
                    st.session_state.result={"title":"Тренировка завершена","body":"Guided-сессия полностью пройдена и сохранена в постоянной базе.","xp":100 if inserted else 0}
                    for k in ["w_step","w_remaining","w_running"]: st.session_state.pop(k,None)
                    go("Результат"); st.rerun()
            else:
                if st.button("▶ Продолжить",type="primary"): st.session_state.w_running=True; st.rerun()
    elif typ=="education":
        st.subheader("🧠 Знание дня")
        q={"q":"Что полезно регулярно отмечать в дневнике при ХОБЛ?","opts":["Одышку, кашель, мокроту и общее самочувствие","Только температуру","Только шаги"],"correct":0,"why":"Регулярное наблюдение помогает замечать изменения относительно привычного состояния и обсуждать их со специалистом.","more":["Одышка — субъективное ощущение затруднённого дыхания.","Изменения кашля и мокроты можно записывать как часть самонаблюдения.","Дневник не ставит диагноз."]}
        if mission_done("education"): st.success("Урок сегодня уже завершён.")
        else:
            st.markdown("### "+q["q"]); ans=st.radio("Выбери вариант",q["opts"],index=None,key="education_option")
            if st.button("Проверить ответ",disabled=ans is None): st.session_state.knowledge_answer=q["opts"].index(ans); st.session_state.knowledge_checked=True; st.rerun()
            if st.session_state.knowledge_checked:
                correct=st.session_state.knowledge_answer==q["correct"]; st.success("✅ Правильно" if correct else "ℹ️ Разбор ответа"); st.markdown("### Правильный ответ"); st.info(q["opts"][q["correct"]]); st.markdown("### Почему"); st.write(q["why"]); st.markdown("### Подробнее"); [st.write("• "+x) for x in q["more"]]
                if st.button("Завершить урок",type="primary"):
                    ins=complete_mission("education",30,10); st.session_state.result={"title":"Знание дня завершено","body":"Материал изучен. Результат сохранён в SQLite.","xp":30 if ins else 0}; go("Результат"); st.rerun()
    elif typ=="checkin":
        st.subheader("❤️ Самонаблюдение")
        if mission_done("checkin"): st.success("Сегодняшний check-in уже сохранён.")
        else:
            b=st.radio("Одышка",["Лучше обычного","Как обычно","Хуже обычного"],index=None,key="cb"); c=st.radio("Кашель",["Лучше обычного","Как обычно","Хуже обычного"],index=None,key="cc"); s=st.radio("Мокрота",["Меньше/обычно","Больше обычного","Без заметных изменений"],index=None,key="cs"); e=st.radio("Энергия",["Лучше обычного","Как обычно","Хуже обычного"],index=None,key="ce"); note=st.text_area("Комментарий",key="cn")
            if st.button("Сохранить check-in",type="primary",disabled=not all([b,c,s,e])):
                save_checkin({"breathlessness":b,"cough":c,"sputum":s,"energy":e,"note":note}); ins=complete_mission("checkin",20,5); st.session_state.result={"title":"Check-in сохранён","body":"Запись добавлена в историю симптомов.","xp":20 if ins else 0}; go("Результат"); st.rerun()
    else:
        st.subheader("🎯 Задача недели")
        if mission_done("weekly"): st.success("Задача недели уже выполнена.")
        else:
            s=st.session_state.weekly_step
            if s==1:
                v=st.radio("Что чаще мешает регулярности?",["Не хватает времени","Забываю","Устаю / сложно вписать в день","Другой барьер"],index=None,key="barrier")
                if st.button("Далее",disabled=v is None): st.session_state.weekly_barrier=v; st.session_state.weekly_step=2; st.rerun()
            elif s==2:
                v=st.radio("Что поможет уменьшить барьер?",["Назначить конкретное время","Поставить напоминание","Разбить задачу на небольшие шаги","Заранее подготовить место"],index=None,key="strategy")
                if st.button("Далее",disabled=v is None): st.session_state.weekly_strategy=v; st.session_state.weekly_step=3; st.rerun()
            else:
                v=st.radio("Когда будет удобнее выполнить следующее занятие?",["Утром","Днём","Вечером"],index=None,key="plan")
                st.write("**Барьер:**",st.session_state.weekly_barrier); st.write("**Стратегия:**",st.session_state.weekly_strategy); st.write("**Время:**",v or "—")
                if st.button("Завершить задачу недели",type="primary",disabled=v is None):
                    ins=complete_mission("weekly",60,15); st.session_state.result={"title":"План на неделю сформирован","body":f"**Барьер:** {st.session_state.weekly_barrier}\n\n**Стратегия:** {st.session_state.weekly_strategy}\n\n**Время:** {v}","xp":60 if ins else 0}; go("Результат"); st.rerun()

# -----------------------------
# Result
# -----------------------------
elif page=="Результат":
    r=st.session_state.result
    if not r: go("Миссии"); st.rerun()
    st.subheader("✅ Задание завершено"); st.markdown(r["body"]); st.metric("Награда",f"+{r['xp']} XP"); st.success("Результат сохранён в SQLite.")
    if st.button("Вернуться к миссиям",type="primary"): reset_mission_state(); go("Миссии"); st.rerun()

# -----------------------------
# Shooter
# -----------------------------
elif page=="Шутер":
    shooter_page()

# -----------------------------
# Symptoms
# -----------------------------
elif page=="Симптомы":
    st.subheader("🫁 Симптомы и самонаблюдение")
    st.info("Это дневник изменений относительно вашего обычного состояния. Он не ставит диагноз.")
    rows=checkin_history(14)
    if not rows: st.write("История пока пуста. Сделайте первый check-in через «Миссии».")
    else:
        for r in rows:
            st.markdown(f"<div class='card'><b>{r['checkin_date']}</b><br>Одышка: {r['breathlessness']} · Кашель: {r['cough']} · Мокрота: {r['sputum']} · Энергия: {r['energy']}<br><span class='muted'>{html.escape(r['note'] or '')}</span></div>",unsafe_allow_html=True)
        worse=sum(r['breathlessness']=="Хуже обычного" for r in rows[:3]); energy=sum(r['energy']=="Хуже обычного" for r in rows[:3])
        st.subheader("Комментарий по динамике")
        if worse>=2 or energy>=2: st.warning("В нескольких последних записях отмечалось ухудшение отдельных параметров. Это не диагноз; при необычном ухудшении ориентируйтесь на заранее согласованный план действий.")
        else: st.success("По последним записям нет повторяющегося сигнала ухудшения по выбранным отметкам. Продолжайте самонаблюдение и следование согласованной программе.")

# -----------------------------
# Knowledge base
# -----------------------------
elif page=="Знания о ХОБЛ":
    st.subheader("📚 База знаний")
    topics=[("Что такое ХОБЛ?","Хроническое заболевание лёгких, при котором могут сохраняться дыхательные симптомы и ограничение воздушного потока."),("Лёгочная реабилитация","Комплекс тренировочных и образовательных мероприятий, направленных на улучшение физического состояния, переносимости активности и качества жизни."),("Самоменеджмент","Понимание состояния, наблюдение за симптомами, следование согласованному плану и поддержание активности в рамках рекомендаций."),("Зачем вести дневник?","Чтобы видеть изменения относительно привычного состояния и иметь структурированную информацию для обсуждения со специалистом."),("Зачем gamification?","Чтобы поддерживать регулярность: цель → небольшое действие → обратная связь → прогресс → награда.")]
    for title,text in topics:
        with st.expander(title): st.write(text)

# -----------------------------
# Progress
# -----------------------------
elif page=="Прогресс":
    st.subheader("📈 Прогресс")
    u=get_user(); wd=week_training_count(); st.metric("Реабилитация на этой неделе",f"{wd}/{u['weekly_goal']}"); st.progress(min(wd/max(u['weekly_goal'],1),1.0)); st.write(f"XP: **{u['xp']}** · Уровень: **{u['level']}** · Монеты: **🪙 {u['coins']}** · Серия: **🔥 {u['streak']} дн.**")
    rows=checkin_history(7)
    if rows:
        st.subheader("Последнее самонаблюдение"); r=rows[0]; st.write(f"**{r['checkin_date']}** — одышка: {r['breathlessness']}, кашель: {r['cough']}, мокрота: {r['sputum']}, энергия: {r['energy']}")

# -----------------------------
# Character
# -----------------------------
elif page=="Персонаж":
    st.subheader("👤 Персонаж")
    u = get_user()

    st.markdown(
        f"""
        <div style="max-width:420px;margin:auto;">
            {avatar_svg(
                TOPS[u['equipped_top']][1],
                BOTTOMS[u['equipped_bottom']][1],
                SHOES[u['equipped_shoes']][1]
            )}
        </div>
        """,
        unsafe_allow_html=True
    )
    st.markdown("### Надеть")
    a,b,c=st.columns(3)
    with a:
        top=st.selectbox("Верх",list(TOPS),format_func=lambda k:TOPS[k][0],key="equip_top");
        if owns(top) or TOPS[top][2]==0:
            if st.button("Надеть",key="dress_top"): save_user("equipped_top",top); st.rerun()
    with b:
        bottom=st.selectbox("Низ",list(BOTTOMS),format_func=lambda k:BOTTOMS[k][0],key="equip_bottom");
        if owns(bottom) or BOTTOMS[bottom][2]==0:
            if st.button("Надеть",key="dress_bottom"): save_user("equipped_bottom",bottom); st.rerun()
    with c:
        shoes=st.selectbox("Обувь",list(SHOES),format_func=lambda k:SHOES[k][0],key="equip_shoes");
        if owns(shoes) or SHOES[shoes][2]==0:
            if st.button("Надеть",key="dress_shoes"): save_user("equipped_shoes",shoes); st.rerun()
    st.markdown("### Моё пространство"); room=st.selectbox("Комната",list(ROOMS),format_func=lambda k:ROOMS[k][0],key="room_choose")
    if owns(room) or ROOMS[room][1]==0:
        if st.button("Выбрать комнату",key="choose_room"): save_user("room_theme",room); st.rerun()
    st.markdown(room_svg(ROOMS[u['room_theme']][2],ROOMS[u['room_theme']][3]),unsafe_allow_html=True)

# -----------------------------
# Shop
# -----------------------------
elif page=="Магазин":
    st.subheader("🛍️ Магазин"); st.write(f"Баланс: **🪙 {get_user()['coins']}**")
    tabs=st.tabs(["Одежда","Комнаты","Локации"])
    with tabs[0]:
        groups=[TOPS,BOTTOMS,SHOES]
        for group in groups:
            cols=st.columns(4)
            for i,(item_id,(name,color,price)) in enumerate(group.items()):
                with cols[i%4]: item_tile(item_id,name,color,price,"clothes")
    with tabs[1]:
        cols=st.columns(3)
        for i,(item_id,(name,price,bg,sofa)) in enumerate(ROOMS.items()):
            with cols[i]:
                st.markdown(f"<div class='shopitem'><div style='height:70px;border-radius:12px;background:linear-gradient(135deg,{bg},{sofa})'></div><b>{name}</b><div class='muted'>{'Бесплатно' if price==0 else '🪙 '+str(price)}</div></div>",unsafe_allow_html=True)
                if owns(item_id): st.success("Куплено")
                elif price==0: st.success("Базовое")
                elif st.button("Купить",key="room_buy_"+item_id,disabled=get_user()['coins']<price): buy(item_id,price); st.rerun()
    with tabs[2]:
        cols=st.columns(3)
        for i,(item_id,(name,price)) in enumerate(LOCATIONS.items()):
            with cols[i]:
                st.markdown(f"<div class='shopitem'><div style='height:70px;border-radius:12px;background:linear-gradient(135deg,#d7edf6,#93b89a)'></div><b>{name}</b><div class='muted'>{'Бесплатно' if price==0 else '🪙 '+str(price)}</div></div>",unsafe_allow_html=True)
                if owns(item_id): st.success("Куплено")
                elif price==0: st.success("Базовая")
                elif st.button("Купить",key="loc_buy_"+item_id,disabled=get_user()['coins']<price): buy(item_id,price); st.rerun()

else:
    st.subheader("ℹ️ О приложении")
    st.write("Путь дыхания — учебный proof of concept геймифицированного COPD-oriented Digital Health приложения.")
    st.write("Игровые механики поддерживают регулярность реабилитации и самоменеджмент; приложение не диагностирует заболевание и не заменяет врача.")
    st.write("Технологии: Streamlit + SQLite + встроенная SVG-графика. Все покупки, экипировка и прогресс хранятся локально.")
    st.image(str(APP_DIR / "assets" / "ui_concept_reference.png"), caption="Визуальная референс-композиция концепта", use_container_width=True)
