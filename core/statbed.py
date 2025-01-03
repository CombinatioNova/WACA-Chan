import disnake
from typing import Optional
from datetime import datetime

async def create_alert_embed(title: str, description: str = "", footer: Optional[str] = None) -> disnake.Embed:
    """
    Creates an alert embed message with an orange color.
    
    Args:
        title (str): The title of the embed.
        description (str, optional): The main content of the embed. Defaults to "".
        footer (Optional[str]): The footer text of the embed, if any.
    
    Returns:
        disnake.Embed: The created embed object.
    """
    embed = disnake.Embed(title=title, description=description, color=0xFFA500, timestamp=datetime.now())  # Orange color
    embed.set_footer(
        text=footer if footer else "WACA-Chan",
        icon_url="https://cdn.discordapp.com/attachments/1201752284275683389/1262675775924994048/Waca-Chan.png?ex=669775f4&is=66962474&hm=05db08b02f2fb9d95aa2ac1f34f5b3415ce34ce0ad5f016b7385778f71d1aee3&"
    )
    embed.set_author(name="Alert!", icon_url="https://cdn.discordapp.com/emojis/1109510616206557254.webp?size=128&quality=lossless")
    return embed

alert = create_alert_embed

async def create_success_embed(title: str, description: str = "", footer: Optional[str] = None) -> disnake.Embed:
    """
    Creates a success embed message with a green color.
    
    Args:
        title (str): The title of the embed.
        description (str, optional): The main content of the embed. Defaults to "".
        footer (Optional[str]): The footer text of the embed, if any.
    
    Returns:
        disnake.Embed: The created embed object.
    """
    embed = disnake.Embed(title=title, description=description, color=0x00FF00, timestamp=datetime.now())  # Green color
    embed.set_footer(
        text=footer if footer else "WACA-Chan",
        icon_url="https://cdn.discordapp.com/attachments/1201752284275683389/1262675775924994048/Waca-Chan.png?ex=669775f4&is=66962474&hm=05db08b02f2fb9d95aa2ac1f34f5b3415ce34ce0ad5f016b7385778f71d1aee3&")
    embed.set_author(name="Success!", icon_url="https://cdn.discordapp.com/emojis/1109510617401917540.webp?size=128&quality=lossless")
    return embed

success = create_success_embed

async def create_critical_failure_embed(title: str, description: str = "", footer: Optional[str] = None) -> disnake.Embed:
    """
    Creates a critical failure embed message with a red color.
    
    Args:
        title (str): The title of the embed.
        description (str, optional): The main content of the embed. Defaults to "".
        footer (Optional[str]): The footer text of the embed, if any.
    
    Returns:
        disnake.Embed: The created embed object.
    """
    embed = disnake.Embed(title=title, description=description, color=0xFF0000, timestamp=datetime.now())  # Red color
    embed.set_footer(
        text=footer if footer else "WACA-Chan",
        icon_url="https://cdn.discordapp.com/attachments/1201752284275683389/1262675775924994048/Waca-Chan.png?ex=669775f4&is=66962474&hm=05db08b02f2fb9d95aa2ac1f34f5b3415ce34ce0ad5f016b7385778f71d1aee3&")
    embed.set_author(name="Critical Failure!", icon_url="https://cdn.discordapp.com/emojis/1109510619687817426.webp?size=128&quality=lossless")
    return embed

error = create_critical_failure_embed
