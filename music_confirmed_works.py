import disnake
from disnake.ext import commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
from dotenv import load_dotenv
import asyncio

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = asyncio.Queue()
        self.current_song = None
        self.is_playing = False
        self.volume = 1.0
        self.repeat = False
        self.dashboard_message = None
        
        load_dotenv()
        self.spotify_client_id = os.getenv('SPOTIFY_CLIENT_ID')
        self.spotify_client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

        if self.spotify_client_id and self.spotify_client_secret:
            try:
                self.spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                    client_id=self.spotify_client_id,
                    client_secret=self.spotify_client_secret
                ))
            except Exception as e:
                print(f"Error initializing Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None

    def create_embed(self, title, description, color):
        embed = disnake.Embed(title=title, description=description, color=color)
        embed.set_author(name="WACA-Chan Music")
        embed.set_footer(text="Powered by WACA-Chan")
        return embed

    async def join_voice_channel(self, inter):
        if not inter.author.voice:
            embed = self.create_embed("Error", "Join a voice channel first.", disnake.Color.red())
            await inter.response.send_message(embed=embed, ephemeral=True)
            return False
        
        if inter.guild.voice_client:
            if inter.guild.voice_client.channel != inter.author.voice.channel:
                await inter.guild.voice_client.move_to(inter.author.voice.channel)
            return True
        
        await inter.author.voice.channel.connect()
        return True

    @commands.slash_command()
    async def play(self, inter, url: str):
        await inter.response.defer()
        if not await self.join_voice_channel(inter):
            return

        if 'open.spotify.com' in url:
            tracks = await self.get_spotify_tracks(url)
            if tracks:
                url = tracks[0][0]  # Get the first track's search query
            else:
                embed = self.create_embed("Error", "Couldn't find any tracks from the Spotify link.", disnake.Color.red())
                await inter.edit_original_response(embed=embed)
                return

        with yt_dlp.YoutubeDL({'format': 'bestaudio'}) as ydl:
            info = ydl.extract_info(url, download=False)
            url2 = info['url']
            title = info['title']
            duration = self.format_duration(info['duration'])

        await self.queue.put((url2, title, duration))
        embed = self.create_embed("Added to Queue", f"🎵 {title} ({duration})", disnake.Color.green())
        await inter.edit_original_response(embed=embed)

        if not self.is_playing:
            await self.play_next()

        await self.update_dashboard()

    async def play_next(self):
        if self.queue.empty() and not self.repeat:
            self.is_playing = False
            self.current_song = None
            await self.update_dashboard()
            return

        self.is_playing = True
        if self.repeat and self.current_song:
            url, title, duration = self.current_song
        else:
            url, title, duration = await self.queue.get()

        def after_playing(error):
            if error:
                print(f"Error playing {title}: {error}")
            self.bot.loop.create_task(self.play_next())

        voice_client = self.bot.voice_clients[0]  # Assuming bot is in only one voice channel
        try:
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                'options': f'-vn -bufsize 8192k -filter:a "volume={self.volume}"'
            }
            voice_client.play(disnake.FFmpegPCMAudio(url, **ffmpeg_options), after=after_playing)
            self.current_song = (url, title, duration)
            await self.update_dashboard()
        except Exception as e:
            print(f"Error playing {title}: {e}")
            await self.play_next()  # Skip to the next song if there's an error

    async def get_spotify_tracks(self, url):
        if self.spotify:
            if '/track/' in url:
                track = self.spotify.track(url)
                duration = self.format_duration(track['duration_ms'] // 1000)
                return [(f"{track['artists'][0]['name']} - {track['name']}", track['name'], duration)]
            elif '/album/' in url:
                album = self.spotify.album(url)
                return [(f"{track['artists'][0]['name']} - {track['name']}", track['name'], self.format_duration(track['duration_ms'] // 1000)) for track in album['tracks']['items']]
            elif '/playlist/' in url:
                playlist = self.spotify.playlist(url)
                return [(f"{track['track']['artists'][0]['name']} - {track['track']['name']}", track['track']['name'], self.format_duration(track['track']['duration_ms'] // 1000)) for track in playlist['tracks']['items']]
        return []

    def format_duration(self, duration_seconds):
        minutes, seconds = divmod(int(duration_seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"

    @commands.slash_command()
    async def stop(self, inter):
        if inter.guild.voice_client and inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()
            self.queue = asyncio.Queue()  # Clear the queue
            self.is_playing = False
            self.current_song = None
            embed = self.create_embed("Stopped", "🛑 Stopped playing and cleared the queue.", disnake.Color.blue())
            await inter.response.send_message(embed=embed)
            await self.update_dashboard()
        else:
            embed = self.create_embed("Nothing Playing", "There's nothing currently playing.", disnake.Color.yellow())
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def skip(self, inter):
        if inter.guild.voice_client and inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()  # This will trigger the after_playing callback
            embed = self.create_embed("Skipped", "⏭️ Skipped the current song.", disnake.Color.blue())
            await inter.response.send_message(embed=embed)
        else:
            embed = self.create_embed("Nothing to Skip", "There's nothing currently playing to skip.", disnake.Color.yellow())
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def queue(self, inter):
        embed = self.create_embed("Music Queue", "", disnake.Color.blue())
        
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song[1]} ({self.current_song[2]})", inline=False)
        
        if self.queue.empty():
            embed.description = "The queue is empty."
        else:
            queue_list = [f"{i+1}. {song[1]} ({song[2]})" for i, song in enumerate(self.queue._queue)]
            queue_text = "\n".join(queue_list)
            embed.add_field(name="Upcoming Songs", value=queue_text, inline=False)
        
        await inter.response.send_message(embed=embed)

    @commands.slash_command()
    async def dashboard(self, inter):
        embed = self.create_dashboard_embed()
        components = self.create_dashboard_components()
        self.dashboard_message = await inter.response.send_message(embed=embed, components=components)

    def create_dashboard_embed(self):
        embed = self.create_embed("Music Dashboard", "", disnake.Color.blue())
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song[1]} ({self.current_song[2]})", inline=False)
        else:
            embed.add_field(name="Now Playing", value="Nothing is currently playing", inline=False)
        embed.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)
        embed.add_field(name="Repeat", value="On" if self.repeat else "Off", inline=True)
        return embed

    def create_dashboard_components(self):
        return [
            disnake.ui.Button(style=disnake.ButtonStyle.primary, label="Play/Pause", custom_id="music_play_pause"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, label="Skip", custom_id="music_skip"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, label="Volume Up", custom_id="music_volume_up"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, label="Volume Down", custom_id="music_volume_down"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, label="Repeat", custom_id="music_repeat")
        ]

    async def update_dashboard(self):
        if self.dashboard_message:
            embed = self.create_dashboard_embed()
            components = self.create_dashboard_components()
            await self.dashboard_message.edit(embed=embed, components=components)

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id.startswith("music_"):
            await inter.response.defer()
            if inter.component.custom_id == "music_play_pause":
                if inter.guild.voice_client:
                    if inter.guild.voice_client.is_playing():
                        inter.guild.voice_client.pause()
                    else:
                        inter.guild.voice_client.resume()
            elif inter.component.custom_id == "music_skip":
                if inter.guild.voice_client and inter.guild.voice_client.is_playing():
                    inter.guild.voice_client.stop()
            elif inter.component.custom_id == "music_volume_up":
                self.volume = min(2.0, self.volume + 0.1)
                if inter.guild.voice_client:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_volume_down":
                self.volume = max(0.0, self.volume - 0.1)
                if inter.guild.voice_client:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_repeat":
                self.repeat = not self.repeat
            await self.update_dashboard()

def setup(bot):
    bot.add_cog(Music(bot))