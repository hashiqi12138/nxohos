# Where things are: the repository (and the nxnative subtree in it), the player's own folder (settings, the APK signing key).
import os, sys
from pathlib import Path

REPO = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent   # the bundle: nxnative/ beside nx2android.exe
NXNATIVE = REPO / 'nxnative'
DM = NXNATIVE / 'dm'
LOCAL_TOOLS = REPO / 'tools'   # a developer's toolchains next to the repository (not committed); the player installs their own


def user_dir():
    """The player's nx2android folder: %APPDATA%\\nx2android on Windows, ~/Library/Application Support/nx2android on a Mac, ~/.config/nx2android elsewhere.
    NX2A_HOME overrides it (tests)"""
    if os.environ.get('NX2A_HOME'): d = Path(os.environ['NX2A_HOME'])
    elif os.name == 'nt': d = Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'nx2android'
    elif sys.platform == 'darwin': d = Path.home() / 'Library/Application Support/nx2android'
    else: d = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'nx2android'
    d.mkdir(parents=True, exist_ok=True)
    return d


def player_tools():   # the toolchains `porter tools --download` fetched for the player: android-ndk-r27c/, dotnet/, platform-tools/
    return user_dir() / 'toolchains'


def signing_key():   # one key per player: every app they port keeps its signer, so a new port installs over the last one
    return user_dir() / 'signing-key.json'
