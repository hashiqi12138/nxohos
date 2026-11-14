# The games the tool knows: every game profile in nxnative/dm/games, and whether it has been ported to HarmonyOS (its port.tsv, dm/tools/port_profile.py).
from dataclasses import dataclass, asdict

from porter.upstream import port_profile, game


@dataclass
class Entry:
    id: str
    name: str
    ported: bool         # has a port.tsv
    status: str          # playable / wip / boots / experimental; 'not ported' without port.tsv
    tested: str
    package: str

    def as_dict(self): return asdict(self)


def entry(g, p):
    if p: return Entry(g.id, g.get('name', g.id), True, p.status, p.tested, p.package)
    return Entry(g.id, g.get('name', g.id), False, 'not ported', '', f'org.nx2a.t{g.id.lower()}')


def all_entries(ported_only=True):
    rows = [entry(g, p) for g, p in port_profile.all_profiles()]
    return [r for r in rows if r.ported or not ported_only]


def by_title(tid):
    """(game profile, port profile or None) of a base game's title ID; (None, None) when no profile matches"""
    g = game.by_title(tid)
    if not g: return None, None
    return g, (port_profile.PortProfile(g) if (g.dir / 'port.tsv').is_file() else None)


def load(gid):
    """The port profile of a game profile id (ProfileError when it has none)"""
    p = port_profile.load(gid)
    if not p: raise port_profile.ProfileError(f'{gid} has no port.tsv: it is not ported to HarmonyOS')
    return p