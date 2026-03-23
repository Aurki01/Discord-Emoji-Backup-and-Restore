import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
import aiohttp
import asyncio
import io
import json
import os
import random
import time
import zipfile
import logging
from datetime import datetime

load_dotenv()  # loads .env when running locally; harmless on Replit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("emoji-bot")

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable is not set.")

intents = discord.Intents.default()
intents.guilds = True
intents.emojis_and_stickers = True

bot = commands.Bot(command_prefix="/", intents=intents)

COMMON_EMOJIS = [
    "😀", "😂", "😍", "🥰", "😎", "🤔", "🙂", "😊",
    "🎉", "🔥", "💯", "👍", "❤️", "✨", "🌟", "🎊",
    "🐶", "🐱", "🦊", "🐼", "🦁", "🐸", "🦄", "🌈",
    "🍕", "🎮", "🎵", "🚀", "💎", "🏆", "⚡", "🌙",
]

RATE_LIMIT_DELAY = 1.5
MAX_RETRIES = 5
CHUNK_SIZE = 5

# ─── PIL image constants ───────────────────────────────────────────────────────
W, H     = 800, 470
PAD      = 28
C_BG     = (30,  31,  34)
C_CARD   = (43,  45,  49)
C_BORDER = (63,  65,  71)
C_BLURP  = (88,  101, 242)   # Discord blurple
C_GREEN  = (87,  242, 135)
C_RED    = (237, 66,  69)
C_YELLOW = (251, 188, 4)
C_WHITE  = (255, 255, 255)
C_GRAY   = (181, 186, 193)
C_DIM    = (120, 124, 134)
C_BAR_BG = (56,  58,  64)
C_PEND   = (64,  68,  75)    # pending/inactive bar
# ──────────────────────────────────────────────────────────────────────────────


def _load_fonts():
    """Return (large, medium, small) PIL fonts."""
    try:
        return (
            ImageFont.load_default(size=24),
            ImageFont.load_default(size=16),
            ImageFont.load_default(size=13),
        )
    except Exception:
        f = ImageFont.load_default()
        return f, f, f


def _draw_bar(draw, x, y, w, h, pct, color):
    """Draw a rounded progress bar with a percentage label to its right."""
    r = h // 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=C_BAR_BG)
    if pct > 0:
        fw = min(w, max(h, int(w * pct)))
        draw.rounded_rectangle([x, y, x + fw, y + h], radius=r, fill=color)


def build_progress_image(
    guild_name: str,
    phase: str,            # "emojis" | "stickers" | "complete"
    e_ok: int, e_total: int, e_fail: int, e_skip: int, e_animated: int,
    s_ok: int, s_total: int, s_fail: int, s_skip: int,
    elapsed: float,
    errors: list,
    zip_size_mb: float = 0.0,
    mode: str = "backup",  # "backup" | "restore"
    hit_limit: bool = False,
    s_hit_limit: bool = False,
) -> io.BytesIO:
    img  = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    f_lg, f_md, f_sm = _load_fonts()

    is_complete = (phase == "complete")

    def t(x, y, txt, font, fill, anchor="lt"):
        draw.text((x, y), str(txt), font=font, fill=fill, anchor=anchor)

    def fmt_elapsed(s):
        s = int(s)
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

    # ── HEADER CARD ───────────────────────────────────────────────────────────
    draw.rounded_rectangle([PAD, PAD, W - PAD, PAD + 74], radius=12, fill=C_CARD)

    if mode == "restore":
        status_text = "Restore Complete!" if is_complete else "Restoring Server..."
    else:
        status_text = "Backup Complete!" if is_complete else "Backing Up Server..."
    status_color = C_GREEN if is_complete else C_YELLOW

    ic_cx, ic_cy, ic_r = PAD + 26, PAD + 28, 8
    draw.ellipse([ic_cx - ic_r, ic_cy - ic_r, ic_cx + ic_r, ic_cy + ic_r], fill=status_color)
    t(ic_cx + ic_r + 10, PAD + 12, status_text, f_lg, status_color)
    t(W - PAD - 16, PAD + 12, guild_name,           f_md, C_GRAY,  anchor="rt")
    t(PAD + 16,     PAD + 46, "Time elapsed",        f_sm, C_DIM)
    t(W - PAD - 16, PAD + 46, fmt_elapsed(elapsed),  f_sm, C_DIM,  anchor="rt")

    # ── HELPER: draw a section card ───────────────────────────────────────────
    def draw_section(top, card_h, title, ok, fail, skip, total, extra, bar_col, tag, tag_col):
        draw.rounded_rectangle([PAD, top, W - PAD, top + card_h], radius=12, fill=C_CARD)

        processed = ok + fail + skip
        pct = processed / total if total > 0 else 0.0

        # Title row: label left, progress count right
        t(PAD + 16,     top + 14, title,                    f_md, C_GRAY)
        t(W - PAD - 16, top + 14, f"{processed} / {total}", f_md, C_WHITE, anchor="rt")

        # Progress bar — 52 px gap on the right for the % label
        bx, by = PAD + 16, top + 46
        bw, bh = W - PAD * 2 - 32 - 52, 20
        _draw_bar(draw, bx, by, bw, bh, pct, bar_col)
        t(bx + bw + 8, by + bh // 2, f"{int(pct * 100)}%", f_sm, C_WHITE, anchor="lm")

        # Stats row: counts on the left, status tag on the right
        sy = by + bh + 12
        if mode == "restore":
            stat_str = f"{ok} uploaded   {fail} failed   {skip} skipped"
        else:
            stat_str = f"{ok} saved   {fail} failed"
        if extra:
            stat_str += f"   {extra}"
        t(bx,           sy, stat_str, f_sm, C_GRAY)
        t(W - PAD - 16, sy, tag,      f_sm, tag_col, anchor="rt")

    # ── EMOJI SECTION ─────────────────────────────────────────────────────────
    s1_top, s1_h = PAD + 74 + 10, 124

    if phase == "emojis":
        e_col, e_tag, e_tag_col = C_BLURP, "In Progress", C_YELLOW
    else:
        e_col, e_tag, e_tag_col = C_GREEN, "Complete",    C_GREEN

    if mode == "backup":
        e_extra = f"( {e_animated} animated )" if is_complete and e_animated > 0 else ""
    else:
        e_extra = "[slot limit]" if hit_limit else ""

    draw_section(s1_top, s1_h, "EMOJIS",
                 e_ok, e_fail, e_skip, e_total, e_extra,
                 e_col, e_tag, e_tag_col)

    # ── STICKER SECTION ───────────────────────────────────────────────────────
    s2_top, s2_h = s1_top + s1_h + 10, 124

    if phase == "stickers":
        s_col, s_tag, s_tag_col = C_BLURP, "In Progress", C_YELLOW
    elif phase == "complete":
        s_col, s_tag, s_tag_col = C_GREEN, "Complete",    C_GREEN
    else:
        s_col, s_tag, s_tag_col = C_PEND,  "Pending",     C_DIM

    if mode == "backup":
        s_extra = f"( {zip_size_mb:.1f} MB total )" if is_complete and zip_size_mb > 0 else ""
    else:
        s_extra = "[slot limit]" if s_hit_limit else ""

    draw_section(s2_top, s2_h, "STICKERS",
                 s_ok, s_fail, s_skip, s_total, s_extra,
                 s_col, s_tag, s_tag_col)

    # ── FOOTER / ERROR CARD ───────────────────────────────────────────────────
    ft_top = s2_top + s2_h + 10
    ft_h   = H - ft_top - PAD
    draw.rounded_rectangle([PAD, ft_top, W - PAD, ft_top + ft_h], radius=12, fill=C_CARD)

    mid_y = ft_top + ft_h // 2
    if errors:
        err_line = f"[!]  {errors[0]}"
        if len(errors) > 1:
            err_line += f"  (+{len(errors) - 1} more)"
        t(PAD + 16, mid_y, err_line, f_sm, C_RED, anchor="lm")
    else:
        t(PAD + 16, mid_y, "No errors encountered", f_sm, C_GREEN, anchor="lm")

    if mode == "backup" and is_complete and zip_size_mb > 0:
        t(W - PAD - 16, mid_y, f"ZIP ready  |  {zip_size_mb:.1f} MB", f_sm, C_DIM, anchor="rm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def make_embed(phase: str, errors: list, invoker: discord.User | discord.Member) -> discord.Embed:
    """Build the embed that wraps the progress image."""
    is_complete = (phase == "complete")
    if errors:
        color = 0xED4245   # red
    elif is_complete:
        color = 0x57F287   # green
    else:
        color = 0x5865F2   # blurple

    embed = discord.Embed(color=color)
    embed.set_image(url="attachment://progress.png")
    embed.set_footer(
        text=f"Requested by {invoker.display_name}",
        icon_url=invoker.display_avatar.url,
    )
    return embed


# ─── Download / upload helpers ────────────────────────────────────────────────

async def download_asset(session: aiohttp.ClientSession, url: str) -> bytes | None:
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", 5))
                    log.warning(f"Rate limited downloading asset. Retrying in {retry_after}s")
                    await asyncio.sleep(retry_after)
                else:
                    log.error(f"Failed to download {url}: HTTP {resp.status}")
                    return None
        except Exception as e:
            log.error(f"Error downloading {url} (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
    return None


async def upload_emoji_with_retry(guild: discord.Guild, name: str, image: bytes) -> discord.Emoji | None:
    for attempt in range(MAX_RETRIES):
        try:
            emoji = await guild.create_custom_emoji(
                name=name, image=image, roles=[],
                reason="Emoji restore via emoji-bot"
            )
            log.info(f"Uploaded emoji: {name}")
            return emoji
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(e.retry_after if hasattr(e, "retry_after") else 5.0)
            elif e.status == 400 and "maximum" in str(e.text).lower():
                raise
            else:
                log.error(f"HTTP error uploading emoji '{name}' (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None
        except Exception as e:
            log.error(f"Unexpected error uploading emoji '{name}': {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                return None
    return None


async def upload_sticker_with_retry(
    guild: discord.Guild, name: str, image: bytes,
    description: str, emoji: str, file_format: discord.StickerFormatType
) -> discord.GuildSticker | None:
    ext_map = {
        discord.StickerFormatType.png: "png",
        discord.StickerFormatType.apng: "png",
        discord.StickerFormatType.lottie: "json",
        discord.StickerFormatType.gif: "gif",
    }
    for attempt in range(MAX_RETRIES):
        try:
            ext  = ext_map.get(file_format, "png")
            file = discord.File(io.BytesIO(image), filename=f"{name}.{ext}")
            sticker = await guild.create_sticker(
                name=name, description=description or name,
                emoji=emoji, file=file,
                reason="Sticker restore via emoji-bot"
            )
            log.info(f"Uploaded sticker: {name}")
            return sticker
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(e.retry_after if hasattr(e, "retry_after") else 5.0)
            elif e.status == 400 and "maximum" in str(e.text).lower():
                raise
            else:
                log.error(f"HTTP error uploading sticker '{name}' (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None
        except Exception as e:
            log.error(f"Unexpected error uploading sticker '{name}': {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                return None
    return None


# ─── Bot events ───────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    log.info(f"Serving {len(bot.guilds)} guild(s)")
    log.info("Slash commands synced globally")


# ─── /backup ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="backup", description="Backup all emojis and stickers in this server to a ZIP file")
@app_commands.guild_only()
@app_commands.default_permissions(manage_emojis=True)
async def backup(interaction: discord.Interaction):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.manage_emojis:
        await interaction.response.send_message(
            "You need the **Manage Emojis and Stickers** permission to use this command.", ephemeral=True)
        return
    if not guild.me.guild_permissions.manage_emojis:
        await interaction.response.send_message(
            "I need the **Manage Emojis and Stickers** permission to back up emojis and stickers.", ephemeral=True)
        return

    await interaction.response.defer()

    # ── state ────────────────────────────────────────────────────────────────
    emoji_total   = len(guild.emojis)
    sticker_total = len(guild.stickers)
    total_assets  = emoji_total + sticker_total

    # Update every 10 % for servers with ≥20 assets, else every 5 s
    use_time_based = total_assets < 20

    e_done = e_failed = e_animated = 0
    s_done = s_failed = 0
    errors: list[str] = []
    start_t = time.monotonic()

    def _snap(phase, zip_mb=0.0):
        buf = build_progress_image(
            guild_name=guild.name, phase=phase,
            e_ok=e_done, e_total=emoji_total, e_fail=e_failed, e_skip=0, e_animated=e_animated,
            s_ok=s_done, s_total=sticker_total, s_fail=s_failed, s_skip=0,
            elapsed=time.monotonic() - start_t,
            errors=errors, zip_size_mb=zip_mb,
            mode="backup",
        )
        embed = make_embed(phase, errors, interaction.user)
        return embed, discord.File(buf, filename="progress.png")

    # ── initial message ───────────────────────────────────────────────────────
    embed, file = _snap("emojis")
    status_msg = await interaction.followup.send(embed=embed, file=file, wait=True)

    async def push_update(phase, zip_mb=0.0):
        embed, file = _snap(phase, zip_mb)
        try:
            await interaction.followup.edit_message(
                status_msg.id,
                embed=embed,
                attachments=[file],
            )
        except Exception as exc:
            log.warning(f"Failed to update progress message: {exc}")

    zip_buffer = io.BytesIO()
    manifest = {
        "guild_id": guild.id,
        "guild_name": guild.name,
        "backup_date": datetime.utcnow().isoformat() + "Z",
        "emojis": [],
        "stickers": [],
    }

    async with aiohttp.ClientSession() as session:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

            # ── EMOJI PHASE ──────────────────────────────────────────────────
            MIN_GAP        = 4.0   # seconds — minimum between any two updates
            TIME_INTERVAL  = 8.0   # seconds — interval used for small servers

            last_update_t      = time.monotonic() - MIN_GAP   # allow first update immediately
            last_update_bucket = -1

            for i, emoji in enumerate(guild.emojis):
                ext  = "gif" if emoji.animated else "png"
                data = await download_asset(session, str(emoji.url))
                if data:
                    fname = f"emojis/{emoji.name}.{ext}"
                    zf.writestr(fname, data)
                    manifest["emojis"].append({
                        "name": emoji.name, "id": emoji.id,
                        "animated": emoji.animated, "filename": fname,
                        "managed": emoji.managed,
                        "require_colons": emoji.require_colons,
                    })
                    e_done += 1
                    if emoji.animated:
                        e_animated += 1
                else:
                    e_failed += 1
                    errors.append(f"Failed to download emoji: {emoji.name}")

                await asyncio.sleep(0.05)

                now    = time.monotonic()
                pct    = e_done / emoji_total if emoji_total > 0 else 1.0
                bucket = int(pct * 10)
                gap_ok = now - last_update_t >= MIN_GAP
                if use_time_based:
                    should = now - last_update_t >= TIME_INTERVAL
                else:
                    should = bucket > last_update_bucket and gap_ok
                if should:
                    last_update_t      = now
                    last_update_bucket = bucket
                    await push_update("emojis")

            # guarantee a final emoji update before switching phases
            await push_update("emojis")

            # ── STICKER PHASE ────────────────────────────────────────────────
            last_update_t      = time.monotonic() - MIN_GAP
            last_update_bucket = -1

            for i, sticker in enumerate(guild.stickers):
                fmt     = sticker.format
                ext_map = {
                    discord.StickerFormatType.png: "png",
                    discord.StickerFormatType.apng: "png",
                    discord.StickerFormatType.lottie: "json",
                    discord.StickerFormatType.gif: "gif",
                }
                ext  = ext_map.get(fmt, "png")
                data = await download_asset(session, str(sticker.url))
                rel  = sticker.emoji if hasattr(sticker, "emoji") and sticker.emoji else None
                if data:
                    fname = f"stickers/{sticker.name}.{ext}"
                    zf.writestr(fname, data)
                    manifest["stickers"].append({
                        "name": sticker.name, "id": sticker.id,
                        "description": sticker.description or "",
                        "emoji": rel, "format": fmt.name, "filename": fname,
                    })
                    s_done += 1
                else:
                    s_failed += 1
                    errors.append(f"Failed to download sticker: {sticker.name}")

                await asyncio.sleep(0.05)

                now    = time.monotonic()
                pct    = s_done / sticker_total if sticker_total > 0 else 1.0
                bucket = int(pct * 10)
                gap_ok = now - last_update_t >= MIN_GAP
                if use_time_based:
                    should = now - last_update_t >= TIME_INTERVAL
                else:
                    should = bucket > last_update_bucket and gap_ok
                if should:
                    last_update_t      = now
                    last_update_bucket = bucket
                    await push_update("stickers")

            await push_update("stickers")

            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    zip_buffer.seek(0)
    zip_size_mb  = zip_buffer.getbuffer().nbytes / (1024 * 1024)
    zip_filename = f"{guild.name}_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"

    # ── final "complete" image ────────────────────────────────────────────────
    await push_update("complete", zip_mb=zip_size_mb)

    # ── send the ZIP to the channel ───────────────────────────────────────────
    zip_buffer.seek(0)
    await interaction.channel.send(
        content=(
            f"📦 **Emoji & Sticker Backup** — {guild.name}\n"
            f"• {len(manifest['emojis'])} emoji(s)  "
            f"• {len(manifest['stickers'])} sticker(s)  "
            f"• {zip_size_mb:.1f} MB\n"
            f"Use `/restore` and attach this file to restore everything."
        ),
        file=discord.File(zip_buffer, filename=zip_filename),
    )


# ─── /restore ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="restore", description="Restore emojis and stickers from a backup ZIP file")
@app_commands.guild_only()
@app_commands.default_permissions(manage_emojis=True)
@app_commands.describe(backup_file="The backup ZIP file created by /backup")
async def restore(interaction: discord.Interaction, backup_file: discord.Attachment):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.manage_emojis:
        await interaction.response.send_message(
            "You need the **Manage Emojis and Stickers** permission to use this command.", ephemeral=True)
        return
    if not guild.me.guild_permissions.manage_emojis:
        await interaction.response.send_message(
            "I need the **Manage Emojis and Stickers** permission to restore emojis and stickers.", ephemeral=True)
        return
    if not backup_file.filename.endswith(".zip"):
        await interaction.response.send_message(
            "The attached file must be a `.zip` backup file created by `/backup`.", ephemeral=True)
        return

    await interaction.response.defer()
    status_msg = await interaction.followup.send("Downloading backup file...", wait=True)

    async with aiohttp.ClientSession() as session:
        zip_data = await download_asset(session, backup_file.url)

    if not zip_data:
        await interaction.followup.edit_message(
            status_msg.id, content="Failed to download the backup file. Please try again.")
        return

    # ── restore state ─────────────────────────────────────────────────────────
    emoji_ok = emoji_fail = emoji_skip = 0
    sticker_ok = sticker_fail = sticker_skip = 0
    emoji_total = sticker_total = 0
    hit_limit = False
    s_hit_limit = False
    errors: list[str] = []
    start_t = time.monotonic()

    def _snap(phase):
        buf = build_progress_image(
            guild_name=guild.name, phase=phase,
            e_ok=emoji_ok, e_total=emoji_total, e_fail=emoji_fail,
            e_skip=emoji_skip, e_animated=0,
            s_ok=sticker_ok, s_total=sticker_total, s_fail=sticker_fail,
            s_skip=sticker_skip,
            elapsed=time.monotonic() - start_t,
            errors=errors, zip_size_mb=0.0,
            mode="restore", hit_limit=hit_limit, s_hit_limit=s_hit_limit,
        )
        embed = make_embed(phase, errors, interaction.user)
        return embed, discord.File(buf, filename="progress.png")

    async def push_update(phase):
        embed, file = _snap(phase)
        try:
            await interaction.followup.edit_message(
                status_msg.id, embed=embed, attachments=[file], content=None)
        except Exception as exc:
            log.warning(f"Failed to update restore progress: {exc}")

    try:
        zip_buffer = io.BytesIO(zip_data)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            if "manifest.json" not in zf.namelist():
                await interaction.followup.edit_message(
                    status_msg.id, content="Invalid backup file: missing `manifest.json`.")
                return

            manifest      = json.loads(zf.read("manifest.json"))
            emojis_data   = manifest.get("emojis", [])
            stickers_data = manifest.get("stickers", [])
            emoji_total   = len(emojis_data)
            sticker_total = len(stickers_data)

            # send initial image (replaces "Downloading..." text)
            await push_update("emojis")

            MIN_GAP       = 4.0
            TIME_INTERVAL = 8.0
            use_time_based = (emoji_total + sticker_total) < 20

            # ── EMOJI PHASE ──────────────────────────────────────────────────
            last_update_t      = time.monotonic() - MIN_GAP
            last_update_bucket = -1
            existing = {e.name for e in guild.emojis}

            e_chunks = [emojis_data[i:i + CHUNK_SIZE] for i in range(0, len(emojis_data), CHUNK_SIZE)]
            for chunk_idx, chunk in enumerate(e_chunks):
                if hit_limit:
                    emoji_skip += len(chunk)
                    continue
                for entry_idx, entry in enumerate(chunk):
                    name     = entry["name"]
                    filename = entry.get("filename", f"emojis/{name}.png")
                    if name in existing:
                        emoji_skip += 1
                        continue
                    if filename not in zf.namelist():
                        emoji_fail += 1
                        errors.append(f"Missing file: {filename}")
                        continue
                    try:
                        r = await upload_emoji_with_retry(guild, name, zf.read(filename))
                        if r:
                            emoji_ok += 1
                        else:
                            emoji_fail += 1
                    except discord.HTTPException as e:
                        if "maximum" in str(e.text).lower():
                            hit_limit = True
                            emoji_skip += len(chunk) - entry_idx
                            break
                        emoji_fail += 1
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                now    = time.monotonic()
                pct    = (emoji_ok + emoji_fail + emoji_skip) / emoji_total if emoji_total else 1.0
                bucket = int(pct * 10)
                gap_ok = now - last_update_t >= MIN_GAP
                should = (now - last_update_t >= TIME_INTERVAL) if use_time_based \
                         else (bucket > last_update_bucket and gap_ok)
                if should:
                    last_update_t      = now
                    last_update_bucket = bucket
                    await push_update("emojis")

            await push_update("emojis")

            # ── STICKER PHASE ─────────────────────────────────────────────────
            last_update_t      = time.monotonic() - MIN_GAP
            last_update_bucket = -1
            s_existing = {s.name for s in guild.stickers}

            await push_update("stickers")

            s_chunks = [stickers_data[i:i + CHUNK_SIZE] for i in range(0, len(stickers_data), CHUNK_SIZE)]
            for chunk_idx, chunk in enumerate(s_chunks):
                if s_hit_limit:
                    sticker_skip += len(chunk)
                    continue
                for entry_idx, entry in enumerate(chunk):
                    name         = entry["name"]
                    description  = entry.get("description", name)
                    filename     = entry.get("filename", f"stickers/{name}.png")
                    format_name  = entry.get("format", "png")
                    stored_emoji = entry.get("emoji")
                    rel_emoji    = stored_emoji if stored_emoji else random.choice(COMMON_EMOJIS)

                    if name in s_existing:
                        sticker_skip += 1
                        continue
                    if filename not in zf.namelist():
                        sticker_fail += 1
                        errors.append(f"Missing file: {filename}")
                        continue

                    fmt_map = {
                        "png": discord.StickerFormatType.png,
                        "apng": discord.StickerFormatType.apng,
                        "lottie": discord.StickerFormatType.lottie,
                        "gif": discord.StickerFormatType.gif,
                    }
                    fmt = fmt_map.get(format_name.lower(), discord.StickerFormatType.png)

                    try:
                        r = await upload_sticker_with_retry(
                            guild, name, zf.read(filename), description, rel_emoji, fmt)
                        if r:
                            sticker_ok += 1
                        else:
                            sticker_fail += 1
                    except discord.HTTPException as e:
                        if "maximum" in str(e.text).lower():
                            s_hit_limit = True
                            sticker_skip += len(chunk) - entry_idx
                            break
                        sticker_fail += 1
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                now    = time.monotonic()
                pct    = (sticker_ok + sticker_fail + sticker_skip) / sticker_total if sticker_total else 1.0
                bucket = int(pct * 10)
                gap_ok = now - last_update_t >= MIN_GAP
                should = (now - last_update_t >= TIME_INTERVAL) if use_time_based \
                         else (bucket > last_update_bucket and gap_ok)
                if should:
                    last_update_t      = now
                    last_update_bucket = bucket
                    await push_update("stickers")

            await push_update("complete")

    except zipfile.BadZipFile:
        await interaction.followup.edit_message(
            status_msg.id, content="The file appears to be corrupted or is not a valid ZIP.")
        return
    except Exception as e:
        log.exception(f"Unexpected error during restore: {e}")
        await interaction.followup.edit_message(
            status_msg.id, content=f"An unexpected error occurred: {e}")
        return

    limit_note = "  |  slot limit reached for some items" if hit_limit or s_hit_limit else ""
    await interaction.channel.send(
        content=(
            f"**Restore Complete** -- {guild.name}{limit_note}\n\n"
            f"Emojis:   {emoji_ok} uploaded   {emoji_fail} failed   {emoji_skip} skipped\n"
            f"Stickers: {sticker_ok} uploaded   {sticker_fail} failed   {sticker_skip} skipped"
        )
    )


# ─── Global error handler ─────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You need the **Manage Emojis and Stickers** permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I need the **Manage Emojis and Stickers** permission to do that."
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = "This command can only be used inside a server."
    else:
        log.exception(f"Unhandled app command error: {error}")
        msg = f"An unexpected error occurred: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


bot.run(DISCORD_TOKEN, log_handler=None)
