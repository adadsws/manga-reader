from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
avd = ROOT / "~temp/avd/ReaderAosp35.avd"
avd.mkdir(parents=True, exist_ok=True)
config = avd / "config.ini"
if not config.exists():
    config.write_text(
        """AvdId=ReaderAosp35
avd.ini.encoding=UTF-8
abi.type=x86_64
hw.cpu.arch=x86_64
hw.cpu.ncore=4
hw.ramSize=3072
hw.lcd.width=1080
hw.lcd.height=1920
hw.lcd.density=320
hw.gpu.enabled=yes
hw.gpu.mode=auto
hw.keyboard=yes
hw.mainKeys=no
hw.audioInput=no
hw.audioOutput=yes
tag.id=default
tag.display=Default
PlayStore.enabled=false
disk.dataPartition.size=6442450944
fastboot.forceColdBoot=yes
"""
        + "image.sysdir.1="
        + (
            ROOT / "~temp/android-sdk/system-images/android-35/default/x86_64"
        ).as_posix()
        + "/\n",
        encoding="utf-8",
    )
(avd.parent / "ReaderAosp35.ini").write_text(
    "avd.ini.encoding=UTF-8\npath=" + str(avd) + "\ntarget=android-35\n",
    encoding="utf-8",
)
print(avd)
