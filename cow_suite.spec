# PyInstaller spec for the release build. Run via build_release.bat.
#
# Three windowed exes, ONE one-dir COLLECT: all land in the same folder and
# share a single _internal/ (they need the same numpy / Pillow / ttkbootstrap),
# so the data folders beside them -- reference/, pipeline/, wildcards/ -- serve
# all three. No data is bundled into _internal/: the tools find it through
# tools/approot.py, which anchors to the exe's folder when frozen.

from PyInstaller.utils.hooks import collect_all

tb_datas, tb_binaries, tb_hidden = collect_all("ttkbootstrap")

EXCLUDES = ["matplotlib", "scipy", "pandas", "IPython", "pytest", "PyQt5", "PySide6"]


def analysis(script):
    return Analysis(
        [script],
        pathex=["tools"],
        binaries=tb_binaries,
        datas=tb_datas,
        hiddenimports=tb_hidden,
        excludes=EXCLUDES,
        noarchive=False,
    )


def exe(a, name, icon):
    return EXE(
        PYZ(a.pure),
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        icon=icon,
        console=False,
        upx=False,
    )


prev = analysis("tools/previewer.py")
wild = analysis("tools/wildcard_gui.py")
fixer = analysis("tools/aifix_gui.py")

# One icon per exe, the same tank in three armies (tools/build_promo.py), so the
# programs are told apart in the taskbar. guikit.py reads it back for the title bar.
prev_exe = exe(prev, "CoW Sprite Previewer", "promo/icon_previewer.ico")
wild_exe = exe(wild, "CoW Wildcard Generator", "promo/icon_wildcards.ico")
fixer_exe = exe(fixer, "CoW AI Sprite Fixer", "promo/icon_aifix.ico")

coll = COLLECT(
    prev_exe, prev.binaries, prev.datas,
    wild_exe, wild.binaries, wild.datas,
    fixer_exe, fixer.binaries, fixer.datas,
    upx=False,
    name="CoW Unit Sprite Suite",
)
