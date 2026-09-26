"""luauwhitelist - Discord bot (Luarmor-style whitelist panels)."""
from __future__ import annotations

import io
import json
import logging
import os
import secrets

import discord
from discord import app_commands
from discord.ext import commands

import db
from config import DATA_DIR, DISCORD_TOKEN, PUBLIC_URL, TRIAL_DAYS

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("luauwhitelist")

LOADER_DIR = os.path.join(DATA_DIR, "loaders")
os.makedirs(LOADER_DIR, exist_ok=True)

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
NAMED_COLORS = {
    "black": 0x000000, "red": 0xED4245, "green": 0x57F287, "blue": 0x5865F2,
    "yellow": 0xFEE75C, "purple": 0x9B59B6, "white": 0xFFFFFF, "pink": 0xEB459E,
}


def parse_color(value: str | None) -> int:
    """Чёрный по умолчанию - та самая чёрная полоска слева у эмбеда."""
    if not value:
        return 0x000000
    v = value.strip().lstrip("#").lower()
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    try:
        return int(v, 16)
    except ValueError:
        return 0x000000


async def store_attachment(att: discord.Attachment) -> str:
    """Кладём файл скрипта/лоадера в папку раздачи и возвращаем /raw/ ссылку."""
    data = await att.read()
    ext = os.path.splitext(att.filename)[1] or ".lua"
    name = secrets.token_hex(8) + (ext if ext.startswith(".") else "." + ext)
    with open(os.path.join(LOADER_DIR, name), "wb") as f:
        f.write(data)
    db.add_loader(att.filename, filename=name)
    return f"{PUBLIC_URL}/raw/{name}"


def loadstring_for(url: str) -> str:
    return f'loadstring(game:HttpGet("{url}"))()'


def build_panel_embed(panel) -> discord.Embed:
    e = discord.Embed(
        title=panel["title"] or "luauwhitelist",
        description=panel["description"] or "",
        color=panel["color"] or 0,
    )
    if panel["author"]:
        e.set_author(name=panel["author"])
    if panel["thumbnail"]:
        e.set_thumbnail(url=panel["thumbnail"])
    if panel["image"]:
        e.set_image(url=panel["image"])
    e.add_field(
        name="How to use",
        value="`1.` Press **Redeem Key** and enter your key\n"
              "`2.` Press **Get Script** / **Get Loader** to get your script\n"
              "`3.` Use **Stats** for key info and **Reset HWID** if needed\n"
              "`4.` Press **Get Role** after redeeming your key",
        inline=False,
    )
    e.set_footer(text=panel["footer"] or "luauwhitelist",
                 icon_url=panel["thumbnail"] or None)
    return e


def not_whitelisted_embed() -> discord.Embed:
    return discord.Embed(
        title="NOT WHITELISTED",
        description="buy and redeem key or get trial 7 days key",
        color=0xED4245,
    )


def allowed_for_panel(interaction: discord.Interaction, panel) -> bool:
    """Если у панели заданы роли - кнопки доступны только им (+ админам сервера)."""
    roles = db.panel_roles(panel)
    if not roles:
        return True
    if interaction.user.guild_permissions.administrator:
        return True
    member_roles = {r.id for r in getattr(interaction.user, "roles", [])}
    return bool(member_roles & set(roles))


# --------------------------------------------------------------------------- #
#  Modal: redeem key
# --------------------------------------------------------------------------- #
class RedeemModal(discord.ui.Modal, title="Redeem Key"):
    key_input = discord.ui.TextInput(
        label="Your key",
        placeholder="LUUAU-XXXX-XXXX-XXXX",
        min_length=4,
        max_length=64,
        style=discord.TextStyle.short,
    )

    def __init__(self, panel_id: int, kind: str):
        super().__init__()
        self.panel_id = panel_id
        self.kind = kind

    async def on_submit(self, interaction: discord.Interaction) -> None:
        status, row = db.redeem(str(self.key_input.value), interaction.user.id, self.kind)

        if status == "ok":
            e = discord.Embed(title="KEY REDEEMED", color=0x57F287)
            e.add_field(name="Key", value=f"`{row['key']}`", inline=False)
            e.add_field(name="Type", value=self.kind.upper(), inline=True)
            e.add_field(name="HWID", value=f"`{row['hwid']}`", inline=True)
            if row["expires_at"]:
                e.add_field(name="Expires", value=row["expires_at"].replace("T", " "), inline=True)
            e.add_field(
                name="Next step",
                value="Press **Get Role** to receive your role, then "
                      "**Get Script** / **Get Loader**.",
                inline=False,
            )
            e.set_footer(text="luauwhitelist")
            await interaction.response.send_message(embed=e, ephemeral=True)
            return

        reason = {
            "not_found": "This key does not exist.",
            "wrong_kind": f"This key is not valid for a {self.kind} panel.",
            "banned": "This key is banned. Contact an administrator.",
            "already_used": "This key is already used by another account.",
            "expired": "This key is expired.",
        }.get(status, "Unknown error.")
        e = discord.Embed(title="REDEEM FAILED", description=reason, color=0xED4245)
        e.set_footer(text="luauwhitelist")
        await interaction.response.send_message(embed=e, ephemeral=True)


# --------------------------------------------------------------------------- #
#  Persistent panel view
# --------------------------------------------------------------------------- #
class PanelView(discord.ui.View):
    """Redeem Key / Get Script (Get Loader) / Stats / Reset HWID / Get Role."""

    def __init__(self, panel_id: int, kind: str):
        super().__init__(timeout=None)
        self.panel_id = panel_id
        self.kind = kind

        get_label = "Get Loader" if kind == "external" else "Get Script"

        redeem = discord.ui.Button(label="Redeem Key", style=discord.ButtonStyle.green,
                                   emoji="\U0001F511", custom_id=f"lw:redeem:{panel_id}")
        get = discord.ui.Button(label=get_label, style=discord.ButtonStyle.secondary,
                                emoji="\U0001F4DC", custom_id=f"lw:get:{panel_id}")
        stats = discord.ui.Button(label="Stats", style=discord.ButtonStyle.blurple,
                                  emoji="\U0001F4CA", custom_id=f"lw:stats:{panel_id}")
        reset = discord.ui.Button(label="Reset HWID", style=discord.ButtonStyle.red,
                                  emoji="\u267B\uFE0F", custom_id=f"lw:reset:{panel_id}")
        role = discord.ui.Button(label="Get Role", style=discord.ButtonStyle.green,
                                 emoji="\u2705", custom_id=f"lw:role:{panel_id}")

        redeem.callback = self.on_redeem
        get.callback = self.on_get
        stats.callback = self.on_stats
        reset.callback = self.on_reset
        role.callback = self.on_role

        for b in (redeem, get, stats, reset, role):
            self.add_item(b)

    # ---------------- utils ---------------- #
    async def _panel(self, interaction: discord.Interaction):
        panel = db.get_panel(self.panel_id)
        if panel is None:
            await interaction.response.send_message(
                embed=discord.Embed(title="PANEL NOT FOUND",
                                    description="This panel was deleted from the database.",
                                    color=0xED4245),
                ephemeral=True,
            )
            return None
        if not allowed_for_panel(interaction, panel):
            await interaction.response.send_message(
                embed=discord.Embed(title="NO ACCESS",
                                    description="You do not have the required role "
                                                "for this panel.",
                                    color=0xED4245),
                ephemeral=True,
            )
            return None
        return panel

    async def _require_whitelist(self, interaction: discord.Interaction, panel):
        row = db.active_key(interaction.user.id, self.kind)
        if row is None:
            await interaction.response.send_message(embed=not_whitelisted_embed(), ephemeral=True)
            return None
        return row

    # ---------------- handlers ---------------- #
    async def on_redeem(self, interaction: discord.Interaction):
        # redeem доступен всем ролям
        await interaction.response.send_modal(RedeemModal(self.panel_id, self.kind))

    async def on_get(self, interaction: discord.Interaction):
        panel = await self._panel(interaction)
        if not panel:
            return
        row = await self._require_whitelist(interaction, panel)
        if not row:
            return

        url = panel["content_url"]
        db.bump_executions(row["key"])
        title = "LOADER" if self.kind == "external" else "SCRIPT"
        e = discord.Embed(title=f"YOUR {title}", color=0x57F287)
        if url:
            e.add_field(name="Loadstring",
                        value=f"```lua\n{loadstring_for(url)}\n```", inline=False)
        else:
            e.add_field(name="Script", value="No file was attached to this panel.", inline=False)
        e.add_field(name="Key", value=f"`{row['key']}`", inline=True)
        e.add_field(name="HWID", value=f"`{row['hwid']}`", inline=True)
        e.set_footer(text="luauwhitelist")
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def on_stats(self, interaction: discord.Interaction):
        panel = await self._panel(interaction)
        if not panel:
            return
        row = await self._require_whitelist(interaction, panel)
        if not row:
            return

        e = discord.Embed(title="YOUR STATS", color=0x5865F2)
        e.add_field(name="Key", value=f"`{row['key']}`", inline=False)
        e.add_field(name="Type", value=row["kind"].upper(), inline=True)
        e.add_field(name="HWID", value=f"`{row['hwid']}`", inline=True)
        e.add_field(name="Executions", value=str(row["executions"] or 0), inline=True)
        e.add_field(name="HWID resets", value=str(row["resets"] or 0), inline=True)
        e.add_field(name="Redeemed", value=(row["used_at"] or "-").replace("T", " "), inline=True)
        e.add_field(name="Expires", value=(row["expires_at"] or "never").replace("T", " "),
                    inline=True)
        e.set_footer(text="luauwhitelist")
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def on_reset(self, interaction: discord.Interaction):
        panel = await self._panel(interaction)
        if not panel:
            return
        row = await self._require_whitelist(interaction, panel)
        if not row:
            return
        new_hwid = db.reset_hwid(row["key"])
        e = discord.Embed(title="HWID RESET", description=f"New HWID: `{new_hwid}`", color=0xFEE75C)
        e.set_footer(text="luauwhitelist")
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def on_role(self, interaction: discord.Interaction):
        panel = await self._panel(interaction)
        if not panel:
            return
        row = await self._require_whitelist(interaction, panel)
        if not row:
            return

        role_ids = db.panel_roles(panel)
        if not role_ids:
            await interaction.response.send_message(
                embed=discord.Embed(title="NO ROLE CONFIGURED",
                                    description="This panel has no role configured.",
                                    color=0xFEE75C),
                ephemeral=True,
            )
            return

        added, failed = [], []
        for rid in role_ids:
            role = interaction.guild.get_role(rid)
            if role is None:
                failed.append(f"id {rid}")
                continue
            try:
                await interaction.user.add_roles(role, reason="luauwhitelist get role")
                added.append(role.mention)
            except discord.Forbidden:
                failed.append(role.mention)

        e = discord.Embed(title="ROLE", color=0x57F287)
        if added:
            e.add_field(name="Added", value=", ".join(added), inline=False)
        if failed:
            e.add_field(name="Failed", value=", ".join(failed) +
                        "\n(check that the bot role is above these roles)", inline=False)
        if not added and not failed:
            e.description = "Nothing to do."
        e.set_footer(text="luauwhitelist")
        await interaction.response.send_message(embed=e, ephemeral=True)


# --------------------------------------------------------------------------- #
#  Команды: создание панелей
# --------------------------------------------------------------------------- #
async def create_panel(interaction: discord.Interaction, kind: str, title: str, author: str,
                       description: str, thumbnail: str, image: str, footer: str, color: str,
                       file, script_url: str, role1, role2, role3, channel):
    await interaction.response.defer(ephemeral=True, thinking=True)

    content_url = (script_url or "").strip()
    if file is not None:
        content_url = await store_attachment(file)

    role_ids = [r.id for r in (role1, role2, role3) if r is not None]
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.followup.send("Channel must be a text channel.", ephemeral=True)
        return

    panel_id = db.add_panel(
        guild_id=interaction.guild.id,
        channel_id=target.id,
        message_id=0,
        kind=kind,
        title=title or "luauwhitelist",
        author=author or "",
        description=description or "",
        thumbnail=thumbnail or "",
        image=image or "",
        footer=footer or "",
        color=parse_color(color),
        content_url=content_url,
        role_ids=json.dumps(role_ids),
    )
    panel = db.get_panel(panel_id)
    msg = await target.send(embed=build_panel_embed(panel), view=PanelView(panel_id, kind))
    db.set_panel_message(panel_id, msg.id)

    e = discord.Embed(title="PANEL CREATED", color=0x57F287)
    e.add_field(name="ID", value=f"`#{panel_id}`", inline=True)
    e.add_field(name="Type", value=kind.upper(), inline=True)
    e.add_field(name="Channel", value=target.mention, inline=True)
    e.add_field(name="Roles", value=", ".join(f"<@&{r}>" for r in role_ids) or "everyone",
                inline=False)
    if content_url:
        e.add_field(name="Script URL", value=content_url, inline=False)
    else:
        e.add_field(name="Warning", value="No file/url attached - add one on the website.",
                    inline=False)
    e.set_footer(text="luauwhitelist")
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="createscriptpanel", description="Create a script whitelist panel")
@app_commands.describe(
    title="Panel title", author="Small text above the title", description="Panel description",
    thumbnail="Thumbnail image URL", image="Big image URL (banner)", footer="Footer text",
    color="Embed color (black / #000000 / red / blue ...)",
    file="The script .lua file (recommended)",
    script_url="Or a direct raw URL to the script",
    role1="Role required to use the buttons", role2="Another role", role3="Another role",
    channel="Where to send the panel",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def createscriptpanel(interaction: discord.Interaction,
                            title: str = "luauwhitelist", author: str = None,
                            description: str = None, thumbnail: str = None,
                            image: str = None, footer: str = None, color: str = None,
                            file: discord.Attachment = None, script_url: str = None,
                            role1: discord.Role = None, role2: discord.Role = None,
                            role3: discord.Role = None, channel: discord.TextChannel = None):
    await create_panel(interaction, "script", title, author, description, thumbnail, image,
                       footer, color, file, script_url, role1, role2, role3, channel)


@bot.tree.command(name="createexternalpanel", description="Create an external whitelist panel")
@app_commands.describe(
    title="Panel title", author="Small text above the title", description="Panel description",
    thumbnail="Thumbnail image URL", image="Big image URL (banner)", footer="Footer text",
    color="Embed color (black / #000000 / red / blue ...)",
    file="The external .lua file (recommended)",
    script_url="Or a direct raw URL to the external",
    role1="Role required to use the buttons", role2="Another role", role3="Another role",
    channel="Where to send the panel",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def createexternalpanel(interaction: discord.Interaction,
                              title: str = "luauwhitelist", author: str = None,
                              description: str = None, thumbnail: str = None,
                              image: str = None, footer: str = None, color: str = None,
                              file: discord.Attachment = None, script_url: str = None,
                              role1: discord.Role = None, role2: discord.Role = None,
                              role3: discord.Role = None, channel: discord.TextChannel = None):
    await create_panel(interaction, "external", title, author, description, thumbnail, image,
                       footer, color, file, script_url, role1, role2, role3, channel)


# --------------------------------------------------------------------------- #
#  Команды: удаление панелей
# --------------------------------------------------------------------------- #
async def delete_panel_by_id(interaction: discord.Interaction, panel_id: int) -> None:
    panel = db.get_panel(panel_id)
    if panel is None:
        await interaction.response.send_message("Panel not found.", ephemeral=True)
        return

    deleted_msg = False
    try:
        channel = interaction.guild.get_channel(panel["channel_id"]) or \
            await interaction.guild.fetch_channel(panel["channel_id"])
        msg = await channel.fetch_message(panel["message_id"])
        await msg.delete()
        deleted_msg = True
    except Exception as ex:  # noqa: BLE001
        log.warning("could not delete the panel message: %s", ex)

    db.delete_panel(panel_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="PANEL DELETED",
            description=f"Panel `#{panel_id}` removed from the database. "
                        f"Message deleted: {deleted_msg}.",
            color=0x57F287,
        ),
        ephemeral=True,
    )


class PanelDeleteSelect(discord.ui.Select):
    def __init__(self, panels):
        options = [
            discord.SelectOption(label=f"#{p['id']} - {p['title']}"[:100],
                                 description=f"channel {p['channel_id']}",
                                 value=str(p["id"]))
            for p in panels[:25]
        ]
        super().__init__(placeholder="Choose a panel to delete", options=options)

    async def callback(self, interaction: discord.Interaction):
        await delete_panel_by_id(interaction, int(self.values[0]))


class PanelDeleteView(discord.ui.View):
    def __init__(self, panels, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.add_item(PanelDeleteSelect(panels))


async def delete_panel_cmd(interaction: discord.Interaction, kind: str, panel_id: int | None):
    if panel_id is not None:
        await delete_panel_by_id(interaction, panel_id)
        return
    panels = db.list_panels(interaction.guild.id, kind)
    if not panels:
        await interaction.response.send_message(f"No {kind} panels on this server.", ephemeral=True)
        return
    await interaction.response.send_message(f"Select a {kind} panel to delete:",
                                            view=PanelDeleteView(panels), ephemeral=True)


@bot.tree.command(name="deletescriptpanel", description="Delete a script panel")
@app_commands.describe(panel_id="Panel id (optional - otherwise you get a list)")
@app_commands.checks.has_permissions(manage_guild=True)
async def deletescriptpanel(interaction: discord.Interaction, panel_id: int = None):
    await delete_panel_cmd(interaction, "script", panel_id)


@bot.tree.command(name="deleteexternalpanel", description="Delete an external panel")
@app_commands.describe(panel_id="Panel id (optional - otherwise you get a list)")
@app_commands.checks.has_permissions(manage_guild=True)
async def deleteexternalpanel(interaction: discord.Interaction, panel_id: int = None):
    await delete_panel_cmd(interaction, "external", panel_id)


@bot.tree.command(name="panelinfo", description="List all panels of this server")
async def panelinfo(interaction: discord.Interaction):
    panels = db.list_panels(interaction.guild.id)
    if not panels:
        await interaction.response.send_message("No panels here.", ephemeral=True)
        return
    e = discord.Embed(title="PANELS", color=0x5865F2)
    for p in panels[:20]:
        e.add_field(name=f"#{p['id']} [{p['kind']}] {p['title']}",
                    value=f"<#{p['channel_id']}> - message {p['message_id']}", inline=False)
    e.set_footer(text="luauwhitelist")
    await interaction.response.send_message(embed=e, ephemeral=True)


# --------------------------------------------------------------------------- #
#  Команды: ключи
# --------------------------------------------------------------------------- #
async def generate_keys(interaction: discord.Interaction, kind: str, count: int):
    await interaction.response.defer(ephemeral=True, thinking=True)
    keys = db.create_keys(kind, count)
    file = discord.File(io.BytesIO("\n".join(keys).encode()),
                        filename=f"{kind}_keys_{len(keys)}.txt")
    e = discord.Embed(title="KEYS GENERATED", color=0x57F287)
    e.add_field(name="Type", value=kind.upper(), inline=True)
    e.add_field(name="Amount", value=str(len(keys)), inline=True)
    e.add_field(name="Preview", value="\n".join(f"`{k}`" for k in keys[:5]) or "-", inline=False)
    e.set_footer(text="luauwhitelist")
    await interaction.followup.send(embed=e, file=file, ephemeral=True)


@bot.tree.command(name="generate100scriptkey", description="Generate script keys (default 100)")
@app_commands.describe(count="How many keys to generate (1-500)")
@app_commands.checks.has_permissions(administrator=True)
async def generate100scriptkey(interaction: discord.Interaction, count: int = 100):
    await generate_keys(interaction, "script", max(1, min(count, 500)))


@bot.tree.command(name="generate100extkey", description="Generate external keys (default 100)")
@app_commands.describe(count="How many keys to generate (1-500)")
@app_commands.checks.has_permissions(administrator=True)
async def generate100extkey(interaction: discord.Interaction, count: int = 100):
    await generate_keys(interaction, "external", max(1, min(count, 500)))


@bot.tree.command(name="generatetrialkey", description="Generate trial keys (7 days)")
@app_commands.describe(kind="What the key is for", count="How many keys")
@app_commands.choices(kind=[
    app_commands.Choice(name="script", value="script"),
    app_commands.Choice(name="external", value="external"),
])
@app_commands.checks.has_permissions(administrator=True)
async def generatetrialkey(interaction: discord.Interaction, kind: str, count: int = 1):
    await interaction.response.defer(ephemeral=True, thinking=True)
    keys = db.create_keys(kind, max(1, min(count, 100)), days=TRIAL_DAYS, note="trial")
    e = discord.Embed(title="TRIAL KEYS", description="\n".join(f"`{k}`" for k in keys[:25]),
                      color=0xFEE75C)
    e.add_field(name="Expires in", value=f"{TRIAL_DAYS} days", inline=True)
    e.set_footer(text="luauwhitelist")
    await interaction.followup.send(embed=e, ephemeral=True)


# --------------------------------------------------------------------------- #
#  errors / ready
# --------------------------------------------------------------------------- #
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True)
        return
    log.exception("command error")
    msg = "Something went wrong. Check the bot logs."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@bot.event
async def on_ready():
    for p in db.list_panels():
        bot.add_view(PanelView(p["id"], p["kind"]))
    try:
        await bot.tree.sync()
    except Exception:  # noqa: BLE001
        log.exception("command sync failed")
    log.info("logged in as %s (%s), panels: %s", bot.user, bot.user.id, len(db.list_panels()))


def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set")
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
