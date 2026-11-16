/*
 * GameBadges.ets - what a game is, as a list of badges (S5).
 *
 * The pattern is NX Play's (varyform/nx-native-wrapper, Packages/NXPlayKit/Views/GameCard+Badges.swift): a game's
 * state is not one enum value but a LIST of independent facts. A game can be complete AND kept AND carry a save -
 * a single CardState could only ever say one of those. Each badge also carries a full sentence of help, because a
 * badge whose meaning needs the docs is a badge the player has to leave the app to read.
 *
 * The badge text is the accessibility label too: it is what a screen reader says and what the "+N" lists when a
 * card cannot fit them all.
 */

import { Semantic } from '../theme/Tokens';
import { GameEntry, GameState, gameState, isComplete, hasSaveData } from '../pages/GameLibrary';

/** One fact about a game. */
export interface BadgeSpec {
  /** Short label: what the card shows and what a screen reader says. */
  text: string;
  /** The token colour it draws in. */
  color: string;
  /** The full sentence: what it means, and what changes it. */
  help: string;
  /** Most telling first: an overflowing card keeps the first ones. */
  rank: number;
}

/** Which badges to build, so the grid and the list can differ without two functions. */
export interface BadgeOptions {
  /** Include the state badge (complete / incomplete). The grid always does; a compact row may not. */
  state?: boolean;
  /** Include the save badge. Reading the save folder costs a directory listing, so a caller may skip it. */
  save?: boolean;
}

/**
 * Every fact about one game, most telling first.
 *
 * Order matters: a card that can only show two badges must show the two that decide whether the game plays. That
 * is why "incomplete" outranks everything - nothing else matters if the game cannot start.
 */
export function badgesFor(g: GameEntry, opts?: BadgeOptions): BadgeSpec[] {
  const o: BadgeOptions = opts ?? {};
  const out: BadgeSpec[] = [];
  const state: GameState = gameState(g);

  if (o.state !== false) {
    if (state === GameState.Ready) {
      out.push({
        text: '可运行',
        color: Semantic.accent,
        help: '游戏数据完整：RomFS 与重写后的模块都在。点卡片开始游戏。',
        rank: 10
      });
    } else if (state === GameState.Extracted) {
      out.push({
        text: '不完整',
        color: Semantic.danger,
        help: 'RomFS 在，但 mods/main.patched.bin 缺失：重写后的模块没有导入。重新导入这个游戏的 .nxpk 可以补上。',
        rank: 0
      });
    } else {
      out.push({
        text: '未解压',
        color: Semantic.danger,
        help: '这个游戏目录里只有包，没有解压出 RomFS。导入一个 .nxpk 游戏包即可。',
        rank: 0
      });
    }
  }

  // A kept build is a promise the player made: it is not rebuilt when the host changes. NX Play calls it "Kept".
  if (g.kept) {
    out.push({
      text: '已锁定',
      color: Semantic.warning,
      help: '这个构建不会因为 host 更新而重建：它保持现在能跑的样子。解锁后重新构建才会替换它。',
      rank: 20
    });
  }

  // A build older than the host it was made from may be missing fixes the host now has. NX Play's "App out of date".
  if (g.stale) {
    out.push({
      text: '旧构建',
      color: Semantic.warning,
      help: '这个构建比当前的 host 代码旧，可能缺少新修的问题。重新构建一次即可更新。',
      rank: 30
    });
  }

  if (o.save !== false && hasSaveData(g)) {
    out.push({
      text: '有存档',
      color: Semantic.success,
      help: '这个游戏在设备上有存档。长按卡片可以备份、恢复或清除它。',
      rank: 40
    });
  }

  out.sort((a: BadgeSpec, b: BadgeSpec): number => a.rank - b.rank);
  return out;
}

/** The badges a card with room for `n` shows, plus how many it had to leave out. */
export interface BadgeFit {
  shown: BadgeSpec[];
  overflow: number;
}

export function fitBadges(all: BadgeSpec[], n: number): BadgeFit {
  if (n <= 0) return { shown: [], overflow: all.length };
  if (all.length <= n) return { shown: all, overflow: 0 };
  // one slot goes to the "+N" itself, so the last shown badge is the (n-1)th
  return { shown: all.slice(0, Math.max(0, n - 1)), overflow: all.length - Math.max(0, n - 1) };
}

/** Every badge's help as one paragraph: what the detail sheet shows under the name. */
export function badgeHelp(g: GameEntry): string {
  const all: BadgeSpec[] = badgesFor(g);
  const parts: string[] = [];
  for (const b of all) parts.push(b.text + '：' + b.help);
  return parts.join('\n');
}
