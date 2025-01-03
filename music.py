import disnake
from disnake.ext import commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
from dotenv import load_dotenv
import asyncio
import re
from collections import deque
from core.statbed import create_alert_embed, create_success_embed, create_critical_failure_embed
import requests
import aiohttp
import concurrent.futures

# Remove logging setup
# logging.basicConfig(filename='music_bot.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

async def update_status(self):
    if self.current_song:
        await self.bot.change_presence(activity=disnake.Activity(type=disnake.ActivityType.listening, name=self.current_song['title']))
    else:
        await self.bot.change_presence(activity=None)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queue = deque()
        self.current_song = None
        self.is_playing = False
        self.volume = 1.0
        self.repeat = False
        self.dashboard_message = None
        self.dashboard_channel = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=6)
        
        load_dotenv()
        self.spotify_client_id = os.getenv('SPOTIFY_CLIENT_ID')
        self.spotify_client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
        self.youtube_api_key = os.getenv('YOUTUBE_API_KEY')

        if self.spotify_client_id and self.spotify_client_secret:
            try:
                self.spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                    client_id=self.spotify_client_id,
                    client_secret=self.spotify_client_secret
                ))
                print("Spotify client initialized successfully")
            except Exception as e:
                print(f"Error initializing Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None

        self.bot.loop.create_task(self.start_playback_loop())  # Start the playback loop

    async def start_playback_loop(self):
        await self.bot.wait_until_ready()
        self.bot.loop.create_task(self.playback_loop())

    async def playback_loop(self):
        while not self.bot.is_closed():
            if self.is_playing and not self.bot.voice_clients[0].is_playing():
                await self.play_next()
            await asyncio.sleep(1)  # Check every second

    async def join_voice_channel(self, inter):
        if not inter.author.voice:
            return await create_alert_embed("Join a voice channel first")
        
        if inter.guild.voice_client:
            if inter.guild.voice_client.channel != inter.author.voice.channel:
                await inter.guild.voice_client.move_to(inter.author.voice.channel)
            return True
        
        await inter.author.voice.channel.connect()
        return True
    
    async def handle_query(self, inter, query):
        if 'open.spotify.com' in query:
            return await self.handle_spotify_query(inter, query)
        elif 'youtube.com/playlist' in query or ('youtube.com/watch?v=' in query and '&list=' in query):
            return await self.handle_youtube_playlist_query(inter, query)
        elif re.match(r'^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$', query):
            return await self.handle_youtube_query(inter, query)
        elif 'soundcloud.com' in query:
            return await self.handle_soundcloud_query(inter, query)
        else:
            return await self.handle_search_query(inter, query)

    async def handle_spotify_query(self, inter, query):
        tracks = await self.get_spotify_tracks(query)
        if tracks:
            return await self.process_tracks(inter, tracks, "Spotify")
        else:
            embed = await create_alert_embed("Error", "Couldn't find any tracks from the Spotify link.")
            await inter.edit_original_response(embed=embed)
            return []

    async def handle_youtube_playlist_query(self, inter, query):
        playlist_id = re.findall(r'list=([a-zA-Z0-9_-]+)', query)[0]
        playlist_items = await self.get_youtube_playlist_items(playlist_id)
        if playlist_items:
            return await self.process_tracks(inter, playlist_items, "YouTube")
        else:
            embed = await create_alert_embed("Error", "Couldn't find any tracks from the YouTube playlist.")
            await inter.edit_original_response(embed=embed)
            return []

    async def handle_youtube_query(self, inter, query):
        track = await self.process_youtube_url(query)
        if track:
            return [track]
        else:
            embed = await create_alert_embed("Error", "Failed to process the YouTube video.")
            await inter.edit_original_response(embed=embed)
            return []

    async def handle_soundcloud_query(self, inter, query):
        track = await self.process_soundcloud_url(query)
        if track:
            embed = await create_success_embed("Added to Queue", f"🎵 {track['title']} ({track['duration']})")
            await inter.edit_original_response(embed=embed)  # Send the embed
            return [track]
        else:
            embed = await create_alert_embed("Error", "Failed to process the SoundCloud track.")
            await inter.edit_original_response(embed=embed)
            return []

    async def handle_search_query(self, inter, query):
        search_results = await self.search_youtube(query)
        if isinstance(search_results, disnake.Embed):
            await inter.edit_original_response(embed=search_results)
            return []
        if not search_results:
            embed = await create_alert_embed("Error", f"No results found for your search query: '{query}'. Please try a different search term.")
            await inter.edit_original_response(embed=embed)
            return []

        search_results = search_results[:5]
        view = SongChoiceView(self, search_results, inter.author.id)
        embed = self.create_embed("Search Results", "Please select a song from the menu below:", disnake.Color.blue())
        if len(search_results) == 5:
            embed.set_footer(text="Showing first 5 results")

        await inter.edit_original_response(embed=embed, view=view)
        await view.wait()
        if view.selected_song:
            track = await self.process_youtube_url(view.selected_song['url'])
            if track:
                return [track]
        return []

    async def process_tracks(self, inter, tracks, source):
        processed_tracks = []
        total_tracks = len(tracks)
        embed = self.create_embed(f"Processing {source} Playlist", f"0/{total_tracks} tracks processed.", disnake.Color.blue(), imageless=True)
        message = await inter.edit_original_response(embed=embed)

        for i, track in enumerate(tracks):
            if source == "YouTube":
                video_id = track['snippet']['resourceId']['videoId']
                url = f"https://www.youtube.com/watch?v={video_id}"
                processed_track = await self.process_youtube_url(url)
            elif source == "Spotify":
                processed_track = await self.process_spotify_track(track)
            else:
                processed_track = None

            if processed_track:
                processed_tracks.append(processed_track)

            # Update progress
            embed.description = f"{i + 1}/{total_tracks} tracks processed."
            await message.edit(embed=embed)

        if processed_tracks:
            embed = await create_success_embed(f"{source} Playlist", f"Added {len(processed_tracks)} tracks to the queue.")
        else:
            embed = await create_alert_embed("Error", f"Couldn't add any tracks from the {source} playlist.")
        
        await message.edit(embed=embed)
        return processed_tracks

    async def process_youtube_url(self, url):
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(self.executor, self._download_info, url)
            if info:
                track = {
                    'url': info['url'],
                    'title': info['title'],
                    'duration': self.format_duration(info['duration']),
                    'thumbnail': info['thumbnail']
                }
                print(f"Processed YouTube URL: {url} - Title: {track['title']}, Duration: {track['duration']}")
                return track
        except Exception as e:
            print(f"Error processing YouTube URL: {url} - {e}")
        return None

    async def process_soundcloud_url(self, url):
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(self.executor, self._download_info, url)
            if info:
                track = {
                    'url': info['url'],
                    'title': info['title'],
                    'duration': self.format_duration(info['duration']),
                    'thumbnail': info['thumbnail']
                }
                print(f"Processed SoundCloud URL: {url} - Title: {track['title']}, Duration: {track['duration']}")
                return track
        except Exception as e:
            print(f"Error processing SoundCloud URL: {url} - {e}")
        return None

    async def process_spotify_track(self, track):
        if isinstance(track, tuple):
            title, name, duration = track
            search_query = f"{title} - {name}"
        else:
            search_query = track
        search_results = await self.search_youtube(search_query)
        if search_results:
            url = search_results[0]['url']
            return await self.process_youtube_url(url)
        return None

    def _download_info_with_retries(self, url, retries=3):
        ydl_opts = {
            'format': 'bestaudio',
            'ignoreerrors': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0'
        }
        for attempt in range(retries):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if 'entries' in info:
                        info = info['entries'][0]
                    return {
                        'url': info['url'],
                        'title': info['title'],
                        'duration': info['duration'],
                        'thumbnail': info['thumbnail']
                    }
            except yt_dlp.utils.DownloadError as e:
                if '403 Forbidden' in str(e):
                    print(f"403 Forbidden error encountered. Retrying {attempt + 1}/{retries}...")
                    continue
                else:
                    print(f"Error downloading info: {url} - {e}")
                    break
        return None

    def _download_info(self, url):
        return self._download_info_with_retries(url)

    async def search_youtube(self, query):
        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'key': self.youtube_api_key,
            'maxResults': 5
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data['items']
                        return [
                            {
                                'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                                'title': item['snippet']['title'],
                                'duration': 'Unknown'
                            }
                            for item in items
                        ]
                    else:
                        print(f"Error searching YouTube. Status code: {response.status}")
                        return []
        except Exception as e:
            print(f"Error searching YouTube: {e}")
            return []

    async def get_youtube_playlist_items(self, playlist_id):
        playlist_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=50&playlistId={playlist_id}&key={self.youtube_api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(playlist_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['items']
                    else:
                        print(f"Error fetching YouTube playlist items. Status code: {response.status}")
                        return []
        except Exception as e:
            print(f"Error fetching YouTube playlist items: {e}")
            return []

    async def get_spotify_tracks(self, url, retries=3):
        if self.spotify:
            print(f"Fetching Spotify tracks for URL: {url}")
            for attempt in range(retries):
                try:
                    if '/track/' in url:
                        track = self.spotify.track(url)
                        print(f"Found track: {track['name']} by {track['artists'][0]['name']}")
                        return [(track['artists'][0]['name'], track['name'], self.format_duration(track['duration_ms'] // 1000))]
                    elif '/album/' in url:
                        album = self.spotify.album(url)
                        print(f"Found album: {album['name']} by {album['artists'][0]['name']}")
                        return [(track['artists'][0]['name'], track['name'], self.format_duration(track['duration_ms'] // 1000)) for track in album['tracks']['items']]
                    elif '/playlist/' in url:
                        playlist = self.spotify.playlist(url)
                        print(f"Found playlist: {playlist['name']} by {playlist['owner']['display_name']}")
                        return [(track['track']['artists'][0]['name'], track['track']['name'], self.format_duration(track['track']['duration_ms'] // 1000)) for track in playlist['tracks']['items']]
                except spotipy.exceptions.SpotifyException as e:
                    print(f"Error fetching Spotify tracks: {e}. Retrying {attempt + 1}/{retries}...")
                    await asyncio.sleep(1)  # Wait a bit before retrying
            print("Failed to fetch Spotify tracks after retries.")
        return []

    def format_duration(self, duration_seconds):
        minutes, seconds = divmod(int(duration_seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"

    @commands.slash_command()
    async def play(self, inter: disnake.ApplicationCommandInteraction, query: str):
        print(f"Received play command with query: {query}")
        await inter.response.defer()
        join_result = await self.join_voice_channel(inter)
        if isinstance(join_result, disnake.Embed):
            await inter.edit_original_response(embed=join_result)
            return

        tracks = await self.handle_query(inter, query)
        if tracks:
            for track in tracks:
                self.song_queue.append(track)
            if not self.is_playing:
                await self.play_next()
            await self.update_dashboard()
            embed = await create_success_embed("Added to Queue", f"Added {len(tracks)} track(s) to the queue.")
            await inter.edit_original_response(embed=embed)

    async def play_next(self):
        if self.repeat and self.current_song:
            # If repeat is enabled, re-play the current song
            await self.play_song(self.current_song)
        else:
            if not self.song_queue:
                self.current_song = None
                self.is_playing = False
                await self.update_dashboard()
                await update_status(self)
                return

            self.current_song = self.song_queue.popleft()
            self.is_playing = True
            await self.play_song(self.current_song)
            await self.update_dashboard()
            await update_status(self)

    async def play_song(self, song):
        voice_client = self.bot.voice_clients[0]
        if voice_client.is_playing():
            voice_client.stop()
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn -buffer_size 16M'
        }
        audio_source = await disnake.FFmpegOpusAudio.from_probe(song['url'], **ffmpeg_options)
        
        voice_client.play(audio_source)
        self.is_playing = True  # Ensure the bot knows it's playing
        await update_status(self)  # Update the bot's status

    

    @commands.slash_command()
    async def skip(self, inter):
        if inter.guild.voice_client and inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()
            await self.play_next()
            await inter.response.send_message(embed=await create_success_embed("Skipped the current song."), ephemeral=True)  # Acknowledge the interaction
        else:
            embed = await create_alert_embed("Nothing to Skip", "There's nothing currently playing to skip.")
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def queue(self, inter):
        await self.show_queue(inter, ephemeral=False)

    async def show_queue(self, inter, page=1, ephemeral=False, followup=False, response=True):
        items_per_page = 10
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        
        queue_items = list(self.song_queue)[start_index:end_index]
        embed = self.create_embed("Music Queue", "", disnake.Color.blue(), imageless=True)
        
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song['title']} ({self.current_song['duration']})", inline=False)
        
        if not queue_items:
            embed.description = "The queue is empty."
        else:
            queue_text = "\n".join(f"`{i+1}.` {song['title']} ({song['duration']})" for i, song in enumerate(queue_items, start=start_index))
            embed.add_field(name="Upcoming Songs", value=queue_text, inline=False)
        
        total_pages = (len(self.song_queue) + items_per_page - 1) // items_per_page
        embed.set_footer(text=f"Page {page}/{total_pages}")
        embed.set_author(name="WACA-Chan", icon_url=self.bot.user.avatar.url)
        
        view = QueuePaginationView(self, inter, page, total_pages)
        if response:
            await inter.response.send_message(embed=embed, view=view, ephemeral = ephemeral)
        elif followup:
            await inter.followup.send(embed=embed, view=view, ephemeral = ephemeral)
        else:
            await inter.edit_original_response(embed=embed, view=view)

    @commands.slash_command()
    async def dashboard(self, inter):
        embed = self.create_dashboard_embed()
        components = self.create_dashboard_components()
        message = await inter.response.send_message(embed=embed, components=components)
        self.dashboard_message = await inter.original_message()
        self.dashboard_channel = inter.channel

    def create_embed(self, title, description, color, imageless=False):
        embed = disnake.Embed(title=title, description=description, color=color, timestamp=disnake.utils.utcnow())
        embed.set_footer(text=f"WACA-Chan 1.2", icon_url=self.bot.user.avatar.url)
        
        if not imageless:
            if self.current_song and 'thumbnail' in self.current_song:
                embed.set_image(url=self.current_song['thumbnail'])
            else:
                embed.set_image(url="https://cdn.discordapp.com/attachments/913207064136925254/1262876163962044456/Something_new.png?ex=66983094&is=6696df14&hm=beebf7e3450d353dd58fea1981d8a566fc3d3f32a5f4a106b06c7764bdb4c65c&")
        
        return embed

    def create_dashboard_embed(self):
        embed = self.create_embed("Music Dashboard", "", disnake.Color.blue())
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song['title']} ({self.current_song['duration']})", inline=False)
        else:
            embed.add_field(name="Now Playing", value="Nothing is currently playing", inline=False)
        embed.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)
        embed.add_field(name="Repeat", value="On" if self.repeat else "Off", inline=True)
        embed.set_thumbnail(url=self.bot.user.avatar.url)
        return embed

    def create_dashboard_components(self):
        play_pause_style = disnake.ButtonStyle.secondary if self.is_playing else disnake.ButtonStyle.success
        play_pause_emoji = "<:Pause:1262673070854901770>" if self.is_playing else "<:Play:1262672920984027157>"
        repeat_style = disnake.ButtonStyle.secondary if self.repeat else disnake.ButtonStyle.primary
        return [
            disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:VolDown:1262671144910061650>", custom_id="music_volume_down"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:PreviousTrack:1262671148525682760>", custom_id="music_previous"),
            disnake.ui.Button(style=play_pause_style, emoji=play_pause_emoji, custom_id="music_play_pause"),
            
            disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:NextTrack:1262671150291353625>", custom_id="music_skip"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:VolUp:1262671143890976798>", custom_id="music_volume_up"),
            disnake.ui.Button(style=disnake.ButtonStyle.secondary,label="-", disabled=True, custom_id="button_disabled2"),
            disnake.ui.Button(style=repeat_style, emoji="<:RepeatOne:1262671948140384298>", custom_id="music_repeat"),
            disnake.ui.Button(style=disnake.ButtonStyle.success, emoji="<:AddToList:1262671146491445249>", custom_id="music_add_to_playlist"),
            disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:Queue:1262673071626522644>", custom_id="music_view_queue"),
            disnake.ui.Button(style=disnake.ButtonStyle.secondary,label="-", disabled=True, custom_id="button_disabled")
        ]

    async def update_dashboard(self):
        if self.dashboard_message and self.dashboard_channel:
            embed = self.create_dashboard_embed()
            components = self.create_dashboard_components()
            try:
                await self.dashboard_message.edit(embed=embed, components=components)
            except disnake.NotFound:
                # If the message was deleted, reset the dashboard
                self.dashboard_message = None
                self.dashboard_channel = None

    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id.startswith("queue_"):
            await inter.response.defer(ephemeral=True)
            if "first_page" in inter.component.custom_id:
                await self.show_queue(inter, page=1, ephemeral=True, followup=False, response=False)
            elif "previous_page" in inter.component.custom_id:
                current_page = int(inter.component.custom_id.split('_')[-1])
                await self.show_queue(inter, page=current_page - 1, ephemeral=True, followup=False, response=False)
            elif "next_page" in inter.component.custom_id:
                current_page = int(inter.component.custom_id.split('_')[-1])
                await self.show_queue(inter, page=current_page + 1, ephemeral=True, followup=False, response=False)
            elif "last_page" in inter.component.custom_id:
                total_pages = int(inter.component.custom_id.split('_')[-1])
                await self.show_queue(inter, page=total_pages, ephemeral=True, followup=False, response=False)
        elif inter.component.custom_id.startswith("music_"):
            if inter.component.custom_id == "music_add_to_playlist":
                await inter.response.send_modal(
                    title="Add to Playlist",
                    custom_id="add_to_playlist_modal",
                    components=[
                        disnake.ui.TextInput(
                            label="Query",
                            placeholder="Search or Enter a Song Link",
                            custom_id="song_url",
                            style=disnake.TextInputStyle.short,
                            max_length=200,
                        ),
                    ],
                )
                return  # Skip the update_dashboard call for this case
            else:
                await inter.response.defer(ephemeral=True)

            if inter.component.custom_id == "music_previous":
                await inter.response.send_message(embed=await create_alert_embed("This feature is not available yet."), ephemeral=True)
            elif inter.component.custom_id == "music_play_pause":
                if inter.guild.voice_client:
                    if inter.guild.voice_client.is_playing():
                        inter.guild.voice_client.pause()
                        self.is_playing = False
                    else:
                        inter.guild.voice_client.resume()
                        self.is_playing = True
            elif inter.component.custom_id == "music_skip":
                if inter.guild.voice_client and inter.guild.voice_client.is_playing():
                    inter.guild.voice_client.stop()
                    await self.play_next()
                else:
                    embed = await create_alert_embed("Nothing to Skip", "There's nothing currently playing to skip.")
                    await inter.followup.send(embed=embed, ephemeral=True)
            elif inter.component.custom_id == "music_volume_up":
                self.volume = round(min(2.0, self.volume + 0.1), 1)
                if inter.guild.voice_client and inter.guild.voice_client.source:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_volume_down":
                self.volume = round(max(0.0, self.volume - 0.1), 1)
                if inter.guild.voice_client and inter.guild.voice_client.source:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_repeat":
                self.repeat = not self.repeat
            elif inter.component.custom_id == "music_view_queue":
                await self.show_queue(inter, ephemeral=True, followup=True, response=False)

            await self.update_dashboard()
            await inter.edit_original_response(embed=self.create_dashboard_embed(), components=self.create_dashboard_components())
        

    @commands.Cog.listener()
    async def on_modal_submit(self, inter: disnake.ModalInteraction):
        if inter.custom_id == "add_to_playlist_modal":
            song_url_or_search = inter.text_values["song_url"]
            if song_url_or_search.startswith("http"):
                await self.play(inter, query=song_url_or_search)
            else:
                search_query = song_url_or_search
                await self.play(inter, query=search_query)

    @commands.slash_command()
    async def debug(self, inter: disnake.ApplicationCommandInteraction):
        """Displays debug information about the music client."""
        embed = disnake.Embed(title="Music Client Debug Info", color=disnake.Color.green())
        
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"{self.current_song['title']} ({self.current_song['duration']})", inline=False)
        else:
            embed.add_field(name="Now Playing", value="Nothing is currently playing", inline=False)
        
        embed.add_field(name="Is Playing", value=str(self.is_playing), inline=True)
        embed.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)
        embed.add_field(name="Repeat", value="On" if self.repeat else "Off", inline=True)
        embed.add_field(name="Queue Length", value=str(len(self.song_queue)), inline=True)
        
        if self.bot.voice_clients:
            voice_client = self.bot.voice_clients[0]
            embed.add_field(name="Connected to Voice Channel", value=str(voice_client.channel), inline=False)
        else:
            embed.add_field(name="Connected to Voice Channel", value="Not connected", inline=False)
        
        await inter.response.send_message(embed=embed)

class SongChoiceView(disnake.ui.View):
    def __init__(self, cog, search_results, author_id):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.search_results = search_results
        self.author_id = author_id
        self.selected_song = None
        self.add_item(SongChoiceSelect(cog, search_results, author_id))

class SongChoiceSelect(disnake.ui.Select):
    def __init__(self, cog, search_results, author_id):
        self.cog = cog
        self.search_results = search_results
        self.author_id = author_id
        options = []
        for i, result in enumerate(self.search_results):
            label = result['title']
            if len(label) > 100:
                label = label[:97] + "..."
            options.append(
                disnake.SelectOption(
                    label=label,
                    description=f"Duration: {result['duration']}",
                    value=str(i)
                )
            )
        super().__init__(placeholder="Choose a song", options=options, custom_id="song_select")

    async def callback(self, inter: disnake.MessageInteraction):
        if inter.author.id != self.author_id:
            await inter.response.send_message("You didn't initiate this search.", ephemeral=True)
            return

        await inter.response.defer()
        selected_index = int(self.values[0])
        selected_song = self.search_results[selected_index]
        self.cog.selected_song = selected_song
        url = selected_song['url']
        title = selected_song['title']
        duration = selected_song['duration']
        
        processed_track = await self.cog.process_youtube_url(url)
        if processed_track:
            self.cog.song_queue.append(processed_track)  # Append the processed track dictionary
            embed = await create_success_embed("Added to Queue", f"🎵 {processed_track['title']} ({processed_track['duration']})")
            await inter.edit_original_response(embed=embed, view=None)

            if not self.cog.is_playing:
                await self.cog.play_next()
            await self.cog.update_dashboard()

class QueuePaginationView(disnake.ui.View):
    def __init__(self, cog, inter, current_page, total_pages):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.inter = inter
        self.current_page = current_page
        self.total_pages = total_pages

        self.add_item(disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:firstpage:1262797966528352266>", custom_id=f"queue_first_page_{current_page}", disabled=current_page == 1))
        self.add_item(disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:back:1262797968545943643>", custom_id=f"queue_previous_page_{current_page}", disabled=current_page == 1))
        self.add_item(disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:next:1262797963223371807>", custom_id=f"queue_next_page_{current_page}", disabled=current_page == total_pages or total_pages == 0))
        self.add_item(disnake.ui.Button(style=disnake.ButtonStyle.primary, emoji="<:lastpage:1262797965655806022>", custom_id=f"queue_last_page_{total_pages}", disabled=current_page == total_pages or total_pages == 0))


    

