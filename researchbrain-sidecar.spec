from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH)
datas = [
    (
        str(ROOT / "src" / "researchbrain" / "migrations"),
        "researchbrain/migrations",
    ),
    (str(ROOT / "versions.lock"), "."),
]
binaries = []
hiddenimports = collect_submodules("researchbrain")
for package in (
    "bibtexparser",
    "keyring",
    "lancedb",
    "mcp",
    "pyarrow",
    "pymupdf",
    "rispy",
):
    excluded_prefixes = {
        "keyring": ("keyring.testing",),
        "lancedb": ("lancedb.conftest",),
        "mcp": ("mcp.cli",),
        "pyarrow": ("pyarrow.tests",),
        "pymupdf": ("pymupdf.__main__",),
    }.get(package, ())
    package_filter = lambda name, prefixes=excluded_prefixes: not name.startswith(prefixes)
    package_datas, package_binaries, package_hidden = collect_all(
        package,
        filter_submodules=package_filter,
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(ROOT / "sidecar_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "notebook", "IPython"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="researchbrain-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
