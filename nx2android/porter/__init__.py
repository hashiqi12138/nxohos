# porter: the port pipeline. A player's game files (NSP / XCI, or NSZ / XCZ) + their prod.keys + a game profile → a HarmonyOS HAP and the game's data pack (docs/ARCHITECTURE.md).
#   python -m porter doctor | profiles | inspect <folder> | port <folder> -k <prod.keys> | tools | config
__version__ = '0.2.0'
