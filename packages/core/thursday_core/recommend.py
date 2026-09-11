"""What this machine should run, in words a person chose (EASY INSTALL) — Sprint 63.

The requirement is blunt about the shape of this: *"ผู้ใช้ไม่ต้องเลือก Model เอง"* — the owner
does not pick a model. Thursday inspects the machine and proposes, and what the owner sees is

    FAST      เบาและเร็ว
    BALANCED  แนะนำ
    SMART     เก่งกว่าแต่ใช้ทรัพยากรมาก
    PRIVATE   ในเครื่องเท่านั้น

plus a download size and a disk requirement. Not `llama3.1:8b-instruct-q4_K_M`.

Three rules keep this from being worse than no recommendation at all.

**A recommendation that will not fit is not a recommendation.** Disk is checked before
anything is proposed. Suggesting a 4.8 GB download to a machine with 3 GB free is a failed
install that the owner discovers after the progress bar.

**RAM is not capability.** A laptop with 32 GB and no discrete GPU cannot usefully run what a
16 GB machine with a 4090 runs. VRAM gates the larger classes, which is why
`ComputeProfile.has_gpu` keys on VRAM rather than on a GPU's name (Sprint 54).

**PRIVATE never silently becomes cloud.** This is the one that would be a breach rather than a
disappointment. An owner choosing PRIVATE on a weak machine gets the smallest local model with
a warning that it will be slow — and if literally nothing local can run, they are told that,
not quietly routed to a cloud provider they excluded. The other presets may fall back to cloud;
this one may not, and the difference is the whole meaning of the word.

Nothing here downloads, installs or configures. It produces a *proposal* — §39 requires the
model, its size, its source and its disk cost to be shown before anything is fetched, and this
is what populates that screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from thursday_shared.compute import GIB, ComputeProfile

#: Headroom the operating system and Thursday itself need. A machine with 8 GB does not have
#: 8 GB to give a model, and recommending as though it did is how the first real question
#: swaps to disk and takes a minute to answer.
RESERVED_RAM = 4 * GIB

#: Free disk to leave behind after the download. A full disk is a broken machine, and the
#: owner will remember which program filled it.
DISK_HEADROOM = 5 * GIB


class AIPreset(StrEnum):
    """What the owner picks from. Four words, no model names (§"Auto Model Selection")."""

    FAST = "FAST"
    BALANCED = "BALANCED"
    SMART = "SMART"
    PRIVATE = "PRIVATE"


@dataclass(frozen=True)
class ModelClass:
    """A size of local model, described by what it needs rather than by what it is called.

    Deliberately not a model name. Names change with every release and differ per runtime;
    what does not change is that a 7B-class model wants roughly this much memory. The
    registry maps a class to whatever is actually installed (Sprint 55), so this module never
    has to know that `llama3.1:8b` and `qwen2.5:7b` are the same decision.
    """

    key: str
    label_en: str
    label_th: str
    min_ram_bytes: int
    min_vram_bytes: int
    download_bytes: int

    @property
    def disk_required_bytes(self) -> int:
        return self.download_bytes + DISK_HEADROOM

    def fits(self, profile: ComputeProfile, *, free_disk: int | None = None) -> bool:
        usable_ram = max(0, profile.ram_bytes - RESERVED_RAM)
        if usable_ram < self.min_ram_bytes:
            return False
        if self.min_vram_bytes and profile.vram_bytes < self.min_vram_bytes:
            return False
        disk = profile.disk_free_bytes if free_disk is None else free_disk
        return not (disk and disk < self.disk_required_bytes)


#: The ladder, smallest first. Sizes are the requirement's own worked examples: 8 GB and no
#: GPU gets the lightweight class, 16 GB with 6 GB of VRAM the medium one, 32 GB with 12 GB
#: the large one.
CLASSES: tuple[ModelClass, ...] = (
    ModelClass(
        key="lightweight",
        label_en="a small local model",
        label_th="AI ขนาดเล็กในเครื่อง",
        min_ram_bytes=3 * GIB,
        min_vram_bytes=0,
        download_bytes=2 * GIB,
    ),
    ModelClass(
        key="medium",
        label_en="a mid-sized local model",
        label_th="AI ขนาดกลางในเครื่อง",
        min_ram_bytes=8 * GIB,
        min_vram_bytes=5 * GIB,
        download_bytes=5 * GIB,
    ),
    ModelClass(
        key="large",
        label_en="a large local model",
        label_th="AI ขนาดใหญ่ในเครื่อง",
        min_ram_bytes=20 * GIB,
        min_vram_bytes=11 * GIB,
        download_bytes=20 * GIB,
    ),
)

#: How ambitious each preset is willing to be, and whether it may leave the machine.
_WANTS: dict[AIPreset, str] = {
    AIPreset.FAST: "lightweight",
    AIPreset.BALANCED: "medium",
    AIPreset.SMART: "large",
    AIPreset.PRIVATE: "medium",
}


@dataclass(frozen=True)
class Recommendation:
    """What Thursday proposes, in the shape the setup screen renders."""

    preset: AIPreset
    #: None when nothing local can run here.
    model_class: ModelClass | None
    #: Whether Thursday will use a cloud model as well as, or instead of, a local one.
    uses_cloud: bool
    #: Plain sentences for the owner. Never a model name, never a technical term.
    reasons: tuple[str, ...] = field(default_factory=tuple)
    #: Why something better was not proposed. Shown under "Advanced Options", and the
    #: honest half — an owner told "we picked the small one" deserves to know why.
    limits: tuple[str, ...] = field(default_factory=tuple)

    @property
    def runs_locally(self) -> bool:
        return self.model_class is not None

    @property
    def download_bytes(self) -> int:
        return self.model_class.download_bytes if self.model_class else 0

    @property
    def disk_required_bytes(self) -> int:
        return self.model_class.disk_required_bytes if self.model_class else 0

    def summary(self, *, thai: bool = True) -> str:
        """The one line the setup screen shows, with a size and no jargon."""
        if self.model_class is None:
            return (
                "เครื่องนี้ยังไม่พร้อมใช้ AI ภายในเครื่อง"
                if thai
                else "this machine cannot run a local AI yet"
            )
        label = self.model_class.label_th if thai else self.model_class.label_en
        size = _gb(self.model_class.download_bytes)
        return (
            f"Thursday แนะนำ {label} ขนาด {size}"
            if thai
            else f"Thursday recommends {label}, {size}"
        )


def recommend(
    profile: ComputeProfile,
    *,
    preset: AIPreset = AIPreset.BALANCED,
    free_disk: int | None = None,
    cloud_available: bool = True,
) -> Recommendation:
    """Propose a configuration for this machine.

    `preset` is the owner's stated intent, and it is honoured as far as the hardware allows —
    downwards, never upwards. Asking for SMART on a laptop gets the best that laptop can do
    plus a sentence saying why, rather than a recommendation that will not run.
    """
    wanted = _WANTS[preset]
    ceiling = next(i for i, klass in enumerate(CLASSES) if klass.key == wanted)
    affordable = [
        klass for klass in CLASSES[: ceiling + 1] if klass.fits(profile, free_disk=free_disk)
    ]
    best = affordable[-1] if affordable else None

    reasons: list[str] = []
    limits: list[str] = []

    if best is not None and best.key != wanted:
        limits.append(_why_not(CLASSES[ceiling], profile, free_disk))
    if best is None:
        limits.append(_why_not(CLASSES[0], profile, free_disk))

    if preset is AIPreset.PRIVATE:
        # The rule that makes the word mean something. No cloud, whatever the hardware says.
        if best is None:
            reasons.append("โหมดส่วนตัวใช้ได้เฉพาะ AI ในเครื่อง และเครื่องนี้ยังรันไม่ไหว")
        else:
            reasons.append("โหมดส่วนตัว: ข้อมูลไม่ออกจากเครื่องนี้")
            if best.key == "lightweight" and wanted != "lightweight":
                reasons.append("จะช้ากว่าปกติ แต่ยังทำงานได้")
        return Recommendation(
            preset, best, uses_cloud=False, reasons=tuple(reasons), limits=tuple(limits)
        )

    if best is None:
        reasons.append(
            "เครื่องนี้ยังไม่พร้อมรัน AI ในเครื่อง Thursday จะใช้ AI บนคลาวด์แทน"
            if cloud_available
            else "เครื่องนี้ยังไม่พร้อมรัน AI ในเครื่อง และยังไม่ได้ตั้งค่า AI บนคลาวด์"
        )
        return Recommendation(
            preset, None, uses_cloud=cloud_available, reasons=tuple(reasons), limits=tuple(limits)
        )

    reasons.append("ใช้ AI ในเครื่องเป็นหลัก ข้อมูลส่วนใหญ่ไม่ออกจากเครื่อง")
    if cloud_available:
        reasons.append("งานที่ยากเป็นพิเศษจะขอใช้ AI บนคลาวด์ช่วย")
    return Recommendation(
        preset, best, uses_cloud=cloud_available, reasons=tuple(reasons), limits=tuple(limits)
    )


def _why_not(klass: ModelClass, profile: ComputeProfile, free_disk: int | None) -> str:
    """One sentence saying what stopped a bigger choice. Checked in the order a person
    would ask: is there room on disk, is there enough memory, is there a graphics card."""
    disk = profile.disk_free_bytes if free_disk is None else free_disk
    if disk and disk < klass.disk_required_bytes:
        return f"พื้นที่ดิสก์ว่างไม่พอสำหรับตัวที่ใหญ่กว่า (ต้องการ {_gb(klass.disk_required_bytes)})"
    if max(0, profile.ram_bytes - RESERVED_RAM) < klass.min_ram_bytes:
        return f"แรมไม่พอสำหรับตัวที่ใหญ่กว่า (ต้องการประมาณ {_gb(klass.min_ram_bytes + RESERVED_RAM)})"
    if klass.min_vram_bytes and profile.vram_bytes < klass.min_vram_bytes:
        return (
            "เครื่องนี้ไม่มีการ์ดจอแยก จึงใช้ตัวที่เล็กกว่า"
            if not profile.has_gpu
            else f"หน่วยความจำการ์ดจอไม่พอ (ต้องการ {_gb(klass.min_vram_bytes)})"
        )
    return "เครื่องนี้เหมาะกับตัวที่เล็กกว่า"


def _gb(value: int) -> str:
    return f"{value / GIB:.1f} GB".replace(".0 GB", " GB")
