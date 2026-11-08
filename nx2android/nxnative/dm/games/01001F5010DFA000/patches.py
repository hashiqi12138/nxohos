# Pokémon Legends: Arceus (01001F5010DFA000): the rewriter's patches of this game's own builds. dm/tools/patch_nso.py loads every profile's patches.py and applies those of a module's NSO build ID;
# CUTS as described there ("Registered patches"). Patches of Nintendo SDK module builds are in dm/tools/sdk_patches.py
CUTS = {
  # Pokémon Legends: Arceus 1.1.1 (v262144) main: 60 fps (30 on the Switch). The start-up code builds the display's settings on the stack (main+0x277d7xx) and hands them to the
  # display setup (main+0x29d6a64 → main+0x2aa24c4), which sets the window's present interval to that word + 1 (main+0x2aa2788). The word is 1: interval 2, 30 fps
  'aee8f150dda1b5a838806e1a5ea6827a': [
    (0x277d838, '280080d2', '080080d2', 'display settings: present interval − 1 mov x8,#1 → mov x8,#0 (interval 1)'),
  ],
}
