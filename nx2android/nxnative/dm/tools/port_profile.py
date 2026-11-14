#!/usr/bin/env python3
# A game profile's port keys: dm/games/<id>/port.tsv (key <TAB> value, # comments), beside the profile's game.tsv. A profile without one has not been ported to HarmonyOS.
# Keys:
#   status       playable | wip (plays, but parts are missing) | boots | experimental
#   tested       <version> <device>: what the status was checked on
#   package      the app's package name (default org.nx2a.t<title ID in lowercase>): recorded in the data pack's manifest (the HAP itself installs as dev.eden_emu.ohos)
#   orientation  landscape (default) | any
#   requires     device features, separated by spaces: bcn (BC texture compression)
#   setting      <name>=<value>, one row each: the pause menu's defaults on first launch (overlay, rumble, docked, fps, opacity)
#   env          <KEY>=<VALUE>, one row each: host switches the game needs (the data pack's env.txt)
# Usage: port_profile.py list; port_profile.py <id> [<key>] (the profile's keys, or one key's value)
# From Python: import port_profile; p = port_profile.load(<id>) (None without port.tsv); p.status, p.package, p.settings, p.env
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'games'))
import game   # noqa: E402  (the profile folders, DM_GAMES_DIR included)

STATUS = ('playable', 'wip', 'boots', 'experimental')
SETTINGS = {'overlay': ('0', '1'), 'rumble': ('0', '1'), 'docked': ('0', '1'), 'fps': ('0', '1'),
            'opacity': tuple(str(v) for v in range(20, 81, 5))}
REQUIRES = ('bcn',)
SINGLE = ('status', 'tested', 'package', 'orientation', 'requires')
REPEAT = ('setting', 'env')


class ProfileError(Exception):
    pass


class PortProfile:
    def __init__(self, g):
        self.game, self.id, self.path = g, g.id, g.dir / 'port.tsv'
        self.rows = []
        for n, line in enumerate(self.path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip() or line.lstrip().startswith('#'): continue
            k, _, v = line.partition('\t'); k, v = k.strip(), v.strip()
            if k not in SINGLE + REPEAT: raise ProfileError(f'{self.path}:{n}: unknown key {k!r} (keys: {", ".join(SINGLE + REPEAT)})')
            if k in SINGLE and any(r[0] == k for r in self.rows): raise ProfileError(f'{self.path}:{n}: {k} given twice')
            self.rows.append((k, v))
        self.check()

    def get(self, key, default=''):
        return next((v for k, v in self.rows if k == key), default)

    def values(self, key):
        return [v for k, v in self.rows if k == key]

    @property
    def name(self): return self.game.get('name', self.id)
    @property
    def status(self): return self.get('status', 'experimental')
    @property
    def tested(self): return self.get('tested')
    @property
    def package(self): return self.get('package') or f'org.nx2a.t{self.id.lower()}'
    @property
    def landscape(self): return self.get('orientation', 'landscape') == 'landscape'
    @property
    def requires(self): return self.get('requires').split()

    @property
    def settings(self):   # {'overlay': '1', ...}: the defaults the data pack's settings.txt starts with
        return dict(v.split('=', 1) for v in self.values('setting'))

    @property
    def env(self):   # ['KEY=VALUE', ...]
        return self.values('env')

    def check(self):
        bad = []
        if self.status not in STATUS: bad.append(f'status {self.status!r}: one of {", ".join(STATUS)}')
        if not re.fullmatch(r'[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+', self.package): bad.append(f'package {self.package!r} is not a package name')
        if self.get('orientation', 'landscape') not in ('landscape', 'any'): bad.append('orientation: landscape or any')
        bad += [f'requires {r!r}: one of {", ".join(REQUIRES)}' for r in self.requires if r not in REQUIRES]
        for v in self.values('setting'):
            k, eq, val = v.partition('=')
            if not eq or k not in SETTINGS or val not in SETTINGS[k]: bad.append(f'setting {v!r}: ' + ', '.join(f'{n}={"|".join(vs)}' for n, vs in SETTINGS.items()))
        bad += [f'env {v!r}: KEY=VALUE' for v in self.values('env') if not re.fullmatch(r'[A-Z_][A-Z0-9_]*=.*', v)]
        if bad: raise ProfileError(f'{self.path}: ' + '; '.join(bad))


def load(gid):
    """The port profile of a game profile (a title ID or a folder), None when the game has no port.tsv"""
    g = game.load(gid)
    return PortProfile(g) if (g.dir / 'port.tsv').is_file() else None


def all_profiles():   # every game profile, with its port profile or None
    return [(g, PortProfile(g) if (g.dir / 'port.tsv').is_file() else None) for g in game.all_games()]


if __name__ == '__main__':
    a = sys.argv[1:]
    try:
        if a == ['list']:
            for g, p in all_profiles():
                if p: print(f'{g.id}\t{p.status}\t{p.package}\t{g.get("name", "")}')
        elif len(a) in (1, 2):
            p = load(a[0])
            if not p: sys.exit(f'{a[0]} has no port.tsv: not ported to HarmonyOS')
            if len(a) == 2: print(getattr(p, a[1]) if a[1] in ('status', 'package', 'tested') else ' '.join(p.values(a[1])))
            else:
                for k, v in p.rows: print(f'{k}\t{v}')
        else:
            sys.exit('usage: port_profile.py list | <id> [<key>]')
    except ProfileError as e:
        sys.exit(f'[RED] {e}')