// OHOS port: cheats are off. hle_cheats.c is Apple-centric (mach vm_write, dispatch queues, blocks) and excluded from the
// OHOS build; these no-ops satisfy dm.h's declarations so the menu / lifecycle / present callers link. No cheat ever loads or runs.
#include <stddef.h>

void dm_cheats_init(void) {}
void dm_cheats_present(void) {}
void dm_cheats_exit(void) {}
void dm_cheats_reload(void) {}
void dm_cheat_toggle(int i) { (void)i; }
void dm_cheats_master_set(int on) { (void)on; }
void dm_cheats_safe_restore(void) {}

int dm_cheats_found(void) { return 0; }
int dm_cheats_ncheats(void) { return 0; }
int dm_cheats_nothers(void) { return 0; }
int dm_cheat_kind_at(int i) { (void)i; return 0; }
int dm_cheat_on_at(int i) { (void)i; return 0; }
int dm_cheat_flags_at(int i) { (void)i; return 0; }
int dm_cheat_pair_at(int i) { (void)i; return -1; }
int dm_cheats_master(void) { return 0; }
int dm_cheats_safe(void) { return 0; }
int dm_cheats_nskipped(void) { return 0; }
int dm_cheats_nsources(void) { return 0; }
int dm_cheat_preview(int i, char *out, size_t cap) { (void)i; if (out && cap) out[0] = 0; return 0; }
int dm_cheat_value_get(int i, char *out, size_t cap) { (void)i; if (out && cap) out[0] = 0; return 0; }
int dm_cheat_value_set(int i, const char *text) { (void)i; (void)text; return -1; }

const char *dm_cheats_build(void) { return ""; }
const char *dm_cheats_dir_path(void) { return ""; }
const char *dm_cheats_other(int i) { (void)i; return ""; }
const char *dm_cheat_name_at(int i) { (void)i; return ""; }
const char *dm_cheat_why_at(int i) { (void)i; return ""; }
const char *dm_cheat_section_at(int i) { (void)i; return ""; }
const char *dm_cheat_note_at(int i) { (void)i; return ""; }
const char *dm_cheat_type_at(int i) { (void)i; return ""; }
const char *dm_cheat_value_bounds(int i) { (void)i; return ""; }
const char *dm_cheats_report(void) { return ""; }
const char *dm_cheats_version(void) { return ""; }
const char *dm_cheats_other_version(int i) { (void)i; return ""; }
const char *dm_cheats_skipped_name(int i) { (void)i; return ""; }
const char *dm_cheats_skipped_why(int i) { (void)i; return ""; }
const char *dm_cheats_source(int i) { (void)i; return ""; }