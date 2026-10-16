@echo off
rem Builds the public release: dist\CoW Unit Sprite Suite\ and dist\CoW Unit Sprite Suite.zip
rem
rem   build_release.bat            run the test suites, then build
rem   build_release.bat notests    skip the tests
rem
rem Needs Python with PyInstaller, ttkbootstrap, Pillow and numpy installed.

setlocal
cd /d "%~dp0"

set "NAME=CoW Unit Sprite Suite"
set "OUT=dist\%NAME%"
set "ZIP=dist\%NAME%.zip"

if /i "%~1"=="notests" goto build

echo === Running test suites ===
for %%T in (tools\test_*.py) do (
    echo --- %%~nxT
    python "%%T" > "%TEMP%\cow_test.log" 2>&1
    if errorlevel 1 (
        type "%TEMP%\cow_test.log"
        echo.
        echo FAILED: %%~nxT -- release not built.
        exit /b 1
    )
)

:build
echo.
echo === Building exes (PyInstaller, one-dir) ===
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build\pyinstaller cow_suite.spec
if errorlevel 1 (
    echo PyInstaller failed -- release not built.
    exit /b 1
)

echo.
echo === Copying data beside the exes ===
rem The exes find all of this through tools\approot.py, relative to their own folder.
call :copydir "reference\colortables"                               "%OUT%\reference\colortables" || exit /b 1
call :copydir "pipeline\reference"                                  "%OUT%\pipeline\reference"    || exit /b 1
call :copydir "pipeline\palette"                                    "%OUT%\pipeline\palette"      || exit /b 1
call :copydir "wildcards"                                           "%OUT%\wildcards"             || exit /b 1
call :copydir "samples"                                             "%OUT%\samples"               || exit /b 1
rem The .ico files are build inputs; the promo folder only ships the images
rem (not promo/trailer: the video, its clips and key frames, nor promo/source:
rem the raw screenshots behind images 11-16 -- both stay in the source).
robocopy "promo" "%OUT%\promo" /E /XF *.ico /XD trailer source /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 exit /b 1

copy /y "pipeline\classes.json"            "%OUT%\pipeline\" >nul || exit /b 1
copy /y "pipeline\prompts.json"            "%OUT%\pipeline\" >nul || exit /b 1
copy /y "wildcards.config.example.json"    "%OUT%\" >nul || exit /b 1
copy /y "guide.md"                         "%OUT%\" >nul || exit /b 1
copy /y "readme.md"                        "%OUT%\" >nul || exit /b 1
copy /y "promo.md"                         "%OUT%\" >nul || exit /b 1
copy /y "LICENSE"                          "%OUT%\LICENSE.txt" >nul || exit /b 1
rem Link shortcuts (Commander Wars Discord, homepage, author links).
copy /y "*.url"                            "%OUT%\" >nul || exit /b 1
copy /y "reference\Commander_Wars-LICENSE-LGPL-3.0.txt" "%OUT%\reference\" >nul || exit /b 1

rem docs/ is developer documentation and stays in the source repository.
rem PyInstaller --noconfirm already replaced the whole folder, so nothing stale survives.

echo.
echo === Zipping ===
powershell -NoProfile -Command "Compress-Archive -Path 'dist\%NAME%' -DestinationPath '%ZIP%' -CompressionLevel Optimal -Force"
if errorlevel 1 (
    echo Zipping failed.
    exit /b 1
)

echo.
echo Done: %ZIP%
exit /b 0

:copydir
rem robocopy exit codes 0-7 are success; 8 and up are failures.
robocopy %1 %2 /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Copy failed: %1
    exit /b 1
)
exit /b 0
