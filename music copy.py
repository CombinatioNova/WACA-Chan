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
import logging
import threading

# Set up logging
logging.basicConfig(filename='music_bot.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

async def update_status(self):
    if self.current_song:
        await self.bot.change_presence(activity=disnake.Activity(type=disnake.ActivityType.listening, name=self.current_song[1]))
    else:
        await self.bot.change_presence(activity=None)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queue = asyncio.Queue()  # Renamed from self.queue to self.song_queue
        self.current_song = None
        self.is_playing = False
        self.volume = 1.0
        self.repeat = False
        self.dashboard_message = None
        self.dashboard_channel = None
        self.previous_songs = deque(maxlen=10)  # Store up to 10 previous songs
        self.disconnect_task = None
        self.download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)  # Separate executor for downloading
        self.playback_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)  # Separate executor for playback
        self.play_next_lock = asyncio.Lock()  # Add a lock to prevent race conditions
        
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
                logging.debug("Spotify client initialized successfully")
            except Exception as e:
                logging.error(f"Error initializing Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None

    async def join_voice_channel(self, inter):
        if not inter.author.voice:
            return await create_alert_embed("Join a voice channel first")
        
        if inter.guild.voice_client:
            if inter.guild.voice_client.channel != inter.author.voice.channel:
                await inter.guild.voice_client.move_to(inter.author.voice.channel)
            return True
        
        await inter.author.voice.channel.connect()
        return True
    

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
            response = requests.get(search_url, params=params)
            response.raise_for_status()
            results = response.json().get('items', [])
            if not results:
                logging.debug(f"No results found for query: {query}")
                return []
            
            search_results = []
            for item in results:
                video_id = item['id']['videoId']
                title = item['snippet']['title']
                duration = await self.get_video_duration(video_id)
                search_results.append({
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'title': title,
                    'duration': duration
                })
            return search_results
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                error_message = f"403 Forbidden error when searching YouTube: {e}"
                logging.error(error_message)
                return await create_critical_failure_embed("YouTube API Error", error_message)
        except Exception as e:
            logging.error(f"Error searching YouTube: {e}")
            return []

    async def get_video_duration(self, video_id):
        video_url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'contentDetails',
            'id': video_id,
            'key': self.youtube_api_key
        }
        try:
            response = requests.get(video_url, params=params)
            response.raise_for_status()
            items = response.json().get('items', [])
            if not items:
                return "Unknown"
            
            duration = items[0]['contentDetails']['duration']
            return self.parse_duration(duration)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                error_message = f"403 Forbidden error when getting video duration: {e}"
                logging.error(error_message)
                return await create_critical_failure_embed("YouTube API Error", error_message)
        except Exception as e:
            logging.error(f"Error getting video duration: {e}")
            return "Unknown"

    def parse_duration(self, duration):
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return "Unknown"
        
        hours, minutes, seconds = match.groups()
        hours = int(hours) if hours else 0
        minutes = int(minutes) if minutes else 0
        seconds = int(seconds) if seconds else 0
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    def get_queue_items(self):
        return [song[1] for song in self.song_queue._queue]

    @commands.slash_command()
    async def play(self, inter: disnake.ApplicationCommandInteraction, query: str, dashboard: bool = False):
        logging.debug(f"Received play command with query: {query}")
        if not dashboard:
            await inter.response.defer()
        else:
            await inter.response.defer(ephemeral=True)
        join_result = await self.join_voice_channel(inter)
        if isinstance(join_result, disnake.Embed):
            await inter.edit_original_response(embed=join_result)
            return

        match query:
            case _ if 'open.spotify.com' in query:
                tracks = await self.get_spotify_tracks(query)
                if tracks:
                    await self.process_playlist(inter, tracks, "Spotify")
                else:
                    embed = await create_alert_embed("Error", "Couldn't find any tracks from the Spotify link.")
                    await inter.edit_original_response(embed=embed)
                    return

            case _ if 'youtube.com/playlist' in query or 'youtube.com/watch?v=' in query and '&list=' in query:
                playlist_id = re.findall(r'list=([a-zA-Z0-9_-]+)', query)[0]
                playlist_items = await self.get_youtube_playlist_items(playlist_id)
                if playlist_items:
                    tracks_added = await self.process_playlist(inter, playlist_items, "YouTube")
                    if tracks_added > 0:
                        if not self.is_playing:
                            await self.play_next()
                    else:
                        embed = await create_alert_embed("Error", "Couldn't add any tracks from the YouTube playlist.")
                        await inter.edit_original_response(embed=embed)
                else:
                    embed = await create_alert_embed("Error", "Couldn't find any tracks from the YouTube playlist.")
                    await inter.edit_original_response(embed=embed)
                return

            case _ if re.match(r'^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$', query):
                # If it's a YouTube link, process it directly
                url, title, duration = await self.process_url(query)
                if url:
                    await self.song_queue.put((url, title, duration))
                    logging.debug(f"Added to queue: {title} ({duration})")
                    embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
                    await inter.edit_original_response(embed=embed)

                    if not self.is_playing:
                        await self.play_next()

                    await self.update_dashboard()
                else:
                    embed = await create_alert_embed("Error", "Failed to process the YouTube video.")
                    await inter.edit_original_response(embed=embed)

            case _ if 'soundcloud.com' in query:
                try:
                    url, title, duration = await self.process_url(query)
                    if url:
                        await self.song_queue.put((url, title, duration))
                        logging.debug(f"Added to queue: {title} ({duration})")
                        embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
                        await inter.edit_original_response(embed=embed)

                        if not self.is_playing:
                            await self.play_next()

                        await self.update_dashboard()
                    else:
                        embed = await create_alert_embed("Error", "Failed to process the SoundCloud track.")
                        await inter.edit_original_response(embed=embed)
                except Exception as e:
                    logging.error(f"Couldn't process SoundCloud link: {e}")
                    embed = await create_alert_embed("Error", f"Couldn't process SoundCloud link: {e}")
                    await inter.edit_original_response(embed=embed)

            case _ if 'deezer.com' in query or 'deezer.page.link' in query:
                try:
                    track_id = query.split('/')[-1]
                    track_info = await self.get_deezer_track_info(track_id)
                    if track_info:
                        title, artist, preview_url, duration = track_info
                        await self.song_queue.put((preview_url, f"{artist} - {title}", duration))
                        logging.debug(f"Added to queue: {artist} - {title} ({duration})")
                        embed = await create_success_embed("Added to Queue", f"🎵 {artist} - {title} ({duration})")
                        await inter.edit_original_response(embed=embed)

                        if not self.is_playing:
                            await self.play_next()

                        await self.update_dashboard()
                    else:
                        embed = await create_alert_embed("Error", "Couldn't find the track on Deezer.")
                        await inter.edit_original_response(embed=embed)
                except Exception as e:
                    logging.error(f"Couldn't process Deezer link: {e}")
                    embed = await create_alert_embed("Error", f"Couldn't process Deezer link: {e}")
                    await inter.edit_original_response(embed=embed)

            case _ if 'facebook.com' in query or 'fb.watch' in query:
                url, title, duration = await self.process_url(query)
                if url:
                    await self.song_queue.put((url, title, duration))
                    logging.debug(f"Added to queue: {title} ({duration})")
                    embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
                    await inter.edit_original_response(embed=embed)

                    if not self.is_playing:
                        await self.play_next()

                    await self.update_dashboard()
                else:
                    embed = await create_alert_embed("Error", "Failed to process the Facebook video.")
                    await inter.edit_original_response(embed=embed)

            case _:
                # If it's not a YouTube link, treat it as a search query
                search_results = await self.search_youtube(query)
                if isinstance(search_results, disnake.Embed):
                    await inter.edit_original_response(embed=search_results)
                    return
                if not search_results:
                    embed = await create_alert_embed("Error", f"No results found for your search query: '{query}'. Please try a different search term.")
                    await inter.edit_original_response(embed=embed)
                    return

                # Limit search results to 5
                search_results = search_results[:5]
                view = SongChoiceView(self, search_results, inter.author.id)
                embed = self.create_embed("Search Results", "Please select a song from the menu below:", disnake.Color.blue())
                if len(search_results) == 5:
                    embed.set_footer(text="Showing first 5 results")

                await inter.edit_original_response(embed=embed, view=view)

    async def process_playlist(self, inter, playlist_items, source):
        total_tracks = len(playlist_items)
        logging.debug(f"Processing {total_tracks} tracks from {source} playlist")
        embed = await create_success_embed(f"{source} Playlist", f"Processing {total_tracks} tracks...")
        await inter.edit_original_response(embed=embed)

        processed_tracks = 0
        failed_tracks = 0
        
        def process_tracks():
            nonlocal processed_tracks, failed_tracks
            for item in playlist_items:
                try:
                    if source == "YouTube":
                        video_id = item['snippet']['resourceId']['videoId']
                        url = f"https://www.youtube.com/watch?v={video_id}"
                    elif source == "Spotify":
                        url = item
                    else:
                        continue

                    track = self._download_info(url, 0)
                    if track and track[0]:
                        asyncio.run_coroutine_threadsafe(self.song_queue.put(track), self.bot.loop)
                        processed_tracks += 1
                    else:
                        failed_tracks += 1
                except Exception as e:
                    failed_tracks += 1
                    logging.error(f"Error processing track: {e}")

        # Start processing in a separate thread
        thread = threading.Thread(target=process_tracks)
        thread.start()

        # Update progress while the thread is running
        while thread.is_alive():
            embed = await create_success_embed(f"{source} Playlist", 
                                               f"Progress: {processed_tracks + failed_tracks}/{total_tracks}\n"
                                               f"Processed: {processed_tracks}\n"
                                               f"Failed: {failed_tracks}")
            await inter.edit_original_response(embed=embed)
            await asyncio.sleep(2)  # Update every 2 seconds

        thread.join()  # Ensure the thread has finished

        if not self.is_playing and processed_tracks > 0:
            await self.play_next()

        final_embed = await create_success_embed(f"{source} Playlist Added", 
                                                 f"Total tracks: {total_tracks}\n"
                                                 f"Successfully added: {processed_tracks}\n"
                                                 f"Failed to add: {failed_tracks}")
        await inter.edit_original_response(embed=final_embed)
        await self.update_dashboard()

        # Ensure bot stays in VC
        if inter.guild.voice_client and not inter.guild.voice_client.is_connected():
            await self.join_voice_channel(inter)

        return processed_tracks

    async def process_url(self, youtube_url, retry=0):
        loop = asyncio.get_event_loop()
        try:
            # Run the download in a separate thread
            info = await loop.run_in_executor(self.download_executor, self._download_info, youtube_url, retry)
            if info:
                url, title, duration = info
                logging.debug(f"Processed URL: {youtube_url} - Title: {title}, Duration: {duration}")
                return url, title, duration
            return None, None, None
        except Exception as e:
            logging.error(f"Error processing URL: {youtube_url} - {e}")
            return None, None, None

    def _download_info(self, youtube_url, retry):
        try:
            ydl_opts = {
                'format': 'bestaudio',
                'ignoreerrors': True,
                'noplaylist': True,
                'nocheckcertificate': True,
                'quiet': True,
                'no_warnings': True,
                'default_search': 'auto',
                'source_address': '0.0.0.0',  # Bind to all available IPs
                'socket_timeout': 10,
                'external_downloader_args': ['-nostats', '-loglevel', '0'],  # Ensure no speed cap
                'concurrent_fragment_downloads': 3,  # Limit concurrent fragment downloads
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                url = info['url']
                title = info['title']
                duration = self.format_duration(info['duration'])
            return url, title, duration
        except yt_dlp.utils.DownloadError as e:
            if "403" in str(e) and retry < 3:
                logging.warning(f"Encountered 403 error, retrying... (Attempt {retry + 1})")
                return self._download_info(youtube_url, retry + 1)
            else:
                logging.error(f"Error processing URL: {youtube_url} - {e}")
                return None
        except Exception as e:
            logging.error(f"Error processing URL: {youtube_url} - {e}")
            return None

    

    async def get_youtube_playlist_items(self, playlist_id):
        playlist_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=50&playlistId={playlist_id}&key={self.youtube_api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(playlist_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['items']
                    else:
                        logging.error(f"Error fetching YouTube playlist items. Status code: {response.status}")
                        return []
        except Exception as e:
            logging.error(f"Error fetching YouTube playlist items: {e}")
            return []
    


    async def get_video_duration(self, video_id):
        video_url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'contentDetails',
            'id': video_id,
            'key': self.youtube_api_key
        }
        try:
            response = requests.get(video_url, params=params)
            response.raise_for_status()
            items = response.json().get('items', [])
            if not items:
                return "Unknown"
            
            duration = items[0]['contentDetails']['duration']
            return self.parse_duration(duration)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                error_message = f"403 Forbidden error when getting video duration: {e}"
                logging.error(error_message)
                return await create_critical_failure_embed("YouTube API Error", error_message)
        except Exception as e:
            logging.error(f"Error getting video duration: {e}")
            return "Unknown"

    def parse_duration(self, duration):
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return "Unknown"
        
        hours, minutes, seconds = match.groups()
        hours = int(hours) if hours else 0
        minutes = int(minutes) if minutes else 0
        seconds = int(seconds) if seconds else 0
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    

    async def play_next(self):
        async with self.play_next_lock:  # Ensure only one play_next call at a time
            try:
                url, title, duration = await asyncio.wait_for(self.song_queue.get(), timeout=10)
                logging.debug(f"Playing next song: {title} ({duration})")
            except asyncio.TimeoutError:
                self.is_playing = False
                self.current_song = None
                await self.update_dashboard()
                await self.schedule_disconnect()
                await update_status(self)
                logging.debug("No more songs in the queue. Stopping playback.")
                return

            self.is_playing = True
            if self.repeat and self.current_song:
                url, title, duration = self.current_song
            else:
                if self.current_song:
                    self.previous_songs.appendleft(self.current_song)

            def after_playing(error):
                if error:
                    print(f"Error playing {title}: {error}")
                # Instead of calling play_next directly, we'll set a flag
                self.is_playing = False
                # Schedule a task to check and play the next song
                asyncio.run_coroutine_threadsafe(self.check_queue(), self.bot.loop)

            voice_client = self.bot.voice_clients[0]  # Assuming bot is in only one voice channel
            try:
                if voice_client.is_playing():
                    voice_client.stop()  # Stop the current audio if it's playing
                
                ffmpeg_options = {
                    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                    'options': '-vn'  # Removed buffer size limitation
                }
                audio_source = disnake.FFmpegPCMAudio(url, **ffmpeg_options)
                voice_client.play(disnake.PCMVolumeTransformer(audio_source, volume=self.volume), after=after_playing)
                self.is_playing = True
                self.current_song = (url, title, duration)
                await self.update_dashboard()
                await update_status(self)
                self.cancel_disconnect()
                print(f"Started playing: {title} ({duration})")
            except Exception as e:
                print(f"[STOPPED] Error playing {title}: {e}")
                self.is_playing = False
                self.current_song = None
                await self.update_dashboard()
                # If there was an error, we should still try to play the next song
                asyncio.run_coroutine_threadsafe(self.check_queue(), self.bot.loop)

    async def check_queue(self):
        if not self.is_playing and not self.song_queue.empty():
            await self.play_next()
                # Remove the recursive call to play_next()
                # await self.play_next()  # Skip to the next song if there's an error

    async def schedule_disconnect(self):
        if self.disconnect_task:
            self.disconnect_task.cancel()
        self.disconnect_task = self.bot.loop.create_task(self.disconnect_after_timeout())

    def cancel_disconnect(self):
        if self.disconnect_task:
            self.disconnect_task.cancel()
            self.disconnect_task = None

    async def disconnect_after_timeout(self, timeout=300):  # 5 minutes timeout
        await asyncio.sleep(timeout)
        if not self.is_playing and self.bot.voice_clients:
            await self.bot.voice_clients[0].disconnect()
            self.dashboard_message = None
            self.dashboard_channel = None

    async def play_previous(self):
        if not self.previous_songs:
            return False

        previous_song = self.previous_songs.popleft()
        if self.current_song:
            await self.song_queue.put(self.current_song)
        await self.song_queue.put(previous_song)
        if self.is_playing:
            self.bot.voice_clients[0].stop()
        return True

    async def get_spotify_tracks(self, url):
        if self.spotify:
            logging.debug(f"Fetching Spotify tracks for URL: {url}")
            if '/track/' in url:
                track = self.spotify.track(url)
                duration = self.format_duration(track['duration_ms'] // 1000)
                logging.debug(f"Found track: {track['name']} by {track['artists'][0]['name']}")
                return [(f"{track['artists'][0]['name']} - {track['name']}", track['name'], duration)]
            elif '/album/' in url:
                album = self.spotify.album(url)
                logging.debug(f"Found album: {album['name']} by {album['artists'][0]['name']}")
                return [(f"{track['artists'][0]['name']} - {track['name']}", track['name'], self.format_duration(track['duration_ms'] // 1000)) for track in album['tracks']['items']]
            elif '/playlist/' in url:
                playlist = self.spotify.playlist(url)
                logging.debug(f"Found playlist: {playlist['name']} by {playlist['owner']['display_name']}")
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
            self.song_queue = asyncio.Queue()  # Clear the queue
            self.is_playing = False
            self.current_song = None
            self.previous_songs.clear()  # Clear previous songs
            embed = await create_success_embed("Stopped", "🛑 Stopped playing and cleared the queue.")
            await inter.response.send_message(embed=embed)
            await self.update_dashboard()
            await self.schedule_disconnect()
            await update_status(self)
        else:
            embed = await create_alert_embed("Nothing Playing", "There's nothing currently playing.")
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def skip(self, inter):
        if inter.guild.voice_client and inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()
            await self.play_next()  # Ensure play_next is called after stopping the current song
        else:
            embed = await create_alert_embed("Nothing to Skip", "There's nothing currently playing to skip.")
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def queue(self, inter):
        await self.show_queue(inter)

    async def show_queue(self, inter):
        embed = self.create_embed("Music Queue", "", disnake.Color.blue())
        
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song[1]} ({self.current_song[2]})", inline=False)
        
        if self.song_queue.empty():
            embed.description = "The queue is empty."
        else:
            queue_list = [f"{i+1}. {song[1]} ({song[2]})" for i, song in enumerate(self.song_queue._queue)]
            queue_text = "\n".join(queue_list)
            if len(queue_text) > 1024:
                queue_text = queue_text[:1021] + "..."
            embed.add_field(name="Upcoming Songs", value=queue_text, inline=False)
        
        if self.previous_songs:
            previous_list = [f"{i+1}. {song[1]} ({song[2]})" for i, song in enumerate(self.previous_songs)]
            previous_text = "\n".join(previous_list)
            if len(previous_text) > 1024:
                previous_text = previous_text[:1021] + "..."
            embed.add_field(name="Previous Songs", value=previous_text, inline=False)
        
        if isinstance(inter, disnake.ApplicationCommandInteraction):
            await inter.response.send_message(embed=embed)
        else:
            await inter.response.edit_message(embed=embed)

    @commands.slash_command()
    async def dashboard(self, inter):
        embed = self.create_dashboard_embed()
        components = self.create_dashboard_components()
        message = await inter.response.send_message(embed=embed, components=components)
        self.dashboard_message = await inter.original_message()
        self.dashboard_channel = inter.channel

    def create_embed(self, title, description, color, show_thumbnail=False):
        embed = disnake.Embed(title=title, description=description, color=color)
        if show_thumbnail:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        return embed

    def create_dashboard_embed(self):
        embed = self.create_embed("Music Dashboard", "", disnake.Color.blue(), show_thumbnail=True)
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song[1]} ({self.current_song[2]})", inline=False)
        else:
            embed.add_field(name="Now Playing", value="Nothing is currently playing", inline=False)
        embed.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)
        embed.add_field(name="Repeat", value="On" if self.repeat else "Off", inline=True)
        embed.set_image(url="https://cdn.discordapp.com/attachments/913207064136925254/1262876163962044456/Something_new.png?ex=66983094&is=6696df14&hm=beebf7e3450d353dd58fea1981d8a566fc3d3f32a5f4a106b06c7764bdb4c65c&")
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
        if inter.component.custom_id.startswith("music_"):
            await inter.response.defer(ephemeral=True)
            if inter.component.custom_id == "music_previous":
                await self.play_previous()
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
                    # Immediately play the next track
                    await self.play_next()
                else:
                    embed = await create_alert_embed("Nothing to Skip", "There's nothing currently playing to skip.")
                    await inter.followup.send(embed=embed, ephemeral=True)
            elif inter.component.custom_id == "music_volume_up":
                self.volume = min(2.0, self.volume + 0.1)
                if inter.guild.voice_client and inter.guild.voice_client.source:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_volume_down":
                self.volume = max(0.0, self.volume - 0.1)
                if inter.guild.voice_client and inter.guild.voice_client.source:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_repeat":
                self.repeat = not self.repeat
            elif inter.component.custom_id == "music_view_queue":
                await self.show_queue(inter)
            elif inter.component.custom_id == "music_add_to_playlist":
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

            await self.update_dashboard()
            await inter.edit_original_response(embed=self.create_dashboard_embed(), components=self.create_dashboard_components())
        

    @commands.Cog.listener()
    async def on_modal_submit(self, inter: disnake.ModalInteraction):
        if inter.custom_id == "add_to_playlist_modal":
            song_url_or_search = inter.text_values["song_url"]
            if song_url_or_search.startswith("http"):
                await self.play(inter, query=song_url_or_search, dashboard=True)
            else:
                search_query = song_url_or_search
                await self.play(inter, query=search_query, dashboard=True)


class SongChoiceView(disnake.ui.View):
    def __init__(self, cog, search_results, author_id):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.search_results = search_results
        self.author_id = author_id
        self.add_item(SongChoiceSelect(cog, search_results, author_id))

class SongChoiceView(disnake.ui.View):
    def __init__(self, cog, search_results, author_id):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.search_results = search_results
        self.author_id = author_id
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
import logging

# Set up logging
logging.basicConfig(filename='music_bot.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

async def update_status(self):
    if self.current_song:
        await self.bot.change_presence(activity=disnake.Activity(type=disnake.ActivityType.listening, name=self.current_song[1]))
    else:
        await self.bot.change_presence(activity=None)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queue = asyncio.Queue()
        self.current_song = None
        self.is_playing = False
        self.volume = 1.0
        self.repeat = False
        self.dashboard_message = None
        self.dashboard_channel = None
        self.previous_songs = deque(maxlen=10)
        self.disconnect_task = None
        self.download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)  # Separate executor for downloading
        self.playback_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)  # Separate executor for playback
        self.play_next_lock = asyncio.Lock()
        
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
                logging.debug("Spotify client initialized successfully")
            except Exception as e:
                logging.error(f"Error initializing Spotify client: {e}")
                self.spotify = None
        else:
            self.spotify = None

    async def join_voice_channel(self, inter):
        if not inter.author.voice:
            return await create_alert_embed("Join a voice channel first")
        
        if inter.guild.voice_client:
            if inter.guild.voice_client.channel != inter.author.voice.channel:
                await inter.guild.voice_client.move_to(inter.author.voice.channel)
            return True
        
        await inter.author.voice.channel.connect()
        return True
    

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
            response = requests.get(search_url, params=params)
            response.raise_for_status()
            results = response.json().get('items', [])
            if not results:
                logging.debug(f"No results found for query: {query}")
                return []
            
            search_results = []
            for item in results:
                video_id = item['id']['videoId']
                title = item['snippet']['title']
                duration = await self.get_video_duration(video_id)
                search_results.append({
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'title': title,
                    'duration': duration
                })
            return search_results
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                error_message = f"403 Forbidden error when searching YouTube: {e}"
                logging.error(error_message)
                return await create_critical_failure_embed("YouTube API Error", error_message)
        except Exception as e:
            logging.error(f"Error searching YouTube: {e}")
            return []

    async def get_video_duration(self, video_id):
        video_url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'contentDetails',
            'id': video_id,
            'key': self.youtube_api_key
        }
        try:
            response = requests.get(video_url, params=params)
            response.raise_for_status()
            items = response.json().get('items', [])
            if not items:
                return "Unknown"
            
            duration = items[0]['contentDetails']['duration']
            return self.parse_duration(duration)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                error_message = f"403 Forbidden error when getting video duration: {e}"
                logging.error(error_message)
                return await create_critical_failure_embed("YouTube API Error", error_message)
        except Exception as e:
            logging.error(f"Error getting video duration: {e}")
            return "Unknown"

    def parse_duration(self, duration):
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return "Unknown"
        
        hours, minutes, seconds = match.groups()
        hours = int(hours) if hours else 0
        minutes = int(minutes) if minutes else 0
        seconds = int(seconds) if seconds else 0
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    def get_queue_items(self):
        return [song[1] for song in self.song_queue._queue]

    @commands.slash_command()
    async def play(self, inter: disnake.ApplicationCommandInteraction, query: str, dashboard: bool = False):
        logging.debug(f"Received play command with query: {query}")
        if not dashboard:
            await inter.response.defer()
        else:
            await inter.response.defer(ephemeral=True)
        join_result = await self.join_voice_channel(inter)
        if isinstance(join_result, disnake.Embed):
            await inter.edit_original_response(embed=join_result)
            return

        match query:
            case _ if 'open.spotify.com' in query:
                tracks = await self.get_spotify_tracks(query)
                if tracks:
                    await self.process_playlist(inter, tracks, "Spotify")
                else:
                    embed = await create_alert_embed("Error", "Couldn't find any tracks from the Spotify link.")
                    await inter.edit_original_response(embed=embed)
                    return

            case _ if 'youtube.com/playlist' in query or 'youtube.com/watch?v=' in query and '&list=' in query:
                playlist_id = re.findall(r'list=([a-zA-Z0-9_-]+)', query)[0]
                playlist_items = await self.get_youtube_playlist_items(playlist_id)
                if playlist_items:
                    tracks_added = await self.process_playlist(inter, playlist_items, "YouTube")
                    if tracks_added > 0:
                        if not self.is_playing:
                            await self.play_next()
                    else:
                        embed = await create_alert_embed("Error", "Couldn't add any tracks from the YouTube playlist.")
                        await inter.edit_original_response(embed=embed)
                else:
                    embed = await create_alert_embed("Error", "Couldn't find any tracks from the YouTube playlist.")
                    await inter.edit_original_response(embed=embed)
                return

            case _ if re.match(r'^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+$', query):
                # If it's a YouTube link, process it directly
                url, title, duration = await self.process_url(query)
                if url:
                    await self.song_queue.put((url, title, duration))
                    logging.debug(f"Added to queue: {title} ({duration})")
                    embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
                    await inter.edit_original_response(embed=embed)

                    if not self.is_playing:
                        await self.play_next()

                    await self.update_dashboard()
                else:
                    embed = await create_alert_embed("Error", "Failed to process the YouTube video.")
                    await inter.edit_original_response(embed=embed)

            case _ if 'soundcloud.com' in query:
                try:
                    url, title, duration = await self.process_url(query)
                    if url:
                        await self.song_queue.put((url, title, duration))
                        logging.debug(f"Added to queue: {title} ({duration})")
                        embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
                        await inter.edit_original_response(embed=embed)

                        if not self.is_playing:
                            await self.play_next()

                        await self.update_dashboard()
                    else:
                        embed = await create_alert_embed("Error", "Failed to process the SoundCloud track.")
                        await inter.edit_original_response(embed=embed)
                except Exception as e:
                    logging.error(f"Couldn't process SoundCloud link: {e}")
                    embed = await create_alert_embed("Error", f"Couldn't process SoundCloud link: {e}")
                    await inter.edit_original_response(embed=embed)

            case _ if 'deezer.com' in query or 'deezer.page.link' in query:
                try:
                    track_id = query.split('/')[-1]
                    track_info = await self.get_deezer_track_info(track_id)
                    if track_info:
                        title, artist, preview_url, duration = track_info
                        await self.song_queue.put((preview_url, f"{artist} - {title}", duration))
                        logging.debug(f"Added to queue: {artist} - {title} ({duration})")
                        embed = await create_success_embed("Added to Queue", f"🎵 {artist} - {title} ({duration})")
                        await inter.edit_original_response(embed=embed)

                        if not self.is_playing:
                            await self.play_next()

                        await self.update_dashboard()
                    else:
                        embed = await create_alert_embed("Error", "Couldn't find the track on Deezer.")
                        await inter.edit_original_response(embed=embed)
                except Exception as e:
                    logging.error(f"Couldn't process Deezer link: {e}")
                    embed = await create_alert_embed("Error", f"Couldn't process Deezer link: {e}")
                    await inter.edit_original_response(embed=embed)

            case _ if 'facebook.com' in query or 'fb.watch' in query:
                url, title, duration = await self.process_url(query)
                if url:
                    await self.song_queue.put((url, title, duration))
                    logging.debug(f"Added to queue: {title} ({duration})")
                    embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
                    await inter.edit_original_response(embed=embed)

                    if not self.is_playing:
                        await self.play_next()

                    await self.update_dashboard()
                else:
                    embed = await create_alert_embed("Error", "Failed to process the Facebook video.")
                    await inter.edit_original_response(embed=embed)

            case _:
                # If it's not a YouTube link, treat it as a search query
                search_results = await self.search_youtube(query)
                if isinstance(search_results, disnake.Embed):
                    await inter.edit_original_response(embed=search_results)
                    return
                if not search_results:
                    embed = await create_alert_embed("Error", f"No results found for your search query: '{query}'. Please try a different search term.")
                    await inter.edit_original_response(embed=embed)
                    return

                # Limit search results to 5
                search_results = search_results[:5]
                view = SongChoiceView(self, search_results, inter.author.id)
                embed = self.create_embed("Search Results", "Please select a song from the menu below:", disnake.Color.blue())
                if len(search_results) == 5:
                    embed.set_footer(text="Showing first 5 results")

                await inter.edit_original_response(embed=embed, view=view)

    async def process_playlist(self, inter, playlist_items, source):
        total_tracks = len(playlist_items)
        logging.debug(f"Processing {total_tracks} tracks from {source} playlist")
        embed = await create_success_embed(f"{source} Playlist", f"Processing {total_tracks} tracks...")
        await inter.edit_original_response(embed=embed)

        async def process_track(item):
            if source == "YouTube":
                video_id = item['snippet']['resourceId']['videoId']
                url = f"https://www.youtube.com/watch?v={video_id}"
            elif source == "Spotify":
                url = item
            else:
                return None

            track = await self.process_url(url)
            if track[0]:
                await self.song_queue.put(track)
                return track
            return None

        tasks = [process_track(item) for item in playlist_items]
        processed_tracks = 0
        failed_tracks = 0

        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            try:
                track = await task
                if track:
                    processed_tracks += 1
                else:
                    failed_tracks += 1
            except Exception as e:
                failed_tracks += 1
                logging.error(f"Error processing track {i}/{total_tracks}: {e}")

            if i % 5 == 0 or i == total_tracks:  # Update progress every 5 tracks or at the end
                embed = await create_success_embed(f"{source} Playlist", 
                                                   f"Progress: {i}/{total_tracks}\n"
                                                   f"Processed: {processed_tracks}\n"
                                                   f"Failed: {failed_tracks}")
                await inter.edit_original_response(embed=embed)

        if not self.is_playing and processed_tracks > 0:
            await self.play_next()

        final_embed = await create_success_embed(f"{source} Playlist Added", 
                                                 f"Total tracks: {total_tracks}\n"
                                                 f"Successfully added: {processed_tracks}\n"
                                                 f"Failed to add: {failed_tracks}")
        await inter.edit_original_response(embed=final_embed)
        await self.update_dashboard()

        # Ensure bot stays in VC
        if inter.guild.voice_client and not inter.guild.voice_client.is_connected():
            await self.join_voice_channel(inter)

        return processed_tracks

    

    

    async def process_url(self, youtube_url, retry=0):
        loop = asyncio.get_event_loop()
        try:
            # Run the download in a separate thread
            info = await loop.run_in_executor(self.download_executor, self._download_info, youtube_url, retry)
            if info:
                url, title, duration = info
                logging.debug(f"Processed URL: {youtube_url} - Title: {title}, Duration: {duration}")
                return url, title, duration
            return None, None, None
        except Exception as e:
            logging.error(f"Error processing URL: {youtube_url} - {e}")
            return None, None, None

    def _download_info(self, youtube_url, retry):
        try:
            ydl_opts = {
                'format': 'bestaudio',
                'ignoreerrors': True,
                'noplaylist': True,
                'nocheckcertificate': True,
                'quiet': True,
                'no_warnings': True,
                'default_search': 'auto',
                'source_address': '0.0.0.0',  # Bind to all available IPs
                'socket_timeout': 10,
                'external_downloader_args': ['-nostats', '-loglevel', '0'],  # Ensure no speed cap
                'concurrent_fragment_downloads': 3,  # Limit concurrent fragment downloads
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                url = info['url']
                title = info['title']
                duration = self.format_duration(info['duration'])
            return url, title, duration
        except yt_dlp.utils.DownloadError as e:
            if "403" in str(e) and retry < 3:
                logging.warning(f"Encountered 403 error, retrying... (Attempt {retry + 1})")
                return self._download_info(youtube_url, retry + 1)
            else:
                logging.error(f"Error processing URL: {youtube_url} - {e}")
                return None
        except Exception as e:
            logging.error(f"Error processing URL: {youtube_url} - {e}")
            return None

    

    async def get_youtube_playlist_items(self, playlist_id):
        playlist_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=50&playlistId={playlist_id}&key={self.youtube_api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(playlist_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['items']
                    else:
                        logging.error(f"Error fetching YouTube playlist items. Status code: {response.status}")
                        return []
        except Exception as e:
            logging.error(f"Error fetching YouTube playlist items: {e}")
            return []
    


    async def get_video_duration(self, video_id):
        video_url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'contentDetails',
            'id': video_id,
            'key': self.youtube_api_key
        }
        try:
            response = requests.get(video_url, params=params)
            response.raise_for_status()
            items = response.json().get('items', [])
            if not items:
                return "Unknown"
            
            duration = items[0]['contentDetails']['duration']
            return self.parse_duration(duration)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                error_message = f"403 Forbidden error when getting video duration: {e}"
                logging.error(error_message)
                return await create_critical_failure_embed("YouTube API Error", error_message)
        except Exception as e:
            logging.error(f"Error getting video duration: {e}")
            return "Unknown"

    def parse_duration(self, duration):
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return "Unknown"
        
        hours, minutes, seconds = match.groups()
        hours = int(hours) if hours else 0
        minutes = int(minutes) if minutes else 0
        seconds = int(seconds) if seconds else 0
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    



    async def play_next(self):
        async with self.play_next_lock:  # Ensure only one play_next call at a time
            try:
                url, title, duration = await asyncio.wait_for(self.song_queue.get(), timeout=10)
                logging.debug(f"Playing next song: {title} ({duration})")
            except asyncio.TimeoutError:
                self.is_playing = False
                self.current_song = None
                await self.update_dashboard()
                await self.schedule_disconnect()
                await update_status(self)
                logging.debug("No more songs in the queue. Stopping playback.")
                return

            self.is_playing = True
            if self.repeat and self.current_song:
                url, title, duration = self.current_song
            else:
                if self.current_song:
                    self.previous_songs.appendleft(self.current_song)

            def play_audio():
                voice_client = self.bot.voice_clients[0]  # Assuming bot is in only one voice channel
                try:
                    if voice_client.is_playing():
                        voice_client.stop()  # Stop the current audio if it's playing
                    
                    ffmpeg_options = {
                        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx',
                        'options': '-vn -bufsize 16M -probesize 64M -analyzeduration 2147483647'
                    }
                    audio_source = disnake.FFmpegPCMAudio(url, **ffmpeg_options)
                    voice_client.play(disnake.PCMVolumeTransformer(audio_source, volume=self.volume), after=after_playing)
                    logging.debug(f"Started playing: {title} ({duration})")
                except Exception as e:
                    logging.error(f"[STOPPED] Error playing {title}: {e}")
                    self.bot.loop.create_task(self.handle_playback_error())

            def after_playing(error):
                if error:
                    print(f"Error playing {title}: {error}")
                # Instead of calling play_next directly, we'll set a flag
                self.is_playing = False
                # Check the queue once and then wait for the next play_next call
                self.bot.loop.call_soon_threadsafe(asyncio.create_task, self.check_queue())

            # Start playback in a separate thread
            threading.Thread(target=play_audio, daemon=True).start()

            self.current_song = (url, title, duration)
            await self.update_dashboard()
            await update_status(self)
            self.cancel_disconnect()

    async def handle_playback_error(self):
        logging.debug("Handling playback error")
        self.is_playing = False
        self.current_song = None
        await self.update_dashboard()
        await self.check_queue()

    async def handle_playback_finished(self):
        logging.debug("Handling playback finished")
        self.is_playing = False
        await self.check_queue()

    async def check_queue(self):
        logging.debug("Checking queue")
        if not self.song_queue.empty():
            await self.download_next_song()
            await self.play_next()
        else:
            self.is_playing = False
            self.current_song = None
            await self.update_dashboard()
            await self.schedule_disconnect()
            await update_status(self)

    async def download_next_song(self):
        try:
            url, title, duration = await self.song_queue.get()
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(self.download_executor, self._download_info, url, 0)
            if info:
                url, title, duration = info
                await self.song_queue.put((url, title, duration))
        except Exception as e:
            logging.error(f"Error downloading next song: {e}")

   
            
    async def schedule_disconnect(self):
        if self.disconnect_task:
            self.disconnect_task.cancel()
        self.disconnect_task = self.bot.loop.create_task(self.disconnect_after_timeout())

    def cancel_disconnect(self):
        if self.disconnect_task:
            self.disconnect_task.cancel()
            self.disconnect_task = None

    async def disconnect_after_timeout(self, timeout=600):  # 10 minutes timeout
        await asyncio.sleep(timeout)
        if not self.is_playing and self.bot.voice_clients:
            await self.bot.voice_clients[0].disconnect()
            self.dashboard_message = None
            self.dashboard_channel = None

    async def play_previous(self):
        if not self.previous_songs:
            return False

        previous_song = self.previous_songs.popleft()
        if self.current_song:
            await self.song_queue.put(self.current_song)
        await self.song_queue.put(previous_song)
        if self.is_playing:
            self.bot.voice_clients[0].stop()
        return True

    async def get_spotify_tracks(self, url):
        if self.spotify:
            logging.debug(f"Fetching Spotify tracks for URL: {url}")
            if '/track/' in url:
                track = self.spotify.track(url)
                duration = self.format_duration(track['duration_ms'] // 1000)
                logging.debug(f"Found track: {track['name']} by {track['artists'][0]['name']}")
                return [(f"{track['artists'][0]['name']} - {track['name']}", track['name'], duration)]
            elif '/album/' in url:
                album = self.spotify.album(url)
                logging.debug(f"Found album: {album['name']} by {album['artists'][0]['name']}")
                return [(f"{track['artists'][0]['name']} - {track['name']}", track['name'], self.format_duration(track['duration_ms'] // 1000)) for track in album['tracks']['items']]
            elif '/playlist/' in url:
                playlist = self.spotify.playlist(url)
                logging.debug(f"Found playlist: {playlist['name']} by {playlist['owner']['display_name']}")
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
            self.song_queue = asyncio.Queue()  # Clear the queue
            self.is_playing = False
            self.current_song = None
            self.previous_songs.clear()  # Clear previous songs
            embed = await create_success_embed("Stopped", "🛑 Stopped playing and cleared the queue.")
            await inter.response.send_message(embed=embed)
            await self.update_dashboard()
            await self.schedule_disconnect()
            await update_status(self)
        else:
            embed = await create_alert_embed("Nothing Playing", "There's nothing currently playing.")
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def skip(self, inter):
        if inter.guild.voice_client and inter.guild.voice_client.is_playing():
            inter.guild.voice_client.stop()
            await self.play_next()  # Ensure play_next is called after stopping the current song
        else:
            embed = await create_alert_embed("Nothing to Skip", "There's nothing currently playing to skip.")
            await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command()
    async def queue(self, inter):
        await self.show_queue(inter)

    async def show_queue(self, inter):
        embed = self.create_embed("Music Queue", "", disnake.Color.blue())
        
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song[1]} ({self.current_song[2]})", inline=False)
        
        if self.song_queue.empty():
            embed.description = "The queue is empty."
        else:
            queue_list = [f"{i+1}. {song[1]} ({song[2]})" for i, song in enumerate(self.song_queue._queue)]
            queue_text = "\n".join(queue_list)
            if len(queue_text) > 1024:
                queue_text = queue_text[:1021] + "..."
            embed.add_field(name="Upcoming Songs", value=queue_text, inline=False)
        
        if self.previous_songs:
            previous_list = [f"{i+1}. {song[1]} ({song[2]})" for i, song in enumerate(self.previous_songs)]
            previous_text = "\n".join(previous_list)
            if len(previous_text) > 1024:
                previous_text = previous_text[:1021] + "..."
            embed.add_field(name="Previous Songs", value=previous_text, inline=False)
        
        if isinstance(inter, disnake.ApplicationCommandInteraction):
            await inter.response.send_message(embed=embed)
        else:
            await inter.response.edit_message(embed=embed)

    @commands.slash_command()
    async def dashboard(self, inter):
        embed = self.create_dashboard_embed()
        components = self.create_dashboard_components()
        message = await inter.response.send_message(embed=embed, components=components)
        self.dashboard_message = await inter.original_message()
        self.dashboard_channel = inter.channel

    def create_embed(self, title, description, color, show_thumbnail=False):
        embed = disnake.Embed(title=title, description=description, color=color)
        if show_thumbnail:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        return embed

    def create_dashboard_embed(self):
        embed = self.create_embed("Music Dashboard", "", disnake.Color.blue(), show_thumbnail=True)
        if self.current_song:
            embed.add_field(name="Now Playing", value=f"🎵 {self.current_song[1]} ({self.current_song[2]})", inline=False)
        else:
            embed.add_field(name="Now Playing", value="Nothing is currently playing", inline=False)
        embed.add_field(name="Volume", value=f"{int(self.volume * 100)}%", inline=True)
        embed.add_field(name="Repeat", value="On" if self.repeat else "Off", inline=True)
        embed.set_image(url="https://cdn.discordapp.com/attachments/913207064136925254/1262876163962044456/Something_new.png?ex=66983094&is=6696df14&hm=beebf7e3450d353dd58fea1981d8a566fc3d3f32a5f4a106b06c7764bdb4c65c&")
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
        if inter.component.custom_id.startswith("music_"):
            await inter.response.defer(ephemeral=True)
            if inter.component.custom_id == "music_previous":
                await self.play_previous()
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
                    # Immediately play the next track
                    await self.play_next()
                else:
                    embed = await create_alert_embed("Nothing to Skip", "There's nothing currently playing to skip.")
                    await inter.followup.send(embed=embed, ephemeral=True)
            elif inter.component.custom_id == "music_volume_up":
                self.volume = min(2.0, self.volume + 0.1)
                if inter.guild.voice_client and inter.guild.voice_client.source:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_volume_down":
                self.volume = max(0.0, self.volume - 0.1)
                if inter.guild.voice_client and inter.guild.voice_client.source:
                    inter.guild.voice_client.source.volume = self.volume
            elif inter.component.custom_id == "music_repeat":
                self.repeat = not self.repeat
            elif inter.component.custom_id == "music_view_queue":
                await self.show_queue(inter)
            elif inter.component.custom_id == "music_add_to_playlist":
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

            await self.update_dashboard()
            await inter.edit_original_response(embed=self.create_dashboard_embed(), components=self.create_dashboard_components())
        

    @commands.Cog.listener()
    async def on_modal_submit(self, inter: disnake.ModalInteraction):
        if inter.custom_id == "add_to_playlist_modal":
            song_url_or_search = inter.text_values["song_url"]
            if song_url_or_search.startswith("http"):
                await self.play(inter, query=song_url_or_search, dashboard=True)
            else:
                search_query = song_url_or_search
                await self.play(inter, query=search_query, dashboard=True)


class SongChoiceView(disnake.ui.View):
    def __init__(self, cog, search_results, author_id):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.search_results = search_results
        self.author_id = author_id
        self.add_item(SongChoiceSelect(cog, search_results, author_id))

class SongChoiceView(disnake.ui.View):
    def __init__(self, cog, search_results, author_id):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.search_results = search_results
        self.author_id = author_id
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
        url = selected_song['url']
        title = selected_song['title']
        duration = selected_song['duration']
        url, title, duration = await self.cog.process_url(url)
        await self.cog.song_queue.put((url, title, duration))
        embed = await create_success_embed("Added to Queue", f"🎵 {title} ({duration})")
        await inter.edit_original_response(embed=embed, view=None)

        if not self.cog.is_playing:
            await self.cog.play_next()

        await self.cog.update_dashboard()

