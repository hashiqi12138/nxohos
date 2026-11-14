# Gameplay detector for boot_to_gameplay.sh, NEO: The World Ends with You. Its title and menus keep the left ~40% of the screen mostly black (the logo
# over black); gameplay fills it with the city. A plain brightness test was not enough: the title's character art is bright. Write one of these per game
# from two screenshots, one of the title and one of gameplay, and check it against both before trusting it.
import numpy as np


def is_gameplay(rgb):
    g = rgb.astype(float).mean(axis=2); h, w = g.shape
    left = g[int(h * .15):int(h * .85), int(w * .02):int(w * .4)]
    return (left < 20).mean() < 0.25 and left.mean() > 55
