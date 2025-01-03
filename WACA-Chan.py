### THIS IS A BETA BUILD. NOT FOR PUBLIC RELEASE. ###

import os
import sys
import datetime
import webbrowser
import requests
import json
import subprocess
from pathlib import Path
import logging

logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    filename='music_bot.log',
                    filemode='w')
# Configuration
CONFIG = {
    "verbose": True,
    "terminalMode": True,
    "terminalAsk": True,
    "betaMode": False
}

def waca_sign(testing):
    sign = r'''
                                                                                                       
                                                                        ,,                             
`7MMF'     A     `7MF' db       .g8"""bgd     db            .g8"""bgd `7MM                             
  `MA     ,MA     ,V  ;MM:    .dP'     `M    ;MM:         .dP'     `M   MM                             
   VM:   ,VVM:   ,V  ,V^MM.   dM'       `   ,V^MM.        dM'       `   MMpMMMb.   ,6"Yb.  `7MMpMMMb.  
    MM.  M' MM.  M' ,M  `MM   MM           ,M  `MM        MM            MM    MM  8)   MM    MM    MM  
    `MM A'  `MM A'  AbmmmqMA  MM.          AbmmmqMA mmmmm MM.           MM    MM   ,pm9MM    MM    MM  
     :MM;    :MM;  A'     VML `Mb.     ,' A'     VML      `Mb.     ,'   MM    MM  8M   MM    MM    MM  
      VF      VF .AMA.   .AMMA. `"bmmmd'.AMA.   .AMMA.      `"bmmmd'  .JMML  JMML.`Moo9^Yo..JMML  JMML.
                                                                                                       
                                                                                                       
    '''
    if testing:
        sign += "\n" + "BETA".center(len(sign.split('\n')[1]))
    return sign
def install_ffmpeg():
    # Check if ffmpeg is in the current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_path = os.path.join(current_dir, "ffmpeg")
    if sys.platform == 'win32':
        ffmpeg_path += ".exe"

    if os.path.exists(ffmpeg_path):
        print("ffmpeg found in the current directory.")
        # Add the current directory to PATH
        os.environ["PATH"] = current_dir + os.pathsep + os.environ["PATH"]
    else:
        try:
            subprocess.run(["ffmpeg", "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("ffmpeg is already installed.")
        except:
            print("Installing ffmpeg...")
            if sys.platform.startswith('linux'):
                subprocess.run(["sudo", "apt", "update"], check=True)
                subprocess.run(["sudo", "apt", "install", "ffmpeg", "-y"], check=True)
            elif sys.platform == 'darwin':
                subprocess.run(["brew", "install", "ffmpeg"], check=True)
            elif sys.platform == 'win32':
                ffmpeg_folder = os.path.join(current_dir, "ffmpeg")
                if os.path.exists(ffmpeg_folder) and os.path.isdir(ffmpeg_folder):
                    print("FFmpeg folder found in the WACA-Chan directory.")
                    # Check for bin folder in all subdirectories
                    for root, dirs, files in os.walk(ffmpeg_folder):
                        if 'bin' in dirs:
                            ffmpeg_bin_path = os.path.join(root, 'bin')
                            print(f"FFmpeg bin folder found at: {ffmpeg_bin_path}")
                            print("Adding FFmpeg to PATH...")
                            os.environ["PATH"] += os.pathsep + ffmpeg_bin_path
                            break
                    else:
                        print("FFmpeg bin folder not found in any subdirectory.")
                else:
                    print("Downloading FFmpeg for Windows...")
                    ffmpeg_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
                    r = requests.get(ffmpeg_url)
                    with open("ffmpeg.zip", "wb") as f:
                        f.write(r.content)
                    
                    print("Extracting FFmpeg...")
                    import zipfile
                    with zipfile.ZipFile("ffmpeg.zip", "r") as zip_ref:
                        zip_ref.extractall("ffmpeg")
                    
                    print("Adding FFmpeg to PATH...")
                    ffmpeg_path = os.path.abspath("ffmpeg/ffmpeg-master-latest-win64-gpl/bin")
                    os.environ["PATH"] += os.pathsep + ffmpeg_path
                    
                    print("Cleaning up...")
                    os.remove("ffmpeg.zip")
            else:
                print("Unsupported operating system. Please install ffmpeg manually.")

    # Verify installation
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("FFmpeg installation verified.")
    except:
        print("FFmpeg installation failed. Please install it manually.")
        sys.exit(1)
def setup():
    def install_pip_if_needed():
        try:
            subprocess.run(["pip", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
            print("Installing pip...")
            subprocess.run(["curl", "https://bootstrap.pypa.io/get-pip.py", "-o", "get-pip.py"], check=True)
            subprocess.run(["python3", "get-pip.py"], check=True)

    def install_dependencies():
        path = Path("./requirements.txt").resolve()
        result = subprocess.run(["pip", "install", "-r", str(path)], check=True)
        if result.returncode == 0:
            print("Setup Complete! Welcome to WACA-Chan...")
        else:
            print("Error installing dependencies. Please check the logs.")

    def update_spotipy():
        print("Updating spotipy...")
        subprocess.run(["pip", "install", "--upgrade", "spotipy"], check=True)

    def pin_requests_version():
        print("Pinning requests version...")
        subprocess.run(["pip", "install", "requests==2.25.1"], check=True)

    install_pip_if_needed()
    install_dependencies()
    update_spotipy()
    pin_requests_version()
    install_ffmpeg()

def terminal():
    install_ffmpeg()
    commands = {
        "whatis": lambda query: search_google(query),
        "google": lambda query: webbrowser.open(f"https://www.google.com/search?q={query}"),
        "youtube": lambda query: webbrowser.open(f"https://www.youtube.com/results?search_query={query}"),
        "open": lambda url: webbrowser.open(url),
        "start": lambda *args: startup(**parse_start_args(args)),
        "qping": lambda address: os.system(f"ping {'- c 1' if os.name != 'nt' else '/n 1'} {address}"),
        "ping": lambda address, count=5: os.system(f"ping {'- c' if os.name != 'nt' else '/n'} {count} {address}"),
        "about": lambda: print(waca_sign(CONFIG["testingMode"])),
        "clear": lambda: os.system("clear" if os.name != "nt" else "cls"),
        "find": lambda file: os.system(f"{'find' if os.name != 'nt' else 'dir'} {file}"),
        "date": lambda: print(datetime.datetime.now().strftime("Current Date: %m/%d/%Y")),
        "time": lambda: print(datetime.datetime.now().strftime("Current Time: %H:%M:%S")),
        "setup": setup,
        "backup": lambda src, dst: os.system(f"{'cp' if os.name != 'nt' else 'copy'} {src} {dst}"),
        "delete": lambda file: os.system(f"{'rm' if os.name != 'nt' else 'del'} {file}"),
        "move": lambda src, dst: os.system(f"{'mv' if os.name != 'nt' else 'move'} {src} {dst}"),
        "run": lambda file: os.system(f"{'python3' if os.name != 'nt' else 'python'} {file}"),
        "pshell": lambda: os.system("python3" if os.name != "nt" else "python"),
        "testimport": lambda module: __import__(module),
        "testsystem": lambda args: startup(**parse_start_args(args), testStart=True)
    }

    while True:
        command = input("WACA-Chan: ").strip().split()
        if not command:
            continue
        if command[0] in ["exit", "quit"]:
            print("Quitting WACA-Chan...")
            break
        if command[0] in commands:
            try:
                commands[command[0]](*command[1:])
            except Exception as e:
                print(f"Error executing command: {e}")
        else:
            print("Unknown Command")

def search_google(query):
    url = f"https://www.googleapis.com/customsearch/v1/search?key=AIzaSyBqoXqD51lRSh_V_5spz1cwrsreL_NK5cs&cx=f578d2388baaa4ec8&q={query}"
    response = requests.get(url)
    data = json.loads(response.content)
    return data["items"][0]["snippet"] if data["items"] else "Sorry, I couldn't find an answer to your question."

def parse_start_args(args):
    return {
        "testingMode": "-t" in args,
        "verbose": "-v" in args
    }

def startup(testingMode=False, testStart=False, verbose=True):
    def vprint(text):
        if verbose:
            print(text)
    print(waca_sign(testingMode))  # Print the WACA-Chan sign on startup
    print("Starting WACA-Chan...")
    print("Importing Modules...")
    vprint("Importing disnake...")
    import disnake
    from disnake.ext import commands, tasks
    vprint("Importing disnake complete")
    vprint("Importing music...")
    import music
    vprint("Importing music complete")
    command_sync_flags = commands.CommandSyncFlags.default()
    command_sync_flags.sync_commands_debug = True
    activity = disnake.Activity(name='over NETWACA', type=disnake.ActivityType.watching)
    client = disnake.Client(activity=activity)
    bot = commands.Bot(
        command_prefix='!',
        command_sync_flags=command_sync_flags,
        intents=disnake.Intents.all()
        )
    
    bot.add_cog(music.Music(bot))
    if testingMode:
        bot.run(CONFIG["testingToken"])
    else:
        bot.run(CONFIG["token"])
    print("Completed! All tasks have completed. Beginning WACA-Chan...")
    pass

if __name__ == "__main__":
    if CONFIG["terminalMode"] and not CONFIG["terminalAsk"]:
        terminal()
    elif CONFIG["terminalAsk"]:
        choice = input("Would you like to start WACA-Chan in Terminal mode? [y/n]: ").lower()
        if choice == "y":
            terminal()
        elif choice == "n":
            startup(CONFIG["testingMode"], False, CONFIG["verbose"])
        else:
            print("Invalid choice. Exiting.")
    else:
        startup(CONFIG["testingMode"], False, CONFIG["verbose"])
