"""Force OpenOCD to connect to the target under hardware reset.

The STM32F4-Discovery's ST-LINK drives the MCU's NRST line, so asserting
reset during connect lets uploading succeed even when the previously flashed
firmware breaks the SWD link (sleep/stop mode or remapped PA13/PA14 pins).

The board config file (board/stm32f4discovery.cfg) sets
``reset_config ... connect_deassert_srst`` and OpenOCD processes options in
order with last-one-wins, so the override must be inserted *after* the -f
board config but *before* the "-c program ..." command.
"""

Import("env")

flags = list(env.get("UPLOADERFLAGS", []))

insert_at = len(flags)
for i, flag in enumerate(flags):
    if flag == "-c" and i + 1 < len(flags) and "program" in flags[i + 1]:
        insert_at = i
        break

flags[insert_at:insert_at] = [
    "-c",
    "reset_config srst_only srst_nogate connect_assert_srst",
]
env.Replace(UPLOADERFLAGS=flags)
