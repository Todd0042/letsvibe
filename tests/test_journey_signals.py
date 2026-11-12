"""User-journey evidence gathered from repositories without AI."""

from pathlib import Path

from letsvibe.analyzers.journey_signals import gather_journey_signals, journey_context


def _w(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_desktop_web_app_journey(tmp_path):
    _w(tmp_path, "packaging/windows/installer.iss", "[Setup]")
    _w(tmp_path, "packaging/aur/PKGBUILD", "pkgname=x")
    _w(tmp_path, "start.sh", "python main.py")
    _w(tmp_path, "templates/setup.html", "<h1>Welcome! First run setup wizard</h1>")
    _w(tmp_path, "app.py", '''
@app.get("/")
def home(): ...
@app.get("/settings/audio/advanced/mic")
def mic(): ...
@app.get("/favicon.ico")
def icon(): ...
toast("Settings saved")
messagebox.showerror("Error", "Could not open the file, please check the path")
''')
    j = gather_journey_signals(tmp_path, readme="# App\\n## Quick Start\\nRun start.sh")
    assert any("Windows installer" in i for i in j["install"]) and any("Arch" in i for i in j["install"])
    assert "start.sh" in j["launch_scripts"] and j["readme_quickstart"]
    assert "templates/setup.html" in j["first_run_files"]
    assert "/favicon.ico" not in j["screens"]                 # files are not screens
    assert j["settings_max_route_depth"] == 4                 # settings buried four levels deep is flagged
    assert any("saved" in c.lower() for c in j["confirmation_examples"])
    assert j["error_ui_calls"] >= 1 and j["recovery_examples"]
    text = journey_context(j)
    assert "USER JOURNEY EVIDENCE" in text and "deepest settings route depth: 4" in text


def test_android_permissions_and_compose_navigation(tmp_path):
    _w(tmp_path, "app/src/main/AndroidManifest.xml",
       '<uses-permission android:name="android.permission.RECORD_AUDIO"/><activity android:name=".MainActivity"/>')
    _w(tmp_path, "app/build.gradle.kts", "android {}")
    _w(tmp_path, "app/src/main/java/Nav.kt", '''
NavHost(nav, "home") { composable("home") {}; composable("settings") {} }
val p = rememberPermissionState(Manifest.permission.RECORD_AUDIO)
Snackbar { Text("Recording saved") }
''')
    j = gather_journey_signals(tmp_path)
    assert j["android_permissions"] == ["RECORD_AUDIO"] and j["runtime_permission_requests"] >= 1
    assert "home" in j["screens"] and "settings" in j["settings_entry_points"]
    assert any("Android/Gradle" in i for i in j["install"])


def test_firmware_uses_own_code_not_vendored_libraries(tmp_path):
    _w(tmp_path, "platformio.ini", "[env:x]\nboard = esp32-s3-devkitc-1\n")
    _w(tmp_path, "lib/ThirdPartyLib/library.properties", "name=ThirdPartyLib")
    _w(tmp_path, "lib/ThirdPartyLib/platformio.ini", "[env:demo]")
    _w(tmp_path, "lib/ThirdPartyLib/src/Demo.cpp", "void demoScreen() {}")
    _w(tmp_path, "src/ui.cpp", 'void speedScreen() {}\nvoid settingsMenu() {}\nShow("ALL SENSORS CALIBRATED - DONE");')
    files = {"src/ui.cpp": (tmp_path / "src/ui.cpp").read_text()}  # the ingested set excludes vendored libraries
    j = gather_journey_signals(tmp_path, files=files)
    assert j["install"] == ["Firmware build & flash config (PlatformIO) (platformio.ini)"]
    assert "demoScreen" not in j["screens"] and "speedScreen" in j["screens"]
    assert "settingsMenu" in j["settings_entry_points"]
