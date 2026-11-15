/*
 * ControllerProbe.ets - what the device reports about gamepads, and what keys they actually send (P2.4).
 *
 * OHOS exposes no joystick axis to an app (the NDK's Input_AxisEvent carries only mouse scroll, pinch and
 * rotate), so the plan is to drive dm_pad_set from the gamepad's *buttons*, which arrive as ordinary key codes.
 * That plan lives or dies on one unknown: does a paired gamepad's button reach this app as a KeyCode at all, and
 * which codes does it use?
 *
 * This probe answers it on the device. It lists every input device with its source types and reports each key
 * code as it arrives, so a single press of each button builds the mapping table from evidence instead of a guess.
 *
 * Reached from the settings page's version row (three taps), so it costs no UI on the product path.
 */

import { inputDevice } from '@kit.InputKit';

/** One attached input device, as the probe shows it. */
export interface DeviceRow {
  id: number;
  name: string;
  sources: string;
  /** True when any of its sources is 'joystick'. */
  isPad: boolean;
}

/** A key code the probe has seen, with how many times. */
export interface KeySeen {
  code: number;
  count: number;
  /** The last device id that sent it, so a pad's keys can be told from the keyboard's. */
  deviceId: number;
}

/** List the attached input devices, flagging the gamepads. */
export async function listInputDevices(): Promise<DeviceRow[]> {
  const out: DeviceRow[] = [];
  try {
    const ids: number[] = await inputDevice.getDeviceIds();
    for (const id of ids) {
      try {
        const d: inputDevice.InputDeviceData = await inputDevice.getDevice(id);
        const srcs: string[] = (d.sources ?? []).map((s: string): string => s);
        out.push({
          id: id,
          name: d.name ?? `device ${id}`,
          sources: srcs.join(', '),
          isPad: srcs.indexOf('joystick') >= 0
        });
      } catch (e) {
        // a device that vanished between the list and the read
      }
    }
  } catch (e) {
    // no permission, or the service is unavailable: an empty list says so honestly
  }
  out.sort((a: DeviceRow, b: DeviceRow): number => a.id - b.id);
  return out;
}

/** The mapping the product would use, keyed by the OHOS key codes a gamepad reports. */
export interface PadKeyMap {
  code: number;
  /** The Switch button bit (DM_BTN_*). */
  bit: number;
  label: string;
}

/**
 * The gamepad mapping, from OHOS's own key code table.
 *
 * The first version of this table guessed 2017..2026 for the face and shoulder buttons and was wrong for every
 * one of them: OHOS puts the gamepad at 2301..2315 (`KEYCODE_BUTTON_A = 2301`), while 2012..2015 really are the
 * d-pad. The device proved it - pressing Select and Start logged 2311 and 2312, codes the guessed table had never
 * heard of - so the mapping comes from `@ohos.multimodalInput.keyCode` now.
 *
 * The Switch bits are DM_BTN_* from dm_input.h.
 */
export const PAD_KEY_CANDIDATES: PadKeyMap[] = [
  { code: 2301, bit: 1 << 0, label: 'A' },        // KEYCODE_BUTTON_A
  { code: 2302, bit: 1 << 1, label: 'B' },        // KEYCODE_BUTTON_B
  { code: 2304, bit: 1 << 2, label: 'X' },        // KEYCODE_BUTTON_X
  { code: 2305, bit: 1 << 3, label: 'Y' },        // KEYCODE_BUTTON_Y
  { code: 2307, bit: 1 << 6, label: 'L' },        // KEYCODE_BUTTON_L1
  { code: 2308, bit: 1 << 7, label: 'R' },        // KEYCODE_BUTTON_R1
  { code: 2309, bit: 1 << 8, label: 'ZL' },       // KEYCODE_BUTTON_L2
  { code: 2310, bit: 1 << 9, label: 'ZR' },       // KEYCODE_BUTTON_R2
  { code: 2312, bit: 1 << 10, label: 'Plus' },    // KEYCODE_BUTTON_START
  { code: 2311, bit: 1 << 11, label: 'Minus' },   // KEYCODE_BUTTON_SELECT
  { code: 2012, bit: 1 << 13, label: 'Up' },      // KEYCODE_DPAD_UP
  { code: 2013, bit: 1 << 15, label: 'Down' },    // KEYCODE_DPAD_DOWN
  { code: 2014, bit: 1 << 12, label: 'Left' },    // KEYCODE_DPAD_LEFT
  { code: 2015, bit: 1 << 14, label: 'Right' },   // KEYCODE_DPAD_RIGHT
];

/** The Switch button bit for an OHOS key code, or 0 when the code is not a pad button. */
export function padBitFor(code: number): number {
  for (const m of PAD_KEY_CANDIDATES) {
    if (m.code === code) return m.bit;
  }
  return 0;
}
