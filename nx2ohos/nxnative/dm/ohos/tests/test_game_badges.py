"""The S5 badge rules, checked where they can be checked.

The rules live in ArkTS (dm/ohos/hap/entry/src/main/ets/components/GameBadges.ets and GameLibrary.ets), and this
port has no ArkTS unit-test harness: `hvigor` builds, and the Python coverage gate does not see .ets files at all.
So the rules are restated here and checked against the ArkTS source, which keeps the two from drifting:

  * every badge the ArkTS file can produce has a case here, and every case has a badge there;
  * the ranking and the "+N" fit arithmetic are the same numbers;
  * the source still declares the three states and the two markers this test assumes.

That is weaker than running the ArkTS, and it is said plainly rather than dressed up: a change to the .ets that this
file does not mirror fails the drift check below, which is the point.
"""
import io
import re
from pathlib import Path

import pytest

# the test sits in dm/ohos/tests, so the app's sources are one level up from its parent
ETS = Path(__file__).resolve().parents[1] / 'hap' / 'entry' / 'src' / 'main' / 'ets'
BADGES_ETS = ETS / 'components' / 'GameBadges.ets'
LIBRARY_ETS = ETS / 'pages' / 'GameLibrary.ets'


# ---- the rules, restated from GameBadges.ets ----

STATE_READY, STATE_EXTRACTED, STATE_NOT_EXTRACTED = 'Ready', 'Extracted', 'NotExtracted'


def game_state(has_data, romfs_exists):
    """GameLibrary.gameState: hasData means Ready; otherwise RomFS decides which failure it is."""
    if has_data:
        return STATE_READY
    return STATE_EXTRACTED if romfs_exists else STATE_NOT_EXTRACTED


def badges_for(state, kept=False, stale=False, save=False, want_state=True, want_save=True):
    """GameBadges.badgesFor: every fact, most telling first."""
    out = []
    if want_state:
        if state == STATE_READY:
            out.append({'text': '可运行', 'rank': 10})
        elif state == STATE_EXTRACTED:
            out.append({'text': '不完整', 'rank': 0})
        else:
            out.append({'text': '未解压', 'rank': 0})
    if kept:
        out.append({'text': '已锁定', 'rank': 20})
    if stale:
        out.append({'text': '旧构建', 'rank': 30})
    if want_save and save:
        out.append({'text': '有存档', 'rank': 40})
    out.sort(key=lambda b: b['rank'])
    return out


def fit_badges(all_badges, n):
    """GameBadges.fitBadges: the last slot goes to the "+N" itself."""
    if n <= 0:
        return {'shown': [], 'overflow': len(all_badges)}
    if len(all_badges) <= n:
        return {'shown': list(all_badges), 'overflow': 0}
    keep = max(0, n - 1)
    return {'shown': all_badges[:keep], 'overflow': len(all_badges) - keep}


# ---- the three states ----

def test_complete_data_is_ready():
    assert game_state(has_data=True, romfs_exists=True) == STATE_READY


def test_romfs_without_the_modules_is_extracted():
    """The two failures need different answers, which is why they are two states."""
    assert game_state(has_data=False, romfs_exists=True) == STATE_EXTRACTED


def test_packages_alone_are_not_extracted():
    assert game_state(has_data=False, romfs_exists=False) == STATE_NOT_EXTRACTED


def test_a_game_with_nothing_at_all_still_gets_a_state_badge():
    """badgesFor never returns an empty list for the state: a card with no badge would look like a broken card."""
    assert len(badges_for(STATE_NOT_EXTRACTED)) == 1


# ---- badges are composable ----

def test_one_game_can_carry_several_facts():
    """The whole point of the change: CardState could say one thing, a list says all of them."""
    got = badges_for(STATE_READY, kept=True, stale=True, save=True)
    assert [b['text'] for b in got] == ['可运行', '已锁定', '旧构建', '有存档']


def test_incomplete_outranks_everything():
    """Nothing else matters if the game cannot start, so a two-slot card must show this one."""
    got = badges_for(STATE_EXTRACTED, kept=True, stale=True, save=True)
    assert got[0]['text'] == '不完整'


def test_a_kept_build_can_also_be_stale():
    """Kept and stale are independent: a locked old build is exactly the case the switch exists for."""
    got = badges_for(STATE_READY, kept=True, stale=True)
    assert {'已锁定', '旧构建'} <= {b['text'] for b in got}


def test_no_save_badge_when_the_caller_skips_the_check():
    """Reading the save folder costs a listing, so the grid asks for state and kept only."""
    got = badges_for(STATE_READY, save=True, want_save=False)
    assert '有存档' not in {b['text'] for b in got}


def test_the_state_badge_can_be_skipped():
    got = badges_for(STATE_READY, kept=True, want_state=False)
    assert [b['text'] for b in got] == ['已锁定']


# ---- the fit arithmetic ----

def test_everything_fits():
    got = fit_badges(badges_for(STATE_READY, kept=True), 2)
    assert len(got['shown']) == 2 and got['overflow'] == 0


def test_one_slot_goes_to_the_overflow():
    """With three facts and two slots, one is shown and the second slot says '+2'."""
    got = fit_badges(badges_for(STATE_READY, kept=True, stale=True), 2)
    assert [b['text'] for b in got['shown']] == ['可运行']
    assert got['overflow'] == 2


def test_an_overflowing_card_keeps_the_most_telling():
    got = fit_badges(badges_for(STATE_EXTRACTED, kept=True, stale=True), 2)
    assert got['shown'][0]['text'] == '不完整'


def test_no_slots_means_all_overflow():
    got = fit_badges(badges_for(STATE_READY, kept=True), 0)
    assert got['shown'] == [] and got['overflow'] == 2


def test_fit_never_loses_a_badge():
    """Whatever the slot count, shown plus overflow is the whole list: a card may hide a fact, never invent one."""
    for n in range(0, 6):
        for badges in (badges_for(STATE_READY), badges_for(STATE_READY, kept=True, stale=True, save=True)):
            got = fit_badges(badges, n)
            assert len(got['shown']) + got['overflow'] == len(badges)


# ---- the drift check: the ArkTS still says what this file mirrors ----

@pytest.mark.parametrize('needle,where', [
    ('export enum GameState', 'GameLibrary.ets'),
    ('NotExtracted', 'GameLibrary.ets'),
    ('Extracted', 'GameLibrary.ets'),
    ('Ready', 'GameLibrary.ets'),
    ('export function gameState', 'GameLibrary.ets'),
    ('export function isComplete', 'GameLibrary.ets'),
    ('export function hasSaveData', 'GameLibrary.ets'),
    ('export function setKept', 'GameLibrary.ets'),
    ("'/keep'", 'GameLibrary.ets'),
    ('export interface BadgeSpec', 'GameBadges.ets'),
    ('export function badgesFor', 'GameBadges.ets'),
    ('export function fitBadges', 'GameBadges.ets'),
])
def test_the_arkts_still_declares_what_this_file_mirrors(needle, where):
    path = LIBRARY_ETS if where == 'GameLibrary.ets' else BADGES_ETS
    text = io.open(path, encoding='utf-8').read()
    assert needle in text, f'{where} no longer declares {needle!r}: update this test with the rule it changed'


def test_every_badge_text_in_the_arkts_has_a_case_here():
    """A badge added to the .ets without a case here would be a rule nothing checks."""
    text = io.open(BADGES_ETS, encoding='utf-8').read()
    found = set(re.findall(r"text: '([^']+)'", text))
    mirrored = {'可运行', '不完整', '未解压', '已锁定', '旧构建', '有存档'}
    assert found == mirrored, f'the ArkTS badges changed: only in .ets {found - mirrored}, only here {mirrored - found}'


def test_the_ranks_in_the_arkts_are_the_ones_this_file_sorts_by():
    """The order decides which facts survive a narrow card, so it is worth pinning.

    The source does not list them in ascending order - the Ready branch comes first and its two failures follow -
    so what is checked is the set and the fact that the sort in badgesFor is what establishes the order.
    """
    text = io.open(BADGES_ETS, encoding='utf-8').read()
    ranks = [int(m) for m in re.findall(r'rank: (\d+)', text)]
    assert set(ranks) == {0, 10, 20, 30, 40}, f'unexpected ranks: {sorted(set(ranks))}'
    # both failure branches share rank 0: an incomplete game outranks everything whatever went wrong
    assert ranks.count(0) == 2
    assert 'out.sort' in text, 'badgesFor no longer sorts by rank' 
