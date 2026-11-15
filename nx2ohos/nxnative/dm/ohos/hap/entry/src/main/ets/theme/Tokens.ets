/*
 * Tokens.ets - the design system's single source of truth.
 *
 * Every colour, spacing, size, radius and motion value in the game library comes from here. Screen and component
 * code never writes a raw hex value: a theme change is one edit in this file, and the contrast ratios below are
 * the ones the design was checked against (DESIGN-S1-DesignSystem.md).
 *
 * The dark palette is the only one: a game library is used in a dim room, and a light variant would need its own
 * contrast pass before it could ship.
 */

/** Background layers, darkest to lightest. Depth comes from these four, not from borders. */
export class Bg {
  /** The app background. Not pure black: pure black smears on OLED when the screen scrolls. */
  static readonly base: string = '#0B1220';
  /** Panels and bottom sheets, one step above the page. */
  static readonly elevated: string = '#131C2E';
  /** A game card at rest. */
  static readonly card: string = '#1A2438';
  /** A game card under focus or press. */
  static readonly cardHover: string = '#22304A';
  /** The scrim behind a sheet or a dialog. */
  static readonly overlay: string = 'rgba(4,8,16,0.72)';
}

/** Text. The ratios in the comments are against Bg.base; secondary text must stay at or above 3:1. */
export class Text {
  /** Titles and game names. 15.8:1 */
  static readonly primary: string = '#F1F5F9';
  /** Metadata: size, version, state. 6.4:1 */
  static readonly secondary: string = '#94A3B8';
  /** Hints and placeholders. 3.4:1 - decorative only, never the sole carrier of meaning. */
  static readonly tertiary: string = '#64748B';
  /** Text drawn on an accent-filled button. */
  static readonly inverse: string = '#0B1220';
}

/** Semantic colours: focus, actions, state. */
export class Semantic {
  /** The focus ring, the primary call to action, the progress fill. */
  static readonly accent: string = '#EC4899';
  static readonly accentHover: string = '#F472B6';
  /** The glow behind a focused card. */
  static readonly accentGlow: string = 'rgba(236,72,153,0.32)';
  /** Destructive actions. */
  static readonly danger: string = '#EF4444';
  static readonly dangerHover: string = '#F87171';
  /** A game that can run. */
  static readonly success: string = '#22C55E';
  /** A game whose data is incomplete. */
  static readonly warning: string = '#F59E0B';
  /** Hairlines. Never a solid grey: a translucent white sits correctly on every layer. */
  static readonly borderSubtle: string = 'rgba(255,255,255,0.08)';
  /** The 2 dp ring on a focused element. */
  static readonly borderFocus: string = '#EC4899';
  /** The track behind a progress fill. */
  static readonly track: string = 'rgba(255,255,255,0.08)';
}

/**
 * Placeholder cover backgrounds, picked by the title ID so a game keeps its colour across restarts.
 *
 * A pack with no icon still has to be recognisable at a glance: six muted, equally dark hues that keep white
 * initials readable and never compete with the accent pink.
 */
export class CoverPalette {
  static readonly colors: string[] = [
    '#1E3A5F',   // deep blue
    '#3B1F4E',   // deep violet
    '#1F3B2E',   // deep green
    '#3B2A1F',   // deep brown
    '#3B1F2A',   // deep red
    '#1F3340'    // deep teal
  ];

  /** Stable per title: the character codes summed, modulo the palette size. */
  static forTitle(titleId: string): string {
    let sum: number = 0;
    for (let i = 0; i < titleId.length; i++) sum += titleId.charCodeAt(i);
    return CoverPalette.colors[sum % CoverPalette.colors.length];
  }

  /** The first character of the display name, for the placeholder's watermark. */
  static initialOf(name: string): string {
    const trimmed: string = name.trim();
    return trimmed.length ? trimmed.substring(0, 1).toUpperCase() : '?';
  }

  /** '#RRGGBB' -> 'rgba(r,g,b,a)': the gradient base, the glow and the ambient wash all share one hue per game. */
  static rgba(hex: string, a: number): string {
    const r: number = parseInt(hex.substring(1, 3), 16);
    const g: number = parseInt(hex.substring(3, 5), 16);
    const b: number = parseInt(hex.substring(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  /** '#RRGGBB' scaled by f (< 1 darkens): the placeholder gradient's lower stop, kept in the same hue. */
  static darken(hex: string, f: number): string {
    const r: number = Math.round(parseInt(hex.substring(1, 3), 16) * f);
    const g: number = Math.round(parseInt(hex.substring(3, 5), 16) * f);
    const b: number = Math.round(parseInt(hex.substring(5, 7), 16) * f);
    const to2: (n: number) => string = (n: number): string => n.toString(16).padStart(2, '0');
    return `#${to2(r)}${to2(g)}${to2(b)}`;
  }
}

/** Spacing, a 4 dp rhythm. */
export class Space {
  static readonly s1: number = 4;
  static readonly s2: number = 8;
  static readonly s3: number = 12;
  static readonly s4: number = 16;
  static readonly s5: number = 24;
  static readonly s6: number = 32;
  static readonly s7: number = 48;
  static readonly s8: number = 64;
}

/** Font sizes with their line heights. The system family is used throughout: it covers Chinese. */
export class Font {
  static readonly display: number = 32;
  static readonly displayLine: number = 40;
  static readonly title: number = 20;
  static readonly titleLine: number = 28;
  static readonly body: number = 16;
  static readonly bodyLine: number = 24;
  static readonly label: number = 14;
  static readonly labelLine: number = 20;
  static readonly caption: number = 12;
  static readonly captionLine: number = 16;
  /** The initial on a placeholder cover. */
  static readonly coverInitial: number = 56;
  static readonly coverInitialLine: number = 64;
}

/** Corner radii. */
export class Radius {
  static readonly sm: number = 8;
  static readonly md: number = 12;
  static readonly lg: number = 16;
  static readonly full: number = 999;
}

/**
 * Motion. Durations in milliseconds.
 *
 * Every animation is state feedback, not decoration: a press confirms the touch landed, a focus move shows where
 * the pointer went. Under a reduced-motion preference all of them collapse to zero (see Motion.enabled).
 */
export class Motion {
  static readonly fast: number = 120;    // press feedback, colour changes
  static readonly base: number = 200;    // focus moves, sheet in/out
  static readonly slow: number = 320;    // page transitions
  /** A pressed card shrinks to this scale. */
  static readonly scalePress: number = 0.97;
  /** A focused card grows to this scale. */
  static readonly scaleFocus: number = 1.06;
  /** Fast in, slow out: the curve that reads as "responsive" rather than "floaty". */
  static readonly curve: Curve = Curve.FastOutSlowIn;

  /**
   * Whether to animate at all. ArkTS has no media-query API for the reduced-motion setting, so this is a switch
   * the app sets once from its own preference; every component multiplies its duration by it.
   */
  static enabled: boolean = true;

  static duration(ms: number): number {
    return Motion.enabled ? ms : 0;
  }
}

/** Stacking order. Higher covers lower. */
export class Z {
  static readonly base: number = 0;
  static readonly sticky: number = 10;
  static readonly sheet: number = 20;
  static readonly modal: number = 30;
  static readonly toast: number = 40;
}

/** Component sizes that more than one file needs to agree on. */
export class Size {
  /** The top bar's height. */
  static readonly topBar: number = 64;
  /** A game card's cover edge in the landscape grid (a square, like the console's home screen). */
  static readonly cardCoverLandscape: number = 260;
  /** The cover edge in the portrait list row. */
  static readonly cardCoverPortrait: number = 96;
  /** The portrait row's height. */
  static readonly cardRowPortrait: number = 112;
  /** Buttons. Every one is at least the 48 dp touch target. */
  static readonly buttonLg: number = 48;
  static readonly buttonMd: number = 40;
  static readonly buttonSm: number = 32;
  /** The minimum touch target, applied through hit slop where a control is drawn smaller. */
  static readonly touchTarget: number = 48;
  /** The progress bar's track height. */
  static readonly progressHeight: number = 8;
  /** The thin progress bar in the top bar during an import. */
  static readonly progressHeightThin: number = 4;
  /** A focus ring's width. */
  static readonly focusRing: number = 2;
  /** The glow radius behind a focused card. */
  static readonly focusGlow: number = 8;
  /** The drag handle on a bottom sheet. */
  static readonly sheetHandleWidth: number = 40;
  static readonly sheetHandleHeight: number = 4;
  /** A dialog's width in landscape; in portrait it is the screen width minus Space.s6. */
  static readonly dialogWidth: number = 480;
}
